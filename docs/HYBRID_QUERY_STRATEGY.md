# Hybrid Query Strategy: Database Filters + Document Search

**Status:** proposed, not implemented yet · **Date:** 2026-09-26

## 1. The problem

Users ask questions in a prompt. Some are plain questions, which the current RAG pipeline answers from the top few chunks:

> "Who issued the invoice for Half of You?"

Others combine **exact filters on database columns** with **conditions on document content**, and ask for a **list**:

> "Show me all the assets whose provenance includes *X* and *Y*, only paintings by Pablo Picasso, between 1959 and 1970."

| Part of the request | Kind of condition | Where the data lives |
|---|---|---|
| paintings, by Pablo Picasso, 1959–1970 | Exact filters | MySQL columns |
| provenance includes X and Y | Condition on document content | Chunks in ChromaDB |
| "show me **all**" | A list of assets, not an answer | — |

Provenance is only one example. Conditions can refer to any document type (valuation reports, certificates, exhibition literature, …) and can be fuzzy, so the strategy must not depend on exact matching or on extracting specific fields per document type.

## 2. Why the current pipeline can't answer this

**Top-k search misses results.** The pipeline asks Chroma for the 3 most similar chunks. If 30 assets match, the user sees at most 3.

**Filtering after search misses results too.** Searching the whole account, then keeping only chunks whose asset came back from SQL, fails when other assets' chunks fill the top n. The candidates are crowded out before the filter runs. The filter has to be part of the search query (*pre-filtering*), not applied after it (*post-filtering*).

**Similarity is a ranking, not a yes/no.** Vector search always returns the n closest chunks, and something is always closest. An embedding captures a chunk's overall meaning ("a list of past owners"), so a single name inside it barely moves the vector. For "provenance includes Seiferheld", a provenance chunk *with* Seiferheld and one *without* it score almost the same (e.g. 0.45 vs 0.41), and no score threshold reliably separates them.

## 3. The strategy: filter exactly, then rank by relevance

```
User prompt
   │
   ▼
1. PLAN      LLM turns the prompt into structured filters + a document query     (1 LLM call)
   │
   ▼
2. FILTER    SQL with the exact filters + accountId      → candidate asset IDs
   │
   ▼
3. SEARCH    Chroma, restricted to the candidates, n = all their chunks
   │
   ▼
4. RANK      Group chunks by asset, score each asset by its best chunk
   │
   ▼
5. RESPOND   Ranked list of assets, each with its best evidence (snippet + file + page)
             and a notice that results may include partial matches
```

The result is **exact on the database filters** and **ranked on the document condition**. Every candidate asset appears in the list; the most relevant come first. The user judges each item from the evidence shown next to it.

### Prerequisite: minimal chunk metadata

Chunk metadata holds **only what links a chunk to the database, or what the database doesn't know**. Everything else (file name, category, asset name, artist, …) stays in MySQL and is joined in at response time. Data that lives only in SQL can't go stale in ChromaDB: renaming a file or changing its category needs no re-ingest.

| Field | Source | Why it's on the chunk |
|---|---|---|
| `accountId` | Catalog | Tenant filter. The plain-question route searches the whole account with no SQL step, and it's defence in depth if a bug ever produces a wrong asset list |
| `assetId` | Catalog (`file.entityId`) | Links the chunk to its asset; used to restrict search to the SQL candidates |
| `fileId` | S3 key | An asset has many files: needed to replace or delete one file's chunks on re-ingest, and the key for joining file name and category from SQL |
| `page` | Parser | Not in the database; only known while parsing |

```python
doc.metadata["accountId"] = entry.account_id
doc.metadata["assetId"] = entry.entity_id    # file.entityId (entityType = ASSET)
doc.metadata["fileId"] = entry.file_id
doc.metadata["page"] = page_number           # set by the PDF parser
```

> The current happy-path code stores `docId` (= fileId) and `fileName`; it will be aligned to these four fields when this strategy is implemented.

### Step 1: Plan

The LLM **translates** the prompt into a fixed, whitelisted structure. It never writes SQL. LangChain's `with_structured_output` returns a validated Pydantic object:

```python
from pydantic import BaseModel, Field


class AssetFilters(BaseModel):
    """Exact filters on asset columns. Only these fields are allowed."""
    artist: str | None = None
    object_type: str | None = None          # e.g. "painting"
    year_from: int | None = None
    year_to: int | None = None


class QueryPlan(BaseModel):
    """How to answer one user prompt."""
    kind: str = Field(description="'answer' for a question, 'list' for 'show me all ...'")
    filters: AssetFilters
    document_query: str | None = Field(
        None, description="The part of the request about document content, rewritten as a search query"
    )


planner = make_llm().with_structured_output(QueryPlan)
plan = planner.invoke(
    "Show me all the assets whose provenance includes X and Y, "
    "only paintings by Pablo Picasso between 1959 and 1970"
)
# QueryPlan(kind='list',
#           filters=AssetFilters(artist='Pablo Picasso', object_type='painting', year_from=1959, year_to=1970),
#           document_query='provenance includes X and Y')
```

