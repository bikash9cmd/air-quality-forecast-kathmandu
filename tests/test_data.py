from __future__ import annotations

import numpy as np
import pandas as pd

from hawa import data


def test_trailing_comma_does_not_shift_columns(tmp_path):
    # Exactly the shape of the DoE export: 4 header fields, 5 fields per row.
    p = tmp_path / "x.csv"
    p.write_text(
        "datetime, Max, Min, Average\n"
        "01/01/2017 00, 116.9, 77.8, 90.36, \n"
        "01/01/2017 01, 95.2, 75.4, 81.94, \n"
    )
    df = data.read_raw(p)
    assert list(df.columns) == ["max", "min", "average", "ts"]
    assert df["ts"].iloc[0] == pd.Timestamp("2017-01-01 00:00")
    assert df["average"].iloc[0] == 90.36


def _raw(rows):
    df = pd.DataFrame(rows, columns=["ts", "max", "min", "average"])
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def test_hour_containing_sentinel_is_dropped_even_if_average_looks_valid():
    raw = _raw([
        ("2017-06-13 13:00", 60, 50, 55),
        # Real row from Pulchowk: Min is -999 but the average came out positive.
        ("2017-06-13 14:00", 70, -999, 59.27),
        ("2017-06-13 15:00", 60, 50, 52),
    ])
    s, rep = data.clean_station(raw, "pulchowk")
    assert np.isnan(s[pd.Timestamp("2017-06-13 14:00")])
    assert rep.dropped["sentinel_in_hour"] == 1


def test_spike_above_instrument_range_is_dropped():
    raw = _raw([("2017-01-09 17:00", 80, 60, 70), ("2017-01-09 18:00", 1456.6, 40.2, 75.7)])
    s, rep = data.clean_station(raw, "ratnapark")
    assert rep.dropped["above_instrument_range"] == 1
    assert np.isnan(s[pd.Timestamp("2017-01-09 18:00")])


def test_output_is_complete_hourly_grid():
    raw = _raw([("2017-01-01 00:00", 1, 1, 1), ("2017-01-01 05:00", 1, 1, 1)])
    s, rep = data.clean_station(raw, "x")
    assert len(s) == 24
    assert rep.hours_valid == 2


def test_stuck_sensor_runs_are_masked():
    s = pd.Series([1.0, 2, 5, 5, 5, 5, 5, 5, 3, np.nan, 3])
    mask = data.mask_stuck_runs(s, run_hours=6)
    assert mask.tolist() == [False, False, True, True, True, True, True, True, False, False, False]


def test_real_data_quality(real_wide):
    # Guards the numbers quoted in the README.
    assert set(real_wide.columns) == {"ratnapark", "pulchowk"}
    assert real_wide.min().min() >= 0
    assert real_wide.max().max() < 1000
    assert real_wide.index.is_monotonic_increasing and real_wide.index.is_unique
