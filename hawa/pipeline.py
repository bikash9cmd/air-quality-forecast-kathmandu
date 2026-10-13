"""The hourly job and the weekly retrain, independent of where they run (laptop, Docker, Lambda)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from hawa import features as F
from hawa.alerts import message, should_alert
from hawa.forecast import forecast
from hawa.models import Forecaster, weekly_profile
from hawa.store import ForecastStore

log = logging.getLogger("hawa")


@dataclass
class GateResult:
    passed: bool
    candidate_mae: float
    baseline_mae: float
    champion_mae: float | None
    holdout_days: int

    def as_dict(self) -> dict:
        return self.__dict__


def train_with_gate(wide: pd.DataFrame, champion: Forecaster | None = None,
                    holdout_days: int = 30) -> tuple[Forecaster | None, GateResult]:
    """Train a candidate, score it on the most recent `holdout_days`, and only return a model
    if it beats both the weekly-profile baseline and the current champion on that window.

    A retrain that makes things worse (bad week of sensor data, a code change) is the most
    common way an ML system silently degrades; the gate makes it fail loudly instead.
    """
    df = F.build(wide)
    cutoff = wide.index.max() - pd.Timedelta(days=holdout_days)
    train = df[(df["target_time"] <= cutoff) & df["target"].notna()]
    hold = df[(df["ts"] > cutoff) & df["target"].notna() & df["sameh_7d"].notna() & df["mean_24"].notna()]
    candidate = Forecaster.fit(train)
    y = hold["target"].to_numpy()
    cand_mae = float(np.mean(np.abs(candidate.predict(hold)["p50"].to_numpy() - y)))
    base_mae = float(np.mean(np.abs(weekly_profile(hold) - y)))
    champ_mae = None
    if champion is not None:
        champ_mae = float(np.mean(np.abs(champion.predict(hold)["p50"].to_numpy() - y)))
    passed = cand_mae < base_mae and (champ_mae is None or cand_mae <= champ_mae * 1.02)
    gate = GateResult(passed, cand_mae, base_mae, champ_mae, holdout_days)
    if not passed:
        return None, gate
    # The gate passed on the holdout; ship a model refit on all the data.
    return Forecaster.fit(df[df["target"].notna()]), gate


def run_hour(wide: pd.DataFrame, model: Forecaster, store: ForecastStore, at: pd.Timestamp,
             publish=None) -> list[dict]:
    """Forecast every station at hour `at`, store the result, publish alerts. Returns the forecasts."""
    results = []
    for fc in forecast(wide, model, at):
        d = fc.as_dict()
        store.put(d)
        if should_alert(fc):
            msg = message(fc)
            if publish:
                publish(fc.station, msg)
            d["alert"] = msg
        log.info(json.dumps({"event": "forecast", "station": fc.station, "issued_at": fc.issued_at,
                             "mode": fc.mode, "p50": fc.next24h_mean_p50,
                             "exceeds": fc.exceeds_standard}))
        results.append(d)
    return results


def replay(wide: pd.DataFrame, model: Forecaster, store: ForecastStore, start: str, end: str,
           out: Path | None = None) -> pd.DataFrame:
    """Run the hourly job over a past period as if it were live, one hour at a time."""
    rows = []
    for at in pd.date_range(start, end, freq="h"):
        # Only data up to `at` exists at time `at`: slicing here is what makes replay honest.
        for d in run_hour(wide.loc[:at], model, store, at):
            rows.append({k: d[k] for k in ["station", "issued_at", "mode", "next24h_mean_p50",
                                           "next24h_mean_p90", "exceeds_standard"]}
                        | {"alert": "alert" in d})
    log_df = pd.DataFrame(rows)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        log_df.to_csv(out, index=False)
    return log_df
