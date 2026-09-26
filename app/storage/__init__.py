"""Storage module for document management."""

from app.storage.s3_storage import S3Storage, StorageError, StoredObject

__all__ = ["S3Storage", "StorageError", "StoredObject"]
