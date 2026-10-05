# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Web Platform RAG answers questions about the documents (PDFs) attached to assets on the Overstone platform.

- **Ingestion:** MySQL lists an account's PDF files → each PDF is fetched from S3 → parsed with pypdf (scanned pages are OCR'd with AWS Textract) → chunked → embedded with AWS Bedrock Titan v2 → stored in ChromaDB.
- **Query:** an LLM planner splits the user's query into SQL filters, a document query and a `kind`; SQL narrows the candidate assets only when there are filters; vector search runs within that scope; the result is either a written answer with citations (`answer`) or a ranked list of assets (`list`).

Deployment: ingestion as an AWS Batch job (started manually for now), queries as a Lambda invoked by the platform server (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

## Project docs

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): what runs in AWS, how to redeploy, configuration, costs, security gaps.
- [docs/HYBRID_QUERY_STRATEGY.md](docs/HYBRID_QUERY_STRATEGY.md): how a question is answered (plain language), known limits, planned market-sales data.
- [docs/RESPONSE_CONTRACT.md](docs/RESPONSE_CONTRACT.md): the query response shape (keep in sync with `app/query.py`).
- [docs/FILTER_DILEMMAS.md](docs/FILTER_DILEMMAS.md): exact filters vs similarity search, trade-offs and measured examples.

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
uv run poe batch             # ingestion job (app/batch_ingestion.py): new PDFs in, deleted files out
uv run poe ask "<query>"     # any query: plan -> (SQL) -> vector search -> answer or ranked list (app/query.py)
uv run poe lambda events/ask_answer.json   # invoke the query Lambda handler locally (uses .env)
uv run poe planner           # print the query plan for the sample prompts
uv run poe eval              # score search + answers against evals/questions.json
uv run poe copy-pdfs 46 --source-bucket <b> --destination-bucket <b>   # copy an account's PDFs between S3 buckets
uv run poe chroma            # print every record in the Chroma collection
uv run poe verify            # service health checks
uv run poe test              # unit tests; `uv run poe test -m integration` for integration tests
uv run poe pipeline          # happy_path.py demo (stale: hard-coded file list, reads flat bucket-root keys)
uv run poe hybrid            # hybrid_search.py demo (account 7; superseded by `ask`)
```
When adding a new runnable script or CLI, also add a task for it.

Tests use pytest (`tests/unit`, `tests/integration`; integration tests need Docker services and are skipped/deselected otherwise). No linter is configured in the project; `uvx ruff check` is used ad hoc.

### Inspecting state directly
```bash
curl -s http://localhost:9000/minio/health/live     # MinIO health
curl -s http://localhost:8000/api/v2/heartbeat       # ChromaDB health
docker exec -it mysql mysql -uroot -p overstone_v2   # the platform's dev MySQL (read-only for this project)
```
MinIO console at http://localhost:9001 (minioadmin/minioadmin). ChromaDB has no UI, only the HTTP API (`uv run poe chroma`).

## Architecture

- `app/config.py` — pydantic-settings groups, each reading its own env prefix from `.env`: `S3Settings` (`S3_`), `ChromaSettings` (`CHROMA_`), `EmbeddingsSettings` (`EMBEDDINGS_`), `ChunkingSettings` (`CHUNK_`), `BedrockSettings` (`BEDROCK_`, plus `AWS_PROFILE`), `DatabaseSettings` (`RDS_`), `RetrievalSettings` (`RETRIEVAL_`). A module-level `config = AppSettings.from_env()` singleton is imported everywhere instead of reading `os.environ`.
- `app/db/database.py` — `Database`, a read-only MySQL session; all SQL lives here and is parameterized. `list_account_ids(account_id=None)` (optional single-account filter for pilots), `files_for_account()` (PDFs of non-deleted assets, excluding `IMAGES`/`CR_IMAGE`), `lookup_file()` / `resolve_owner()`, `candidate_asset_ids(account_id, filters)` (planner filters → SQL), `asset_names()` / `file_names()` (batch name lookups), `category_ids()`.
- `app/storage/s3_storage.py` — `S3Storage`, a boto3 client only: **every method takes the bucket as its first argument** (`get_object(bucket, key)` → `StoredObject`, raising `StorageError`). `S3_ENDPOINT_URL` set → MinIO with its keys; empty → real AWS S3 via `AWS_PROFILE` (or the IAM role in AWS).
- `app/rag.py` — query-side building blocks: `make_embeddings()` / `make_collection()` (cosine) / `make_llm()`, `search()` (top-k chunks of one account), `answer()` (LLM answer with `[n]` citations). Deliberately free of ingestion libraries so the query Lambda bundle stays small.
- `app/happy_path.py` — ingestion building blocks: `parse_pdf()` (pypdf, falling back to `ocr_page()` = pypdfium2 render + Textract `AnalyzeDocument` with LAYOUT for scanned pages), `make_textract()`. Its own `main()` is the original step-by-step demo.
- `app/batch_ingestion.py` — the ingestion job. Per account: load PDF records, skip files whose stored chunks already match (`assetId`, `category`) — file content never changes — ingest the rest, delete chunks of files no longer in the database. S3 key is `config.s3.key()`: `<accountId>/<fileId>` in dev, `<fileId>` in prod (`S3_ACCOUNT_FOLDERS=false`); missing files are logged as one-line warnings and skipped. `PILOT_ACCOUNT_ID` (46) and `MAX_FILES_PER_CATEGORY` (10) limit demo runs; set both to `None` for everything.
- `app/planner.py` — `plan(query, llm)` uses `llm.with_structured_output(QueryPlan)`: `kind` (`answer`/`list`), `filters` (`AssetFilters`: asset type, artist, years, medium, period, condition; enums mirror MySQL, kept in sync by `tests/integration/test_planner_values.py`), `document_query`. `closest_value()` maps near-miss values (e.g. "POST-WAR") onto valid enum values.
- `app/query.py` — `ask(query, account_id, ...)`, the single query entry point (what the Lambda calls). Returns the UI contract in [docs/RESPONSE_CONTRACT.md](docs/RESPONSE_CONTRACT.md): `version`, `kind`, `plan` (filters, document_category, document_query, explanation), `trace`, `answer` + `citations` or `assets` (each with up to 3 evidence chunks), `error`. Every key is always present; change the contract (and `RESPONSE_VERSION`) together with the doc.
- `app/hybrid_search.py` — `rank_assets()` and `search_candidates()`, used only by its own `main()`, an older demo (`query.py` has its own `group_by_asset()`).
- `app/eval.py` + `evals/questions.json` — retrieval (hit@1/hit@3) and answer (must-contain phrases) scoring.
- `lambda/main.py` — the query Lambda: `handler(event)` with `{"query", "accountId"}` → `ask()`. Loads `RDS_*` from Secrets Manager (`AWS_SECRET_NAME`) before `app.config` is imported; clients are created once per container. Only `lambda/requirements.txt` is bundled (`chromadb-client`, not the full `chromadb`).
- `cdk_app.py`, `cdk.json`, `cdk_rag/rag_stack.py`, `config/{dev,prod}.json` — CDK for the query Lambda, following the `cdk-pdf-reports` repo (Dev/Prod stacks, account `555547688340`, `eu-west-1`, existing VPC/subnets/security group, env vars from `config/*.json`). The bundle is `app/` + `lambda/main.py` + `lambda/requirements.txt`, built in Docker by an explicit bundling command (not `PythonFunction`, because `app/` lives outside `lambda/`). Deploy with `cdk deploy <stack>`; CDK libraries are in the `cdk` dependency group. Sample events in `events/`.
- `scripts/` — `copy_account_pdfs.py` (server-side S3 copy into `<accountId>/<fileId>`), `show_chroma.py`, `verify_services.py`.

### Chunks in ChromaDB

Collection `overstone-assets` (cosine distance, 1024-dim Titan v2 vectors). Chunk id `<fileId>:<index>`; metadata exactly `accountId`, `assetId`, `fileId`, `page`, `category`. Names (file and asset) are not stored on chunks; they are looked up in MySQL at response time. Every search filters by `accountId` (tenant isolation).

### Configuration

All runtime config comes from `.env` (read by pydantic-settings, which needs `python-dotenv`; `scripts/verify_services.py` parses it manually). `.env.example` lists every variable. Notable: `S3_BUCKET` (the bucket ingestion reads from), `S3_ENDPOINT_URL` (set = MinIO, empty = AWS), `AWS_PROFILE`, `BEDROCK_*` (embeddings region, LLM model/region), `RDS_*`, `CHROMA_*`, `CHUNK_SIZE`/`CHUNK_OVERLAP` (1000/150).

### Infrastructure details

- **Document catalog = the platform's dev MySQL** (`overstone_v2`, Docker container `mysql`, port 3306, `RDS_*` in `.env`, `config.db`). **Read-only: never write to it unless the user explicitly asks** (record any such change, with revert SQL, in the commit or a doc). The owning account is resolved via `file.entityId` → `asset.accountId`; files with no resolvable owner are never ingested.
- **S3 layout:** in the dev bucket used by ingestion (`overstone-wp-files-v2-dev`), objects are `<accountId>/<fileId>` (e.g. `46/<uuid>.pdf`), populated from production by `copy-pdfs`. `fileId` extensions may be upper case (`.PDF`).
- MinIO runs the `quay.io/minio/aistor/minio` image, which requires a license file mounted at `/minio.license` (`minio.license` in repo root, gitignored, referenced by `compose.yaml`). Without it the container won't start. MinIO data persists in the bind-mounted `minio-data/` directory (gitignored).
- ChromaDB data persists in a named Docker volume (`overstone-chroma-data`), not a bind mount.
- AWS services used: Bedrock (Titan v2 embeddings in `eu-west-2`, LLM in `BEDROCK_LLM_REGION`), Textract (`eu-west-2`), S3.
