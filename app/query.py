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

The response shape is the contract with the UI: docs/RESPONSE_CONTRACT.md. Every key
is always present (empty when it doesn't apply), so the UI never checks for missing keys.

Run: uv run poe ask "Who issued the invoice for Interieur?"
"""

import sys
import time

from app.config import config
from app.db import Database
from app.planner import plan
from app.rag import answer, check_question, make_collection, make_embeddings, make_llm

ACCOUNT_ID = 46  # mocked logged-in account
ANSWER_CHUNKS = 3  # how many chunks the LLM sees for an 'answer' query
EVIDENCE_PER_ASSET = 3  # chunks shown under each asset of a ranked 'list'
RESPONSE_VERSION = 1  # bump when the response shape changes (docs/RESPONSE_CONTRACT.md)


def empty_response(query: str) -> dict:
    """The response skeleton: every key the UI reads, with empty values."""
    return {
        "version": RESPONSE_VERSION,
        "query": query,
        "kind": None,
        "plan": {"filters": {}, "document_category": None, "document_query": None, "explanation": None},
        "trace": {
            "sql_candidates": None,
            "chunks_searched": 0,
            "documents_searched": 0,
            "cutoff": None,
            "guardrail": None,
            "timings_ms": {},
        },
        "answer": None,
        "citations": [],
        "assets": [],
        "total_assets": 0,
        "error": None,
    }


def error_response(query: str, message: str) -> dict:
    """Same shape as a normal response, with `error` set (bad input, or a failure)."""
    return {**empty_response(query), "error": message}


def ask(query: str, account_id: int, db, llm, embeddings, collection) -> dict:
    """Plan the query, search within its scope, and return a list or an answer."""
    started = time.perf_counter()
    result = empty_response(query)
    timings = result["trace"]["timings_ms"]

    # Safety check on the question first (Bedrock guardrail): a blocked question is never planned
    # or searched. "skipped" when no guardrail is configured (e.g. local runs).
    blocked = check_question(query)
    timings["guardrail"] = elapsed_ms(started)
    configured = bool(config.bedrock.guardrail_id and config.bedrock.guardrail_version)
    result["trace"]["guardrail"] = "blocked" if blocked else "passed" if configured else "skipped"
    if blocked:
        result["error"] = blocked
        return finish(result, started)

    plan_started = time.perf_counter()
    query_plan = plan(query, llm)
    timings["plan"] = elapsed_ms(plan_started)
    filters = query_plan.filters.model_dump(exclude_none=True, mode="json")
    category = query_plan.document_category.value if query_plan.document_category else None
    result["kind"] = query_plan.kind
    result["plan"] = {
        "filters": filters,
        "document_category": category,
        "document_query": query_plan.document_query,
        "explanation": query_plan.explanation,
    }

    # Scope: the account, narrowed to the SQL candidates only when there are filters.
    where = {"accountId": account_id}  # tenant filter, always
    if category:
        # The document type lives on the chunks, so it narrows the Chroma search directly.
        where = {"$and": [where, {"category": category}]}
    asset_ids = None
    if filters:
        asset_ids = db.candidate_asset_ids(account_id, query_plan.filters)
        result["trace"]["sql_candidates"] = len(asset_ids)
        if not asset_ids:  # nothing passes the filters: nothing to search
            if query_plan.kind == "answer":
                result["answer"] = "No assets match the filters."
            return finish(result, started)
        where = {"$and": [where, {"assetId": {"$in": asset_ids}}]}

    search_started = time.perf_counter()
    scope = collection.get(where=where, include=["metadatas"])["metadatas"]
    in_scope = len(scope)
    result["trace"]["chunks_searched"] = in_scope
    # Distinct files: what users understand ("searched 64 documents"), unlike chunk counts.
    result["trace"]["documents_searched"] = len({meta["fileId"] for meta in scope})

    if query_plan.kind == "list":
        if not query_plan.document_query:
            if category:
                # Document type only ("which assets have a certificate?"): an exact answer,
                # the assets that have at least one chunk of that type. No scores, no cutoff;
                # the evidence is the file of that type (the "why" is the category itself).
                stored = collection.get(where=where, include=["metadatas"])["metadatas"]
                assets = category_assets(stored)
            else:
                # Filters only ("show me all paintings"): the SQL result is the answer.
                assets = [{"assetId": asset_id, "score": None, "evidence": []} for asset_id in asset_ids or []]
        else:
            # All chunks in scope, so no matching asset is cut off; then one entry per asset.
            chunks = vector_search(collection, embeddings, query_plan.document_query, where, in_scope)
            result["trace"]["cutoff"] = {
                "min_score": config.retrieval.list_min_score,
                "max_assets": config.retrieval.list_max_assets,
            }
            assets = cut_noise(group_by_asset(chunks))
        timings["search"] = elapsed_ms(search_started)
        result["assets"] = add_names(db, assets)
        result["total_assets"] = len(result["assets"])
        return finish(result, started)

    # 'answer': only the best few chunks, then the LLM writes the answer.
    chunks = vector_search(collection, embeddings, query_plan.document_query or query, where, ANSWER_CHUNKS)
    timings["search"] = elapsed_ms(search_started)
    citations = to_citations(db, chunks)
    result["citations"] = citations
    answer_started = time.perf_counter()
    if citations:
        # answer() numbers the sources [1], [2], ... in this order, so [n] = citations[n-1].
        sources = [{"label": f"{c['fileName']}, page {c['page']}", "text": c["text"]} for c in citations]
        result["answer"] = answer(llm, query, sources)
    else:
        result["answer"] = "No documents found."
    timings["answer"] = elapsed_ms(answer_started)
    return finish(result, started)


def finish(result: dict, started: float) -> dict:
    """Record the total time and return the response."""
    result["trace"]["timings_ms"]["total"] = elapsed_ms(started)
    return result


def elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def chunk_entry(text, meta: dict, distance) -> dict:
    """One evidence chunk or citation: where it is, how well it matched, and its text."""
    return {
        "assetId": meta["assetId"],
        "fileId": meta["fileId"],
        "page": meta.get("page"),
        "category": meta.get("category"),
        "score": None if distance is None else 1 - distance,  # cosine distance -> similarity
        "text": text,
    }


def group_by_asset(chunks: dict) -> list[dict]:
    """Ranked chunks -> one entry per asset, scored by its best chunk, with its top chunks.

    Chunks arrive best first, so each asset's first chunk is its best and sets its score.
    """
    assets = {}  # assetId -> entry, in order of first (= best) appearance
    for text, meta, distance in zip(chunks["documents"][0], chunks["metadatas"][0], chunks["distances"][0]):
        entry = assets.setdefault(meta["assetId"], {"assetId": meta["assetId"], "score": 1 - distance, "evidence": []})
        if len(entry["evidence"]) < EVIDENCE_PER_ASSET:
            entry["evidence"].append(chunk_entry(text, meta, distance))
    return list(assets.values())


def category_assets(stored: list[dict]) -> list[dict]:
    """Assets that have a document of the filtered category, with that file as evidence."""
    assets = {}
    for meta in stored:
        entry = assets.setdefault(meta["assetId"], {"assetId": meta["assetId"], "score": None, "evidence": []})
        if all(item["fileId"] != meta["fileId"] for item in entry["evidence"]):
            # One entry per file (not per chunk); no text or score: the match is exact.
            entry["evidence"].append({**chunk_entry(None, meta, None), "page": None})
    return sorted(assets.values(), key=lambda asset: asset["assetId"])


def cut_noise(ranked: list[dict]) -> list[dict]:
    """Keep the best-scoring assets of a ranked 'list' result, dropping the noise tail.

    Every asset in scope gets a similarity score, even ones unrelated to the query
    (e.g. 0.006 for a sofa's valuation report), so without a cutoff a 'list' query
    returns the whole account.
    """
    kept = [asset for asset in ranked if asset["score"] >= config.retrieval.list_min_score]
    return kept[: config.retrieval.list_max_assets]


def add_names(db, assets: list[dict]) -> list[dict]:
    """Add rank, assetName and each evidence's fileName (names live in MySQL, not on chunks)."""
    asset_names = db.asset_names([asset["assetId"] for asset in assets])
    file_names = db.file_names(list({item["fileId"] for asset in assets for item in asset["evidence"]}))
    for rank, asset in enumerate(assets, start=1):
        asset["rank"] = rank
        asset["assetName"] = asset_names.get(asset["assetId"])
        for item in asset["evidence"]:
            item["fileName"] = file_names.get(item["fileId"])
    return assets


def vector_search(collection, embeddings, text: str, where: dict, n: int) -> dict:
    """Chroma query: the n chunks closest in meaning to `text`, within `where`."""
    empty = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
    if n == 0:
        return empty
    return collection.query(query_embeddings=[embeddings.embed_query(text)], n_results=n, where=where)


def to_citations(db, chunks: dict) -> list[dict]:
    """Chroma's result -> numbered citations with asset and file names, for an 'answer'."""
    citations = [
        chunk_entry(text, meta, distance)
        for text, meta, distance in zip(chunks["documents"][0], chunks["metadatas"][0], chunks["distances"][0])
    ]
    asset_names = db.asset_names(list({c["assetId"] for c in citations}))
    file_names = db.file_names(list({c["fileId"] for c in citations}))
    for number, citation in enumerate(citations, start=1):
        citation["n"] = number  # matches [n] in the answer text
        citation["assetName"] = asset_names.get(citation["assetId"])
        citation["fileName"] = file_names.get(citation["fileId"])
    return citations


def main():
    """Ask one query from the command line and print how it was handled."""
    query = " ".join(sys.argv[1:]) or "Who issued the invoice for Interieur?"
    db, llm = Database(), make_llm()
    embeddings, collection = make_embeddings(), make_collection()

    result = ask(query, ACCOUNT_ID, db, llm, embeddings, collection)
    query_plan = result["plan"]
    print(f"QUERY: {query}")
    print(
        f"  kind: {result['kind']} | filters: {query_plan['filters']} | "
        f"category: {query_plan['document_category']} | document_query: {query_plan['document_query']}"
    )
    print(f"  explanation: {query_plan['explanation']}")
    print(f"  trace: {result['trace']}")

    if result["kind"] == "list":
        print(f"\n{result['total_assets']} asset(s), best match first:")
        for asset in result["assets"]:
            score = "exact match" if asset["score"] is None else f"score {asset['score']:.3f}"
            print(f"{asset['rank']}. asset {asset['assetId']} {asset['assetName']!r}  {score}")
            for item in asset["evidence"][:1]:
                print(f"     {item['fileName']}, page {item['page']} ({item['category']})")
    else:
        print(f"\nA: {result['answer']}")
        for citation in result["citations"]:
            print(f"   [{citation['n']}] {citation['fileName']}, page {citation['page']} ({citation['category']})")


if __name__ == "__main__":
    main()
