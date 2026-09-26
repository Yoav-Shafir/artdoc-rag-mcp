"""JSON asset document parser."""

import logging
from typing import Optional

from app.documents.parser import DocumentParser, ParsedDocument

logger = logging.getLogger(__name__)


class JsonAssetParser(DocumentParser):
    """Parser for JSON asset documents."""

    REQUIRED_FIELDS = ["title"]
    ID_FIELDS = ["id", "assetId", "asset_id"]

    def validate(self, data: dict) -> bool:
        """Validate JSON asset data.

        Args:
            data: Asset data to validate

        Returns:
            True if valid, False otherwise
        """
        if not isinstance(data, dict):
            return False

        for field in self.REQUIRED_FIELDS:
            if field not in data:
                logger.warning(f"Missing required field: {field}")
                return False

        has_id = any(field in data for field in self.ID_FIELDS)
        if not has_id:
            logger.warning(f"Missing ID field (one of {self.ID_FIELDS})")
            return False

        return True

    def parse(self, data: dict) -> Optional[ParsedDocument]:
        """Parse JSON asset into structured document.

        Args:
            data: Asset data

        Returns:
            ParsedDocument or None if parsing fails
        """
        if not self.validate(data):
            logger.error("Invalid asset data")
            return None

        try:
            doc_id = next(
                (str(data.get(field, "")) for field in self.ID_FIELDS if field in data),
                ""
            )
            title = str(data.get("title", ""))

            content_parts = [title]
            if "description" in data:
                content_parts.append(str(data["description"]))
            if "category" in data:
                content_parts.append(f"Category: {data['category']}")
            if "artist" in data:
                content_parts.append(f"Artist: {data['artist']}")
            if "year" in data:
                content_parts.append(f"Year: {data['year']}")

            content = "\n".join(content_parts)

            exclude_fields = set(self.ID_FIELDS) | {"title", "description"}
            metadata = {
                k: v
                for k, v in data.items()
                if k not in exclude_fields
            }

            return ParsedDocument(
                id=doc_id, title=title, content=content, metadata=metadata
            )
        except Exception as e:
            logger.error(f"Failed to parse asset: {e}")
            return None
