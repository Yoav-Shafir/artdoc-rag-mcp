#!/usr/bin/env python3
"""CDK entry point (cdk.json runs it). Not named app.py: that would clash with the app/ package."""

import aws_cdk as cdk

from cdk_rag.mcp_stack import OverstoneMcpStackDev
from cdk_rag.rag_stack import OverstoneRagQueryStackDev, OverstoneRagQueryStackProd

ENV = cdk.Environment(account="555547688340", region="eu-west-1")

app = cdk.App()

# Query Lambda
OverstoneRagQueryStackProd(app, "OverstoneLambdaRagQueryStackProd", env=ENV)
OverstoneRagQueryStackDev(app, "OverstoneLambdaRagQueryStackDev", env=ENV)

# MCP server Lambda (dev only for now)
OverstoneMcpStackDev(app, "OverstoneLambdaRagMcpStackDev", env=ENV)

app.synth()
