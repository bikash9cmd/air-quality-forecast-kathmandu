"""Read-only HTTP API over the forecast store (local Docker). On AWS the same routes are served
by hawa/aws/handlers.py:api_handler behind API Gateway, without FastAPI, to keep cold starts small."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Response

from hawa import config
from hawa.alerts import message, should_alert
from hawa.forecast import StationForecast
from hawa.health import check as check_health
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
def health(response: Response) -> dict:
    """Healthy means: every station has a forecast issued within the last 2 hours, and none of
    them is `stale`. The local demo runs on a historical replay clock, so "now" is the newest
    forecast here; the Lambda uses the real clock. Unhealthy answers are HTTP 503, so a plain
    uptime check catches a dead pipeline."""
    result = check_health({st: store.latest(st) for st in config.STATIONS})
    response.headers["cache-control"] = "no-store"
    if not result["ok"]:
        response.status_code = 503
    return result


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
