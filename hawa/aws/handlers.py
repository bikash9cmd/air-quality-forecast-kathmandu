"""AWS Lambda entry points. Same core code as the local pipeline; only I/O differs.

  ingest_handler    hourly (EventBridge Scheduler): new readings -> S3 archive + DynamoDB hot window
  forecast_handler  hourly, 10 min after ingest: hot window -> model -> forecasts table + SNS alerts
  retrain_handler   weekly: S3 archive -> train_with_gate -> new model version if the gate passes
  api_handler       API Gateway HTTP API: GET /forecast/{station}, GET /health

Environment variables are set by infra/template.yaml.
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import boto3
import pandas as pd
from boto3.dynamodb.conditions import Key

from hawa import config
from hawa.alerts import message, should_alert
from hawa.data import clean_station
from hawa.forecast import StationForecast
from hawa.health import check as check_health
from hawa.models import Forecaster
from hawa.pipeline import run_hour, train_with_gate
from hawa.store import DynamoStore

log = logging.getLogger()
log.setLevel(logging.INFO)

NPT = timedelta(hours=5, minutes=45)  # Nepal time, UTC+05:45
HOT_WINDOW_DAYS = 10

_model_cache: dict[str, Forecaster] = {}


def _aws(service: str, kind: str = "client"):
    return getattr(boto3, kind)(service)


def _now_npt(event: dict) -> pd.Timestamp:
    """Current hour in Nepal time. REPLAY_OFFSET_DAYS shifts 'now' into the historical data so
    the whole system can run on AWS before a live data feed is wired in."""
    if "now" in event:  # tests and manual backfills
        return pd.Timestamp(event["now"]).floor("h")
    now = datetime.now(UTC).replace(tzinfo=None) + NPT
    now -= timedelta(days=float(os.environ.get("REPLAY_OFFSET_DAYS", "0")))
    return pd.Timestamp(now).floor("h")


def _metric(name: str, value: float, unit: str = "Count", **props: str) -> None:
    """CloudWatch Embedded Metric Format: a structured log line becomes a metric, no API call.

    Deliberately no dimensions. Every distinct dimension value is a separate billed custom
    metric, and an alarm only matches the exact dimension set it names: the first version put
    `station` on MissingReading and the dimensionless SensorDownAlarm could never fire.
    `props` are logged alongside (searchable in Logs Insights) without becoming dimensions.
    """
    print(json.dumps({
        "_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
            "Namespace": "Hawa", "Dimensions": [[]], "Metrics": [{"Name": name, "Unit": unit}]}]},
        name: value, **props}))


# ---------- ingest ----------

def _replay_readings(at: pd.Timestamp) -> pd.DataFrame:
    """Readings for hour `at` from the seeded historical file (the 'replay' data source).

    A live source (DoE API or OpenAQ) would replace this function and return the same shape:
    columns station, ts, max, min, average. See docs/ARCHITECTURE.md, "Going live".
    """
    s3 = _aws("s3")
    body = s3.get_object(Bucket=os.environ["DATA_BUCKET"], Key="seed/raw_hourly.csv")["Body"].read()
    df = pd.read_csv(io.BytesIO(body), parse_dates=["ts"])
    return df[df["ts"] == at]


def ingest_handler(event: dict, _ctx=None) -> dict:
    at = _now_npt(event)
    readings = _replay_readings(at)
    s3 = _aws("s3")
    table = _aws("dynamodb", "resource").Table(os.environ["READINGS_TABLE"])
    ttl = int(time.time()) + HOT_WINDOW_DAYS * 2 * 86400
    written = 0
    for st in config.STATIONS:
        row = readings[readings["station"] == st]
        # Raw goes to S3 untouched (even -999s): the archive is the source of truth for retraining,
        # and cleaning rules may change later.
        key = f"raw/station={st}/date={at:%Y-%m-%d}/{at:%H}.json"
        payload = row.drop(columns="station").to_dict("records")
        s3.put_object(Bucket=os.environ["DATA_BUCKET"], Key=key,
                      Body=json.dumps(payload, default=str).encode())
        if row.empty:
            _metric("MissingReading", 1, station=st)
            continue
        r = row.iloc[0]
        table.put_item(Item={"station": st, "ts": at.isoformat(), "max": Decimal(str(r["max"])),
                             "min": Decimal(str(r["min"])), "average": Decimal(str(r["average"])),
                             "expires_at": ttl})
        written += 1
    _metric("ReadingsIngested", written)
    return {"at": str(at), "written": written}


# ---------- forecast ----------

def _load_champion() -> Forecaster:
    """Model version lives in SSM Parameter Store; artifacts in S3. Cached across warm starts."""
    version = _aws("ssm").get_parameter(Name=os.environ["CHAMPION_PARAM"])["Parameter"]["Value"]
    if version not in _model_cache:
        d = Path(tempfile.mkdtemp())
        _aws("s3").download_file(os.environ["DATA_BUCKET"], f"models/{version}/forecaster.joblib",
                                 str(d / "forecaster.joblib"))
        _model_cache.clear()
        _model_cache[version] = Forecaster.load(d)
    return _model_cache[version]


def _hot_window(at: pd.Timestamp) -> pd.DataFrame:
    table = _aws("dynamodb", "resource").Table(os.environ["READINGS_TABLE"])
    since = (at - timedelta(days=HOT_WINDOW_DAYS)).isoformat()
    series = []
    for st in config.STATIONS:
        items, kwargs = [], {"KeyConditionExpression": Key("station").eq(st) & Key("ts").between(since, at.isoformat())}
        while True:
            resp = table.query(**kwargs)
            items += resp["Items"]
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        raw = pd.DataFrame([{k: float(v) if isinstance(v, Decimal) else v for k, v in i.items()} for i in items])
        if raw.empty:
            s = pd.Series(dtype=float, name=st)
        else:
            raw["ts"] = pd.to_datetime(raw["ts"])
            s, _ = clean_station(raw[["ts", "max", "min", "average"]], st)
        series.append(s)
    grid = pd.date_range(at - timedelta(days=HOT_WINDOW_DAYS), at, freq="h")
    return pd.concat(series, axis=1).reindex(grid)


def forecast_handler(event: dict, _ctx=None) -> dict:
    t0 = time.time()
    at = _now_npt(event)
    wide = _hot_window(at)
    sns = _aws("sns")

    def publish(station: str, msg: dict) -> None:
        sns.publish(TopicArn=os.environ["ALERT_TOPIC"], Subject=f"Hawa air alert: {station}",
                    Message=f"{msg['en']}\n\n{msg['ne']}",
                    MessageAttributes={"station": {"DataType": "String", "StringValue": station}})

    results = run_hour(wide, _load_champion(), DynamoStore(), at, publish)
    fallbacks = [r for r in results if r["mode"] != "primary"]
    _metric("FallbackForecasts", len(fallbacks), stations=",".join(r["station"] for r in fallbacks))
    _metric("ForecastLatency", (time.time() - t0) * 1000, "Milliseconds")
    return {"at": str(at), "modes": {r["station"]: r["mode"] for r in results}}


# ---------- retrain ----------

def _archive_frame() -> pd.DataFrame:
    s3 = _aws("s3")
    bucket = os.environ["DATA_BUCKET"]
    rows = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="raw/"):
        for obj in page.get("Contents", []):
            st = obj["Key"].split("/")[1].split("=")[1]
            for r in json.loads(s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()):
                rows.append(r | {"station": st})
    raw = pd.DataFrame(rows)
    raw["ts"] = pd.to_datetime(raw["ts"])
    return pd.concat([clean_station(raw[raw["station"] == st], st)[0] for st in config.STATIONS], axis=1).asfreq("h")


def retrain_handler(event: dict, _ctx=None) -> dict:
    ssm, s3 = _aws("ssm"), _aws("s3")
    wide = _archive_frame()
    champion = _load_champion() if not event.get("bootstrap") else None
    model, gate = train_with_gate(wide, champion)
    _metric("RetrainGatePassed", int(gate.passed))
    if model is None:
        log.warning(json.dumps({"event": "gate_failed", **gate.as_dict()}))
        return {"promoted": False, "gate": gate.as_dict()}
    version = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    d = Path(tempfile.mkdtemp())
    model.save(d, {"gate": gate.as_dict()})
    for name in ["forecaster.joblib", "metadata.json"]:
        s3.upload_file(str(d / name), os.environ["DATA_BUCKET"], f"models/{version}/{name}")
    ssm.put_parameter(Name=os.environ["CHAMPION_PARAM"], Value=version, Type="String", Overwrite=True)
    return {"promoted": True, "version": version, "gate": gate.as_dict()}


# ---------- API ----------

def _response(status: int, body: dict, cache: str = "max-age=300") -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": cache},
            "body": json.dumps(body, ensure_ascii=False)}


def api_handler(event: dict, _ctx=None) -> dict:
    path = event.get("rawPath", "")
    store = DynamoStore()
    if path == "/health":
        # Compare against the real clock (shifted by REPLAY_OFFSET_DAYS like every other handler):
        # a forecast job that stopped keeps serving its last item, and only its age gives it away.
        result = check_health({st: store.latest(st) for st in config.STATIONS}, now=_now_npt(event))
        return _response(200 if result["ok"] else 503, result, cache="no-store")
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "forecast":
        st = parts[1]
        if st not in config.STATIONS:
            return _response(404, {"error": f"unknown station {st}"})
        fc = store.latest(st)
        if fc is None:
            return _response(503, {"error": "no forecast yet"})
        if len(parts) == 3 and parts[2] == "alert":
            sf = StationForecast(**fc)
            return _response(200, {"alert": should_alert(sf), "exceeds_standard": sf.exceeds_standard,
                                   **({"message": message(sf)} if should_alert(sf) else {})})
        return _response(200, fc)
    return _response(404, {"error": "not found"})
