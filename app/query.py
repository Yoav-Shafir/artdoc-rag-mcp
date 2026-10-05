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
        "version": RESPONSE_VERSION,  # reply format version
        "query": query,  # the user's question
        "kind": None,  # "answer" or "list"
        # How the question was understood
        "plan": {
            "filters": {},  # asset details asked for, e.g. furniture; used to filter assets in MySQL
            "document_category": None,  # document type asked for, e.g. certificate; used to filter the document search
            "document_query": None,  # words searched in the documents
            "explanation": None,  # the AI's reading of the question
        },
        # How the search went
        "trace": {
            "sql_candidates": None,  # assets matching the details
            "chunks_searched": 0,  # text pieces checked
            "documents_searched": 0,  # documents checked
            "cutoff": None,  # minimum match and maximum results
            "guardrail": None,  # safety check result
            "timings_ms": {},  # time per step
        },
        "answer": None,  # the written answer
        "citations": [],  # sources of the answer
        "assets": [],  # matching assets, best first
        "total_assets": 0,  # number of assets found
        "error": None,  # problem message, if any
    }


def error_response(query: str, message: str) -> dict:
    """Same shape as a normal response, with `error` set (bad input, or a failure)."""
    return {**empty_response(query), "error": message}


def ask(query: str, account_id: int, db, llm, embeddings, collection) -> dict:
    """Plan the query, search within its scope, and return a list or an answer."""
    started = time.perf_counter()  # start time, used to measure how long the answer takes
    result = empty_response(query)  # blank reply, filled in step by step below
    # Besides the total, each step gets its own time: guardrail, plan, search, answer
    timings = result["trace"]["timings_ms"]

    # Safety check on the question first (Bedrock guardrail): a blocked question is never planned
    # or searched. "skipped" when no guardrail is configured (e.g. local runs).
    blocked = check_question(query)
    timings["guardrail"] = elapsed_ms(started)

    configured = bool(config.bedrock.guardrail_id and config.bedrock.guardrail_version)
    result["trace"]["guardrail"] = "blocked" if blocked else "passed" if configured else "skipped"
    if blocked:
        result["error"] = blocked
        return finish(result, started)  # stop here: a blocked question is not searched

    plan_started = time.perf_counter()
    query_plan = plan(query, llm)  # the AI works out what the question asks for and how to search
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
        # Ask MySQL for this account's assets that match the details, e.g. all its furniture
        asset_ids = db.candidate_asset_ids(account_id, query_plan.filters)
        result["trace"]["sql_candidates"] = len(asset_ids)
        if not asset_ids:  # MySQL found no matching assets, so there are no documents to search
            if query_plan.kind == "answer":
                result["answer"] = "No assets match the filters."
            return finish(result, started)  # stop here: no assets, so no documents to search
        # Use the asset IDs MySQL found to narrow the document search: every chunk in ChromaDB
        # carries its asset ID, so only chunks of these assets will be searched
        where = {"$and": [where, {"assetId": {"$in": asset_ids}}]}

    search_started = time.perf_counter()
    # collection.get() fetches chunks from ChromaDB by their metadata, like a database lookup:
    # "give me every chunk where accountId is 46 and assetId is one of these".
    # We ask for the metadata only (not the text), just to count chunks and documents.
    # `scope` is a list of dictionaries, one dictionary per chunk, each holding that chunk's metadata, e.g.:
    # (fileIds are UUIDs, shortened here)
    #   [
    #       {"accountId": 46, "assetId": 6681, "fileId": "0e89a21d-….pdf", "page": 1, "category": "PROVENANCE"},
    #       {"accountId": 46, "assetId": 7055, "fileId": "62f4b8c1-….pdf", "page": 1, "category": "VALUATION_REPORTS"}
    #   ]
    scope = collection.get(where=where, include=["metadatas"])["metadatas"]
    in_scope = len(scope)
    result["trace"]["chunks_searched"] = in_scope
    # Distinct files: what users understand ("searched 64 documents"), unlike chunk counts.
    result["trace"]["documents_searched"] = len({meta["fileId"] for meta in scope})

    # kind is the planner's decision about what kind of reply the question needs:
    # The user wants several assets: "Which…", "Show me all…", "Which assets have…"
    if query_plan.kind == "list":
        # Check if we also need to search the documents' content.
        if not query_plan.document_query:
            if category:
                # Only a document type was asked for, e.g. "Which assets have a certificate?".
                # List every asset that has a document of that type. No text search needed:
                # having the document is the answer, so there's no score.
                assets = group_category_chunks_by_asset(scope)  # reuses the chunks already fetched above
            else:
                # Filters only ("show me all paintings"): the SQL result is the answer.
                assets = [{"assetId": asset_id, "score": None, "evidence": []} for asset_id in asset_ids or []]
        else:
            # Search the documents' text and rank every chunk, so no matching asset is missed.
            # Then combine the chunks into one entry per asset, and keep only the best matches.
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


def group_category_chunks_by_asset(chunks: list[dict]) -> list[dict]:
    """Turn the chunks of one document type into a list of assets, one entry per asset.

    Example: for "Which artworks have a certificate of authenticity?"
    (fileIds are the platform's file UUIDs, shortened here)

    Input: 3 chunks, 2 of them from the same asset and file
        [{"assetId": 7035, "fileId": "3f1c9a2e-….pdf", "page": 1, ...},
         {"assetId": 7035, "fileId": "3f1c9a2e-….pdf", "page": 2, ...},
         {"assetId": 7460, "fileId": "8b7d4e10-….pdf", "page": 1, ...}]

    Output: 2 assets, each file listed once
        [{"assetId": 7035, "evidence": [{"fileId": "3f1c9a2e-….pdf", ...}]},
         {"assetId": 7460, "evidence": [{"fileId": "8b7d4e10-….pdf", ...}]}]

    How the input became the output:
      - 3 chunks became 2 assets: asset 7035's two chunks were merged into one entry.
      - The file is listed once: both of 7035's chunks came from the same file (pages 1
        and 2 of one certificate), so it appears once in the evidence.
      - No score, text or page: the match is exact (the asset has a certificate, full
        stop), so there's no relevance score or matching text to show.
      - Sorted by asset ID, since there's no "best match" order.

    Example 2: one asset with two different certificate files
      Input:
        [{"assetId": 7035, "fileId": "3f1c9a2e-….pdf", ...},
         {"assetId": 7035, "fileId": "c52e7f93-….pdf", ...}]   # a second certificate

      Output: one asset, two files in its evidence
        [{"assetId": 7035, "evidence": [{"fileId": "3f1c9a2e-….pdf", ...},
                                        {"fileId": "c52e7f93-….pdf", ...}]}]

    In short: chunks are merged per asset, and evidence is listed per file (never per
    chunk or page).
    """
    assets = {}
    for meta in chunks:
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
    """Ask a question from your terminal, without the Lambda, and print the result.

    Useful for testing a change on your machine before deploying. It runs the same ask()
    the Lambda uses, with account 46 and the settings from your local .env.
    The Lambda never calls this; it only runs when you start this file yourself.

    Example:
        uv run poe ask "Which furniture pieces were bought at Christie's?"
    """
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
