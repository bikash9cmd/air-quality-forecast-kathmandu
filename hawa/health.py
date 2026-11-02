"""One definition of "healthy", shared by the local FastAPI app and the API Lambda.

Healthy means every station has a forecast issued within MAX_FORECAST_AGE_HOURS of `now`, and
none of them is `mode: stale` (both sensors dark, so the forecast carries no numbers).
A forecast job that silently stops keeps serving its last forecast, so checking only that a
forecast *exists* would never catch it; the age check is the whole point of this route.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

# The forecast job runs hourly; two missed runs in a row means it has stopped.
MAX_FORECAST_AGE_HOURS = 2


def check(latest: Mapping[str, dict | None], now: datetime | None = None) -> dict:
    """Summarise the newest forecast per station.

    `latest` maps station -> its newest forecast (or None). `now` is the current time in the
    same clock as `issued_at` (naive Nepal time). When `now` is None, as in the local replay
    whose clock is historical, the newest forecast stands in for it: that still catches one
    station falling behind, but not the whole pipeline stopping.
    """
    stations: dict[str, dict] = {}
    issued = []
    for st, fc in latest.items():
        if fc is None:
            stations[st] = {"mode": "missing", "issued_at": None}
        else:
            stations[st] = {"mode": fc["mode"], "issued_at": fc["issued_at"]}
            issued.append(datetime.fromisoformat(fc["issued_at"]))

    reference = now if now is not None else (max(issued) if issued else None)
    cutoff = reference - timedelta(hours=MAX_FORECAST_AGE_HOURS) if reference is not None else None
    for info in stations.values():
        at = info["issued_at"]
        info["fresh"] = at is not None and datetime.fromisoformat(at) >= cutoff

    return {
        "ok": bool(stations) and all(i["fresh"] and i["mode"] != "stale" for i in stations.values()),
        "stations": stations,
        "latest_issue": str(max(issued)) if issued else None,
        "checked_against": str(reference) if reference is not None else None,
        "max_age_hours": MAX_FORECAST_AGE_HOURS,
    }
