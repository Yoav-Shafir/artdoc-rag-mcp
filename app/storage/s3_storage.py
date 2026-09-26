"""S3/MinIO storage implementation."""

import json
import logging
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# This doesn't import the config module - it imports the specific
# config variable (the AppSettings instance)
from app.config import config

logger = logging.getLogger(__name__)

DEFAULT_CONTENT_TYPE = "application/octet-stream"


class StorageError(Exception):
    """Raised when a storage operation fails."""


@dataclass
class StoredObject:
    """A raw object read from storage: its bytes plus source metadata."""

    key: str
    body: bytes
    content_type: str
    etag: str
    last_modified: Optional[datetime] = None


class S3Storage:
    """Manages document storage in S3/MinIO."""

    def __init__(self, cfg: Optional[Any] = None):
        """Initialize S3 storage client.

        Args:
            cfg: Configuration object (uses global config if None)
        """
        # If no custom config is passed in, it falls back to config.s3
        self.cfg = cfg or config.s3
        self.client = self._create_client()
        self.bucket = self.cfg.bucket

    def _create_client(self):
        """Create and return S3 client."""
        return boto3.client(
            "s3",
            endpoint_url=self.cfg.endpoint_url,
            aws_access_key_id=self.cfg.access_key,
            aws_secret_access_key=self.cfg.secret_key,
            region_name=self.cfg.region,
            config=Config(
                signature_version="s3v4", s3={"addressing_style": "path"}
            ),
        )

    def ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist yet.

        Raises:
            StorageError: If the bucket can't be checked or created
        """
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
                raise StorageError(f"Failed to check bucket {self.bucket}: {e}") from e
        except BotoCoreError as e:
            raise StorageError(f"Failed to check bucket {self.bucket}: {e}") from e

        try:
            self.client.create_bucket(Bucket=self.bucket)
            logger.info(f"Created bucket {self.bucket}")
        except (ClientError, BotoCoreError) as e:
            raise StorageError(f"Failed to create bucket {self.bucket}: {e}") from e

    def put_object(
        self, key: str, body: bytes, content_type: str = DEFAULT_CONTENT_TYPE
    ) -> None:
        """Store raw bytes at a key.

        Raises:
            StorageError: If the upload fails
        """
        try:
            self.client.put_object(
                Bucket=self.bucket, Key=key, Body=body, ContentType=content_type
            )
            logger.info(f"Uploaded s3://{self.bucket}/{key} ({len(body)} bytes)")
        except (ClientError, BotoCoreError) as e:
            raise StorageError(f"Failed to upload {key}: {e}") from e

    def get_object(self, key: str) -> StoredObject:
        """Read an object's raw bytes and source metadata.

        Raises:
            StorageError: If the object is missing or can't be read
        """
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response["Body"].read()
        except (ClientError, BotoCoreError) as e:
            raise StorageError(f"Failed to download {key}: {e}") from e

        logger.info(f"Downloaded s3://{self.bucket}/{key} ({len(body)} bytes)")
        return StoredObject(
            key=key,
            body=body,
            content_type=response.get("ContentType", DEFAULT_CONTENT_TYPE),
            etag=response.get("ETag", "").strip('"'),
            last_modified=response.get("LastModified"),
        )

    def upload_file(self, local_path: Path, key: str) -> None:
        """Upload a local file, guessing its content type from the extension.

        Raises:
            StorageError: If the upload fails
        """
        content_type = mimetypes.guess_type(local_path.name)[0] or DEFAULT_CONTENT_TYPE
        self.put_object(key, Path(local_path).read_bytes(), content_type)

    def upload_document(self, key: str, document: dict) -> bool:
        """Upload a dict as a JSON document.

        Args:
            key: S3 object key/path
            document: Document data as dictionary

        Returns:
            True if successful, False otherwise
        """
        try:
            self.put_object(key, json.dumps(document).encode("utf-8"), "application/json")
            return True
        except StorageError as e:
            logger.error(str(e))
            return False

    def download_document(self, key: str) -> Optional[dict]:
        """Download a JSON document as a dict.

        Args:
            key: S3 object key/path

        Returns:
            Document data or None if not found or not valid JSON
        """
        try:
            return json.loads(self.get_object(key).body.decode("utf-8"))
        except (StorageError, UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.error(f"Failed to download document {key}: {e}")
            return None

    def list_documents(self, prefix: str = "") -> list[str]:
        """List all object keys under a prefix, across every result page.

        Args:
            prefix: Optional prefix to filter documents

        Returns:
            List of document keys ("folder" placeholder keys ending in / are skipped)
        """
        try:
            keys = []
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys.extend(
                    obj["Key"]
                    for obj in page.get("Contents", [])
                    if not obj["Key"].endswith("/")
                )
            logger.info(f"Listed {len(keys)} documents with prefix '{prefix}'")
            return keys
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to list documents: {e}")
            return []

    def delete_document(self, key: str) -> bool:
        """Delete document from S3.

        Args:
            key: S3 object key/path

        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            logger.info(f"Deleted document from s3://{self.bucket}/{key}")
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to delete document: {e}")
            return False

    def document_exists(self, key: str) -> bool:
        """Check if document exists in S3.

        Args:
            key: S3 object key/path

        Returns:
            True if document exists, False otherwise
        """
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except (ClientError, BotoCoreError):
            return False
