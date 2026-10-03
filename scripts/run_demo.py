#!/usr/bin/env python
"""Run the demo queries against the deployed query Lambda and print a short summary of each.

Each query in events/demo_queries.json shows a different capability (answer, list,
hybrid filter, document category, ...). Full responses are saved to demo_results.json.

Run: uv run poe demo                      (uses AWS_PROFILE from .env, or the default credentials)
     uv run poe demo --function <name>    (another Lambda, e.g. the prod one)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
QUERIES_FILE = ROOT / "events" / "demo_queries.json"
RESULTS_FILE = ROOT / "demo_results.json"
MAX_ASSETS_SHOWN = 10


def invoke(client, function: str, query: str, account_id: int) -> dict:
    """Call the Lambda synchronously and return its JSON response."""
    response = client.invoke(
        FunctionName=function,
        Payload=json.dumps({"query": query, "accountId": account_id}),
    )
    return json.loads(response["Payload"].read())


def print_result(number: int, item: dict, result: dict, seconds: float) -> None:
    """One compact block per query: how it was planned, then the answer or the ranked assets."""
    print(f"\n{'=' * 90}\n{number}. {item['query']}\n   shows: {item['shows']}  ({seconds:.1f}s)")
    if "errorMessage" in result or "error" in result:
        print(f"   ERROR: {result.get('errorMessage') or result.get('error')}")
        return
    print(
        f"   plan: kind={result['kind']} filters={result['filters']} "
        f"category={result.get('document_category')} document_query={result['document_query']!r}"
    )
    if result["kind"] == "answer":
        print(f"\n   {result['answer']}")
        for index, hit in enumerate(result.get("hits", []), start=1):
            print(f"     [{index}] {hit['label']} ({hit['metadata'].get('category')}, score {hit['score']:.3f})")
    else:
        assets = result.get("assets", [])
        print(f"\n   {len(assets)} asset(s):")
        for asset in assets[:MAX_ASSETS_SHOWN]:
            score = "  -  " if asset["score"] is None else f"{asset['score']:.3f}"
            print(f"     {score}  {asset['assetId']}  {asset['assetName']}")
        if len(assets) > MAX_ASSETS_SHOWN:
            print(f"     ... and {len(assets) - MAX_ASSETS_SHOWN} more")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--function", default="cdk-rag-query-dev", help="Lambda function name")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")  # AWS_PROFILE for local runs
    demo = json.loads(QUERIES_FILE.read_text())
    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"), region_name="eu-west-1")
    # Cold starts plus an LLM call can take a while: allow more than the default 60s read timeout.
    client = session.client("lambda", config=Config(read_timeout=120))

    results = []
    for number, item in enumerate(demo["queries"], start=1):
        start = time.time()
        result = invoke(client, args.function, item["query"], demo["accountId"])
        print_result(number, item, result, time.time() - start)
        results.append({**item, "response": result})

    RESULTS_FILE.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nFull responses saved to {RESULTS_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
