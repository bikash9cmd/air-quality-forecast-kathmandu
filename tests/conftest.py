from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hawa import data


@pytest.fixture
def synthetic_wide() -> pd.DataFrame:
    """60 days of two stations with a daily cycle, a slow trend and some gaps."""
    idx = pd.date_range("2017-01-01", periods=24 * 60, freq="h")
    rng = np.random.default_rng(0)
    hour = idx.hour.to_numpy()
    daily = 1 + 0.5 * np.cos(2 * np.pi * (hour - 8) / 24)
    level = np.linspace(80, 30, len(idx))
    a = level * daily * rng.lognormal(0, 0.1, len(idx))
    b = 0.8 * a * rng.lognormal(0, 0.1, len(idx))
    wide = pd.DataFrame({"ratnapark": a, "pulchowk": b}, index=idx)
    wide.iloc[100:110, 0] = np.nan
    wide.iloc[500:530, 1] = np.nan
    wide.index.name = "ts"
    return wide


@pytest.fixture(scope="session")
def real_wide() -> pd.DataFrame:
    try:
        wide, _ = data.load_clean()
    except FileNotFoundError:
        pytest.skip("raw data not downloaded (python -m hawa download)")
    return wide