`kind` also acts as the router: `answer` goes to the existing top-k pipeline, `list` to this strategy.

#### Mapping user wording to database values (enums)

Users write "Fine Art"; the database stores `asset.type = 'FINE_ART'`. The planner does this translation, and validation guarantees it can only produce real values:

1. **The schema lists the exact DB values**, with readable labels in the description:
   ```python
   from enum import Enum


   class AssetType(str, Enum):
       """Mirrors the asset.type enum in MySQL."""
       FINE_ART = "FINE_ART"
       WINE = "WINE"
       FURNITURE = "FURNITURE"
       DEC_ART = "DEC_ART"
       WATCHES = "WATCHES"
       # ... the other asset.type values


   class AssetFilters(BaseModel):
       asset_type: AssetType | None = Field(
           None,
           description="Asset type. FINE_ART = Fine Art (paintings, drawings, prints, sculpture), "
                       "DEC_ART = Decorative Art, WATCHES = Watches, WINE = Wine, ...",
       )
   ```
2. **The LLM maps the user's wording** ("Fine Art", "fine-art", "artworks") to `FINE_ART`. The labels are what make less obvious phrasings work ("decorative objects" → `DEC_ART`).
3. **Pydantic rejects anything else.** An invented value like `"PAINTING"` fails validation instead of reaching SQL.
4. **SQL uses the value as-is:** `a.type = %(asset_type)s`.

**Keeping the Python enum in sync with MySQL:** hard-code it, plus a test that reads `information_schema.columns` for `asset.type` and fails if the values differ. (The alternative, building the enum at startup, is always in sync but makes the schema dynamic and harder to read.) The same pattern applies to every enum column (`conditionStatus`, `status`, `insurance`, …). Free-text columns such as the artist name need a lookup against the `maker` table instead.

### Step 2: Filter with SQL

The code, not the LLM, turns the filters into **parameterized** SQL, and always adds the account:

```python
# Column names are placeholders: check the real asset schema (asset / maker / category tables).
FILTER_SQL = {
    "artist":      "m.name = %(artist)s",
    "object_type": "a.objectType = %(object_type)s",
    "year_from":   "a.year >= %(year_from)s",
    "year_to":     "a.year <= %(year_to)s",
}


def candidate_asset_ids(catalog, account_id: int, filters: AssetFilters) -> list[int]:
    """Asset IDs of one account that pass every exact filter."""
    given = filters.model_dump(exclude_none=True)
    conditions = ["a.accountId = %(account_id)s"] + [FILTER_SQL[name] for name in given]
    sql = (
        "SELECT a.id FROM asset a LEFT JOIN maker m ON m.id = a.makerId "
        "WHERE " + " AND ".join(conditions)
    )
    rows = catalog._query(sql, {"account_id": account_id, **given})
    return [row["id"] for row in rows]
```

Only whitelisted fields can reach the query, the values are parameters (no SQL injection), and `accountId` can't be forgotten.

### Step 3: Search only the candidates, n = all

The candidate filter goes **into** the Chroma query. `n_results` is set to the number of chunks the candidates have, so nothing is cut off:

```python
def search_candidates(collection, embeddings, query: str, account_id: int, asset_ids: list[int]) -> dict:
    """Every chunk of the candidate assets, sorted by similarity to the query."""
    where = {"$and": [
        {"accountId": account_id},
        {"assetId": {"$in": asset_ids}},    # pre-filtering: only the SQL results
    ]}
    total = len(collection.get(where=where, include=[])["ids"])   # n = "all"
    return collection.query(
        query_embeddings=[embeddings.embed_query(query)],
        n_results=total,
        where=where,
    )
```

### Step 4: Rank assets

Chroma ranks **chunks**; the user wants **assets**. Each asset is scored by its best chunk, and that chunk becomes its evidence:

