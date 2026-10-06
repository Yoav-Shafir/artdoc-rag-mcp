# Overstone AI Search: Deployment Guide

## Architecture Overview

```
                                   ┌──────────────────────┐
                                   │  Web platform client │
                                   │  (browser, React)    │
                                   └───────┬───────┬──────┘
                               askAssetsAI │       ▲ results
┌──────────────────────────────────────────┼───────┼──────────────┐
│ AWS Environment (eu-west-1)              │       │              │
├──────────────────────────────────────────┼───────┼──────────────┤
│                                          │       │              │
│ ┌────────────────────────────────────────┼───────┼────────────┐ │
│ │  Private VPC                           ▼       │            │ │
│ │                                ┌───────┴───────┴──────┐     │ │
│ │                                │  Web platform server │     │ │
│ │                                │  (Elastic Beanstalk) │     │ │
│ │                                └───────┬───────┬──────┘     │ │
│ │                                invokes ▼       ▲ reply      │ │
│ │  ┌──────────────────────┐      ┌───────┴───────┴──────┐     │ │
│ │  │  AWS Batch Job       │      │  AWS Lambda          │     │ │
│ │  │  (Ingestion)         │      │  (Search & Answer)   │     │ │
│ │  │  • Fetch PDFs (S3)   │      │  • Safety check      │     │ │
│ │  │  • Parse + OCR       │      │  • Plan the question │     │ │
│ │  │  • Chunk + Embed     │      │  • SQL filters       │     │ │
│ │  │  • Store in ChromaDB │      │  • Search ChromaDB   │     │ │
│ │  │                      │      │  • Answer or list    │     │ │
│ │  └───────────┬──────────┘      └───────┬───────┬──────┘     │ │
│ │              │ writes chunks           ▼       ▲            │ │
│ │              └──────►──────┬─────◄─────┘       │            │ │
│ │                            ▼                   │            │ │
│ │              ┌─────────────┴─────────────┐     │            │ │
│ │              │  EC2: wp-chromadb         │     │ chunks     │ │
│ │              │  ChromaDB (vector store)  ├─►───┘            │ │
│ │              │  10.0.30.114:8000         │                  │ │
│ │              │  t3.small, 30GB disk      │                  │ │
│ │              └───────────────────────────┘                  │ │
│ │                                                             │ │
│ │  RDS MySQL (file catalogue) is also only reachable here     │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## AWS Services

| Service | Component | Purpose |
|---------|-----------|---------|
| **AWS Batch (Fargate)** | Ingestion job | Download, parse, chunk, embed and store PDFs; run manually for now |
| **ECR** | `overstone-batch-ingestion` | Docker image for the Batch job |
| **AWS Lambda** | `cdk-rag-query-dev` | On-demand questions: safety check, plan, search, answer or list |
| **CDK / CloudFormation** | `OverstoneLambdaRagQueryStackDev` | Deploys the Lambda and its IAM role |
| **S3** | `overstone-wp-files-prod-sse` | Source PDFs (read by Batch only) |
| **Textract** | OCR | Text from scanned pages during ingestion |
| **RDS MySQL** | `overstone_v2` | File catalogue and asset details (read-only) |
| **EC2** | `wp-chromadb` (t3.small, 30GB) | Hosts ChromaDB |
| **ChromaDB** | `overstone-assets` collection | Chunks, embeddings and their metadata |
| **Bedrock** | Titan v2, Nova 2 Lite | Embeddings, and the planner and answer LLM |
| **Bedrock Guardrails** | `4zibaxd3h7sd` v1 | Blocks prompt attacks and abusive questions |
| **Secrets Manager** | `web-platform-server-v2` | Database credentials |
| **IAM** | Batch job and execution roles, Lambda role | Permissions for each component |
| **VPC** | Private subnets, security group, NAT | Private access to ChromaDB and RDS, and outbound access to AWS services |
| **CloudWatch** | Logs | Batch job and Lambda logs |
| **EventBridge** | (not set up yet) | Future: run the Batch job on a schedule |

## Data Flow

### Ingestion (AWS Batch): `app/batch_ingestion.py`

1. **Trigger:** manual for now: submit a job in the Batch console (no schedule yet).
   - `["python", "-m", "app.batch_ingestion", "--check-only"]`: only counts the account's PDFs per document type and checks they exist in S3.
   - `["python", "-m", "app.batch_ingestion"]`: the real ingestion.
2. **Start-up:** reads the database login from Secrets Manager, then connects to MySQL, S3, Bedrock, ChromaDB and Textract.
3. **Per account** (only the pilot account for now, `PILOT_ACCOUNT_ID`):
   - **MySQL:** get the list of the account's PDF files (images and deleted assets are left out).
   - **ChromaDB:** get the list of files that were already ingested in earlier runs.
   - **Demo limit:** at most `MAX_FILES_PER_CATEGORY` (10) files per document type.
   - **Skip** files that were already ingested, so each file is only processed once.
4. **Per file:**
   - **Download** from S3 (prod: key = `fileId`, flat bucket).
   - **Read the text** with pypdf; pages with no text are turned into images and read by **Textract** (OCR).
   - **Cut into chunks** of ~1,000 characters, overlapping by 150.
   - **Embed** each chunk with Bedrock Titan v2 (1,024 numbers).
   - **Save** to ChromaDB, replacing any old chunks of the file.
   - A file missing in S3 is logged and skipped; any other error is logged in full, and the job carries on.
5. **Clean-up:** chunks of files no longer in MySQL (deleted on the platform) are removed.
6. **Output:** chunks in ChromaDB with metadata `accountId`, `assetId`, `fileId`, `page`, `category`; the log ends with counts, e.g. `{'ingested': 13, 'unchanged': 0, 'failed': 0, 'deleted': 0}`.

### Search (AWS Lambda): `lambda/main.py` → `app/query.py`

1. **Input:** the user's question and account, sent by the platform server: `{"query": "...", "accountId": 46}`. For safety, the server decides the account, not the user.
2. **Safety check:** Bedrock Guardrails checks the question; a blocked question stops here.
3. **Plan:** the LLM (Nova 2 Lite, temperature 0) reads the question and returns:
   - `kind`: should the reply be a written answer or a list of assets?
   - `filters`: which assets, e.g. paintings or furniture (searched in MySQL)
   - `document_category`: which type of document, e.g. certificates (only if the question says so)
   - `document_query`: what to look for inside the documents, e.g. "bought at Christie's"
4. **Scope:** the search is limited to the account, plus the document type if one was asked for, plus the assets MySQL matched if there are filters.
5. **Search and reply:** one of four ways, depending on the plan:
   - **"Which assets have a certificate?"** → every asset that has that type of document.
   - **"Show me all paintings"** → the assets MySQL found.
   - **"Which assets were bought at Christie's?"** → search the documents' text and return the 10 best matching assets.
   - **"Who issued the invoice?"** → the 3 best matching pieces of text go to the LLM, which writes an answer with sources [1], [2]…
6. **Output:** the response contract v1 ([RESPONSE_CONTRACT.md](RESPONSE_CONTRACT.md)): the plan, timings, answer and citations or ranked assets with matching text, and an error if any. Asset and file names are looked up in MySQL.

## How It's Deployed and How to Redeploy

### 1. One-time setup (done)

| Part | What exists |
|------|-------------|
| **Database** | The platform's RDS MySQL `overstone_v2`, read-only. Login from the existing secret `web-platform-server-v2`. |
| **PDFs** | Prod S3 bucket `overstone-wp-files-prod-sse` (flat: key = `fileId`). Read by Batch only. |
| **ChromaDB** | EC2 `wp-chromadb` (t3.small, 30GB disk), private IP `10.0.30.114`, port 8000. |
| **Batch** | Fargate compute environment and job queue (set up in the console). Job definition `wp-batch-ingestion-check-prod` (2 vCPU, 4GB), settings as environment variables in the definition. |
| **Batch roles** | Execution role `wp-batch-execution-role` (pull the image, write logs). Job role (S3, Bedrock, Textract, Secrets Manager). Without the job role, the container has no AWS access. |
| **Image** | ECR repository `overstone-batch-ingestion`, tag `latest`. |
| **Lambda** | `cdk-rag-query-dev` (1,024MB, 60s timeout), deployed with CDK; its role and settings come from the stack and `config/dev.json`. |
| **Guardrail** | Bedrock Guardrail `4zibaxd3h7sd` version 1 (Standard tier, EU profile). |

### 2. Update the Batch job

After changing ingestion code:

```bash
# Log in to ECR (lasts 12 hours)
aws ecr get-login-password --region eu-west-1 --profile overstone \
  | docker login --username AWS --password-stdin 555547688340.dkr.ecr.eu-west-1.amazonaws.com

