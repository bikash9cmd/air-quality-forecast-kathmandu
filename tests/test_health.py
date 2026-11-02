from __future__ import annotations

from datetime import datetime

from hawa.health import check

NOW = datetime(2017, 12, 7, 23)


def _fc(issued_at: str, mode: str = "primary") -> dict:
    return {"mode": mode, "issued_at": issued_at}


def test_fresh_forecasts_are_healthy():
    out = check({"ratnapark": _fc("2017-12-07 23:00:00"), "pulchowk": _fc("2017-12-07 22:00:00")}, now=NOW)
    assert out["ok"] is True
    assert out["latest_issue"] == "2017-12-07 23:00:00"
    assert out["checked_against"] == "2017-12-07 23:00:00"


def test_exactly_two_hours_old_is_still_fresh_but_older_is_not():
    out = check({"ratnapark": _fc("2017-12-07 21:00:00"), "pulchowk": _fc("2017-12-07 20:00:00")}, now=NOW)
    assert out["stations"]["ratnapark"]["fresh"] is True
    assert out["stations"]["pulchowk"]["fresh"] is False
    assert out["ok"] is False


def test_whole_pipeline_stopped_is_caught_when_now_is_given():
    # Both forecasts are equally old, so comparing them to each other would say "healthy".
    latest = {"ratnapark": _fc("2017-12-05 23:00:00"), "pulchowk": _fc("2017-12-05 23:00:00")}
    assert check(latest)["ok"] is True  # local replay clock: newest forecast is "now"
    assert check(latest, now=NOW)["ok"] is False


def test_missing_station_is_unhealthy():
    out = check({"ratnapark": _fc("2017-12-07 23:00:00"), "pulchowk": None}, now=NOW)
    assert out["ok"] is False
    assert out["stations"]["pulchowk"] == {"mode": "missing", "issued_at": None, "fresh": False}


def test_no_forecasts_at_all_is_unhealthy():
    out = check({"ratnapark": None, "pulchowk": None})
    assert out["ok"] is False and out["latest_issue"] is None and out["checked_against"] is None


def test_stale_mode_is_unhealthy_even_though_the_job_ran():
    # mode "stale" means both sensors are dark: the job ran, but there is no forecast to serve.
    out = check({"ratnapark": _fc("2017-12-07 23:00:00", "stale"), "pulchowk": _fc("2017-12-07 23:00:00")}, now=NOW)
    assert out["stations"]["ratnapark"]["fresh"] is True
    assert out["ok"] is False


def test_neighbour_fallback_is_still_healthy():
    out = check({"ratnapark": _fc("2017-12-07 23:00:00", "neighbour"), "pulchowk": _fc("2017-12-07 23:00:00")}, now=NOW)
    assert out["ok"] is True
