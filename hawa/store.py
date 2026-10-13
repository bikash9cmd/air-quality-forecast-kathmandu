"""Where forecasts live. Locally a JSON file per station; on AWS a DynamoDB table.

Both implement the same two methods, so the API and the pipeline never know which one they use.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from hawa import config


class ForecastStore(Protocol):
    def put(self, forecast: dict) -> None: ...
    def latest(self, station: str) -> dict | None: ...


class LocalStore:
    def __init__(self, root: Path = config.ARTIFACT_DIR / "forecasts") -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, forecast: dict) -> None:
        path = self.root / f"{forecast['station']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(forecast, ensure_ascii=False, indent=2))
        tmp.replace(path)  # atomic: the API never reads a half-written file

    def latest(self, station: str) -> dict | None:
        path = self.root / f"{station}.json"
        return json.loads(path.read_text()) if path.exists() else None


def _to_dynamo(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamo(v) for v in obj]
    return obj


def _from_dynamo(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _from_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_dynamo(v) for v in obj]
    return obj


class DynamoStore:
    """Partition key = station, sort key = issued_at. Items expire after 30 days (TTL)."""

    TTL_SECONDS = 30 * 24 * 3600

    def __init__(self, table_name: str | None = None, client=None) -> None:
        import boto3

        resource = client or boto3.resource("dynamodb")
        self.table = resource.Table(table_name or os.environ["FORECAST_TABLE"])

    def put(self, forecast: dict) -> None:
        import time

        item = _to_dynamo(forecast) | {"expires_at": int(time.time()) + self.TTL_SECONDS}
        self.table.put_item(Item=item)

    def latest(self, station: str) -> dict | None:
        from boto3.dynamodb.conditions import Key

        resp = self.table.query(KeyConditionExpression=Key("station").eq(station),
                                ScanIndexForward=False, Limit=1)
        items = resp.get("Items", [])
        if not items:
            return None
        item = _from_dynamo(items[0])
        item.pop("expires_at", None)
        return item
