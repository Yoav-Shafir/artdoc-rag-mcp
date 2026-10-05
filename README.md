# Web Platform RAG: Batch Ingestion & Query Lambda

A Retrieval-Augmented Generation (RAG) project with AWS Batch ingestion and Lambda query endpoints, using local S3 storage (MinIO) and vector database (ChromaDB) for development.

## Prerequisites

- Python 3.12+
- Docker & Docker Compose
- `uv` package manager
- MinIO license file (`minio.license`) in the project root — required for Docker Compose to start the MinIO container

## Project Structure

```
overstone-rag/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── query.py
│   ├── rag.py
│   ├── planner.py
│   ├── batch_ingestion.py
│   ├── happy_path.py
│   ├── hybrid_search.py
│   ├── eval.py
│   ├── db/
│   │   ├── __init__.py
│   │   └── database.py
│   └── storage/
│       ├── __init__.py
│       └── s3_storage.py
├── lambda/
│   ├── main.py
│   └── requirements.txt
├── cdk_rag/
│   ├── __init__.py
│   └── rag_stack.py
├── cdk_app.py
├── cdk.json
├── scripts/
│   ├── __init__.py
│   ├── copy_account_pdfs.py
│   ├── show_chroma.py
│   └── verify_services.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── __init__.py
│   │   └── test_config.py
│   └── integration/
│       ├── __init__.py
│       ├── test_database.py
│       ├── test_planner_values.py
│       └── test_s3_storage.py
├── events/
│   ├── ask_answer.json
│   └── ask_list.json
├── docs/
│   ├── RAG_IMPLEMENTATION_PLAN.md
│   ├── DEPLOYMENT.md
│   └── HYBRID_QUERY_STRATEGY.md
├── config/
│   ├── dev.json
│   └── prod.json
├── evals/
│   └── questions.json
├── compose.yaml
├── .env
├── .env.example
├── pyproject.toml
├── uv.lock
├── minio-data/
└── chroma.sqlite3
```

## Environment Setup

The project supports two environments:

### Development (Local)
- **MySQL:** Read from Secrets Manager (`web-platform-server-v2-dev`)
- **S3:** MinIO (local) or AWS dev bucket (toggle in `.env`)
- **ChromaDB:** localhost:8000

**To use dev:**
```bash
# .env is already set for dev (default)
uv sync
docker compose up -d
```

### Production
- **RDS:** Read from Secrets Manager (`web-platform-server-v2`)
- **S3:** AWS production bucket
- **ChromaDB:** EC2 instance (10.0.30.114:8000)

**To switch to prod:**
```bash
# Use .env.prod instead of .env
export DOTENV_FILE=.env.prod
# Or manually copy settings to .env
```

**For batch ingestion with prod:** Use `.env.prod` or set `AWS_SECRET_NAME=web-platform-server-v2` in your environment.

---

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

All scripts run with `uv run` (no need to manually activate the venv):

### Verify Services

```bash
uv run poe verify
```

### Run Ingestion

```bash
uv run poe batch
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

### MinIO Console
Access the MinIO web UI at **http://localhost:9001** to view and manage S3 buckets:
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


## Package Management

### List All Packages

```bash
uv pip list
```

### Show Dependency Tree

```bash
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

