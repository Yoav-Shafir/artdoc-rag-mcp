"""Tests for app.config."""

import pytest
from pydantic import ValidationError

from app.config import ChromaSettings, ChunkingSettings


def test_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("CHROMA_PORT", "9999")
    assert ChromaSettings().port == 9999


def test_chunk_overlap_must_be_smaller_than_size():
    with pytest.raises(ValidationError, match="must be less than"):
        ChunkingSettings(size=100, overlap=100)


def test_chunk_overlap_zero_is_allowed():
    assert ChunkingSettings(size=100, overlap=0).overlap == 0


def test_sample_asset_fixture_has_identity_fields(sample_asset):
    assert {"accountId", "assetId", "title"} <= sample_asset.keys()
