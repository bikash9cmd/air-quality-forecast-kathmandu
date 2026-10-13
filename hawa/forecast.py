"""Inference with a fallback chain.

A forecast is only useful if it shows up every hour, so inference never trusts a single path:

  1. primary      own sensor has >= 6 valid hours in the last 24 -> full LightGBM model
  2. neighbour    own sensor is down, the other station is up   -> cross-station model
  3. refuse       neither station has enough recent data         -> no forecast, mode="stale"

Every result says which mode produced it, so a dashboard can show it and an alarm can fire
when "neighbour" or "stale" persists. The measured cost of each mode is in docs/FAILURE_MODES.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from hawa import config
from hawa import features as F
from hawa.models import Forecaster


@dataclass
class StationForecast:
    station: str
    issued_at: str
    mode: str                 # primary | neighbour | stale
    next24h_mean_p50: float | None
    next24h_mean_p90: float | None
    exceeds_standard: str     # likely | possible | unlikely | unknown
    hourly: list[dict]
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def classify(p50: float | None, p90: float | None, threshold: float = config.NEPAL_PM25_24H_STANDARD) -> str:
    if p50 is None or p90 is None:
        return "unknown"
    if p50 > threshold:
        return "likely"
    if p90 > threshold:
        return "possible"
    return "unlikely"


def forecast(wide: pd.DataFrame, model: Forecaster, issued_at: pd.Timestamp | None = None) -> list[StationForecast]:
    """Forecast the next 24 hours for every station from the hourly history in `wide`."""
    issued_at = issued_at or wide.index.max()
    history = wide.loc[: issued_at].tail(24 * 10)  # 10 days covers every lag and rolling window
    feats = F.build(history, with_target=False)
    feats = feats[feats["ts"] == issued_at]
    pred = model.predict(feats)
    out = []
    for station in wide.columns:
        rows = feats["station"] == station
        f, p = feats[rows], pred[rows]
        own_hours = float(f["valid_24"].iloc[0])
        other_hours = float(f["other_valid_24"].iloc[0])
        last_own = history[station].last_valid_index()
        stale_hours = (issued_at - last_own).total_seconds() / 3600 if last_own is not None else np.inf

        if own_hours >= config.MIN_OWN_HOURS_FOR_PRIMARY and np.isfinite(p["p50"]).all():
            mode, p50, p90, note = "primary", p["p50"], p["p90"], ""
        elif other_hours >= config.MIN_OWN_HOURS_FOR_PRIMARY and np.isfinite(p["cross_p50"]).all():
            mode, p50, p90 = "neighbour", p["cross_p50"], p["cross_p50"] * config.NEIGHBOUR_P90_RATIO
            note = f"own sensor has {own_hours:.0f}/24 valid hours; using neighbouring station"
        else:
            out.append(StationForecast(station, str(issued_at), "stale", None, None, "unknown", [],
                                       f"no usable data (own sensor silent {stale_hours:.0f}h)"))
            continue
        hourly = [
            {"time": str(t), "p50": round(float(a), 1), "p90": round(float(b), 1)}
            for t, a, b in zip(f["target_time"], p50, p90, strict=True)
        ]
        m50, m90 = float(np.mean(p50)), float(np.mean(p90))
        out.append(StationForecast(station, str(issued_at), mode, round(m50, 1), round(m90, 1),
                                   classify(m50, m90), hourly, note))
    return out


def cleanest_hours(fc: StationForecast, n: int = 3) -> list[str]:
    """The n hours with the lowest forecast PM2.5: when to go for a run or open the windows."""
    best = sorted(fc.hourly, key=lambda h: h["p50"])[:n]
    return sorted(h["time"] for h in best)
