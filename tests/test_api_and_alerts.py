from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hawa import api
from hawa.alerts import message, should_alert
from hawa.forecast import StationForecast, classify
from hawa.store import LocalStore


def _fc(p50, p90, mode="primary"):
    hourly = [{"time": f"2017-12-08 {h:02d}:00:00", "p50": 30.0 + h, "p90": 50.0 + h} for h in range(24)]
    return StationForecast("ratnapark", "2017-12-07 23:00:00", mode, p50, p90, classify(p50, p90), hourly)


def test_classify_uses_p50_for_likely_and_p90_for_possible():
    assert classify(45, 60) == "likely"
    assert classify(35, 45) == "possible"
    assert classify(20, 30) == "unlikely"
    assert classify(None, None) == "unknown"


def test_alert_message_both_languages_and_cleanest_hours():
    msg = message(_fc(45.2, 64.0))
    assert "LIKELY" in msg["en"] and "00:00, 01:00, 02:00" in msg["en"]
    assert "रत्नपार्क" in msg["ne"]


def test_no_message_for_clean_air():
    fc = _fc(20, 30)
    assert not should_alert(fc)
    with pytest.raises(ValueError):
        message(fc)


def test_api_routes(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    monkeypatch.setattr(api, "store", store)
    client = TestClient(api.app)
    assert client.get("/forecast/ratnapark").status_code == 503  # nothing yet
    assert client.get("/health").status_code == 503
    store.put(_fc(45.2, 64.0).as_dict())
    store.put(_fc(20.0, 30.0).as_dict() | {"station": "pulchowk"})
    assert client.get("/forecast/ratnapark").json()["exceeds_standard"] == "likely"
    assert client.get("/forecast/ratnapark/alert").json()["alert"] is True
    assert client.get("/forecast/pulchowk/alert").json()["alert"] is False
    assert client.get("/forecast/thamel").status_code == 404
    health = client.get("/health")
    assert health.status_code == 200 and health.json()["ok"] is True
    assert health.headers["cache-control"] == "no-store"
