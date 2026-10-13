"""Walk-forward backtest.

For each test month M (Mar-Dec 2017) the models train only on rows whose *label* time is
before M starts. Filtering on issue time alone is not enough: a forecast issued at 23:00 on
28 February for 22:00 on 1 March has its label inside the test month, so March data would leak
into training. tests/test_backtest.py guards this.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from hawa import config
from hawa import features as F
from hawa.models import Forecaster, RidgeModel, damped_anomaly, persistence, same_hour_yesterday, weekly_profile

TEST_MONTHS = pd.period_range("2017-03", "2017-12", freq="M")
OUTAGE_MONTHS = pd.period_range("2017-11", "2017-12", freq="M")
HOURLY_MODELS = ["persistence", "same_hour_yesterday", "weekly_profile", "damped_anomaly", "ridge",
                 "cross_station", "lgbm_p50"]


def split(df: pd.DataFrame, month: pd.Period) -> tuple[pd.DataFrame, pd.DataFrame]:
    start, end = month.start_time, month.end_time
    train = df[(df["target_time"] < start) & df["target"].notna()]
    test = df[(df["ts"] >= start) & (df["ts"] <= end)]
    return train, test


def run(wide: pd.DataFrame, months=TEST_MONTHS) -> pd.DataFrame:
    """One row per test (station, issue hour, horizon) with every model's prediction.

    Test rows keep a missing label so the daily alert can still be scored from the
    forecasts; hourly metrics drop them.
    """
    df = F.build(wide)
    out = []
    for m in months:
        train, test = split(df, m)
        fc = Forecaster.fit(train)
        pred = fc.predict(test)
        res = test[["ts", "station", "horizon", "target_time", "target", "target_mean_24",
                    "mean_24", "valid_24"]].copy()
        res["month"] = str(m)
        res["persistence"] = persistence(test)
        res["same_hour_yesterday"] = same_hour_yesterday(test)
        res["weekly_profile"] = weekly_profile(test)
        res["damped_anomaly"] = damped_anomaly(test)
        res["ridge"] = RidgeModel().fit(train).predict(test)
        res["lgbm_p50"] = pred["p50"].to_numpy()
        res["lgbm_p90"] = pred["p90"].to_numpy()
        res["cross_station"] = pred["cross_p50"].to_numpy()
        out.append(res)
    return pd.concat(out, ignore_index=True)


def _comparable(res: pd.DataFrame) -> pd.DataFrame:
    # Score every model on exactly the same rows: label present and every baseline defined.
    return res.dropna(subset=["target", *HOURLY_MODELS])


def hourly_metrics(res: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    res = _comparable(res)

    def _m(g: pd.DataFrame) -> pd.Series:
        row = {f"{m}_mae": (g[m] - g["target"]).abs().mean() for m in HOURLY_MODELS}
        row["n"] = len(g)
        row["skill_vs_best_baseline"] = 1 - row["lgbm_p50_mae"] / min(
            row["persistence_mae"], row["same_hour_yesterday_mae"], row["weekly_profile_mae"],
            row["damped_anomaly_mae"])
        return pd.Series(row)

    if by is None:
        return _m(res).to_frame("all").T
    return res.groupby(by).apply(_m, include_groups=False)


def p90_coverage(res: pd.DataFrame) -> float:
    """Share of outcomes at or below the p90 forecast. A calibrated p90 gives about 0.90."""
    res = res.dropna(subset=["target"])
    return float((res["target"] <= res["lgbm_p90"]).mean())


def daily_forecasts(res: pd.DataFrame) -> pd.DataFrame:
    """Collapse the 24 hourly forecasts from one issue time into a next-24h mean."""
    g = res.groupby(["station", "ts"])
    out = g.agg(
        n_horizons=("horizon", "size"),
        actual=("target_mean_24", "first"),
        persistence=("mean_24", "first"),
        damped_anomaly=("damped_anomaly", "mean"),
        ridge=("ridge", "mean"),
        lgbm_p50=("lgbm_p50", "mean"),
        # Averaging per-hour p90s overstates the p90 of the mean (errors are not perfectly
        # correlated), so this is a deliberately cautious upper estimate.
        lgbm_p90=("lgbm_p90", "mean"),
    ).reset_index()
    return out[(out["n_horizons"] == config.HORIZON_HOURS)].dropna(subset=["actual", "persistence", "lgbm_p50"])


def alert_metrics(daily: pd.DataFrame, threshold: float = config.NEPAL_PM25_24H_STANDARD) -> pd.DataFrame:
    """Precision/recall of 'the next 24 hours will exceed Nepal's 24-hour standard'."""
    actual = daily["actual"] > threshold
    rows = {}
    for name in ["persistence", "damped_anomaly", "ridge", "lgbm_p50", "lgbm_p90"]:
        pred = daily[name] > threshold
        tp, fp, fn = int((pred & actual).sum()), int((pred & ~actual).sum()), int((~pred & actual).sum())
        precision = tp / (tp + fp) if tp + fp else np.nan
        recall = tp / (tp + fn) if tp + fn else np.nan
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else np.nan
        mae = float((daily[name] - daily["actual"]).abs().mean())
        rows[name] = dict(daily_mae=mae, tp=tp, fp=fp, fn=fn, precision=precision, recall=recall, f1=f1)
    out = pd.DataFrame(rows).T
    out.attrs["exceedances"] = int(actual.sum())
    out.attrs["forecasts"] = len(daily)
    return out


def outage_experiment(wide: pd.DataFrame, station: str = "ratnapark",
                      months=OUTAGE_MONTHS) -> pd.DataFrame:
    """What breaks: the station's own sensor goes dark for a whole month.

    Compares, against the observed values:
      healthy   - primary model, sensor working (the normal case)
      broken    - primary model fed the broken inputs (what you'd get with no fallback)
      fallback  - cross-station model, which only looks at the neighbouring station
    """
    clean = F.build(wide)
    rows = []
    key = ["ts", "station", "horizon"]
    for m in months:
        train, test = split(clean, m)
        fc = Forecaster.fit(train)
        test = test[(test["station"] == station) & test["target"].notna()]
        broken_wide = wide.copy()
        broken_wide.loc[m.start_time:m.end_time, station] = np.nan
        broken = test[key].merge(F.build(broken_wide, with_target=False), on=key, how="left")
        y = test["target"].to_numpy()
        healthy = fc.predict(test)["p50"].to_numpy()
        bp = fc.predict(broken)
        rows.append({
            "month": str(m),
            "healthy_mae": float(np.nanmean(np.abs(healthy - y))),
            # With no own data the primary model has no anchor and returns NaN for most rows.
            "broken_primary_coverage": float(np.isfinite(bp["p50"]).mean()),
            "fallback_mae": float(np.nanmean(np.abs(bp["cross_p50"].to_numpy() - y))),
            "naive_last_known_mae": float(np.nanmean(np.abs(
                wide.loc[:m.start_time - pd.Timedelta("1h"), station].dropna().iloc[-24:].mean() - y))),
            "n": len(test),
        })
    return pd.DataFrame(rows)
