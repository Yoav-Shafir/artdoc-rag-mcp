"""CDK stacks for the RAG query Lambda (dev and prod), following cdk-pdf-reports.

The bundle is built from this repo with an explicit command instead of
PythonFunction: the handler (lambda/main.py) imports the shared `app` package,
which lives outside the lambda/ folder. Only lambda/requirements.txt is
installed, so ingestion-only libraries (pypdf, Textract code) stay out.
"""

import json

from aws_cdk import BundlingOptions, Duration, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from constructs import Construct

# Same VPC, private subnets and security group as the other Overstone Lambdas.
VPC_ID = "vpc-0502723d020b83cdf"
AVAILABILITY_ZONES = ["eu-west-1a", "eu-west-1b"]
PRIVATE_SUBNET_IDS = ["subnet-0faf0cb3cc9d9d754", "subnet-09964aca1d382da21"]
SECURITY_GROUP_ID = "sg-0a0402d6ebc1b596f"

# Bedrock models the Lambda calls: Titan embeddings (eu-west-2) and the Nova
# inference profile (eu-west-1), which routes to models in other EU regions.
BEDROCK_RESOURCES = [
    "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0",
    "arn:aws:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0",
    "arn:aws:bedrock:*:*:inference-profile/eu.amazon.nova-2-lite-v1:0",
]


class OverstoneRagQueryStack(Stack):
    """One query Lambda; the subclasses only choose the environment name."""

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
            self, "lambda-vpc-access", security_group_id=SECURITY_GROUP_ID
        )

        role = iam.Role(
            self,
            "rag-query-lambda-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                # CloudWatch logs + network interfaces for running inside the VPC.
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
        )
        role.add_to_policy(
            iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=BEDROCK_RESOURCES)
        )
        if envs.get("BEDROCK_GUARDRAIL_ID"):
            # The guardrail (in the LLM region) and its EU guardrail profile in every destination
            # region: Standard tier routes checks across EU regions, so the region is `*`.
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=["bedrock:ApplyGuardrail"],
                    resources=[
                        f"arn:aws:bedrock:{envs['BEDROCK_LLM_REGION']}:{self.account}:guardrail/{envs['BEDROCK_GUARDRAIL_ID']}",
                        f"arn:aws:bedrock:*:{self.account}:guardrail-profile/eu.guardrail.v1:0",
                    ],
                )
            )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{envs['AWS_SECRET_NAME']}-*"
                ],
            )
        )

        code = _lambda.Code.from_asset(
            ".",
            # Only app/ and lambda/ are sent to the bundling container.
            exclude=["*", "!app", "!app/**", "!lambda", "!lambda/**", "**/__pycache__"],
            bundling=BundlingOptions(
                image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                command=[
                    "bash",
                    "-c",
                    "pip install --no-cache-dir -r lambda/requirements.txt -t /asset-output"
                    " && cp -r app /asset-output/ && cp lambda/main.py /asset-output/",
                ],
            ),
        )

        _lambda.Function(
            self,
            f"cdk-rag-query-{self.env_name}",
            function_name=f"cdk-rag-query-{self.env_name}",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="main.handler",
            code=code,
            memory_size=1024,
            timeout=Duration.seconds(60),  # planner + embeddings + LLM answer
            role=role,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=vpc.private_subnets),
            security_groups=[security_group],
            environment=envs,
        )


class OverstoneRagQueryStackProd(OverstoneRagQueryStack):
    env_name = "prod"


class OverstoneRagQueryStackDev(OverstoneRagQueryStack):
    env_name = "dev"
