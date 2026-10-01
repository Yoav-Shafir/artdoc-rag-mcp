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
    settings = S3Settings(bucket=TEST_BUCKET)
    # These tests create and delete a bucket: only against MinIO, never real AWS S3.
    if not settings.endpoint_url:
        pytest.skip("S3_ENDPOINT_URL is empty (real AWS S3); these tests run against MinIO only")
    s = S3Storage(settings)
    s.ensure_bucket(TEST_BUCKET)
    yield s
    for key in s.list_documents(TEST_BUCKET):
        s.delete_document(TEST_BUCKET, key)
    s.client.delete_bucket(Bucket=TEST_BUCKET)


def test_ensure_bucket_is_idempotent(storage):
    storage.ensure_bucket(TEST_BUCKET)  # already exists: must not raise


def test_bytes_round_trip(storage):
    data = bytes(range(256)) * 10
    storage.put_object(TEST_BUCKET, "roundtrip.bin", data, "application/octet-stream")

    obj = storage.get_object(TEST_BUCKET, "roundtrip.bin")

    assert obj.body == data
    assert obj.key == "roundtrip.bin"
    assert obj.content_type == "application/octet-stream"
    assert obj.etag and obj.last_modified is not None


def test_json_round_trip(storage, sample_asset):
    assert storage.upload_document(TEST_BUCKET, "accounts/account-123/asset-456.json", sample_asset)

    assert storage.download_document(TEST_BUCKET, "accounts/account-123/asset-456.json") == sample_asset
    assert storage.get_object(TEST_BUCKET, "accounts/account-123/asset-456.json").content_type == "application/json"


def test_upload_file_guesses_content_type(storage, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")

    storage.upload_file(TEST_BUCKET, path, "accounts/account-123/notes.txt")

    assert storage.get_object(TEST_BUCKET, "accounts/account-123/notes.txt").content_type == "text/plain"


def test_missing_object_raises_storage_error(storage):
    with pytest.raises(StorageError):
        storage.get_object(TEST_BUCKET, "does/not/exist.json")


def test_download_document_returns_none_when_missing(storage):
    assert storage.download_document(TEST_BUCKET, "does/not/exist.json") is None


def test_list_filters_by_prefix(storage):
    storage.put_object(TEST_BUCKET, "accounts/account-789/a.json", b"{}")
    storage.put_object(TEST_BUCKET, "accounts/account-789/b.json", b"{}")

    keys = storage.list_documents(TEST_BUCKET, "accounts/account-789/")

    assert sorted(keys) == ["accounts/account-789/a.json", "accounts/account-789/b.json"]


def test_list_paginates_past_1000_keys(storage):
    # list_objects_v2 returns at most 1000 keys per call.
    for i in range(1005):
        storage.put_object(TEST_BUCKET, f"many/{i:04d}.txt", b"x")

    assert len(storage.list_documents(TEST_BUCKET, "many/")) == 1005
