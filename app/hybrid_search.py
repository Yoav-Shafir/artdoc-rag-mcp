"""Hybrid search: database filters + document search, for "show me all ..." prompts.

    prompt -> plan() -> candidate_asset_ids() -> search_candidates() -> rank_assets()

1. The planner splits the prompt into exact filters and a document query.
2. SQL finds the account's assets that pass the filters (the candidates).
3. Chroma searches only the candidates' chunks, all of them (not just the top 3).
4. Chunks are grouped by asset; each asset is scored by its best chunk.
The result is exact on the filters and ranked on the document condition.
See docs/HYBRID_QUERY_STRATEGY.md.

Run: uv run poe hybrid
"""

from app.db import Database
from app.happy_path import make_collection, make_embeddings, make_llm
from app.planner import plan

# Mocked "logged-in account": account 7 owns Picasso assets with ingested files.
ACCOUNT_ID = 7
PROMPT = "Show me Picasso works whose provenance includes Sotheby's"


def search_candidates(collection, embeddings, query: str, account_id: int, asset_ids: list[int]) -> dict:
    """Every chunk of the candidate assets, sorted by similarity to the query."""
    # The candidate filter is part of the query itself (pre-filtering), so other
    # assets' chunks can never crowd the candidates out of the results.
    where = {"$and": [
        {"accountId": account_id},  # tenant filter, always
        {"assetId": {"$in": asset_ids}},  # only the assets SQL returned
    ]}
    # n = "all": count the candidates' chunks, then ask for exactly that many.
    total = len(collection.get(where=where, include=[])["ids"])
    if total == 0:
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}
    return collection.query(
        query_embeddings=[embeddings.embed_query(query)],
        n_results=total,
        where=where,
    )


def rank_assets(results: dict, asset_ids: list[int]) -> list[dict]:
    """One entry per candidate asset, best match first, with its strongest evidence."""
    best = {}  # assetId -> the best-scoring chunk seen so far for that asset
    for text, meta, distance in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        score = 1 - distance  # cosine distance -> similarity (1.0 = same direction)
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
    # Candidates without any chunks still belong in the answer, at the end.
    without_docs = [
        {"assetId": asset_id, "score": None, "evidence": None, "fileId": None, "page": None}
        for asset_id in asset_ids
        if asset_id not in best
    ]
    return ranked + without_docs


def main():
    """Run the whole hybrid chain for one prompt and print the ranked assets."""
    db, llm = Database(), make_llm()
    embeddings, collection = make_embeddings(), make_collection()

    # 1. Plan: split the prompt into filters + document query.
    query_plan = plan(PROMPT, llm)
    print(f"PROMPT: {PROMPT}")
    print(f"  filters:        {query_plan.filters.model_dump(exclude_none=True, mode='json')}")
    print(f"  document_query: {query_plan.document_query}")

    # 2. SQL: the account's assets that pass every filter.
    asset_ids = db.candidate_asset_ids(ACCOUNT_ID, query_plan.filters)
    print(f"  candidates:     {len(asset_ids)} assets {asset_ids}")

    # 3 + 4. Search the candidates' chunks, then rank the assets.
    results = search_candidates(
        collection, embeddings, query_plan.document_query or PROMPT, ACCOUNT_ID, asset_ids
    )
    ranked = rank_assets(results, asset_ids)

    # Show each asset with its evidence. Names aren't stored on chunks, so they
    # come from MySQL (one lookup per result for now; batch it later).
    print("\nRanked by relevance. Results may include partial matches.")
    for number, hit in enumerate(ranked, start=1):
        if hit["fileId"] is None:
            print(f"{number}. asset {hit['assetId']}: no documents")
            continue
        record = db.lookup_file(hit["fileId"])
        evidence = " ".join(hit["evidence"].split())[:150]
        print(f"{number}. asset {hit['assetId']} {record.asset_name!r}  score {hit['score']:.3f}")
        print(f"     {record.name}, page {hit['page']}: {evidence!r}")


if __name__ == "__main__":
    main()
