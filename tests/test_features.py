from __future__ import annotations

import numpy as np
import pandas as pd

from hawa import features as F


def test_features_do_not_use_the_future(synthetic_wide):
    """Change every value after t; no feature at t may change. Labels are allowed to."""
    t = synthetic_wide.index[24 * 30]
    base = F.build(synthetic_wide, with_target=False)
    tampered = synthetic_wide.copy()
    tampered.loc[tampered.index > t] *= 5
    after = F.build(tampered, with_target=False)
    cols = [c for c in F.ALL_FEATURES]
    a = base[base["ts"] == t][cols].reset_index(drop=True)
    b = after[after["ts"] == t][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_label_is_value_at_t_plus_h(synthetic_wide):
    df = F.build(synthetic_wide)
    t = synthetic_wide.index[200]
    row = df[(df["ts"] == t) & (df["station"] == "ratnapark") & (df["horizon"] == 5)].iloc[0]
    assert row["target"] == synthetic_wide.loc[t + pd.Timedelta(hours=5), "ratnapark"]
    assert row["target_time"] == t + pd.Timedelta(hours=5)


def test_same_hour_yesterday_is_the_right_clock_hour(synthetic_wide):
    df = F.build(synthetic_wide)
    t = synthetic_wide.index[300]
    for h in (1, 13, 24):
        row = df[(df["ts"] == t) & (df["station"] == "pulchowk") & (df["horizon"] == h)].iloc[0]
        expected = synthetic_wide.loc[t + pd.Timedelta(hours=h - 24), "pulchowk"]
        assert row["sameh_1d"] == expected or (np.isnan(row["sameh_1d"]) and np.isnan(expected))


def test_target_mean_requires_75_percent_valid():
    s = pd.Series(np.ones(48), index=pd.date_range("2017-01-01", periods=48, freq="h"))
    s.iloc[1:8] = np.nan  # 7 missing of the next 24 hours from t=0 -> 17 valid < 18
    assert np.isnan(F.target_mean(s).iloc[0])
    s.iloc[7] = 1.0       # now 18 valid
    assert F.target_mean(s).iloc[0] == 1.0


def test_saturday_flag_uses_target_time():
    idx = pd.date_range("2017-01-06 23:00", periods=1, freq="h")  # a Friday, 23:00
    wide = pd.DataFrame({"a": [10.0], "b": [10.0]}, index=idx)
    df = F.build(wide, with_target=False)
    row = df[(df["station"] == "a") & (df["horizon"] == 1)].iloc[0]
    assert row["target_is_saturday"] == 1
