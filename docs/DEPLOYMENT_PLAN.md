# AWS Deployment Plan: Web Platform RAG

## Architecture Overview

A hybrid deployment with periodic ingestion (AWS Batch) and on-demand search (AWS Lambda).

```
┌─────────────────────────────────────────────────────────────────┐
│ AWS Environment                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────┐          ┌──────────────────────┐    │
│  │   AWS Batch Job      │          │   AWS Lambda         │    │
│  │  (Ingestion)         │          │  (Search & Answer)   │    │
│  │  • Fetch PDFs        │          │  • Embed query       │    │
│  │  • Parse + OCR       │          │  • Search ChromaDB   │    │
│  │  • Chunk + Embed     │          │  • Call Bedrock LLM  │    │
│  │  • Store in ChromaDB │          │  • Return results    │    │
│  └──────────────────────┘          └──────────────────────┘    │
│          │                                    │                 │
│          └────────────────┬───────────────────┘                 │
│                           │                                     │
│          ┌────────────────┴────────────────┐                    │
│          │   Shared Services               │                    │
│          ├─────────────────────────────────┤                    │
│          │ • S3 (PDFs)                     │                    │
│          │ • RDS MySQL (file catalog)      │                    │
│          │ • ChromaDB (vector store)       │                    │
│          │ • AWS Bedrock (embeddings+LLM)  │                    │
│          └─────────────────────────────────┘                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

Server Application
      │
      └──► Invoke Lambda (with query) ──► Response (answer + citations)
```

## AWS Services

| Service | Component | Purpose |
|---------|-----------|---------|
| **AWS Batch** | Ingestion Job | Periodic PDF download, parse, chunk, embed, store |
| **AWS Lambda** | Search Function | On-demand query processing and answer generation |
| **S3** | Storage | PDF files (input), optionally model artifacts |
| **RDS MySQL** | Database | File catalog, asset metadata, ownership (read-only in Batch+Lambda) |
| **ChromaDB** | Vector Store | Chunk embeddings and metadata (managed or self-hosted) |
| **AWS Bedrock** | LLM + Embeddings | Titan v2 embeddings, Nova 2 Lite LLM |
| **CloudWatch** | Logging | Job and Lambda logs |
| **EventBridge** (optional) | Scheduler | Trigger Batch job on a schedule or S3 event |

## Data Flow

### Ingestion (AWS Batch)

1. **Trigger:** Scheduled (cron) or S3 event (new PDF uploaded)
2. **Job runs:**
   - Connect to RDS MySQL (read-only): list files and ownership
   - Connect to S3: download PDFs
   - Parse with pypdf, OCR with Textract for scans
   - Chunk with RecursiveCharacterTextSplitter
   - Embed with Bedrock Titan v2
   - Upsert into ChromaDB
3. **Output:** Chunks in ChromaDB with metadata (accountId, assetId, fileId, page)

### Search (AWS Lambda)

1. **Input:** User query from server application
2. **Function runs:**
   - Embed query with Bedrock Titan v2
   - Search ChromaDB for top-k chunks (tenant-filtered)
   - Optionally: run planner for SQL filters + ranked search
   - Call Bedrock Nova 2 Lite LLM to generate answer with citations
3. **Output:** Answer with source citations, metadata

## Deployment Steps

### Phase 1: Infrastructure Setup

1. **RDS MySQL**
   - Ensure the existing `overstone_v2` database is accessible from AWS
   - Configure security group to allow access from Batch and Lambda
   - Keep credentials in AWS Secrets Manager

2. **S3**
   - S3 bucket for PDF files (already in use locally)
   - Ensure IAM roles for Batch and Lambda can read from it

3. **ChromaDB**
   - Option A: **Self-hosted** on EC2 with persistent volume
     - Run ChromaDB HTTP server on private EC2 instance
     - Configure security group for Batch/Lambda access
   - Option B: **Managed ChromaDB** (if available in your region)
   - Connection details stored in Secrets Manager

4. **IAM Roles and Policies**
   - **Batch Execution Role:** S3 (read), RDS (read), ChromaDB (read+write), Bedrock (invoke), Secrets Manager (read)
   - **Lambda Execution Role:** RDS (read), ChromaDB (read+write), Bedrock (invoke), Secrets Manager (read), CloudWatch (write logs)

5. **Secrets Manager**
   - `rds-credentials`: RDS_HOSTNAME, RDS_PORT, RDS_USERNAME, RDS_PASSWORD, RDS_DB_NAME
   - `bedrock-config`: AWS_PROFILE (or omit if using IAM role), BEDROCK_REGION
   - `chroma-config`: CHROMA_HOST, CHROMA_PORT

### Phase 2: Docker Image for Batch

1. **Dockerfile**
   - Base: Python 3.12
   - Copy app/ and dependencies
   - Copy `.env.example` → `.env` (or read from Secrets at runtime)
   - Entry point: `python -m app.happy_path`

2. **ECR Repository**
   - Create AWS ECR repo: `web-platform-rag-ingestion`
   - Build and push Docker image

