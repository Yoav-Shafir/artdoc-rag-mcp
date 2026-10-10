"""Overstone MCP server: lets an AI assistant search Overstone documents (local only for now).

One tool, run on your machine. Signed-in users search their own account; without sign-in
(stdio) a fixed test account is used. The tool calls the deployed AI search
Lambda, so answers match the Overstone AI chat.

Two ways to run it:
    uv run poe mcp        # stdio, no sign-in: an assistant on this machine starts it
    uv run poe mcp-http   # http://localhost:8080/mcp, users sign in with Overstone (Cognito)
See docs/MCP_AUTH_SETUP.md for the sign-in setup.
"""

import json
import os
import sys

import boto3
from fastmcp import FastMCP
from fastmcp.server.auth.providers.aws import AWSCognitoProvider
from fastmcp.server.dependencies import get_access_token

from app.config import config
from app.db.database import Database

# Used only without sign-in (stdio, Inspector): nobody is signed in, so there's no account to look up.
TEST_ACCOUNT_ID = 46
LAMBDA_NAME = "cdk-rag-query-dev"

# Sign-in (http only): the dev Cognito pool and the local address of this server.
COGNITO_USER_POOL_ID = "eu-west-1_UTwJgz6im"
COGNITO_REGION = "eu-west-1"
HTTP_PORT = 8080
BASE_URL = f"http://localhost:{HTTP_PORT}"

mcp = FastMCP(
    name="Overstone",
    instructions="Search the documents attached to your Overstone assets.",
    version="0.1.0",
)

# Created on first use, then reused for every tool call.
_lambda_client = None
_database = None


def lambda_client():
    global _lambda_client
    if _lambda_client is None:
        session = boto3.Session(profile_name=config.bedrock.aws_profile)  # your AWS profile from .env
        _lambda_client = session.client("lambda", region_name="eu-west-1")
    return _lambda_client


def database():
    global _database
    if _database is None:
        _database = Database()
    return _database


@mcp.tool(title="Search documents")
def search_documents(question: str) -> str:
    """Search the documents attached to the user's Overstone assets (invoices, valuations,
    provenance, certificates of authenticity, insurance, exhibition and loan documents).

    Returns either a written answer with numbered sources, or a list of matching assets with the
    text that matched. Use it for questions about what the documents say, e.g. "Who issued the
    invoice for Half of You?" or "Which assets were bought at Christie's?".
    """
    token = get_access_token()  # None over stdio (no sign-in)
    if token:
        # The signed-in user's own account; never fall back to the test account.
        account_id = database().account_for_user(token.claims["sub"])
        print(f"search_documents by {token.claims.get('username')} (account {account_id}): {question}", file=sys.stderr)
        if account_id is None:
            return "Your sign-in isn't linked to an active Overstone user, so there's nothing to search."
    else:
        account_id = TEST_ACCOUNT_ID
    response = lambda_client().invoke(
        FunctionName=LAMBDA_NAME,
        Payload=json.dumps({"query": question, "accountId": account_id}),
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


def cognito_auth() -> AWSCognitoProvider:
    """Sign-in with the Overstone Cognito login page; any AI client can register itself."""
    return AWSCognitoProvider(
        user_pool_id=COGNITO_USER_POOL_ID,
        aws_region=COGNITO_REGION,
        client_id=os.environ["COGNITO_CLIENT_ID"],  # from .env
        client_secret=os.environ["COGNITO_CLIENT_SECRET"],
        base_url=BASE_URL,  # Cognito sends users back to BASE_URL + /auth/callback
        forward_resource=False,  # Cognito rejects the login code if it gets our server URL as "resource"
    )


if __name__ == "__main__":
    if "--http" in sys.argv:
        from dotenv import load_dotenv

        load_dotenv()  # COGNITO_* are not part of app.config
        mcp.auth = cognito_auth()
        mcp.run(transport="http", port=HTTP_PORT)
    else:
        mcp.run()  # stdio: the assistant starts this program and talks to it directly
