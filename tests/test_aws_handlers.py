"""The Lambda handlers against mocked AWS (moto): proves the cloud wiring without an account."""

from __future__ import annotations

import json
from decimal import Decimal

import boto3
import numpy as np
import pandas as pd
import pytest
from moto import mock_aws

from hawa import features as F
from hawa.models import Forecaster

REGION = "ap-south-1"


@pytest.fixture
def aws_env(monkeypatch, tmp_path, synthetic_wide):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket="hawa-data", CreateBucketConfiguration={"LocationConstraint": REGION})
        ddb = boto3.client("dynamodb", region_name=REGION)
        for name, sk in [("readings", "ts"), ("forecasts", "issued_at")]:
            ddb.create_table(TableName=name, BillingMode="PAY_PER_REQUEST",
                             AttributeDefinitions=[{"AttributeName": "station", "AttributeType": "S"},
                                                   {"AttributeName": sk, "AttributeType": "S"}],
                             KeySchema=[{"AttributeName": "station", "KeyType": "HASH"},
                                        {"AttributeName": sk, "KeyType": "RANGE"}])
        topic = boto3.client("sns", region_name=REGION).create_topic(Name="alerts")["TopicArn"]
        boto3.client("ssm", region_name=REGION).put_parameter(Name="/hawa/champion", Value="bootstrap", Type="String")
        monkeypatch.setenv("DATA_BUCKET", "hawa-data")
        monkeypatch.setenv("READINGS_TABLE", "readings")
        monkeypatch.setenv("FORECAST_TABLE", "forecasts")
        monkeypatch.setenv("ALERT_TOPIC", topic)
        monkeypatch.setenv("CHAMPION_PARAM", "/hawa/champion")

        # A small model trained on the synthetic data, uploaded as the bootstrap champion.
        model = Forecaster.fit(F.build(synthetic_wide))
        model.save(tmp_path / "m")
        s3.upload_file(str(tmp_path / "m" / "forecaster.joblib"), "hawa-data", "models/bootstrap/forecaster.joblib")

        # Replay feed in the raw DoE shape, including one -999 hour that must be cleaned out.
        long = synthetic_wide.reset_index().melt(id_vars="ts", var_name="station", value_name="average").dropna()
        long["max"] = long["average"] * 1.2
        long["min"] = long["average"] * 0.8
        long.loc[long.index[5], "min"] = -999
        s3.put_object(Bucket="hawa-data", Key="seed/raw_hourly.csv",
                      Body=long[["station", "ts", "max", "min", "average"]].to_csv(index=False).encode())

        # The hot window, as ten days of ingest runs would have left it.
        table = boto3.resource("dynamodb", region_name=REGION).Table("readings")
        now = synthetic_wide.index[24 * 40]
        hist = long[(long["ts"] > now - pd.Timedelta(days=10)) & (long["ts"] < now)]
        with table.batch_writer() as bw:
            for r in hist.itertuples():
                bw.put_item(Item={"station": r.station, "ts": r.ts.isoformat(), "max": Decimal(str(r.max)),
                                  "min": Decimal(str(r.min)), "average": Decimal(str(r.average))})
        yield {"now": now, "s3": s3}


def test_ingest_then_forecast_then_api(aws_env):
    from hawa.aws import handlers

    now = aws_env["now"]
    out = handlers.ingest_handler({"now": str(now)})
    assert out["written"] == 2
    keys = [o["Key"] for o in aws_env["s3"].list_objects_v2(Bucket="hawa-data", Prefix="raw/")["Contents"]]
    assert f"raw/station=ratnapark/date={now:%Y-%m-%d}/{now:%H}.json" in keys

    res = handlers.forecast_handler({"now": str(now)})
    assert res["modes"] == {"ratnapark": "primary", "pulchowk": "primary"}

    resp = handlers.api_handler({"rawPath": "/forecast/ratnapark"})
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body["hourly"]) == 24 and body["mode"] == "primary"
    assert np.isfinite(body["next24h_mean_p50"])

    assert handlers.api_handler({"rawPath": "/forecast/nowhere"})["statusCode"] == 404
    health = handlers.api_handler({"rawPath": "/health", "now": str(now)})
    assert health["statusCode"] == 200 and json.loads(health["body"])["ok"] is True
    assert health["headers"]["cache-control"] == "no-store"

    # Three hours later with no new forecast run: the job has stopped, and /health must say so.
    later = handlers.api_handler({"rawPath": "/health", "now": str(now + pd.Timedelta(hours=3))})
    assert later["statusCode"] == 503
    body = json.loads(later["body"])
    assert body["ok"] is False and body["stations"]["ratnapark"]["fresh"] is False


def test_forecast_falls_back_when_a_sensor_goes_dark(aws_env):
    from hawa.aws import handlers

    now = aws_env["now"]
    table = boto3.resource("dynamodb", region_name=REGION).Table("readings")
    # Delete the last 30 hours of Ratnapark readings: the sensor is down.
    for h in range(30):
        table.delete_item(Key={"station": "ratnapark", "ts": (now - pd.Timedelta(hours=h)).isoformat()})
    res = handlers.forecast_handler({"now": str(now)})
    assert res["modes"]["ratnapark"] == "neighbour"
    assert res["modes"]["pulchowk"] == "primary"