## Deployment

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AWS Environment (eu-west-1)                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────┐         Query Flow                                    │
│  │   API Server     │                                                        │
│  │  (Platform)      │                                                        │
│  └────────┬─────────┘                                                        │
│           │                                                                  │
│           │ {"query", "accountId"}                                          │
│           ▼                                                                  │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │              Lambda: cdk-rag-query (dev/prod)                   │        │
│  │  ┌──────────────┐   ┌──────────────────┐  ┌──────────────────┐ │        │
│  │  │ LLM Planner  │──▶│ Candidate Filter │──▶│ Vector Search   │ │        │
│  │  │ (Claude)     │   │    (MySQL)       │  │  (ChromaDB)      │ │        │
│  │  └──────────────┘   └──────────────────┘  └──────────────────┘ │        │
│  │           │                                        │             │        │
│  │           └────────────────┬─────────────────────┘              │        │
│  │                            │                                     │        │
│  │                   ┌────────▼────────┐                           │        │
│  │                   │  LLM Answer     │                           │        │
│  │                   │  (Claude)       │                           │        │
│  │                   └────────┬────────┘                           │        │
│  └────────────────────────────┼───────────────────────────────────┘        │
│                               │                                              │
│                    Response: answer + citations                              │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                        Ingestion Flow (AWS Batch Job)                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────┐   ┌──────────────┐   ┌────────────────┐                │
│  │   AWS Batch    │──▶│   S3 Bucket  │──▶│  PDF Parser    │                │
│  │   (1x/day)     │   │ (PDFs)       │   │  + Textract    │                │
│  └────────────────┘   └──────────────┘   └────────┬───────┘                │
│                                                    │                         │
│                                                    ▼                         │
│                                          ┌────────────────────┐             │
│                                          │ Embed + Chunk      │             │
│                                          │ (Bedrock Titan v2) │             │
│                                          └────────┬───────────┘             │
│                                                    │                         │
│                                                    ▼                         │
│                                          ┌────────────────────┐             │
│                                          │    ChromaDB        │             │
│                                          │   (EC2 Private)    │             │
│                                          └────────────────────┘             │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                      Shared Data Access & Configuration                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────┐   │
│  │  RDS (MySQL)    │    │ Secrets Manager  │    │  Bedrock Services   │   │
│  │  (Catalog +     │    │ (DB Credentials) │    │  - Embeddings       │   │
│  │   Asset Data)   │    │                  │    │  - LLM (Claude)     │   │
│  └─────────────────┘    └──────────────────┘    └─────────────────────┘   │
│         ▲                        ▲                           ▲              │
│         │ SELECT assets          │ GetSecretValue            │ InvokeModel  │
│         └────────────┬───────────┴────────────┬──────────────┘             │
│                      │                        │                             │
│              ┌───────┴────────┐      ┌────────┴─────────┐                 │
│              │    Lambda      │      │   Batch Job     │                 │
│              │    (Query)     │      │   (Ingestion)   │                 │
│              └────────────────┘      └─────────────────┘                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1. VPC & Security Group Setup

#### List existing VPC and subnets
```bash
aws ec2 describe-vpcs --region eu-west-1 --query 'Vpcs[*].[VpcId,CidrBlock]' --output table

aws ec2 describe-subnets --region eu-west-1 --filters "Name=vpc-id,Values=vpc-0502723d020b83cdf" \
  --query 'Subnets[*].[SubnetId,AvailabilityZone,CidrBlock,MapPublicIpOnLaunch]' --output table
```

#### Security Group: Allow Lambda to ChromaDB

Get the Lambda security group ID and add inbound rule to ChromaDB security group:
```bash
# View current rules
aws ec2 describe-security-groups --group-ids sg-0a0402d6ebc1b596f --region eu-west-1 \
  --query 'SecurityGroups[0].IpPermissions' --output table

# Add inbound rule: allow port 8000 from Lambda security group
aws ec2 authorize-security-group-ingress \
  --group-id sg-0a0402d6ebc1b596f \
  --protocol tcp \
  --port 8000 \
  --source-group <lambda-security-group-id> \
  --region eu-west-1
```

#### Security Group: Allow outbound traffic (ChromaDB)
```bash
# Add outbound rule to allow all traffic
aws ec2 authorize-security-group-egress \
  --group-id sg-0a0402d6ebc1b596f \
  --protocol -1 \
  --cidr 0.0.0.0/0 \
  --region eu-west-1
```

### 2. EC2 Instance: ChromaDB Setup

#### Launch EC2 instance in private subnet
```bash
aws ec2 run-instances \
  --image-id ami-0dad359ff462124ca \
  --instance-type t3.small \
  --subnet-id subnet-0faf0cb3cc9d9d754 \
  --security-group-ids sg-0a0402d6ebc1b596f \
  --region eu-west-1 \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=overstone-chromadb}]'
```

#### Connect via Session Manager
```bash
# Get instance ID from above, then connect using either:

# Option 1: Using region
aws ssm start-session --target <instance-id> --region eu-west-1

# Option 2: Using AWS CLI profile (if configured)
aws ssm start-session --target <instance-id> --profile overstone
```

