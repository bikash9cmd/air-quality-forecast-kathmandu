"""Baselines, a linear model, and the LightGBM forecaster used in production.

The forecaster is three LightGBM models, each predicting PM2.5 at hour t+h:
  * p50 (median), using all features: the primary forecast
  * p90, so alerts can say "possible" as well as "likely"
  * a cross-station p50 that never looks at the station's own sensor, used as a fallback
    when that sensor is down (see hawa/forecast.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from hawa import config
from hawa import features as F
from hawa.features import ALL_FEATURES, CROSS_STATION_FEATURES

# Deliberately small trees. The anchor already explains most of the variance, so what's left
# is a weak signal in a lot of noise; bigger trees fit the noise. Chosen on 4 of the 10
# backtest months (Apr, Jul, Oct, Dec); the README also reports the 6 untouched months.
#   num_leaves 31 / 600 trees: MAE 8.44   num_leaves 15: 8.30   num_leaves 7: 7.85   num_leaves 4: 7.70
LGB_PARAMS = dict(
    n_estimators=300,
    learning_rate=0.03,
    num_leaves=4,
    min_child_samples=500,
    subsample=0.8,
    subsample_freq=1,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    random_state=config.SEED,
    verbose=-1,
)


def persistence(df: pd.DataFrame) -> np.ndarray:
    """The next hours look like the latest hour."""
    return df["lag_0"].to_numpy(dtype=float)


def same_hour_yesterday(df: pd.DataFrame) -> np.ndarray:
    """Seasonal naive: 08:00 tomorrow looks like 08:00 today."""
    return df["sameh_1d"].to_numpy(dtype=float)


def weekly_profile(df: pd.DataFrame) -> np.ndarray:
    """Average of the same clock hour over the last 7 days. The strongest simple baseline."""
    return df["sameh_7d"].to_numpy(dtype=float)


def damped_anomaly(df: pd.DataFrame) -> np.ndarray:
    """Statistical baseline with no training: see features.own_anchor."""
    return own_anchor(df)


def own_anchor(df: pd.DataFrame) -> np.ndarray:
    return F.own_anchor(df).to_numpy(dtype=float)


def neighbour_anchor(df: pd.DataFrame) -> np.ndarray:
    return F.neighbour_anchor(df).to_numpy(dtype=float)


def _lgb(alpha: float | None) -> lgb.LGBMRegressor:
    if alpha is None:
        return lgb.LGBMRegressor(objective="regression_l1", **LGB_PARAMS)
    return lgb.LGBMRegressor(objective="quantile", alpha=alpha, **LGB_PARAMS)


class RidgeModel:
    """Linear model on the same features and target, to show what the trees add."""

    def __init__(self) -> None:
        self.pipe = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=1.0))

    def fit(self, df: pd.DataFrame) -> RidgeModel:
        df = df[~np.isnan(own_anchor(df))]
        self.pipe.fit(df[ALL_FEATURES], np.log1p(df["target"]) - np.log1p(own_anchor(df)))
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return np.clip(np.expm1(np.log1p(own_anchor(df)) + self.pipe.predict(df[ALL_FEATURES])), 0, None)


@dataclass
class Forecaster:
    p50: lgb.LGBMRegressor
    p90: lgb.LGBMRegressor
    cross: lgb.LGBMRegressor
    trained_until: str
    n_train: int

    @classmethod
    def fit(cls, df: pd.DataFrame) -> Forecaster:
        df = df.dropna(subset=["target"])
        # Forecasts issued an hour apart share 23 of their 24 input hours, so neighbouring rows
        # are near-duplicates. Training on every 3rd issue hour was 2.5x faster and no worse.
        df = df[df["ts"].dt.hour % config.TRAIN_EVERY_N_HOURS == 0]
        # Predict the log-ratio to an anchor (features.own_anchor), not the level itself.
        # Trees cannot extrapolate: a level model trained on Nov-May never predicts below the
        # lowest level it has seen, so it overshot the monsoon. See docs/CHALLENGES_AND_BUGS.md.
        own = df[~np.isnan(own_anchor(df))]
        y_own = np.log1p(own["target"]) - np.log1p(own_anchor(own))
        p50 = _lgb(None).fit(own[ALL_FEATURES], y_own)
        p90 = _lgb(0.9).fit(own[ALL_FEATURES], y_own)
        nb = df[~np.isnan(neighbour_anchor(df))]
        y_nb = np.log1p(nb["target"]) - np.log1p(neighbour_anchor(nb))
        cross = _lgb(None).fit(nb[CROSS_STATION_FEATURES], y_nb)
        return cls(p50, p90, cross, str(df["ts"].max()), len(own))

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        a_own = np.log1p(own_anchor(df))
        a_nb = np.log1p(neighbour_anchor(df))
        p50 = np.expm1(a_own + self.p50.predict(df[ALL_FEATURES]))
        p90 = np.expm1(a_own + self.p90.predict(df[ALL_FEATURES]))
        cross = np.expm1(a_nb + self.cross.predict(df[CROSS_STATION_FEATURES]))
        # Separately trained quantile models can cross; never report p90 below p50.
        return pd.DataFrame(
            {"p50": p50, "p90": np.fmax(p90, p50), "cross_p50": cross}, index=df.index
        ).clip(lower=0)

    def save(self, path: Path, metadata: dict | None = None) -> None:
        path.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path / "forecaster.joblib")
        meta = {"trained_until": self.trained_until, "n_train": self.n_train,
                "lightgbm": lgb.__version__, **(metadata or {})}
        (path / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))

    @staticmethod
    def load(path: Path) -> Forecaster:
        return joblib.load(path / "forecaster.joblib")

    def importance(self) -> pd.Series:
        imp = self.p50.booster_.feature_importance(importance_type="gain")
        return pd.Series(imp, index=ALL_FEATURES).sort_values(ascending=False)
