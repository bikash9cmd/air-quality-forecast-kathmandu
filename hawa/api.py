"""Read-only HTTP API over the forecast store (local Docker). On AWS the same routes are served
by hawa/aws/handlers.py:api_handler behind API Gateway, without FastAPI, to keep cold starts small."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException

from hawa import config
from hawa.alerts import message, should_alert
from hawa.forecast import StationForecast
from hawa.store import DynamoStore, LocalStore

app = FastAPI(title="Hawa: Kathmandu Valley PM2.5 forecast", version="1.0.0")
store = DynamoStore() if os.environ.get("FORECAST_TABLE") else LocalStore()


def _get(station: str) -> dict:
    if station not in config.STATIONS:
        raise HTTPException(404, f"unknown station '{station}'; try one of {list(config.STATIONS)}")
    fc = store.latest(station)
    if fc is None:
        raise HTTPException(503, "no forecast yet; run the pipeline first")
    return fc


@app.get("/health")
def health() -> dict:
    """Healthy means: every station has a forecast issued within the last 2 hours (replay
    time in the local demo). An uptime check on this route catches a dead pipeline."""
    status = {}
    for st in config.STATIONS:
        fc = store.latest(st)
        status[st] = fc["mode"] if fc else "missing"
    issued = [store.latest(st)["issued_at"] for st in config.STATIONS if store.latest(st)]
    newest = max(issued) if issued else None
    fresh = newest is not None and all(
        datetime.fromisoformat(i) >= datetime.fromisoformat(newest) - timedelta(hours=2) for i in issued
    )
    return {"ok": fresh and "missing" not in status.values(), "stations": status, "latest_issue": newest}


@app.get("/stations")
def stations() -> dict:
    return {"stations": config.STATIONS, "standard_ug_m3": config.NEPAL_PM25_24H_STANDARD}


@app.get("/forecast/{station}")
def get_forecast(station: str) -> dict:
    return _get(station)


@app.get("/forecast/{station}/alert")
def get_alert(station: str) -> dict:
    fc = StationForecast(**_get(station))
    if not should_alert(fc):
        return {"alert": False, "exceeds_standard": fc.exceeds_standard}
    return {"alert": True, "exceeds_standard": fc.exceeds_standard, "message": message(fc)}
