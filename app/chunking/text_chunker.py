"""Text chunking utility."""

import logging
from typing import Optional

from app.config import config

logger = logging.getLogger(__name__)


class TextChunker:
    """Splits text into overlapping chunks."""

    def __init__(
        self,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
    ):
        """Initialize text chunker.

        Args:
            chunk_size: Size of each chunk (uses config if None)
            chunk_overlap: Number of overlapping characters (uses config if None)
        """
        self.chunk_size = chunk_size if chunk_size is not None else config.chunking.size
        self.chunk_overlap = (
            chunk_overlap if chunk_overlap is not None else config.chunking.overlap
        )

        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"Overlap ({self.chunk_overlap}) must be less than chunk size ({self.chunk_size})"
            )

    def chunk(self, text: str) -> list[str]:
        """Split text into overlapping chunks.

        Args:
            text: Text to chunk

        Returns:
            List of text chunks
        """
        if not text or len(text) == 0:
            return []

        chunks = []
        start = 0

        while start < len(text):
            end = start + self.chunk_size
            chunk = text[start:end]
            chunks.append(chunk)

            start = end - self.chunk_overlap

        if chunks:
            logger.debug(
                f"Split text into {len(chunks)} chunks "
                f"(size={self.chunk_size}, overlap={self.chunk_overlap})"
            )

        return chunks

    def chunk_by_sentences(self, text: str) -> list[str]:
        """Split text into chunks respecting sentence boundaries.

        Args:
            text: Text to chunk

        Returns:
            List of text chunks
        """
        sentences = text.split(". ")
        chunks = []
        current_chunk = ""

        for sentence in sentences:
            if len(current_chunk) + len(sentence) > self.chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence
            else:
                if current_chunk:
                    current_chunk += ". "
                current_chunk += sentence

        if current_chunk:
            chunks.append(current_chunk.strip())

        logger.debug(f"Split text into {len(chunks)} sentence-based chunks")
        return chunks