```python
def rank_assets(results: dict, asset_ids: list[int]) -> list[dict]:
    """One entry per candidate asset, best match first, with its strongest evidence."""
    best = {}  # assetId -> best hit
    for text, meta, distance in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        score = 1 - distance
        asset_id = meta["assetId"]
        if asset_id not in best or score > best[asset_id]["score"]:
            best[asset_id] = {
                "assetId": asset_id,
                "score": score,
                "evidence": text,
                "fileId": meta["fileId"],
                "page": meta["page"],
            }
    ranked = sorted(best.values(), key=lambda hit: hit["score"], reverse=True)
    # Candidates with no documents at all still belong in the list, at the end.
    without_docs = [{"assetId": a, "score": None, "evidence": None, "fileId": None, "page": None}
                    for a in asset_ids if a not in best]
    return ranked + without_docs
```

### Step 5: Respond

The display names aren't on the chunks, so they're joined from SQL in **one query** for the whole list:

```python
def add_display_names(catalog, ranked: list[dict]) -> list[dict]:
    """Attach asset name, file name and category from MySQL (one query for all results)."""
    file_ids = [hit["fileId"] for hit in ranked if hit["fileId"]]
    asset_ids = [hit["assetId"] for hit in ranked]
    files = {row["fileId"]: row for row in catalog._query(
        "SELECT fileId, name, category FROM file WHERE fileId IN %(ids)s", {"ids": file_ids})}
    assets = {row["id"]: row["name"] for row in catalog._query(
        "SELECT id, name FROM asset WHERE id IN %(ids)s", {"ids": asset_ids})}
    for hit in ranked:
        hit["assetName"] = assets.get(hit["assetId"])
        if hit["fileId"]:
            hit["source"] = f"{files[hit['fileId']]['name']}, page {hit['page']}"
    return ranked
```

(`pymysql` expands a list parameter in `IN %(ids)s`; that's two queries in total, whatever the number of results.)

Each asset is shown with its evidence, so the user can verify it at a glance, plus a notice about partial matches:

```
Ranked by relevance to "provenance includes X and Y". Results may include partial matches.

1. Asset 1843  "Femme assise"     "Provenance: X, Paris; Y, New York ..."   (PROV-1843.pdf, page 1)
2. Asset 2290  "Tête de femme"    "Provenance: Y, London; private ..."      (report-2290.pdf, page 3)
3. Asset 1704  "Nature morte"     "Provenance: Galerie Z, Basel ..."        (invoice-1704.pdf, page 2)
4. Asset 2318  —                  no documents
```

## 4. Why this is acceptable in production, and when it isn't

It is a standard search pattern: exact where the data is exact, ranked where it is fuzzy. It holds up under three conditions:

1. **Evidence next to every result.** A notice alone is skimmed past; a snippet with file and page is actually checked.
2. **Ranking quality is measured.** Add "list" questions with known correct assets to the evals and track how many of the top results are right (precision at top 5 / 10).
3. **Known blind spots are stated.** Assets whose documents have no extractable text (scanned PDFs, images, which aren't supported yet) can't rank correctly, so a true match may appear low or under "no documents".

**It is not enough** where the list is taken as exact fact: compliance checks, reports ("we hold 12 such works"), or exports acted on without review. Those need the optional verification step below, or human review.

## 5. Optional improvements

**Batched LLM labeling.** One LLM call receives the ranked assets with their evidence and labels each one "matches" / "unclear", without dropping anything. Its cost is 1 call (or a few batches), not one per asset:

```python
prompt = "Criterion: {criterion}\n\n" + "\n\n".join(
    f"Asset {hit['assetId']}:\n{hit['evidence']}" for hit in ranked if hit["evidence"]
) + "\n\nFor each asset, answer 'matches' or 'unclear' and quote the supporting text."
```

**Keyword boost for explicit values.** When the plan contains literal values (names, dealers, auction houses), assets whose chunks contain them can be moved to the top. Chroma filters on text with `where_document={"$contains": "..."}`. This is a boost, not a requirement, since many conditions have no literal values.

**More evidence per asset.** Show the best 2–3 chunks per asset instead of 1 when conditions span several documents.

## 6. Costs and limits

| Item | Cost / limit |
|---|---|
| LLM calls | 1 (plan), +1 per batch if labeling is enabled |
| Embeddings | 1 (the document query) |
| SQL | 1 query for candidates + 1–2 for display names |
| Chroma `$in` list | Fine for hundreds of asset IDs; for thousands, tighten the SQL filters or search in batches |
| Chunks returned | n = all candidate chunks; with thousands of chunks, cap per asset (e.g. best 3) before responding |

## 7. Open questions

1. Where do artist, object type and year live in the MySQL schema (`asset`, `maker`, `category`, …)? The whitelist in step 2 depends on it.
2. What do typical list requests look like? Collect real examples to add as list evals.
3. How are results shown in the UI (pagination, how many items, how evidence is displayed)?
4. Which use cases need exact lists (compliance, reporting) and therefore the verification step?
