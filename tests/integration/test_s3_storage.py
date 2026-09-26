"""Integration tests for S3Storage against the real MinIO container.

Uses a dedicated bucket that is created for the test module and deleted afterwards.
"""

import pytest

from app.config import S3Settings
from app.storage import S3Storage, StorageError

pytestmark = pytest.mark.integration

TEST_BUCKET = "overstone-rag-test"


@pytest.fixture(scope="module")
def storage():
    s = S3Storage(S3Settings(bucket=TEST_BUCKET))
    s.ensure_bucket()
    yield s
    for key in s.list_documents():
        s.delete_document(key)
    s.client.delete_bucket(Bucket=TEST_BUCKET)


def test_ensure_bucket_is_idempotent(storage):
    storage.ensure_bucket()  # already exists: must not raise


def test_bytes_round_trip(storage):
    data = bytes(range(256)) * 10
    storage.put_object("roundtrip.bin", data, "application/octet-stream")

    obj = storage.get_object("roundtrip.bin")

    assert obj.body == data
    assert obj.key == "roundtrip.bin"
    assert obj.content_type == "application/octet-stream"
    assert obj.etag and obj.last_modified is not None


def test_json_round_trip(storage, sample_asset):
    assert storage.upload_document("accounts/account-123/asset-456.json", sample_asset)

    assert storage.download_document("accounts/account-123/asset-456.json") == sample_asset
    assert storage.get_object("accounts/account-123/asset-456.json").content_type == "application/json"


def test_upload_file_guesses_content_type(storage, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")

    storage.upload_file(path, "accounts/account-123/notes.txt")

    assert storage.get_object("accounts/account-123/notes.txt").content_type == "text/plain"


def test_missing_object_raises_storage_error(storage):
    with pytest.raises(StorageError):
        storage.get_object("does/not/exist.json")


def test_download_document_returns_none_when_missing(storage):
    assert storage.download_document("does/not/exist.json") is None


def test_list_filters_by_prefix(storage):
    storage.put_object("accounts/account-789/a.json", b"{}")
    storage.put_object("accounts/account-789/b.json", b"{}")

    keys = storage.list_documents("accounts/account-789/")

    assert sorted(keys) == ["accounts/account-789/a.json", "accounts/account-789/b.json"]


def test_list_paginates_past_1000_keys(storage):
    # list_objects_v2 returns at most 1000 keys per call.
    for i in range(1005):
        storage.put_object(f"many/{i:04d}.txt", b"x")

    assert len(storage.list_documents("many/")) == 1005
