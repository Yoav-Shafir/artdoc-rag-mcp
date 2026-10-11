"""CDK stack for the MCP server Lambda (dev), next to the query Lambda stack.

The server is the same FastMCP http server as `uv run poe mcp-http`. The Lambda Web Adapter layer
runs it as a normal web server inside Lambda (mcp_server/run.sh), so no Lambda-specific code.
Users reach it through a public Function URL; the server's own Cognito sign-in protects it.

Created outside CDK (commands in docs/MCP_AUTH_SETUP.md): the Cognito client secret in Secrets
Manager, and the Function URL as an allowed return address in Cognito.
"""

import json

from aws_cdk import BundlingOptions, CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from constructs import Construct

from cdk_rag.rag_stack import AVAILABILITY_ZONES, PRIVATE_SUBNET_IDS, SECURITY_GROUP_ID, VPC_ID

# Runs the web server inside Lambda (https://github.com/awslabs/aws-lambda-web-adapter)
WEB_ADAPTER_LAYER = "arn:aws:lambda:eu-west-1:753240598075:layer:LambdaAdapterLayerX86:30"


class OverstoneMcpStack(Stack):
    """One MCP server Lambda; the subclasses only choose the environment name."""

    env_name: str = ""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        with open(f"config/mcp-{self.env_name}.json") as file:
            envs = json.load(file)

        # Same network as the query Lambda: the server reads the platform's MySQL.
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

        # Sign-ins (Lambda forgets local files between runs); rows expire by themselves.
        sessions = dynamodb.Table(
            self,
            "mcp-sessions",
            table_name=f"overstone-mcp-sessions-{self.env_name}",
            partition_key=dynamodb.Attribute(name="collection", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="key", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,  # only sign-ins; users just sign in again
        )

        role = iam.Role(
            self,
            "mcp-lambda-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                # CloudWatch logs + network interfaces for running inside the VPC.
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
        )
        sessions.grant_read_write_data(role)
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[f"arn:aws:lambda:{self.region}:{self.account}:function:cdk-rag-query-{self.env_name}"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{envs['AWS_SECRET_NAME']}-*",
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{envs['MCP_COGNITO_SECRET_NAME']}-*",
                ],
            )
        )

        code = _lambda.Code.from_asset(
            ".",
            # Only app/ and mcp_server/ are sent to the bundling container.
            exclude=["*", "!app", "!app/**", "!mcp_server", "!mcp_server/**", "**/__pycache__"],
            bundling=BundlingOptions(
                image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                command=[
                    "bash",
                    "-c",
                    "pip install --no-cache-dir -r mcp_server/requirements.txt -t /asset-output"
                    " && cp -r app mcp_server /asset-output/ && cp mcp_server/run.sh /asset-output/"
                    " && chmod +x /asset-output/run.sh",
                ],
            ),
        )

        function = _lambda.Function(
            self,
            f"cdk-rag-mcp-{self.env_name}",
            function_name=f"cdk-rag-mcp-{self.env_name}",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="run.sh",
            code=code,
            layers=[_lambda.LayerVersion.from_layer_version_arn(self, "web-adapter", WEB_ADAPTER_LAYER)],
            memory_size=512,
            timeout=Duration.seconds(90),  # waits for the query Lambda (up to 60 s)
            role=role,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=vpc.private_subnets),
            security_groups=[security_group],
            environment={
                **envs,
                "MCP_SESSIONS_TABLE": sessions.table_name,
                "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",  # start the Web Adapter
                "AWS_LWA_PORT": "8080",
                "AWS_LWA_READINESS_CHECK_PROTOCOL": "tcp",  # ready once the port is open
                "FASTMCP_HOME": "/tmp/fastmcp",  # the only writable folder on Lambda
            },
        )

        # Public address; anyone can reach it, but every tool call needs a Cognito sign-in.
        url = function.add_function_url(auth_type=_lambda.FunctionUrlAuthType.NONE)
        # AWS also requires InvokeFunction for public URLs, limited to calls made through the URL
        invoke_via_url = _lambda.CfnPermission(
            self,
            "invoke-via-url",
            action="lambda:InvokeFunction",
            function_name=function.function_name,
            principal="*",
        )
        # Not in this CDK version yet, so set on the CloudFormation resource directly
        invoke_via_url.add_property_override("InvokedViaFunctionUrl", True)
        CfnOutput(self, "McpUrl", value=url.url)


class OverstoneMcpStackDev(OverstoneMcpStack):
    env_name = "dev"