3. **Batch Job Definition**
   - Image: ECR image
   - vCPU: 2
   - Memory: 4 GB
   - Environment: inject Secrets Manager values
   - Timeout: 3600 seconds (adjust based on PDF count)

4. **Batch Job Queue & Compute Environment**
   - Compute environment: EC2 or Fargate
   - Job queue: standard priority

### Phase 3: Lambda Function for Search

1. **Lambda Handler** (`lambda_handler.py`)
   - Input: `{"query": "...", "account_id": 46, "n_results": 3}`
   - Initialize: embeddings, ChromaDB client, LLM
   - Run search/answer pipeline
   - Output: `{"answer": "...", "hits": [...]}`

2. **Package and Deploy**
   - Create Lambda layer for dependencies (or bundle all in zip)
   - Upload to AWS Lambda
   - Set timeout: 60 seconds (adjust for LLM latency)
   - Allocate memory: 512 MB (Bedrock calls are fast)

3. **Environment Variables**
   - `RDS_*`, `CHROMA_*`, `BEDROCK_*` from Secrets Manager
   - Or passed by server at invocation

### Phase 4: Scheduling and Monitoring

1. **EventBridge Rule** (optional)
   - Trigger Batch job on schedule (e.g., daily 2 AM)
   - Or: S3 event trigger when PDF uploaded

2. **CloudWatch**
   - Batch job logs: `/aws/batch/job/web-platform-rag-ingestion`
   - Lambda logs: `/aws/lambda/web-platform-rag-search`
   - Set up alarms for failures

3. **Testing**
   - Run Batch job manually to verify ingestion
   - Test Lambda invocation from CLI: `aws lambda invoke --function-name web-platform-rag-search`

## Configuration

### Environment Variables (in AWS Secrets or Lambda env)

```env
# RDS
RDS_HOSTNAME=<rds-endpoint>
RDS_PORT=3306
RDS_USERNAME=<username>
RDS_PASSWORD=<password>
RDS_DB_NAME=overstone_v2

# S3
S3_ENDPOINT_URL=https://s3.amazonaws.com
S3_REGION=us-east-1
S3_BUCKET=<your-bucket>
S3_ACCESS_KEY=<key>
S3_SECRET_KEY=<secret>

# ChromaDB
CHROMA_HOST=<ec2-private-ip or managed-endpoint>
CHROMA_PORT=8000
CHROMA_COLLECTION=overstone-assets

# Bedrock
BEDROCK_REGION=eu-west-2
BEDROCK_LLM_REGION=eu-west-2
AWS_PROFILE=default (or omit; use IAM role)
BEDROCK_LLM_MODEL_ID=us.anthropic.claude-3-5-sonnet-20241022
BEDROCK_LLM_MAX_TOKENS=1024

# Embeddings
EMBEDDINGS_MODEL=amazon.titan-embed-text-v2:0
EMBEDDINGS_DIMENSION=1024

# Chunking
CHUNK_SIZE=1000
CHUNK_OVERLAP=150
```

## Cost Estimates (rough, monthly)

| Service | Usage | Cost |
|---------|-------|------|
| **AWS Batch** | 1 job/day, 10 min, 2 vCPU | $5–15 |
| **Lambda** | 1000 invocations/day, 10 sec avg | $2–5 |
| **RDS MySQL** | db.t3.micro (existing) | $20–30 |
| **ChromaDB (EC2)** | t3.small 24/7 | $15–20 |
| **S3** | 100 GB storage, minimal requests | $2–3 |
| **Bedrock** | 1000 queries/day (embeddings+LLM) | $50–100 |
| **CloudWatch** | Logs, alarms | $5–10 |
| **Total** | | **~$100–180** |

(Costs vary by region and workload; use AWS Pricing Calculator for accuracy)

## Security Considerations

1. **IAM Roles:** Principle of least privilege
   - Batch and Lambda get minimal permissions (no admin)
   - S3 read-only, RDS read-only, ChromaDB scoped to specific ops

2. **Secrets Management**
   - Store credentials in AWS Secrets Manager, not in code or `.env`
   - Rotate credentials periodically

3. **Network**
   - RDS and ChromaDB in private subnets
   - Batch and Lambda access via security group rules
   - S3 bucket policy restricts to AWS account

4. **Logging**
   - CloudWatch logs contain queries and results (may include PII)
   - Set retention policy (e.g., 30 days)
   - Encrypt logs at rest

## Future Improvements

1. **Caching:** Redis or CloudFront for frequently asked questions
2. **Autoscaling:** Lambda concurrent execution limits; Batch spot instances
3. **Multi-region:** Replicate ChromaDB for low-latency access
4. **Monitoring:** Custom metrics (query latency, chunk quality, cost per query)
5. **A/B Testing:** Run multiple LLM models, compare answer quality
6. **Sales-table tool:** Add RDS read access to `sales` table for market queries

## Rollback and Disaster Recovery

1. **Batch Job:** Can safely re-run; upsert is idempotent
2. **Lambda:** Versioning and aliases allow quick rollback
3. **ChromaDB:** Backup before large ingestion; snapshots on EC2
4. **RDS:** Automated backups (existing)

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
