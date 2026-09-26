"""Base document parser interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


from typing import Optional


@dataclass
class ParsedDocument:
    """Represents a parsed document."""

    id: str
    title: str
    content: str
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class DocumentParser(ABC):
    """Abstract base class for document parsers."""

    @abstractmethod
    def parse(self, data: dict) -> ParsedDocument:
        """Parse document data into structured format.

        Args:
            data: Raw document data

        Returns:
            ParsedDocument with structured content
        """
        pass

    @abstractmethod
    def validate(self, data: dict) -> bool:
        """Validate if data is in expected format.

        Args:
            data: Raw document data

        Returns:
            True if valid, False otherwise
        """
        pass
