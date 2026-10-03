"""One entry point for any user query.

    plan -> (SQL candidates, only if there are filters) -> vector search -> list or answer

1. plan(): the LLM splits the query into filters, a document query and a kind.
2. Filters filled in -> MySQL narrows the search to the matching assets.
   No filters -> no SQL; the search covers the whole account.
3. Vector search within that scope.
   A document type ("certificates", "insurance policies") narrows the search to chunks
   of that file category, exactly, without SQL.
4. kind decides the output:
   'answer' -> the top chunks go to the LLM, which writes an answer with citations.
   'list'   -> the best matching assets (score cutoff, see cut_noise()), ranked by their
               best chunk; or, for a document type alone, every asset that has one.

Run: uv run poe ask "Who issued the invoice for Interieur?"
"""

import sys

from app.config import config
from app.db import Database
from app.hybrid_search import rank_assets
from app.planner import plan
from app.rag import answer, make_collection, make_embeddings, make_llm

ACCOUNT_ID = 46  # mocked logged-in account
ANSWER_CHUNKS = 3  # how many chunks the LLM sees for an 'answer' query


def ask(query: str, account_id: int, db, llm, embeddings, collection) -> dict:
    """Plan the query, search within its scope, and return a list or an answer."""
    query_plan = plan(query, llm)
    filters = query_plan.filters.model_dump(exclude_none=True, mode="json")
    category = query_plan.document_category.value if query_plan.document_category else None
    result = {
        "kind": query_plan.kind,
        "filters": filters,
        "document_category": category,
        "document_query": query_plan.document_query,
    }

    # Scope: the account, narrowed to the SQL candidates only when there are filters.
    where = {"accountId": account_id}  # tenant filter, always
    if category:
        # The document type lives on the chunks, so it narrows the Chroma search directly.
        where = {"$and": [where, {"category": category}]}
    asset_ids = None
    if filters:
        asset_ids = db.candidate_asset_ids(account_id, query_plan.filters)
        if not asset_ids:  # nothing passes the filters: nothing to search
            if query_plan.kind == "list":
                result["assets"] = []
            else:
                result["answer"] = "No assets match the filters."
            return result
        where = {"$and": [where, {"assetId": {"$in": asset_ids}}]}

    if query_plan.kind == "list":
        if not query_plan.document_query:
            if category:
                # Document type only ("which assets have a certificate?"): an exact answer,
                # the assets that have at least one chunk of that type. No scores, no cutoff.
                stored = collection.get(where=where, include=["metadatas"])["metadatas"]
                result["assets"] = [
                    {"assetId": asset_id, "score": None}
                    for asset_id in sorted({meta["assetId"] for meta in stored})
                ]
            else:
                # Filters only ("show me all paintings"): the SQL result is the answer.
                result["assets"] = [{"assetId": asset_id, "score": None} for asset_id in asset_ids or []]
            add_asset_names(db, result["assets"])
            return result
        # All chunks in scope, so no matching asset is cut off; then one entry per asset.
        total = len(collection.get(where=where, include=[])["ids"])
        chunks = vector_search(collection, embeddings, query_plan.document_query, where, total)
        result["assets"] = cut_noise(rank_assets(chunks, asset_ids or []))
        add_asset_names(db, result["assets"])
        return result

    # 'answer': only the best few chunks, then the LLM writes the answer.
    chunks = vector_search(collection, embeddings, query_plan.document_query or query, where, ANSWER_CHUNKS)
    hits = to_hits(db, chunks)
    result["hits"] = hits
    result["answer"] = answer(llm, query, hits) if hits else "No documents found."
    return result


def cut_noise(ranked: list[dict]) -> list[dict]:
    """Keep the best-scoring assets of a ranked 'list' result, dropping the noise tail.

    Every asset in scope gets a similarity score, even ones unrelated to the query
    (e.g. 0.006 for a sofa's valuation report), so without a cutoff a 'list' query
    returns the whole account. Assets without documents (score None) are dropped too:
    they cannot match a document query.
    """
    kept = [
        asset for asset in ranked
        if asset["score"] is not None and asset["score"] >= config.retrieval.list_min_score
    ]
    return kept[: config.retrieval.list_max_assets]


def add_asset_names(db, assets: list[dict]) -> None:
    """Add "assetName" to each asset (names live in MySQL, not on the chunks)."""
    names = db.asset_names([asset["assetId"] for asset in assets])
    for asset in assets:
        asset["assetName"] = names.get(asset["assetId"])


def vector_search(collection, embeddings, text: str, where: dict, n: int) -> dict:
    """Chroma query: the n chunks closest in meaning to `text`, within `where`."""
    empty = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
    if n == 0:
        return empty
    return collection.query(query_embeddings=[embeddings.embed_query(text)], n_results=n, where=where)


def to_hits(db, chunks: dict) -> list[dict]:
    """Chroma's result -> one dict per chunk, with a readable label for citations."""
    hits = []
    for text, meta, distance in zip(chunks["documents"][0], chunks["metadatas"][0], chunks["distances"][0]):
        file_name = db.lookup_file(meta["fileId"]).name  # names live in MySQL, not on chunks
        hits.append({
            "text": text,
            "metadata": meta,
            "score": 1 - distance,  # cosine distance -> similarity
            "label": f"{file_name}, page {meta['page']}",
        })
    return hits


def main():
    """Ask one query from the command line and print how it was handled."""
    query = " ".join(sys.argv[1:]) or "Who issued the invoice for Interieur?"
    db, llm = Database(), make_llm()
    embeddings, collection = make_embeddings(), make_collection()

    result = ask(query, ACCOUNT_ID, db, llm, embeddings, collection)
    print(f"QUERY: {query}")
    print(
        f"  kind: {result['kind']} | filters: {result['filters']} | "
        f"category: {result['document_category']} | document_query: {result['document_query']}"
    )

    if result["kind"] == "list":
        print(f"\n{len(result['assets'])} asset(s), best match first:")
        for number, asset in enumerate(result["assets"], start=1):
            score = "no documents" if asset["score"] is None else f"score {asset['score']:.3f}"
            print(f"{number}. asset {asset['assetId']} {asset['assetName']!r}  {score}")
    else:
        print(f"\nA: {result['answer']}")
        for number, hit in enumerate(result.get("hits", []), start=1):
            print(f"   [{number}] {hit['label']} ({hit['metadata'].get('category')})")


if __name__ == "__main__":
    main()
