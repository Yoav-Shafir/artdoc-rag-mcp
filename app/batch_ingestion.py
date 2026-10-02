"""Daily ingestion job, deployed to AWS Batch.

For each account: load its PDF file records from MySQL, ingest the files that
are new (or moved to another asset), and delete the chunks of files that no
longer exist in the database.

Run: uv run poe batch
"""

import json
import logging
import os

import boto3
from langchain_text_splitters import RecursiveCharacterTextSplitter


def load_db_secret() -> None:
	"""Copy RDS_* credentials from Secrets Manager into the environment, before config loads."""
	secret_name = os.environ.get("AWS_SECRET_NAME")
	if not secret_name:
		return
	client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
	secret = json.loads(client.get_secret_value(SecretId=secret_name)["SecretString"])
	for key in ("RDS_HOSTNAME", "RDS_PORT", "RDS_USERNAME", "RDS_PASSWORD", "RDS_DB_NAME"):
		if key in secret:
			os.environ[key] = str(secret[key])


load_db_secret()

from app.config import config
from app.db import Database, FileRecord
from app.happy_path import make_textract, parse_pdf
from app.rag import make_collection, make_embeddings
from app.storage import S3Storage, StorageError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Pilot: only this account is ingested. Set to None to ingest every account.
PILOT_ACCOUNT_ID = 46
# Test runs: process at most this many files per account. Set to None for no limit.
MAX_FILES_PER_ACCOUNT = 50


def stored_metadata(collection, account_id: int) -> dict[str, tuple]:
    """fileId -> (assetId, category) of the files that already have chunks for this account."""
    stored = collection.get(where={"accountId": account_id}, include=["metadatas"])
    return {
        meta["fileId"]: (meta["assetId"], meta.get("category"))  # older chunks have no category
        for meta in stored["metadatas"]
    }


def ingest_file(record: FileRecord, bucket: str, storage, splitter, embeddings, collection, textract) -> int:
    """Fetch, parse, chunk, embed and store one PDF. Returns the number of chunks stored."""
    # Objects are stored under the account's folder: <accountId>/<fileId>, e.g. 46/062d2aed-...pdf
    obj = storage.get_object(bucket, f"{record.account_id}/{record.file_id}")
    pages = parse_pdf(obj.body, record.file_id, textract)

    # IDs and the category go on the chunks (category allows filtering by document type);
    # names stay in MySQL.
    for page in pages:
        page.metadata["accountId"] = record.account_id
        page.metadata["assetId"] = record.asset_id
        page.metadata["category"] = record.category  # e.g. VALUATION_REPORTS, PROVENANCE

    chunks = splitter.split_documents(pages)
    # Delete this file's old chunks first (ChromaDB upsert merges metadata, doesn't replace it).
    collection.delete(where={"fileId": record.file_id})
    if not chunks:  # e.g. blank pages, or OCR found nothing
        return 0

    vectors = embeddings.embed_documents([chunk.page_content for chunk in chunks])
    collection.upsert(
        ids=[f"{record.file_id}:{index}" for index in range(len(chunks))],
        documents=[chunk.page_content for chunk in chunks],
        embeddings=vectors,
        metadatas=[chunk.metadata for chunk in chunks],
    )
    return len(chunks)

def ingest_account(account_id: int, bucket: str, db, storage, splitter, embeddings, collection, textract) -> dict:
    """Ingest an account's new PDFs, then remove chunks of deleted files."""
    counts = {"ingested": 0, "unchanged": 0, "failed": 0, "deleted": 0}
    files = db.files_for_account(account_id)  # PDFs only, image categories excluded
    stored = stored_metadata(collection, account_id)

    # The limit applies to processing only; the deletion check below still uses every file.
    for record in files[:MAX_FILES_PER_ACCOUNT]:
        # File content never changes, so a file with chunks is done, unless its asset or
        # category changed in the database (then its chunks' metadata is out of date).
        if stored.get(record.file_id) == (record.asset_id, record.category):
            counts["unchanged"] += 1
            continue
        try:
            chunk_count = ingest_file(record, bucket, storage, splitter, embeddings, collection, textract)
            logger.info(f"Ingested {record.file_id} ({record.name!r}): {chunk_count} chunks")
            counts["ingested"] += 1
        except StorageError as e:
            # Missing in S3 (or unreadable): expected for some files, so one line, no traceback.
            logger.warning(f"Skipped {record.file_id} ({record.name!r}): {e}")
            counts["failed"] += 1
        except Exception:
            # Unexpected (bad PDF, Textract/Bedrock error...): full traceback, then continue.
            logger.exception(f"Failed to ingest {record.file_id} ({record.name!r})")
            counts["failed"] += 1

    # Files that have chunks in Chroma but are no longer in the database were deleted.
    current_ids = {record.file_id for record in files}
    for file_id in set(stored) - current_ids:
        collection.delete(where={"fileId": file_id})
        logger.info(f"Deleted chunks of removed file {file_id}")
        counts["deleted"] += 1

    return counts


def main():
    """Run the ingestion for every account (or only the pilot account)."""
    db = Database()  # read-only access to the platform's MySQL
    storage = S3Storage()
    bucket = config.s3.bucket  # S3_BUCKET from .env: where the PDFs are read from
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.chunking.size,  # 1000
        chunk_overlap=config.chunking.overlap,  # 150
    )
    embeddings = make_embeddings()  # BedrockEmbeddings instance: AWS Bedrock Titan v2
    collection = make_collection()  # ChromaDB collection object: connected to overstone-assets
    textract = make_textract()  # boto3 Textract client: AWS OCR service for scanned PDFs

    account_ids = db.list_account_ids(PILOT_ACCOUNT_ID)
    logger.info(f"Ingesting {len(account_ids)} account(s): {account_ids}")

    for account_id in account_ids:
        counts = ingest_account(account_id, bucket, db, storage, splitter, embeddings, collection, textract)
        logger.info(f"Account {account_id}: {counts}")


if __name__ == "__main__":
    main()
