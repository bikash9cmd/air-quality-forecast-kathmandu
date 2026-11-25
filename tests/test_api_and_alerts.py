from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from hawa import api
from hawa.alerts import alert_due, message, should_alert
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


def _at(fc: StationForecast, issued_at: str) -> StationForecast:
    fc.issued_at = issued_at
    return fc


def test_alert_due_sends_first_alert_then_suppresses_repeats():
    fc = _fc(45.2, 64.0)
    assert alert_due(fc, None)
    last = {"sent_at": "2017-12-07 23:00:00", "level": "likely"}
    assert not alert_due(_at(_fc(45.2, 64.0), "2017-12-08 00:00:00"), last)
    assert not alert_due(_at(_fc(46.0, 66.0), "2017-12-08 22:00:00"), last)
    assert alert_due(_at(_fc(46.0, 66.0), "2017-12-08 23:00:00"), last)  # daily reminder
    assert not alert_due(_fc(20, 30), None)  # nothing to say


def test_alert_due_escalation_and_clock_going_back():
    possible = {"sent_at": "2017-12-07 20:00:00", "level": "possible"}
    assert alert_due(_fc(45.2, 64.0), possible)  # possible -> likely is news
    assert not alert_due(_fc(35.0, 45.0), possible)
    likely = {"sent_at": "2017-12-07 20:00:00", "level": "likely"}
    assert not alert_due(_fc(35.0, 45.0), likely)  # de-escalation is not worth an SMS
    # A replay restarted at an earlier hour must not be silenced by a "future" alert.
    assert alert_due(_at(_fc(45.2, 64.0), "2017-12-01 00:00:00"), likely)


def test_run_hour_publishes_once_per_episode(tmp_path, monkeypatch):
    from hawa import pipeline

    levels = iter([(45.0, 60.0), (46.0, 61.0), (20.0, 30.0), (44.0, 60.0), (35.0, 45.0), (45.0, 62.0)])
    times = iter(pd.date_range("2017-12-07 20:00", periods=6, freq="h"))

    def fake_forecast(_wide, _model, at):
        p50, p90 = next(levels)
        return [_at(_fc(p50, p90), str(next(times)))]

    monkeypatch.setattr(pipeline, "forecast", fake_forecast)
    store, sent = LocalStore(tmp_path), []
    results = [pipeline.run_hour(None, None, store, None, lambda st, msg: sent.append(st))[0]
               for _ in range(6)]
    # likely, still likely, clears, likely again within 24h, possible, likely: only the first goes out.
    assert sent == ["ratnapark"]
    assert ["alert" in r for r in results] == [True, False, False, False, False, False]
    assert store.latest("ratnapark")["last_alert"] == {"sent_at": "2017-12-07 20:00:00", "level": "likely"}
