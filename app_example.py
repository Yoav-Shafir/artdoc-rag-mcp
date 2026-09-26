"""Example usage of the Web Platform RAG application."""

import logging
from app import IngestionService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Demonstrate the ingestion pipeline."""
    logger.info("Initializing Web Platform RAG Application")

    service = IngestionService()

    logger.info("Services initialized:")
    stats = service.get_ingestion_stats()
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    logger.info("\n" + "=" * 60)
    logger.info("Testing Document Ingestion")
    logger.info("=" * 60 + "\n")

    s3_key = "accounts/account-123/asset-456.json"

    logger.info(f"Ingesting document from: {s3_key}")
    success = service.ingest_document(s3_key)

    if success:
        logger.info("✅ Document ingestion successful\n")

        logger.info("=" * 60)
        logger.info("Testing Search")
        logger.info("=" * 60 + "\n")

        query = "painting blue abstract"
        logger.info(f"Searching for: '{query}'")
        results = service.search(query, n_results=5)

        if results and results.get("ids"):
            logger.info(f"Found {len(results['ids'][0])} results:\n")
            for i, (doc_id, distance, metadata) in enumerate(
                zip(
                    results["ids"][0],
                    results.get("distances", [[]])[0],
                    results.get("metadatas", [[]])[0],
                )
            ):
                logger.info(
                    f"{i+1}. Document: {metadata.get('title', 'N/A')} "
                    f"(distance: {distance:.3f})"
                )
        else:
            logger.warning("No search results found")
    else:
        logger.error("❌ Document ingestion failed")

    logger.info("\n" + "=" * 60)
    logger.info("Final Statistics")
    logger.info("=" * 60)
    stats = service.get_ingestion_stats()
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")


if __name__ == "__main__":
    main()