# Build, tag and push the image
docker build -t overstone-batch-ingestion .
docker tag overstone-batch-ingestion:latest 555547688340.dkr.ecr.eu-west-1.amazonaws.com/overstone-batch-ingestion:latest
docker push 555547688340.dkr.ecr.eu-west-1.amazonaws.com/overstone-batch-ingestion:latest
```

Then in the Batch console, **Create new job** (job definition `wp-batch-ingestion-check-prod`) with a command override:
1. `["python", "-m", "app.batch_ingestion", "--check-only"]`: check the account's files first.
2. `["python", "-m", "app.batch_ingestion"]`: the real ingestion.

Logs: the job's log stream in CloudWatch (`/aws/batch/job`). The account is set in code (`PILOT_ACCOUNT_ID` in `app/batch_ingestion.py`).

### 3. Update the Lambda

After changing query code (`app/` or `lambda/`):

```bash
AWS_PROFILE=overstone uv run --group cdk cdk diff OverstoneLambdaRagQueryStackDev    # see what would change
AWS_PROFILE=overstone uv run --group cdk cdk deploy OverstoneLambdaRagQueryStackDev  # deploy
uv run poe demo                                                                       # check the demo questions
```

Logs: CloudWatch `/aws/lambda/cdk-rag-query-dev`.

### 4. Not done yet

- **Daily schedule:** an EventBridge rule to run the Batch job automatically (today it's started by hand).
- **Alarms:** CloudWatch alarms on Batch failures and Lambda errors.
- **Prod Lambda:** a separate `cdk-rag-query-prod` stack exists in CDK but isn't deployed; the platform server uses the dev Lambda.

## Configuration

### Where settings come from

| Part | Source |
|------|--------|
| **Lambda** | [config/dev.json](../config/dev.json), set as the Lambda's environment variables by CDK |
| **Batch job** | Environment variables in the job definition (`wp-batch-ingestion-check-prod`) |
| **Local runs** | `.env` (template: `.env.example`) |
| **Database login** | Always the secret named in `AWS_SECRET_NAME` (`web-platform-server-v2`), read at start-up |

Anything not set uses the default in [app/config.py](../app/config.py). In AWS, access comes from IAM roles: no AWS keys or `AWS_PROFILE`.

### Variables

| Variable | Value in AWS | Used by | Notes |
|----------|--------------|---------|-------|
| `AWS_SECRET_NAME` | `web-platform-server-v2` | Batch, Lambda | Secret holding `RDS_HOSTNAME`, `RDS_USERNAME`, `RDS_PASSWORD` |
| `RDS_PORT` | `3306` | Batch, Lambda | |
| `RDS_DB_NAME` | `overstone_v2` | Batch, Lambda | |
| `S3_BUCKET` | `overstone-wp-files-prod-sse` | Batch | Where the PDFs are |
| `S3_REGION` | `eu-west-1` | Batch | |
| `S3_ACCOUNT_FOLDERS` | `false` | Batch | Prod bucket is flat (key = `fileId`); dev bucket uses `<accountId>/<fileId>` |
| `S3_ENDPOINT_URL` | (empty) | Batch | Set only for local MinIO |
| `CHROMA_HOST` | `10.0.30.114` | Batch, Lambda | EC2 `wp-chromadb` |
| `CHROMA_PORT` | `8000` | Batch, Lambda | |
| `CHROMA_COLLECTION` | `overstone-assets` | Batch, Lambda | |
| `EMBEDDINGS_MODEL` | `amazon.titan-embed-text-v2:0` | Batch, Lambda | |
| `EMBEDDINGS_DIMENSION` | `1024` | Batch, Lambda | |
| `BEDROCK_REGION` | `eu-west-2` | Batch, Lambda | Region for embeddings |
| `BEDROCK_LLM_MODEL_ID` | `eu.amazon.nova-2-lite-v1:0` | Lambda | Planner and answers (EU cross-region profile) |
| `BEDROCK_LLM_REGION` | `eu-west-1` | Lambda | Also where the guardrail lives |
| `BEDROCK_LLM_MAX_TOKENS` | `1024` | Lambda | |
| `BEDROCK_GUARDRAIL_ID` | `4zibaxd3h7sd` | Lambda | Empty = no safety check |
| `BEDROCK_GUARDRAIL_VERSION` | `1` | Lambda | |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `150` (defaults) | Batch | |
| `RETRIEVAL_LIST_MIN_SCORE` | `0.2` (default) | Lambda | Lowest score kept in ranked lists |
| `RETRIEVAL_LIST_MAX_ASSETS` | `10` (default) | Lambda | Most assets in a ranked list |

Local only: `AWS_PROFILE` (e.g. `overstone`), `S3_ENDPOINT_URL` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` for MinIO, and `RDS_*` directly in `.env` instead of the secret.

