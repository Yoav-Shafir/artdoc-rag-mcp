"""Where the MCP server keeps sign-ins on Lambda: a DynamoDB table.

On your machine FastMCP keeps sign-ins in a local folder. Lambda loses that folder between runs,
so users would be signed out all the time. This small store saves them in DynamoDB instead.

FastMCP's own DynamoDB store needs `aioboto3`, which conflicts with the project's boto3 version,
so this one uses plain boto3. The table (keys `collection` + `key`, TTL on `ttl`) is created by CDK.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

import boto3
from key_value.aio._utils.managed_entry import ManagedEntry
from key_value.aio.stores.base import BaseStore


class DynamoDBStore(BaseStore):
    def __init__(self, table_name: str, region_name: str | None = None) -> None:
        self._table_name = table_name
        self._client = boto3.client("dynamodb", region_name=region_name)
        super().__init__()

    def _item_key(self, key: str, collection: str) -> dict[str, Any]:
        return {"collection": {"S": collection}, "key": {"S": key}}

    async def _get_managed_entry(self, *, key: str, collection: str) -> ManagedEntry | None:
        response = await asyncio.to_thread(
            self._client.get_item, TableName=self._table_name, Key=self._item_key(key, collection)
        )
        item = response.get("Item")
        if not item or "value" not in item:
            return None
        entry = self._serialization_adapter.load_json(json_str=item["value"]["S"])
        if "ttl" in item:  # DynamoDB's expiry time wins
            entry.expires_at = datetime.fromtimestamp(int(item["ttl"]["N"]), tz=timezone.utc)
        return entry

    async def _put_managed_entry(self, *, key: str, collection: str, managed_entry: ManagedEntry) -> None:
        item = self._item_key(key, collection)
        item["value"] = {"S": self._serialization_adapter.dump_json(entry=managed_entry, key=key, collection=collection)}
        if managed_entry.expires_at is not None:
            # DynamoDB deletes the row by itself after this time
            item["ttl"] = {"N": str(int(managed_entry.expires_at.timestamp()))}
        await asyncio.to_thread(self._client.put_item, TableName=self._table_name, Item=item)

    async def _delete_managed_entry(self, *, key: str, collection: str) -> bool:
        response = await asyncio.to_thread(
            self._client.delete_item,
            TableName=self._table_name,
            Key=self._item_key(key, collection),
            ReturnValues="ALL_OLD",
        )
        return "Attributes" in response
