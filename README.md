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
├── app/                      # shared code (Batch job, Lambda, local scripts)
│   ├── config.py             # settings from .env / environment
│   ├── batch_ingestion.py    # ingestion job (AWS Batch)
│   ├── query.py              # ask(): the query flow used by the Lambda
│   ├── planner.py            # the LLM question planner
│   ├── rag.py                # embeddings, ChromaDB, LLM, guardrail check, answer
│   ├── happy_path.py         # PDF parsing + Textract OCR (used by ingestion)
│   ├── hybrid_search.py      # older demo
│   ├── eval.py               # evaluation script
│   ├── db/database.py        # all MySQL queries (read-only)
│   └── storage/s3_storage.py # S3 / MinIO client
├── lambda/
│   ├── main.py               # Lambda entry point
│   └── requirements.txt
├── cdk_rag/rag_stack.py      # CDK stack for the Lambda
├── cdk_app.py, cdk.json
├── config/dev.json, prod.json  # Lambda settings (applied by CDK)
├── Dockerfile                # Batch job image
├── requirements.txt, requirements-batch.txt
├── scripts/                  # copy_account_pdfs, run_demo, show_chroma, verify_services
├── events/                   # sample Lambda events + demo_queries.json
├── evals/questions.json
├── tests/                    # unit + integration tests
├── docs/                     # DEPLOYMENT, HYBRID_QUERY_STRATEGY, RESPONSE_CONTRACT, FILTER_DILEMMAS
├── compose.yaml              # local MinIO + ChromaDB
├── .env.example              # all settings, documented
└── pyproject.toml, uv.lock
```

## Environment Setup

Local runs read settings from `.env` (copy `.env.example` to start).

- **MySQL:** the platform's dev database in the local `mysql` container (`RDS_*` in `.env`).
- **S3:** local MinIO, or the AWS dev bucket (toggle in `.env`).
- **ChromaDB:** the local container on `localhost:8000`.

The values used in AWS are in `config/dev.json` (Lambda) and `.env.prod`; see
[docs/ARCHITECTURE_AND_DEPLOYMENT.md](docs/ARCHITECTURE_AND_DEPLOYMENT.md#configuration). To run a script locally against production
resources, copy the needed values from `.env.prod` into `.env` (only `.env` is read).

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

### Run the Lambda locally, or the demo questions against AWS

```bash
uv run poe lambda events/ask_answer.json   # the Lambda handler on your machine (uses .env)
uv run poe demo                            # events/demo_queries.json against the deployed Lambda
```

The Batch job's check-only mode: `uv run python -m app.batch_ingestion --check-only`.

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

All settings are listed and explained in `.env.example`. The values used in AWS are in
[docs/ARCHITECTURE_AND_DEPLOYMENT.md](docs/ARCHITECTURE_AND_DEPLOYMENT.md#configuration).

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
curl -s http://localhost:8000/api/v2/heartbeat
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

The architecture diagram, AWS services, configuration and redeploy steps are in [docs/ARCHITECTURE_AND_DEPLOYMENT.md](docs/ARCHITECTURE_AND_DEPLOYMENT.md).
The steps below record how the infrastructure was first set up.

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
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=wp-chromadb}]'
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
curl http://localhost:8000/api/v2/heartbeat
```

#### Get private IP
```bash
# Inside the instance
hostname -I

# Take note of the private IP (e.g., 10.0.30.114)
# Update this IP in config/dev.json and config/prod.json as CHROMA_HOST
```

### 3. Secrets Manager: Database Credentials

No new secret is needed: the Lambda and the Batch job reuse the platform's existing secret
`web-platform-server-v2` (`AWS_SECRET_NAME`), which holds `RDS_HOSTNAME`, `RDS_PORT`, `RDS_USERNAME`,
`RDS_PASSWORD` and `RDS_DB_NAME`. **Don't create or overwrite it:** it belongs to the platform server.

Check it exists (this shows its details, not the values):
```bash
aws secretsmanager describe-secret --secret-id web-platform-server-v2 --region eu-west-1 --profile overstone
```

### 4. Lambda Deployment with CDK

#### Configure environment

The Lambda's settings come from `config/dev.json` (all keys are listed in
[docs/ARCHITECTURE_AND_DEPLOYMENT.md](docs/ARCHITECTURE_AND_DEPLOYMENT.md#configuration)), for example:
```json
{
  "AWS_SECRET_NAME": "web-platform-server-v2",
  "CHROMA_HOST": "10.0.30.114",
  "BEDROCK_REGION": "eu-west-2",
  "BEDROCK_LLM_MODEL_ID": "eu.amazon.nova-2-lite-v1:0",
  "BEDROCK_LLM_REGION": "eu-west-1",
  "BEDROCK_GUARDRAIL_ID": "4zibaxd3h7sd",
  "BEDROCK_GUARDRAIL_VERSION": "1"
}
```

#### Install CDK dependencies
```bash
uv sync --group cdk
```

#### Deploy the Lambda
```bash
AWS_PROFILE=overstone uv run --group cdk cdk diff OverstoneLambdaRagQueryStackDev    # see what would change
AWS_PROFILE=overstone uv run --group cdk cdk deploy OverstoneLambdaRagQueryStackDev  # deploy
```
A prod stack (`OverstoneLambdaRagQueryStackProd`) is defined but not deployed; the platform server uses the
dev Lambda.

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

## Links

- [MinIO Documentation](https://docs.min.io/)
- [ChromaDB Documentation](https://docs.trychroma.com/)
- [Boto3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)

## License

Proprietary - Overstone
