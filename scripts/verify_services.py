#!/usr/bin/env python
"""Verification script for Web Platform RAG services."""

import os
import sys
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import boto3
import chromadb
from botocore.config import Config


def load_env():
    """Load environment variables from .env file."""
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ[key] = value


def check_docker_services():
    """Check if Docker services are running."""
    print("📋 1. Checking Docker Services...")
    print()
    try:
        result = subprocess.run(
            ["docker", "compose", "ps"],
            cwd=Path(__file__).parent.parent,
            capture_output=False,
            text=True,
        )
        print()
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def check_minio_health():
    """Check MinIO health."""
    print("🏥 2. MinIO Health Check...")
    try:
        import requests

        response = requests.get("http://localhost:9000/minio/health/live", timeout=5)
        if response.status_code == 200:
            print("✅ MinIO is healthy")
            return True
        else:
            print("❌ MinIO health check failed")
            return False
    except Exception as e:
        print(f"❌ MinIO is not responding: {e}")
        return False


def check_chromadb_health():
    """Check ChromaDB health."""
    print("🏥 3. ChromaDB Health Check...")
    try:
        client = chromadb.HttpClient(
            host=os.environ.get("CHROMA_HOST", "localhost"),
            port=int(os.environ.get("CHROMA_PORT", "8000")),
        )
        heartbeat = client.heartbeat()
        print(f"✅ ChromaDB is healthy (heartbeat: {heartbeat})")
        return True
    except Exception as e:
        print(f"❌ ChromaDB is not responding: {e}")
        return False


def list_s3_buckets():
    """List S3 buckets in MinIO."""
    print("📦 4. Listing S3 Buckets...")
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000"),
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY", "minioadmin"),
            aws_secret_access_key=os.environ.get("S3_SECRET_KEY", "minioadmin"),
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

        response = s3.list_buckets()
        buckets = [b["Name"] for b in response.get("Buckets", [])]

        if buckets:
            print(f"✅ Found {len(buckets)} bucket(s):")
            for bucket in buckets:
                obj_response = s3.list_objects_v2(Bucket=bucket)
                obj_count = obj_response.get("KeyCount", 0)
                print(f"   - {bucket} ({obj_count} objects)")
            return True
        else:
            print("⚠️  No buckets found")
            return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def list_chromadb_collections():
    """List ChromaDB collections."""
    print("📚 5. Listing ChromaDB Collections...")
    try:
        client = chromadb.HttpClient(
            host=os.environ.get("CHROMA_HOST", "localhost"),
            port=int(os.environ.get("CHROMA_PORT", "8000")),
        )

        collections = client.list_collections()

        if collections:
            print(f"✅ Found {len(collections)} collection(s):")
            for col in collections:
                print(f"   - {col.name} ({col.count()} documents)")
            return True
        else:
            print("⚠️  No collections found")
            return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def view_chromadb_database():
    """View ChromaDB database info."""
    print("💾 6. ChromaDB Database Info...")
    try:
        # Copy into a throwaway temp dir (deleted on exit) so no stale copy is left in the project.
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_file = Path(tmp_dir) / "chroma.sqlite3"
            copy = subprocess.run(
                ["docker", "cp", "overstone-chromadb:/data/chroma.sqlite3", str(db_file)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if copy.returncode != 0:
                print(f"⚠️  Could not copy chroma.sqlite3 from container: {copy.stderr.strip()}")
                return True

            db = sqlite3.connect(str(db_file))
            cursor = db.cursor()

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]

            print(f"✅ Database tables ({len(tables)}):")
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                if count > 0:
                    print(f"   - {table}: {count} rows")

            db.close()
            return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def check_catalog():
    """Check the document catalog (dev MySQL `file` table)."""
    print("🗂️  7. Document Catalog (MySQL)...")
    try:
        import pymysql

        db = pymysql.connect(
            host=os.environ.get("RDS_HOSTNAME", "localhost"),
            port=int(os.environ.get("RDS_PORT", "3306")),
            user=os.environ.get("RDS_USERNAME", "root"),
            password=os.environ.get("RDS_PASSWORD", ""),
            database=os.environ.get("RDS_DB_NAME", "overstone_v2"),
            connect_timeout=5,
        )
        with db.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM file")
            count = cursor.fetchone()[0]
        db.close()
        print(f"✅ Catalog reachable ({count} file records)")
        return True
    except Exception as e:
        print(f"❌ Catalog is not reachable: {e}")
        return False


def main():
    """Run all verification checks."""
    print("=" * 60)
    print("Web Platform RAG - Service Verification")
    print("=" * 60)
    print()

    load_env()

    results = {
        "Docker Services": check_docker_services(),
        "MinIO Health": check_minio_health(),
        "ChromaDB Health": check_chromadb_health(),
        "S3 Buckets": list_s3_buckets(),
        "ChromaDB Collections": list_chromadb_collections(),
        "Database Info": view_chromadb_database(),
        "Document Catalog": check_catalog(),
    }

    print()
    print("=" * 60)
    print("Verification Summary")
    print("=" * 60)
    for check, result in results.items():
        status = "✅" if result else "⚠️ "
        print(f"{status} {check}")

    print()

    if all(results.values()):
        print("✅ All checks passed!")
        return 0
    else:
        print("⚠️  Some checks failed. See details above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
