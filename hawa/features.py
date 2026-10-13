"""Feature engineering.

The forecast is hour-by-hour for the next 24 hours. Every training row is
(station, issue time t, horizon h) and the label is PM2.5 at hour t+h.
Features use data at or before t only; tests/test_features.py checks this by changing
future values and asserting that no feature moves.

Kathmandu's PM2.5 has a strong daily shape (a morning peak when the valley's night-time
inversion traps traffic and brick-kiln smoke, a dip in the afternoon, an evening peak), so the
features that matter most describe "what this hour usually looks like" and "how today compares".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from hawa import config

LAGS = [0, 1, 2, 3, 6, 12, 24]
ROLL_MEANS = [3, 6, 24, 72, 168]
HORIZONS = list(range(1, config.HORIZON_HOURS + 1))

OWN_FEATURES = (
    [f"lag_{k}" for k in LAGS]
    + [f"mean_{w}" for w in ROLL_MEANS]
    + ["std_24", "max_24", "valid_24", "trend_24_72", "hours_since_valid"]
    # Same clock hour as the target hour, on previous days.
    + ["sameh_1d", "sameh_2d", "sameh_7d", "sameh_now_7d", "today_vs_profile"]
    # The same signals as log-ratios to the anchor. They are scale-free, so a pattern learned
    # in a 90 ug/m3 January transfers to a 10 ug/m3 July (trees cannot do that from levels).
    + ["r_lag_0", "r_lag_1", "r_mean_3", "r_mean_24", "r_sameh_1d"]
)
OTHER_FEATURES = ["other_lag_0", "other_mean_24", "other_valid_24", "other_sameh_7d", "r_other_lag_0"]
CALENDAR_FEATURES = ["horizon", "target_hour", "issue_hour", "doy_sin", "doy_cos",
                     "target_is_saturday", "station_code"]

ALL_FEATURES = OWN_FEATURES + OTHER_FEATURES + CALENDAR_FEATURES
# Used when the station's own sensor is down: neighbour + calendar only.
CROSS_STATION_FEATURES = OTHER_FEATURES + CALENDAR_FEATURES

STATION_CODES = {name: i for i, name in enumerate(config.STATIONS)}


def _log_ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    return np.log1p(a) - np.log1p(b)


def own_anchor(f: pd.DataFrame) -> pd.Series:
    """Damped-anomaly persistence: the level each forecast is expressed relative to.

    Start from the target clock hour's 7-day average (the daily shape), then add today's
    anomaly (how far the latest hour sits above or below *its* usual level), fading it out
    with horizon: exp(-h / tau). Right after a spike the forecast stays high; a day out it
    has relaxed back to the usual shape. tau was picked on Nov 2016 - Feb 2017 only (before
    any test month). On its own, with no training, this beats every naive baseline.
    """
    profile = f["sameh_7d"].fillna(f["mean_24"])
    anomaly = (np.log1p(f["lag_0"]) - np.log1p(f["sameh_now_7d"])).fillna(0.0)
    return np.expm1(np.log1p(profile) + np.exp(-f["horizon"] / config.ANOMALY_DECAY_HOURS) * anomaly)


def neighbour_anchor(f: pd.DataFrame) -> pd.Series:
    return f["other_sameh_7d"].fillna(f["other_mean_24"])


def _rolling(s: pd.Series, w: int, how: str) -> pd.Series:
    return getattr(s.rolling(w, min_periods=max(1, w // 2)), how)()


def _hours_since_valid(s: pd.Series) -> pd.Series:
    idx = pd.Series(np.arange(len(s), dtype=float), index=s.index)
    return idx - idx.where(s.notna()).ffill()  # NaN before the first valid reading


def _same_hour_profile(s: pd.Series, days: int) -> pd.Series:
    """At index i: mean of s[i], s[i-24], ..., s[i-24*(days-1)] (needs at least half valid)."""
    stacked = pd.concat([s.shift(24 * d) for d in range(days)], axis=1)
    return stacked.mean(axis=1).where(stacked.notna().sum(axis=1) >= max(1, days // 2))


def target_mean(s: pd.Series, horizon: int = config.HORIZON_HOURS) -> pd.Series:
    """Mean of hours t+1 .. t+horizon, NaN unless at least 75% of those hours are valid."""
    fwd_mean = s.rolling(horizon, min_periods=1).mean().shift(-horizon)
    fwd_count = s.notna().astype(float).rolling(horizon).sum().shift(-horizon)
    return fwd_mean.where(fwd_count >= config.MIN_VALID_HOURS_IN_TARGET)


def issue_frame(wide: pd.DataFrame, station: str) -> pd.DataFrame:
    """Horizon-independent features at each issue hour t."""
    s = wide[station]
    others = [c for c in wide.columns if c != station]
    other = wide[others].mean(axis=1) if others else pd.Series(np.nan, index=wide.index)

    f = pd.DataFrame(index=wide.index)
    for k in LAGS:
        f[f"lag_{k}"] = s.shift(k)
    for w in ROLL_MEANS:
        f[f"mean_{w}"] = _rolling(s, w, "mean")
    f["std_24"] = _rolling(s, 24, "std")
    f["max_24"] = _rolling(s, 24, "max")
    f["valid_24"] = s.notna().astype(float).rolling(24, min_periods=1).sum()
    f["trend_24_72"] = f["mean_24"] - f["mean_72"]
    f["hours_since_valid"] = _hours_since_valid(s)
    f["other_lag_0"] = other
    f["other_mean_24"] = _rolling(other, 24, "mean")
    f["other_valid_24"] = other.notna().astype(float).rolling(24, min_periods=1).sum()
    f["issue_hour"] = wide.index.hour
    f["station_code"] = STATION_CODES.get(station, -1)
    f["station"] = station
    # Today's level relative to the last week's: >1 means an unusually bad day so far.
    f["today_vs_profile"] = f["mean_24"] / _rolling(s, 168, "mean")
    return f


def build(wide: pd.DataFrame, with_target: bool = True, horizons=HORIZONS) -> pd.DataFrame:
    """Long frame with one row per (station, issue hour t, horizon h)."""
    frames = []
    for station in wide.columns:
        s = wide[station]
        other = wide.drop(columns=station).mean(axis=1) if wide.shape[1] > 1 else s * np.nan
        base = issue_frame(wide, station)
        own_profile = _same_hour_profile(s, 7)
        other_profile = _same_hour_profile(other, 7)
        mean24 = target_mean(s) if with_target else None
        base["sameh_now_7d"] = own_profile.shift(24)  # usual level for the current clock hour
        for h in horizons:
            f = base.copy()
            # Hour t+h-24 is the same clock hour as the target, one day earlier; it is in the past.
            back = 24 - h
            f["horizon"] = h
            f["sameh_1d"] = s.shift(back)
            f["sameh_2d"] = s.shift(back + 24)
            f["sameh_7d"] = own_profile.shift(back)
            f["other_sameh_7d"] = other_profile.shift(back)
            anchor = own_anchor(f)
            for col in ["lag_0", "lag_1", "mean_3", "mean_24", "sameh_1d"]:
                f[f"r_{col}"] = _log_ratio(f[col], anchor)
            # How far the neighbour is from *its* usual level for the current hour right now.
            f["r_other_lag_0"] = _log_ratio(f["other_lag_0"], other_profile.shift(24))
            tgt_time = wide.index + pd.Timedelta(hours=h)
            f["target_time"] = tgt_time
            f["target_hour"] = tgt_time.hour
            doy = tgt_time.dayofyear.to_numpy()
            f["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
            f["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
            f["target_is_saturday"] = (tgt_time.dayofweek == config.NEPAL_WEEKLY_HOLIDAY).astype(int)
            if with_target:
                f["target"] = s.shift(-h)
                f["target_mean_24"] = mean24
            frames.append(f)
    out = pd.concat(frames)
    out.index.name = "ts"
    return out.reset_index().sort_values(["ts", "station", "horizon"]).reset_index(drop=True)
