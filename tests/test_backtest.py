from __future__ import annotations

import pandas as pd

from hawa import backtest as B
from hawa import features as F


def test_no_training_label_falls_inside_the_test_month(synthetic_wide):
    df = F.build(synthetic_wide)
    month = pd.Period("2017-02", freq="M")
    train, test = B.split(df, month)
    assert train["target_time"].max() < month.start_time
    assert test["ts"].min() >= month.start_time
    # The purge matters: issue-time-only filtering would have let these rows in.
    leaky = df[(df["ts"] < month.start_time) & (df["target_time"] >= month.start_time) & df["target"].notna()]
    assert len(leaky) > 0
    assert not train.index.isin(leaky.index).any()
