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
    """Reads and writes objects in S3/MinIO. Every call names its bucket."""

    def __init__(self, cfg: Optional[Any] = None):
        """Initialize S3 storage client.

        Args:
            cfg: Configuration object (uses global config if None)
        """
        # If no custom config is passed in, it falls back to config.s3
        self.cfg = cfg or config.s3
        self.client = self._create_client()

    def _create_client(self):
        """S3 client for MinIO when S3_ENDPOINT_URL is set, otherwise for real AWS S3."""
        if self.cfg.endpoint_url:
            # MinIO: its own address and keys; path-style URLs (http://host:9000/bucket/key).
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
        # Real AWS S3: AWS_PROFILE in dev; unset in AWS Batch, where the IAM role is used.
        session = boto3.Session(profile_name=config.bedrock.aws_profile)
        return session.client("s3", region_name=self.cfg.region)

    def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket if it doesn't exist yet.

        Raises:
            StorageError: If the bucket can't be checked or created
        """
        try:
            self.client.head_bucket(Bucket=bucket)
            return
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
                raise StorageError(f"Failed to check bucket {bucket}: {e}") from e
        except BotoCoreError as e:
            raise StorageError(f"Failed to check bucket {bucket}: {e}") from e

        try:
            self.client.create_bucket(Bucket=bucket)
            logger.info(f"Created bucket {bucket}")
        except (ClientError, BotoCoreError) as e:
            raise StorageError(f"Failed to create bucket {bucket}: {e}") from e

    def put_object(
        self, bucket: str, key: str, body: bytes, content_type: str = DEFAULT_CONTENT_TYPE
    ) -> None:
        """Store raw bytes at a key.

        Raises:
            StorageError: If the upload fails
        """
        try:
            self.client.put_object(
                Bucket=bucket, Key=key, Body=body, ContentType=content_type
            )
            logger.info(f"Uploaded s3://{bucket}/{key} ({len(body)} bytes)")
        except (ClientError, BotoCoreError) as e:
            raise StorageError(f"Failed to upload {key}: {e}") from e

    def get_object(self, bucket: str, key: str) -> StoredObject:
        """Read an object's raw bytes and source metadata.

        Raises:
            StorageError: If the object is missing or can't be read
        """
        try:
            response = self.client.get_object(Bucket=bucket, Key=key)
            body = response["Body"].read()
        except (ClientError, BotoCoreError) as e:
            raise StorageError(f"Failed to download {key}: {e}") from e

        logger.info(f"Downloaded s3://{bucket}/{key} ({len(body)} bytes)")
        return StoredObject(
            key=key,
            body=body,
            content_type=response.get("ContentType", DEFAULT_CONTENT_TYPE),
            etag=response.get("ETag", "").strip('"'),
            last_modified=response.get("LastModified"),
        )

    def upload_file(self, bucket: str, local_path: Path, key: str) -> None:
        """Upload a local file, guessing its content type from the extension.

        Raises:
            StorageError: If the upload fails
        """
        content_type = mimetypes.guess_type(local_path.name)[0] or DEFAULT_CONTENT_TYPE
        self.put_object(bucket, key, Path(local_path).read_bytes(), content_type)

    def upload_document(self, bucket: str, key: str, document: dict) -> bool:
        """Upload a dict as a JSON document.

        Args:
            key: S3 object key/path
            document: Document data as dictionary

        Returns:
            True if successful, False otherwise
        """
        try:
            self.put_object(bucket, key, json.dumps(document).encode("utf-8"), "application/json")
            return True
        except StorageError as e:
            logger.error(str(e))
            return False

    def download_document(self, bucket: str, key: str) -> Optional[dict]:
        """Download a JSON document as a dict.

        Args:
            key: S3 object key/path

        Returns:
            Document data or None if not found or not valid JSON
        """
        try:
            return json.loads(self.get_object(bucket, key).body.decode("utf-8"))
        except (StorageError, UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.error(f"Failed to download document {key}: {e}")
            return None

    def list_documents(self, bucket: str, prefix: str = "") -> list[str]:
        """List all object keys under a prefix, across every result page.

        Args:
            prefix: Optional prefix to filter documents

        Returns:
            List of document keys ("folder" placeholder keys ending in / are skipped)
        """
        try:
            keys = []
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
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

    def delete_document(self, bucket: str, key: str) -> bool:
        """Delete document from S3.

        Args:
            key: S3 object key/path

        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.delete_object(Bucket=bucket, Key=key)
            logger.info(f"Deleted document from s3://{bucket}/{key}")
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to delete document: {e}")
            return False

    def document_exists(self, bucket: str, key: str) -> bool:
        """Check if document exists in S3.

        Args:
            key: S3 object key/path

        Returns:
            True if document exists, False otherwise
        """
        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return True
        except (ClientError, BotoCoreError):
            return False
