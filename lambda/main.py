"""Query Lambda: answers one user query for one account.

Event:    {"query": "Who issued the invoice?", "accountId": 46}
Response: the dict from app.query.ask(): kind, filters, document_category,
          document_query, and answer + hits ('answer' queries) or assets
          ('list' queries, at most RETRIEVAL_LIST_MAX_ASSETS when scored).
          Bad input -> {"error": "..."}.

Configuration comes from environment variables (config/<env>.json in the CDK
stack). When AWS_SECRET_NAME is set, the database credentials (RDS_*) are read
from Secrets Manager first, as in the other Overstone Lambdas.

Local run (uses .env): uv run poe lambda events/ask_answer.json
"""

import json
import os
import sys

import boto3


def load_db_secret() -> None:
    """Copy RDS_* credentials from Secrets Manager into the environment, before config loads."""
    secret_name = os.environ.get("AWS_SECRET_NAME")
    if not secret_name:
        return
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    secret = json.loads(client.get_secret_value(SecretId=secret_name)["SecretString"])
    for key in ("RDS_HOSTNAME", "RDS_PORT", "RDS_USERNAME", "RDS_PASSWORD", "RDS_DB_NAME"):
        if key in secret:
            os.environ[key] = str(secret[key])


# Must run before `app` is imported: app.config reads the environment at import time.
load_db_secret()

from app.db import Database  # noqa: E402
from app.query import ask  # noqa: E402
from app.rag import make_collection, make_embeddings, make_llm  # noqa: E402

# Clients are created once per Lambda container and reused by warm invocations.
_clients = None


def clients() -> tuple:
    global _clients
    if _clients is None:
        _clients = (Database(), make_llm(), make_embeddings(), make_collection())
    return _clients


def handler(event, context):
    query = (event or {}).get("query")
    account_id = (event or {}).get("accountId")
    if not query or not isinstance(query, str):
        return {"error": "'query' (string) is required"}
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return {"error": "'accountId' (integer) is required"}

    db, llm, embeddings, collection = clients()
    return ask(query, account_id, db, llm, embeddings, collection)


if __name__ == "__main__":
    # Local invoke: python lambda/main.py <event.json>
    with open(sys.argv[1]) as file:
        print(json.dumps(handler(json.load(file), None), indent=2, default=str))