## Cost Estimates (rough, monthly)

Assumes 100 questions a day and one ingestion run a day that only processes new files.

| Service | Usage | Cost |
|---------|-------|------|
| **AWS Batch (Fargate)** | 1 job/day, ~10 min, 2 vCPU, 4GB | < $1 |
| **Textract** | OCR for new scanned pages only | < $1 |
| **Lambda** | 100 questions/day (~3,000/month), ~1.5s, 1GB | < $1 |
| **Bedrock** | 100 questions/day: planner + answer (Nova 2 Lite), embeddings, guardrail check | $5–10 |
| **ChromaDB (EC2)** | t3.small 24/7 + 30GB disk (measured) | ~$19 |
| **CloudWatch** | Batch and Lambda logs | $1–3 |
| **RDS MySQL** | existing platform database (not new cost) | ($20–30) |
| **S3** | existing platform bucket (not new cost) | ($2–3) |
| **Total** | | **~$50–70**, of which **~$30 is new** for this project |

Not included: the NAT gateway (already used by other systems), and the one-off first ingestion of an account (about $1–3 per 1,000 files, mostly Textract).

(Prices are list prices for eu-west-1/eu-west-2 and may change; use the AWS Pricing Calculator for accuracy.)

## Security Considerations

### In place

- **Each account only sees its own documents:** every search is filtered by `accountId`, and the platform server decides the account (never the browser).
- **Safety check on every question:** Bedrock Guardrails blocks prompt attacks and abusive questions before anything is searched.
- **No AWS keys:** Batch, Lambda and the server use IAM roles.
- **Private network:** ChromaDB and RDS are only reachable inside the VPC.
- **Database login in Secrets Manager**, not in code or the image.

