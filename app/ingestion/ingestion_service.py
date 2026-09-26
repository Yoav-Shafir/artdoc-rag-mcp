"""Document ingestion orchestration service."""

import logging
from typing import Optional

from app.chunking import TextChunker
from app.documents import JsonAssetParser
from app.embeddings import BedrockEmbeddings
from app.storage import S3Storage
from app.vector_store import ChromaStore

logger = logging.getLogger(__name__)


class IngestionService:
    """Orchestrates the complete document ingestion pipeline."""

    def __init__(
        self,
        storage: Optional[S3Storage] = None,
        parser: Optional[JsonAssetParser] = None,
        chunker: Optional[TextChunker] = None,
        embeddings: Optional[BedrockEmbeddings] = None,
        vector_store: Optional[ChromaStore] = None,
    ):
        """Initialize ingestion service.

        Args:
            storage: S3 storage instance (creates new if None)
            parser: Document parser (creates new if None)
            chunker: Text chunker (creates new if None)
            embeddings: Embeddings generator (creates new if None)
            vector_store: Vector store (creates new if None)
        """
        self.storage = storage or S3Storage()
        self.parser = parser or JsonAssetParser()
        self.chunker = chunker or TextChunker()
        self.embeddings = embeddings or BedrockEmbeddings()
        self.vector_store = vector_store or ChromaStore()

    def ingest_document(self, s3_key: str) -> bool:
        """Ingest a document from S3.

        Steps:
        1. Download document from S3
        2. Parse document
        3. Chunk text
        4. Generate embeddings
        5. Store in vector database

        Args:
            s3_key: S3 object key

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Starting ingestion for: {s3_key}")

        # Step 1: Download from S3
        logger.debug("Step 1: Downloading document from S3")
        raw_document = self.storage.download_document(s3_key)
        if not raw_document:
            logger.error(f"Failed to download document: {s3_key}")
            return False

        # Step 2: Parse document
        logger.debug("Step 2: Parsing document")
        parsed_doc = self.parser.parse(raw_document)
        if not parsed_doc:
            logger.error("Failed to parse document")
            return False

        # Step 3: Chunk text
        logger.debug("Step 3: Chunking text")
        chunks = self.chunker.chunk(parsed_doc.content)
        if not chunks:
            logger.warning("No chunks generated")
            return False

        # Step 4: Generate embeddings
        logger.debug("Step 4: Generating embeddings")
        chunk_embeddings = self.embeddings.embed_texts(chunks)
        if not chunk_embeddings or all(e is None for e in chunk_embeddings):
            logger.error("Failed to generate embeddings")
            return False

        # Step 5: Store in vector database
        logger.debug("Step 5: Storing in vector database")
        chunk_ids = [f"{parsed_doc.id}_chunk_{i}" for i in range(len(chunks))]
        valid_embeddings = []
        valid_chunks = []
        valid_ids = []

        for chunk_id, chunk, embedding in zip(chunk_ids, chunks, chunk_embeddings):
            if embedding is not None:
                valid_ids.append(chunk_id)
                valid_chunks.append(chunk)
                valid_embeddings.append(embedding)

        if not valid_embeddings:
            logger.error("No valid embeddings to store")
            return False

        success = self.vector_store.add_documents(
            ids=valid_ids,
            documents=valid_chunks,
            embeddings=valid_embeddings,
            metadatas=[
                {
                    "document_id": parsed_doc.id,
                    "title": parsed_doc.title,
                    "chunk_index": i,
                    **parsed_doc.metadata,
                }
                for i in range(len(valid_ids))
            ],
        )

        if success:
            logger.info(
                f"Successfully ingested document {parsed_doc.id} "
                f"with {len(valid_ids)} chunks"
            )
        else:
            logger.error("Failed to store embeddings")

        return success

    def search(self, query_text: str, n_results: int = 5) -> Optional[dict]:
        """Search for similar documents.

        Args:
            query_text: Search query
            n_results: Number of results to return

        Returns:
            Search results or None if search fails
        """
        logger.debug(f"Searching for: {query_text}")

        query_embedding = self.embeddings.embed_text(query_text)
        if not query_embedding:
            logger.error("Failed to generate query embedding")
            return None

        results = self.vector_store.search(query_embedding, n_results=n_results)
        if results:
            logger.info(f"Found {len(results.get('ids', [[]])[0])} results")
        return results

    def get_ingestion_stats(self) -> dict:
        """Get ingestion statistics.

        Returns:
            Dictionary with statistics
        """
        return {
            "vector_store_documents": self.vector_store.count_documents(),
            "embeddings_model": self.embeddings.model_name,
            "embeddings_dimension": self.embeddings.get_dimension(),
            "chunk_size": self.chunker.chunk_size,
            "chunk_overlap": self.chunker.chunk_overlap,
        }
