"""ChromaDB vector store implementation."""

import logging
from typing import Optional

import chromadb

from app.config import config

logger = logging.getLogger(__name__)


class ChromaStore:
    """Manages vector embeddings in ChromaDB."""

    def __init__(self, cfg: Optional[object] = None):
        """Initialize ChromaDB store.

        Args:
            cfg: Configuration object (uses global config if None)
        """
        self.cfg = cfg or config.chroma
        self.client = chromadb.HttpClient(host=self.cfg.host, port=self.cfg.port)
        self.collection_name = self.cfg.collection
        self.collection = None
        self._get_or_create_collection()

    def _get_or_create_collection(self):
        """Get or create the collection."""
        try:
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name
            )
            logger.info(f"Connected to collection: {self.collection_name}")
        except Exception as e:
            logger.error(f"Failed to connect to ChromaDB collection: {e}")
            raise

    def add_documents(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: Optional[list[dict]] = None,
    ) -> bool:
        """Add documents with embeddings to collection.

        Args:
            ids: Document IDs
            documents: Document texts
            embeddings: Embedding vectors
            metadatas: Optional metadata for each document

        Returns:
            True if successful, False otherwise
        """
        if not ids or not documents or not embeddings:
            logger.warning("Missing required data for adding documents")
            return False

        if len(ids) != len(documents) or len(ids) != len(embeddings):
            logger.error("Mismatched number of ids, documents, and embeddings")
            return False

        try:
            self.collection.add(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas or [{} for _ in ids],
            )
            logger.info(f"Added {len(ids)} documents to ChromaDB")
            return True
        except Exception as e:
            logger.error(f"Failed to add documents: {e}")
            return False

    def search(
        self, embedding: list[float], n_results: int = 5
    ) -> Optional[dict]:
        """Search for similar documents.

        Args:
            embedding: Query embedding vector
            n_results: Number of results to return

        Returns:
            Search results or None if search fails
        """
        try:
            results = self.collection.query(query_embeddings=[embedding], n_results=n_results)
            return results
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return None

    def search_text(self, text: str, n_results: int = 5) -> Optional[dict]:
        """Search using text (requires embedding).

        Args:
            text: Query text
            n_results: Number of results to return

        Returns:
            Search results or None if search fails
        """
        try:
            results = self.collection.query(
                query_texts=[text], n_results=n_results
            )
            return results
        except Exception as e:
            logger.error(f"Text search failed: {e}")
            return None

    def get_document(self, doc_id: str) -> Optional[dict]:
        """Get a specific document by ID.

        Args:
            doc_id: Document ID

        Returns:
            Document data or None if not found
        """
        try:
            results = self.collection.get(ids=[doc_id])
            if results["ids"]:
                return {
                    "id": results["ids"][0],
                    "document": results["documents"][0],
                    "embedding": results["embeddings"][0] if results["embeddings"] else None,
                    "metadata": results["metadatas"][0] if results["metadatas"] else {},
                }
            return None
        except Exception as e:
            logger.error(f"Failed to get document: {e}")
            return None

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document from collection.

        Args:
            doc_id: Document ID

        Returns:
            True if successful, False otherwise
        """
        try:
            self.collection.delete(ids=[doc_id])
            logger.info(f"Deleted document {doc_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete document: {e}")
            return False

    def count_documents(self) -> int:
        """Get number of documents in collection.

        Returns:
            Number of documents
        """
        try:
            return self.collection.count()
        except Exception as e:
            logger.error(f"Failed to count documents: {e}")
            return 0

    def clear_collection(self) -> bool:
        """Clear all documents from collection.

        Args:
            Requires deleting and recreating collection

        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.delete_collection(name=self.collection_name)
            self._get_or_create_collection()
            logger.info(f"Cleared collection: {self.collection_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to clear collection: {e}")
            return False
