"""Alert messages in English and Nepali, short enough for one SMS segment each where possible."""

from __future__ import annotations

from datetime import datetime, timedelta

from hawa import config
from hawa.forecast import StationForecast, cleanest_hours

STATION_NAMES_NE = {"ratnapark": "रत्नपार्क", "pulchowk": "पुल्चोक"}


def should_alert(fc: StationForecast) -> bool:
    return fc.exceeds_standard in {"likely", "possible"}


def alert_due(fc: StationForecast, last_alert: dict | None) -> bool:
    """Should the hourly job send `fc`'s alert now, given the last one sent for this station?

    Yes for the first alert, for an escalation from "possible" to "likely", and as a reminder
    once config.ALERT_REPEAT_HOURS have passed. Otherwise the subscribers already know: the
    forecast is recomputed every hour, and re-sending it would be one SMS an hour all day.
    """
    if not should_alert(fc):
        return False
    if not last_alert:
        return True
    sent = datetime.fromisoformat(last_alert["sent_at"])
    now = datetime.fromisoformat(fc.issued_at)
    if now < sent:  # clock moved backwards (a replay or backfill restarted earlier): start over
        return True
    if now - sent >= timedelta(hours=config.ALERT_REPEAT_HOURS):
        return True
    return fc.exceeds_standard == "likely" and last_alert.get("level") != "likely"


def _hhmm(ts: str) -> str:
    return ts[11:16]


def message(fc: StationForecast) -> dict[str, str]:
    """Return {"en": ..., "ne": ...}. Raises if called for a forecast that should not alert."""
    if not should_alert(fc):
        raise ValueError(f"no alert for {fc.station}: {fc.exceeds_standard}")
    std = int(config.NEPAL_PM25_24H_STANDARD)
    best = ", ".join(_hhmm(t) for t in cleanest_hours(fc))
    level = "LIKELY" if fc.exceeds_standard == "likely" else "POSSIBLE"
    en = (
        f"Hawa air alert ({fc.station.title()}): PM2.5 next 24h ~{fc.next24h_mean_p50:.0f} ug/m3, "
        f"national limit {std}. Exceedance {level}. Cleanest hours: {best}. "
        f"Limit outdoor exercise; wear an N95 outdoors."
    )
    name_ne = STATION_NAMES_NE.get(fc.station, fc.station)
    level_ne = "सम्भावित" if fc.exceeds_standard == "possible" else "धेरै सम्भावित"
    ne = (
        f"हावा सूचना ({name_ne}): आगामी २४ घण्टा PM2.5 करिब {fc.next24h_mean_p50:.0f}, "
        f"राष्ट्रिय मापदण्ड {std}। मापदण्ड नाघ्ने {level_ne}। सफा समय: {best}। "
        f"बाहिर व्यायाम कम गर्नुहोस्, N95 मास्क लगाउनुहोस्।"
    )
    return {"en": en, "ne": ne}
