"""Upload the replay feed and bootstrap the first model on a freshly deployed stack.

    python scripts/seed_aws.py --bucket <DataBucketName output> [--backfill-days 14]

1. writes seed/raw_hourly.csv (the replay data source the ingest Lambda reads)
2. backfills raw/ in S3 with history up to the replay 'now', so the first retrain has data
3. uploads a locally trained model as models/bootstrap/ (the SSM parameter's initial value)
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import boto3
import pandas as pd

from hawa import config, data
from hawa.models import Forecaster

NPT = timedelta(hours=5, minutes=45)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--replay-offset-days", type=float, default=3421)
    args = ap.parse_args()
    s3 = boto3.client("s3")

    raw = data.load_raw_long()
    s3.put_object(Bucket=args.bucket, Key="seed/raw_hourly.csv", Body=raw.to_csv(index=False).encode())

    now_npt = datetime.now(UTC).replace(tzinfo=None) + NPT
    now = pd.Timestamp(now_npt - timedelta(days=args.replay_offset_days)).floor("h")
    history = raw[raw["ts"] < now]

    def put(item):
        (st, ts), g = item
        key = f"raw/station={st}/date={ts:%Y-%m-%d}/{ts:%H}.json"
        s3.put_object(Bucket=args.bucket, Key=key,
                      Body=json.dumps(g.drop(columns="station").to_dict("records"), default=str).encode())

    # ~18k small objects: parallel PUTs take a minute instead of half an hour.
    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(put, history.groupby(["station", "ts"])))
    print(f"backfilled {len(history)} raw rows up to replay time {now}")

    model_dir = config.ARTIFACT_DIR / "model"
    Forecaster.load(model_dir)  # fail early if there is no trained model
    for name in ["forecaster.joblib", "metadata.json"]:
        s3.upload_file(str(model_dir / name), args.bucket, f"models/bootstrap/{name}")
    print("uploaded bootstrap model; the hourly schedule takes it from here")


if __name__ == "__main__":
    main()