### Known gaps and next steps

1. **Bank details can appear in answers:** the guardrail's sensitive-information filter is off (for the demo). When enabled, it masks only the written answer; the citation text shown in the UI comes straight from ChromaDB and also needs masking.
2. **The Lambda trusts the account it is given:** anyone allowed to invoke it can read any account. Keep invoke permission narrow; the platform server's Elastic Beanstalk role currently has `AWSLambda_FullAccess` (it can also change or delete any Lambda).
3. **"Read-only" database access is by convention only:** the RAG code reuses the platform server's database login, which can write. A dedicated read-only database user would enforce it. Rotating that login also affects the platform server.
4. **ChromaDB has no login:** anything inside the VPC that reaches `10.0.30.114:8000` can read or delete the stored documents. It's protected only by the network (security group).
5. **Questions are stored in logs:** the server logs each question sent to the Lambda and forwards logs to Datadog; errors go to CloudWatch. Set retention and decide what to redact.
6. **Data is processed across EU regions, not only the UK:** the LLM and the guardrail use EU cross-region routing. Confirm this is acceptable for client contracts (to be covered in the planned privacy/GDPR document).

## Monitoring Checklist

- [ ] Batch job success/failure rate
- [ ] Lambda invocation count, duration, error rate
- [ ] ChromaDB query latency
- [ ] Bedrock API errors (quota, throttling)
- [ ] RDS connection pool, query latency
- [ ] CloudWatch log volume and costs

---

**Status:** Plan (Phase 1 Infrastructure ready; Phase 2–3 to implement)  
**Last Updated:** 2026-09-30
