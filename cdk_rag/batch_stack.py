"""CDK stack for AWS Batch ingestion job (dev and prod), using Fargate compute.

The job runs batch_ingestion.py daily to ingest PDFs from S3 into ChromaDB.
Fargate compute (serverless) handles infrastructure automatically.
"""

import json
import os

from aws_cdk import Duration, Stack
from aws_cdk import aws_batch_alpha as batch
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from constructs import Construct

# Same VPC, private subnets and security group as the Lambda stacks.
VPC_ID = "vpc-0502723d020b83cdf"
AVAILABILITY_ZONES = ["eu-west-1a", "eu-west-1b"]
PRIVATE_SUBNET_IDS = ["subnet-0faf0cb3cc9d9d754", "subnet-09964aca1d382da21"]
SECURITY_GROUP_ID = "sg-0a0402d6ebc1b596f"

# Bedrock models the job calls: Titan embeddings (eu-west-2)
BEDROCK_RESOURCES = [
    "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0",
]


class OverstoneBatchStack(Stack):
	"""One Batch job (ingestion); the subclasses only choose the environment name."""

	env_name: str = ""

	def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
		super().__init__(scope, construct_id, **kwargs)

		with open(f"config/{self.env_name}.json") as file:
			envs = json.load(file)

		# from_vpc_attributes instead of from_lookup: no AWS calls at synth time.
		vpc = ec2.Vpc.from_vpc_attributes(
			self,
			"wp-vpc",
			vpc_id=VPC_ID,
			availability_zones=AVAILABILITY_ZONES,
			private_subnet_ids=PRIVATE_SUBNET_IDS,
		)
		security_group = ec2.SecurityGroup.from_security_group_id(
			self, "batch-vpc-access", security_group_id=SECURITY_GROUP_ID
		)

		# IAM role for Batch job execution
		job_role = iam.Role(
			self,
			"batch-job-role",
			assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
		)
		job_role.add_to_policy(
			iam.PolicyStatement(
				actions=["s3:GetObject", "s3:ListBucket"],
				resources=[
					f"arn:aws:s3:::{envs['S3_BUCKET']}",
					f"arn:aws:s3:::{envs['S3_BUCKET']}/*",
				],
			)
		)
		job_role.add_to_policy(
			iam.PolicyStatement(
				actions=["bedrock:InvokeModel"],
				resources=BEDROCK_RESOURCES,
			)
		)
		job_role.add_to_policy(
			iam.PolicyStatement(
				actions=["secretsmanager:GetSecretValue"],
				resources=[
					f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{envs['AWS_SECRET_NAME']}-*"
				],
			)
		)

		# IAM role for Batch service
		batch_role = iam.Role(
			self,
			"batch-service-role",
			assumed_by=iam.ServicePrincipal("batch.amazonaws.com"),
			managed_policies=[
				iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSBatchServiceRole"),
			],
		)

		# Compute environment (Fargate, serverless)
		compute_env = batch.ComputeEnvironment(
			self,
			f"batch-compute-{self.env_name}",
			compute_resources=batch.ComputeResources(
				type=batch.ComputeResourceType.FARGATE,
				vpc=vpc,
			),
			service_role=batch_role,
		)

		# Job queue. Passed to `self`, so CDK automatically deploys it.
		# No variable needed unless we reference it elsewhere.
		batch.JobQueue(
			self,
			f"batch-queue-{self.env_name}",
			compute_environment=compute_env,
			priority=1,
		)

		# Build and push Docker image to ECR automatically
		image = ecs.ContainerImage.from_asset(
			directory=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
			file="Dockerfile",
		)

		# Job definition
		batch.EcsJobDefinition(
			self,
			f"batch-job-def-{self.env_name}",
			container=batch.EcsJobDefinitionContainer(
				image=image,
				vcpus=ecs.CpuArchitecture.X86_64,
				memory=ecs.Size.mebibytes(4096),
				cpu=ecs.CpuArchitecture.X86_64,
				job_role=job_role,
				environment={
					"AWS_SECRET_NAME": envs["AWS_SECRET_NAME"],
					"AWS_REGION": self.region,
					"S3_BUCKET": envs["S3_BUCKET"],
					"CHROMA_HOST": envs["CHROMA_HOST"],
					"CHROMA_PORT": str(envs["CHROMA_PORT"]),
					"BEDROCK_REGION": envs["BEDROCK_REGION"],
					"BEDROCK_LLM_REGION": envs.get("BEDROCK_LLM_REGION", "eu-west-1"),
					"CHUNK_SIZE": str(envs.get("CHUNK_SIZE", "1000")),
					"CHUNK_OVERLAP": str(envs.get("CHUNK_OVERLAP", "150")),
				},
			),
			job_def_name=f"overstone-batch-ingestion-{self.env_name}",
		)


class OverstoneBatchStackProd(OverstoneBatchStack):
	env_name = "prod"


class OverstoneBatchStackDev(OverstoneBatchStack):
	env_name = "dev"
