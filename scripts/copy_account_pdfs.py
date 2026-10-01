#!/usr/bin/env python
"""Copy an account's PDF files from one AWS S3 bucket to another.

The file list comes from MySQL (Database.files_for_account: non-deleted assets,
no image categories, .pdf only). Each file is read from the source bucket root
(key = fileId) and written under the account's folder in the destination
(key = <accountId>/<fileId>), with a server-side copy inside AWS (nothing is
downloaded). Files already in the destination are skipped; files that fail
(e.g. missing in the source) are logged and skipped.

AWS profile and region come from .env (AWS_PROFILE, BEDROCK_REGION).

Usage:
    uv run poe copy-pdfs 46 --source-bucket <bucket> --destination-bucket <bucket>
"""

import argparse
import sys
from pathlib import Path

# Allow `import app` when run as a plain script from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

from app.config import config  # noqa: E402
from app.db import Database  # noqa: E402


def exists(s3, bucket: str, key: str) -> bool:
    """True if the object is already in the bucket."""
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False
# uv run poe copy-pdfs 46 --source-bucket overstone-wp-files-prod-sse --destination-bucket overstone-wp-files-v2-dev
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("account_id", type=int)
    parser.add_argument("--source-bucket", required=True)
    parser.add_argument("--destination-bucket", required=True)
    args = parser.parse_args()

    # One client for both buckets: same AWS account and profile.
    session = boto3.Session(profile_name=config.bedrock.aws_profile)  # AWS_PROFILE from .env
    s3 = session.client("s3", region_name=config.bedrock.region)  # eu-west-2

    files = Database().files_for_account(args.account_id)  # PDFs only
    print(f"Account {args.account_id}: {len(files)} PDF files")

    counts = {"copied": 0, "skipped": 0, "failed": 0}
    for record in files:
        destination_key = f"{args.account_id}/{record.file_id}"  # e.g. 46/062d2aed-...pdf
        if exists(s3, args.destination_bucket, destination_key):
            counts["skipped"] += 1
            continue
        try:
            # One request; AWS copies the object between the buckets itself.
            s3.copy_object(
                Bucket=args.destination_bucket,
                Key=destination_key,
                CopySource={"Bucket": args.source_bucket, "Key": record.file_id},  # flat at the root
            )
            counts["copied"] += 1
            print(f"copied  {destination_key}  {record.name}")
        except ClientError as e:
            # e.g. NoSuchKey in the source: log it and continue with the next file.
            counts["failed"] += 1
            print(f"FAILED  {record.file_id}  {record.name}: {e.response['Error']['Code']}")

    print(counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
