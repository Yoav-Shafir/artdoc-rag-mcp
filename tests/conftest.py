"""Shared pytest fixtures."""

import pytest


@pytest.fixture
def sample_asset() -> dict:
    """An asset in the same shape as the real objects in MinIO.

    `tags` (a list) and `dimensions` (a nested dict) are deliberately included:
    ChromaDB only accepts scalar metadata, so the parser has to flatten them.
    """
    return {
        "accountId": "account-123",
        "assetId": "asset-456",
        "title": "Blue Horizon",
        "artist": "Jane Doe",
        "category": "painting",
        "year": 2021,
        "description": "An abstract painting built from layered shades of blue.",
        "tags": ["abstract", "blue", "oil"],
        "dimensions": {"width_cm": 120, "height_cm": 80},
    }