#### Install Docker on EC2
```bash
# Run inside the instance via Session Manager
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin

sudo systemctl start docker
sudo systemctl enable docker

# Verify Docker is running
docker --version
docker compose version
```

#### Run ChromaDB container
```bash
# Inside the instance
docker run -d \
  --name chromadb \
  --restart unless-stopped \
  -p 8000:8000 \
  chromadb/chroma:latest

# Verify it's running
docker ps
curl http://localhost:8000/api/v1/heartbeat
```

#### Get private IP
```bash
# Inside the instance
hostname -I

# Take note of the private IP (e.g., 10.0.30.114)
# Update this IP in config/dev.json and config/prod.json as CHROMA_HOST
```

### 3. Secrets Manager: Database Credentials

#### Store RDS credentials in Secrets Manager
```bash
aws secretsmanager create-secret \
  --name web-platform-server-v2 \
  --description "Overstone platform database credentials" \
  --secret-string '{
    "RDS_HOSTNAME": "mysql.example.com",
    "RDS_PORT": 3306,
    "RDS_USERNAME": "user",
    "RDS_PASSWORD": "password",
    "RDS_DB_NAME": "overstone_v2"
  }' \
  --region eu-west-1
```

#### Retrieve and verify
```bash
aws secretsmanager get-secret-value --secret-id web-platform-server-v2 --region eu-west-1
```

### 4. Lambda Deployment with CDK

#### Configure environment

Update `config/dev.json` and `config/prod.json`:
```json
{
  "AWS_SECRET_NAME": "web-platform-server-v2",
  "CHROMA_HOST": "10.0.30.114",
  "CHROMA_PORT": "8000",
  "BEDROCK_REGION": "eu-west-2",
  "BEDROCK_LLM_REGION": "eu-west-1",
  "EMBEDDINGS_MODEL": "amazon.titan-embed-text-v2:0",
  "EMBEDDINGS_DIMENSION": "1024",
  "LLM_MODEL": "amazon.nova-2-lite-v1:0"
}
```

#### Install CDK dependencies
```bash
uv sync --with cdk
```

#### Synthesize CloudFormation template
```bash
uv run cdk synth
```

#### Deploy Lambda stacks
```bash
# Deploy dev stack
uv run cdk deploy OverstoneLambdaRagQueryStackDev --region eu-west-1

# Deploy prod stack
uv run cdk deploy OverstoneLambdaRagQueryStackProd --region eu-west-1
```

#### Verify Lambda was created
```bash
aws lambda list-functions --region eu-west-1 --query 'Functions[?contains(FunctionName, `cdk-rag-query`)]' --output table

aws lambda get-function-configuration --function-name cdk-rag-query-dev --region eu-west-1 --output table
```

### 5. Verification & Testing

#### Test Lambda locally
```bash
# Using sample event
uv run poe lambda events/ask_answer.json
uv run poe lambda events/ask_list.json
```

#### Invoke Lambda in AWS
```bash
# Test the dev Lambda
aws lambda invoke \
  --function-name cdk-rag-query-dev \
  --region eu-west-1 \
  --payload '{"query":"Who issued the invoice?","accountId":46}' \
  /tmp/response.json

cat /tmp/response.json | jq .
```

#### Check Lambda CloudWatch logs
```bash
# View recent logs
aws logs tail /aws/lambda/cdk-rag-query-dev --region eu-west-1 --follow

# Or search for errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/cdk-rag-query-dev \
  --region eu-west-1 \
  --filter-pattern "ERROR" \
  --output table
```

#### Test ChromaDB connectivity from Lambda
```bash
# Create a test event that triggers the Lambda with a simple query
aws lambda invoke \
  --function-name cdk-rag-query-dev \
  --region eu-west-1 \
  --payload '{"query":"test","accountId":46}' \
  /tmp/test-response.json && cat /tmp/test-response.json
```

#### Verify Secrets Manager access
```bash
# The Lambda should be able to read the secret
# Check IAM permissions on the Lambda role
aws iam list-role-policies --role-name cdk-rag-query-lambda-role-<account>-<region>

# Get the role name from the Lambda config
aws lambda get-function-configuration --function-name cdk-rag-query-dev --region eu-west-1 \
  --query 'Role' --output text
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
