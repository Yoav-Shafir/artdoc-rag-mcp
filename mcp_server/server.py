"""Overstone MCP server: lets an AI assistant search Overstone documents (step 1: local only).

Step 1 of docs/MCP_DESIGN.md: one tool, no sign-in, a fixed test account, run on your machine.
The tool calls the deployed AI search Lambda, so answers match the Overstone AI chat.

Run (an assistant starts it for you, see docs/MCP_DESIGN.md):
    uv run poe mcp
"""

import json

import boto3
from fastmcp import FastMCP

from app.config import config

# Step 1 only: a fixed test account. Later the account comes from the signed-in user.
TEST_ACCOUNT_ID = 46
LAMBDA_NAME = "cdk-rag-query-dev"

mcp = FastMCP(
    name="Overstone",
    instructions="Search the documents attached to your Overstone assets.",
    version="0.1.0",
)

# Created on first use, then reused for every tool call.
_lambda_client = None


def lambda_client():
    global _lambda_client
    if _lambda_client is None:
        session = boto3.Session(profile_name=config.bedrock.aws_profile)  # your AWS profile from .env
        _lambda_client = session.client("lambda", region_name="eu-west-1")
    return _lambda_client


@mcp.tool(title="Search documents")
def search_documents(question: str) -> str:
    """Search the documents attached to the user's Overstone assets (invoices, valuations,
    provenance, certificates of authenticity, insurance, exhibition and loan documents).

    Returns either a written answer with numbered sources, or a list of matching assets with the
    text that matched. Use it for questions about what the documents say, e.g. "Who issued the
    invoice for Half of You?" or "Which assets were bought at Christie's?".
    """
    response = lambda_client().invoke(
        FunctionName=LAMBDA_NAME,
        Payload=json.dumps({"query": question, "accountId": TEST_ACCOUNT_ID}),
    )
    result = json.loads(response["Payload"].read())
    return format_result(result)


def format_result(result: dict) -> str:
    """Turn the AI search reply into short readable text for the assistant."""
    if result.get("error") or "errorMessage" in result:
        return f"The search failed: {result.get('error') or result.get('errorMessage')}"

    if result["kind"] == "answer":
        lines = [result["answer"], "", "Sources:"]
        for c in result["citations"]:
            # One line per source, e.g.:
            #   [1] YONA0004_2.pdf (VALUATION_REPORTS), page 1, asset: Half of You
            lines.append(f"[{c['n']}] {c['fileName']} ({c['category']}), page {c['page']}, asset: {c['assetName']}")
        return "\n".join(lines)

    lines = [f"{result['total_assets']} matching asset(s), best match first:"]
    for a in result["assets"]:
        # One line per asset, e.g.:
        #   - May 15, 2001 (asset ID 6945): KEMA0001 Doyle.pdf (PROVENANCE) — "…sold at Doyle New York…"
        line =f"- {a['assetName']} (asset ID {a['assetId']})"
        if a["evidence"]:
            best = a["evidence"][0]
            line += f": {best['fileName']} ({best['category']})"
            if best.get("text"):
                line += f" — \"{' '.join(best['text'].split())[:200]}…\""
        lines.append(line)
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()  # stdio: the assistant starts this program and talks to it directly
