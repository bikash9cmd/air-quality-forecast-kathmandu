"""Download, parse and clean the DoE hourly PM2.5 files.

Each raw row is one station-hour with the Max, Min and Average of the sub-hourly readings.
The cleaning rules below come from problems found in the real files; each one is documented
in docs/CHALLENGES_AND_BUGS.md and covered by a test.
"""

from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from hawa import config

# Pinned to a commit so the data can never change underneath the results.
MIRROR_COMMIT = "5574cf989094b551fab41a782a336668d2be52e6"
MIRROR_BASE = f"https://raw.githubusercontent.com/hbvj99/NPGovAirPollution/{MIRROR_COMMIT}/"

RAW_FILES = {
    "Ratnapark, Kathmandu_PM2.5 Inst_2016-11-01_2016-12-30_Hourly.csv":
        "40b797d2cb5b5601baf9a69eee50de46942a3ced644c7d3d4522d4ac05f97c48",
    "Ratnapark, Kathmandu_PM2.5 Inst_2017-01-01_2017-12-30_Hourly.csv":
        "e2325e833ae7254ee86f0a37a1f68c384c32ad40517ff79ced9c5d9486a22621",
    "Pulchowk, Lalitpur_PM2.5 Inst_2016-11-01_2016-12-30_Hourly.csv":
        "b4319863c4adc0b92fd704b8d990121489646f3b908bddc7c0d9c6a35cb34e10",
    "Pulchowk, Lalitpur_PM2.5 Inst_2017-01-01_2017-12-30_Hourly.csv":
        "552cda3931f59a96f642162c814bd2aa0e2b575acaf3695a7724cd055e2eb842",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(raw_dir: Path = config.RAW_DIR) -> list[Path]:
    """Fetch the raw CSVs (skipping ones already present) and verify their checksums."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, expected in RAW_FILES.items():
        path = raw_dir / name
        if not path.exists():
            url = MIRROR_BASE + urllib.parse.quote(name)
            with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (pinned https URL)
                path.write_bytes(resp.read())
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"checksum mismatch for {name}: {actual}")
        paths.append(path)
    return paths


def read_raw(path: Path) -> pd.DataFrame:
    """Read one DoE export.

    Bug we hit: every data row ends with a trailing comma, so rows have one more field than the
    header. pandas then silently uses the first column as the index and shifts every column one
    place left ("datetime" ends up holding the Max values). index_col=False stops that.
    """
    df = pd.read_csv(path, skipinitialspace=True, index_col=False)
    df.columns = [c.strip().lower() for c in df.columns]
    expected = {"datetime", "max", "min", "average"}
    if set(df.columns) != expected:
        raise ValueError(f"{path.name}: unexpected columns {list(df.columns)}")
    # Timestamps are Nepal local time (UTC+05:45) with the hour only, e.g. "01/31/2017 13".
    df["ts"] = pd.to_datetime(df["datetime"], format="%m/%d/%Y %H")
    return df.drop(columns="datetime")


@dataclass
class QualityReport:
    station: str
    rows_raw: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    hours_total: int = 0
    hours_valid: int = 0

    @property
    def completeness(self) -> float:
        return self.hours_valid / self.hours_total if self.hours_total else 0.0

    def as_dict(self) -> dict:
        return {
            "station": self.station,
            "rows_raw": self.rows_raw,
            "dropped": self.dropped,
            "hours_total": self.hours_total,
            "hours_valid": self.hours_valid,
            "completeness": round(self.completeness, 3),
        }


def mask_stuck_runs(s: pd.Series, run_hours: int = config.STUCK_RUN_HOURS) -> pd.Series:
    """Return a boolean mask of values that sit in a run of >= run_hours identical readings."""
    valid = s.notna()
    change = (s != s.shift()) | ~valid
    run_id = change.cumsum()
    run_len = s.groupby(run_id).transform("size")
    return valid & (run_len >= run_hours)


def clean_station(raw: pd.DataFrame, station: str) -> tuple[pd.Series, QualityReport]:
    """Turn raw rows for one station into an hourly series on a complete grid (NaN = no data)."""
    rep = QualityReport(station=station, rows_raw=len(raw))
    df = raw.sort_values("ts").copy()

    def drop(name: str, bad: pd.Series) -> None:
        nonlocal df
        rep.dropped[name] = int(bad.sum())
        df = df[~bad]

    # The hourly Average is computed from sub-hourly readings *including* -999 "no data" codes,
    # so an hour whose Min is -999 has a poisoned average (e.g. -634.9) even when it looks positive.
    drop("sentinel_in_hour", df["min"] <= config.SENTINEL)
    drop("negative_average", df["average"] < 0)
    # A single spike above the instrument range drags the hourly mean; we can't recover it.
    drop("above_instrument_range", df["max"] > config.MAX_PLAUSIBLE)
    drop("duplicate_timestamp", df["ts"].duplicated(keep="last"))

    s = df.set_index("ts")["average"].astype(float)
    grid = pd.date_range(s.index.min().floor("D"), s.index.max().ceil("D") - pd.Timedelta("1h"), freq="h")
    s = s.reindex(grid)
    stuck = mask_stuck_runs(s)
    rep.dropped["stuck_sensor"] = int(stuck.sum())
    s[stuck] = np.nan
    s.name = station
    s.index.name = "ts"
    rep.hours_total = len(s)
    rep.hours_valid = int(s.notna().sum())
    return s, rep


def load_clean(raw_dir: Path = config.RAW_DIR) -> tuple[pd.DataFrame, list[QualityReport]]:
    """Return a wide hourly frame (one column per station) plus a quality report per station."""
    series, reports = [], []
    for key, prefix in config.STATIONS.items():
        files = sorted(p for p in raw_dir.glob(f"{prefix}_PM2.5*Hourly.csv"))
        if not files:
            raise FileNotFoundError(f"no raw files for {prefix} in {raw_dir}; run `python -m hawa download`")
        raw = pd.concat([read_raw(p) for p in files], ignore_index=True)
        s, rep = clean_station(raw, key)
        series.append(s)
        reports.append(rep)
    wide = pd.concat(series, axis=1).asfreq("h")
    wide.index.name = "ts"
    return wide, reports


def load_raw_long(raw_dir: Path = config.RAW_DIR) -> pd.DataFrame:
    """All raw rows (uncleaned) as station, ts, max, min, average. Used to seed the AWS replay feed."""
    frames = []
    for key, prefix in config.STATIONS.items():
        for p in sorted(raw_dir.glob(f"{prefix}_PM2.5*Hourly.csv")):
            frames.append(read_raw(p).assign(station=key))
    return pd.concat(frames, ignore_index=True)[["station", "ts", "max", "min", "average"]]
