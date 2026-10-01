# Web Platform RAG

A Retrieval-Augmented Generation (RAG) project with local S3 storage (MinIO) and vector database (ChromaDB) support.

## Prerequisites

- Python 3.12+
- Docker & Docker Compose
- `uv` package manager

## Project Structure

```
overstone-rag/
├── main.py              # Main application script
├── compose.yaml         # Docker Compose configuration
├── .env                 # Environment variables
├── pyproject.toml       # Project dependencies
├── minio-data/          # MinIO S3 data storage
└── chroma.sqlite3       # ChromaDB database (SQLite)
```

## Setup

### 1. Install Python Dependencies

```bash
cd /home/yoav/dev/overstone/repos/overstone-rag
uv sync
```

### 2. Start Services with Docker Compose

```bash
docker compose up -d
```

This starts:
- **MinIO** (S3-compatible storage) on ports 9000 & 9001
- **ChromaDB** (Vector database) on port 8000

### 3. Verify Services are Running

```bash
docker compose ps
```

Expected output:
```
CONTAINER ID   IMAGE                             STATUS      PORTS
...            overstone-minio                   Up 2 minutes   0.0.0.0:9000->9000, 0.0.0.0:9001->9001
...            overstone-chromadb                Up 2 minutes   0.0.0.0:8000->8000
```

## Running the Project

### Activate Virtual Environment

```bash
cd /home/yoav/dev/overstone/repos/overstone-rag
source .venv/bin/activate
```

### Run Main Script

```bash
python main.py
```

Expected output:
```
Testing MinIO through Boto3...
Bucket already exists: overstone-rag-documents
Uploaded object: accounts/account-123/assets/asset-456/asset.json
Downloaded object:
{
  "accountId": "account-123",
  "assetId": "asset-456",
  "title": "Blue Horizon",
  ...
}

Testing ChromaDB...
ChromaDB heartbeat: [timestamp]
Collection available: overstone-assets
Documents in collection: 0

All local services are working.
```

### Ask a Query

Plans the query, then returns either a written answer or a ranked list of assets (account 46):

```bash
uv run poe ask "Who issued the invoice and on what date?"
uv run poe ask "Show me all assets whose provenance includes Christie's"
```

## Service Endpoints & Ports

| Service | Endpoint | Port | Purpose |
|---------|----------|------|---------|
| MinIO Console | http://localhost:9001 | 9001 | S3 web UI |
| MinIO API | http://localhost:9000 | 9000 | S3-compatible API |
| ChromaDB API | http://localhost:8000 | 8000 | Vector database API |

### MinIO Console Credentials
- Username: `minioadmin`
- Password: `minioadmin`

## Environment Variables

The `.env` file contains:

```bash
# MinIO Configuration
MINIO_ROOT_USER=overstone
MINIO_ROOT_PASSWORD=password

# S3/MinIO Client Settings
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_REGION=eu-west-2
S3_BUCKET=overstone-rag-documents

# ChromaDB Settings
CHROMA_HOST=localhost
CHROMA_PORT=8000
CHROMA_COLLECTION=overstone-assets
```

## Verification Commands

### 1. Check All Services Running

```bash
docker compose ps
```

### 2. MinIO Health Check

```bash
curl -s http://localhost:9000/minio/health/live
```

### 3. ChromaDB Health Check

```bash
curl -s http://localhost:8000/api/v1/heartbeat
```

### 4. List S3 Buckets

```bash
source .venv/bin/activate
python << 'EOF'
import os
import boto3
from botocore.config import Config

os.environ['MINIO_ROOT_USER'] = 'overstone'
os.environ['MINIO_ROOT_PASSWORD'] = 'password'

s3 = boto3.client(
    "s3",
    endpoint_url="http://localhost:9000",
    aws_access_key_id="minioadmin",
    aws_secret_access_key="minioadmin",
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"})
)

buckets = s3.list_buckets()
print("Buckets:", [b['Name'] for b in buckets.get('Buckets', [])])
EOF
```

### 5. List ChromaDB Collections

```bash
source .venv/bin/activate
python << 'EOF'
import chromadb

client = chromadb.HttpClient(host="localhost", port=8000)
collections = client.list_collections()
print(f"Collections: {len(collections)}")
for col in collections:
    print(f"  - {col.name} ({col.count()} documents)")
EOF
```

### 6. View ChromaDB Data (SQLite)

```bash
# Copy database from Docker container
docker cp overstone-chromadb:/data/chroma.sqlite3 ./chroma.sqlite3

# Query with Python
source .venv/bin/activate
python << 'EOF'
import sqlite3

db = sqlite3.connect('chroma.sqlite3')
cursor = db.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
print("Tables:", [row[0] for row in cursor.fetchall()])
db.close()
EOF
```

## Package Management

### List All Packages

```bash
source .venv/bin/activate
uv pip list
```

### Show Dependency Tree

```bash
source .venv/bin/activate
uv tree
```

### Add a New Dependency

```bash
uv add package-name
```

## Stopping Services

```bash
docker compose down
```

To also remove data volumes:

```bash
docker compose down -v
```

## Troubleshooting

### Port Already in Use

If ports 9000, 9001, or 8000 are already in use:

```bash
# Find process using port
lsof -i :9000
lsof -i :9001
lsof -i :8000

# Kill process (replace PID)
kill -9 <PID>
```

### Services Won't Start

```bash
# Check logs
docker compose logs -f minio
docker compose logs -f chromadb

# Rebuild containers
docker compose down
docker compose up -d --build
```

### Python Import Errors

```bash
# Ensure correct venv is active
source .venv/bin/activate

# Reinstall dependencies
uv sync
```

### MinIO License Error

The project uses MinIO's AIStore image which requires a license file (`minio.license`). Ensure the file exists in the project root.

## Architecture

```
┌─────────────────────────────────────┐
│      Python Application             │
│      (main.py)                      │
└────────────┬────────────────────────┘
             │
    ┌────────┴────────┐
    │                 │
    v                 v
┌─────────────┐  ┌──────────────┐
│   MinIO     │  │  ChromaDB    │
│ (S3 compat) │  │  (Vector DB) │
│ :9000-9001  │  │   :8000      │
└─────────────┘  └──────────────┘
```

## Links

- [MinIO Documentation](https://docs.min.io/)
- [ChromaDB Documentation](https://docs.trychroma.com/)
- [Boto3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)

## License

Proprietary - Overstone
