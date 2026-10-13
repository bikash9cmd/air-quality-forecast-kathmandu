"""Project-wide constants. Everything that a reviewer might question lives here, with a reason."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("HAWA_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
ARTIFACT_DIR = Path(os.environ.get("HAWA_ARTIFACT_DIR", ROOT / "artifacts"))

# Kathmandu Valley stations run by Nepal's Department of Environment (DoE).
# Raw files were scraped from pollution.gov.np and mirrored on GitHub (see data/README.md).
STATIONS = {
    "ratnapark": "Ratnapark, Kathmandu",
    "pulchowk": "Pulchowk, Lalitpur",
}

# Nepal National Ambient Air Quality Standard (2012): PM2.5, 24-hour mean, 40 ug/m3.
NEPAL_PM25_24H_STANDARD = 40.0
# WHO 2021 air quality guideline, 24-hour PM2.5. Shown for context, not used for alerts.
WHO_PM25_24H_GUIDELINE = 15.0

# The forecast target: mean PM2.5 over the next 24 hours.
HORIZON_HOURS = 24
# A 24-hour mean counts only if at least 75% of hours are valid (18 of 24),
# the usual completeness rule for daily air-quality averages.
MIN_VALID_HOURS_IN_TARGET = 18

# Sensor sanity limits. The DoE feed uses -999 as "no reading", and the monitors'
# upper measurement range is 1000 ug/m3; anything outside these is not a real value.
SENTINEL = -999.0
MAX_PLAUSIBLE = 1000.0
# A beta-attenuation monitor never reports the exact same value for this many hours in a row.
STUCK_RUN_HOURS = 6

# Fallback chain thresholds (see docs/FAILURE_MODES.md).
MIN_OWN_HOURS_FOR_PRIMARY = 6   # valid own-station hours in last 24h to trust the primary model

# Nepal's weekly holiday is Saturday (not Sunday), which changes traffic patterns.
NEPAL_WEEKLY_HOLIDAY = 5  # pandas dayofweek: Monday=0 ... Saturday=5

# Damped-anomaly anchor: today's deviation from the usual level fades with this e-folding time.
# Chosen on Nov 2016 - Feb 2017 (before the first test month) from {1,2,3,4,6,9,12,24}.
ANOMALY_DECAY_HOURS = 9.0

# The neighbour-only fallback has no quantile model. Its p90 is p50 x this ratio: the 90th
# percentile of observed / fallback-forecast across the Mar-Dec 2017 backtest (monthly range 1.2-2.2).
NEIGHBOUR_P90_RATIO = 1.77

TRAIN_EVERY_N_HOURS = 3

SEED = 42
