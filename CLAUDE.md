# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Web Platform RAG is a Retrieval-Augmented Generation pipeline built on local S3-compatible storage (MinIO) and a vector database (ChromaDB). The ingestion flow is: download a JSON asset from S3 → parse it → chunk the text → embed the chunks → store the embeddings in ChromaDB, with search running the same embedding step against stored vectors.

## Active implementation plan

Work is following [docs/RAG_IMPLEMENTATION_PLAN.md](docs/RAG_IMPLEMENTATION_PLAN.md). At the start of any implementation session, read its "How to use this plan" section, continue from the first unchecked step in the Progress tracker, and when a step is done tick it and append to the Session log. Keep the plan updated if the approach changes. Some notes below (mock embeddings, no tests) describe the pre-plan state and will change as phases complete.

## Commands

### Environment setup
```bash
uv sync                        # install/sync dependencies into .venv (uv is the only package manager used — never use pip/pip install directly)
source .venv/bin/activate      # activate the venv (Python 3.12+)
```

### Infrastructure (Docker)
```bash
docker compose up -d           # start MinIO (9000 API / 9001 console) + ChromaDB (8000)
docker compose ps              # check container status
docker compose logs -f minio   # or chromadb
docker compose down            # stop services
docker compose down -v         # stop and wipe volumes (minio-data, chroma_data)
```

### Task shortcuts (poethepoet, the `npm run` equivalent)
```bash
uv run poe                   # list all tasks (defined under [tool.poe.tasks] in pyproject.toml)
uv run poe verify            # service health checks
uv run poe chroma            # print every record in the Chroma collection
uv run poe test              # unit tests; `uv run poe test -m integration` for integration tests
uv run poe demo              # end-to-end demo (app_example.py)
```
When adding a new runnable script or CLI, also add a task for it.

### Running the app
```bash
python app_example.py                    # end-to-end ingestion + search demo, entry point for the app/ package
python scripts/verify_services.py         # checks Docker status, MinIO/ChromaDB health, lists S3 buckets & Chroma collections, dumps chroma.sqlite3 table counts
```

Tests use pytest (`tests/unit`, `tests/integration`); no linter or build step is configured.

### Inspecting state directly
```bash
curl -s http://localhost:9000/minio/health/live     # MinIO health
curl -s http://localhost:8000/api/v1/heartbeat       # ChromaDB health
docker cp overstone-chromadb:/data/chroma.sqlite3 ./chroma.sqlite3   # pull Chroma's sqlite file to inspect tables directly
```
MinIO console at http://localhost:9001 (minioadmin/minioadmin) shows bucket contents visually; ChromaDB has no UI, only the HTTP API / sqlite file.

## Architecture

The `app/` package is organized as one directory per pipeline stage, each exposing a single class re-exported from its `__init__.py`:

- `app/config.py` — dataclass-based settings (`S3Config`, `ChromaConfig`, `EmbeddingsConfig`, `ChunkingConfig`) all read from env vars at import time via a module-level `config = AppConfig.from_env()` singleton. Other modules import this shared `config` object rather than reading `os.environ` themselves.
- `app/db/database.py` — `Database` reads the platform's MySQL `file` table (read-only session). Processing is database-driven: `list_account_ids()` → `files_for_account(id)` (all records in one query, with category, asset and account) → fetch each file from S3 by `fileId`.
- `app/storage/s3_storage.py` — `S3Storage` wraps boto3 for the MinIO bucket: raw bytes (`put_object`/`get_object` → `StoredObject`, raising `StorageError`), JSON helpers (`upload_document`/`download_document`), paginated `list_documents`. Objects are stored flat at the bucket root, key = `fileId`.
- `app/documents/` — `DocumentParser` is an ABC (`parse`, `validate`) returning a `ParsedDocument` (`id`, `title`, `content`, `metadata`). `JsonAssetParser` is the concrete implementation for the asset JSON schema; it accepts `id`/`assetId`/`asset_id` interchangeably as the identifier field (`ID_FIELDS`) and folds known fields (`description`, `category`, `artist`, `year`) into `content`, everything else into `metadata`.
- `app/chunking/text_chunker.py` — `TextChunker` does fixed-size overlapping chunking (`chunk`) or sentence-aware chunking (`chunk_by_sentences`); overlap must be smaller than chunk size or the constructor raises.
- `app/embeddings/bedrock_embeddings.py` — `BedrockEmbeddings` is named for its intended production backend (AWS Bedrock) but **currently generates a deterministic mock embedding from an MD5 hash of the text**, not a real model call. Swap `_generate_embedding` for a real embedding call before relying on semantic search quality.
- `app/vector_store/chroma_store.py` — `ChromaStore` wraps a `chromadb.HttpClient` bound to one collection (`get_or_create_collection`), with add/search/get/delete/count/clear operations.
- `app/ingestion/ingestion_service.py` — `IngestionService` composes all of the above (each dependency is injectable via constructor, defaulting to a fresh instance of the corresponding class). `ingest_document(s3_key)` runs the full download→parse→chunk→embed→store pipeline and bails out early (returning `False`, logging the reason) at whichever step fails. `search(query_text)` embeds the query and delegates to `ChromaStore.search`.

`app_example.py` at the repo root is the reference/demo entry point wiring `IngestionService` end-to-end against the sample asset stored in MinIO.

### Configuration

All runtime config comes from `.env` (loaded via `python-dotenv` in app code, and via manual parsing in `scripts/verify_services.py` since that script must run before the venv's dotenv import). Key variables: `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`, `S3_BUCKET`, `CHROMA_HOST`, `CHROMA_PORT`, `CHROMA_COLLECTION`. `EMBEDDINGS_MODEL`/`EMBEDDINGS_DIMENSION`/`CHUNK_SIZE`/`CHUNK_OVERLAP` are read by `app/config.py` but not currently present in `.env` (they fall back to defaults: `all-MiniLM-L6-v2`, 384, 512, 50).

### Infrastructure details

- **Document catalog = the platform's dev MySQL** (`overstone_v2`, Docker container `mysql`, port 3306, `RDS_*` in `.env`, `config.db`). **Read-only: never write to it unless the user explicitly asks** (any such change is logged, with revert SQL, in the plan's Session log). S3/MinIO object keys are `file.fileId` (`<uuid>.<ext>`), flat at the bucket root with no folders, matching production; the owning account is resolved via `file.entityId` → `asset.accountId`. Files with no resolvable owner are never ingested. Inspect with `docker exec -it mysql mysql -uroot -p overstone_v2`.

- MinIO runs the `quay.io/minio/aistor/minio` image, which requires a license file mounted at `/minio.license` (`minio.license` in repo root, gitignored, referenced by `compose.yaml`). Without it the container won't start.
- ChromaDB data persists in a named Docker volume (`overstone-chroma-data`), not a bind mount — use `docker cp overstone-chromadb:/data/chroma.sqlite3 .` to inspect it from the host.
- MinIO data persists in the bind-mounted `minio-data/` directory (gitignored).
