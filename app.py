"""Streamlit demo of Hawa: pick an hour in the 2016-17 data and see the next 24 hours' PM2.5 forecast.

    streamlit run app.py

Needs the raw DoE files and a trained model; if they are missing they are fetched (checksummed)
and trained once, like `make train` (about 20 seconds).
"""

from datetime import date, datetime, time

import pandas as pd
import streamlit as st

from hawa import config, data
from hawa.alerts import message, should_alert
from hawa.forecast import forecast
from hawa.models import Forecaster

st.set_page_config(page_title="Hawa", page_icon="🌫️", layout="centered")

STANDARD = 40  # Nepal's 24-hour PM2.5 standard, ug/m3


@st.cache_resource(show_spinner="Fetching the sensor data and training the model (first visit only)...")
def load():
    model_dir = config.ARTIFACT_DIR / "model"
    try:
        wide, _ = data.load_clean()
    except FileNotFoundError:
        from hawa.__main__ import cmd_download

        cmd_download(None)
        wide, _ = data.load_clean()
    if not (model_dir / "forecaster.joblib").exists():
        from hawa.pipeline import train_with_gate

        model, gate = train_with_gate(wide.loc[:"2017-12-01"], None)
        model.save(model_dir, {"gate": gate.as_dict(), "data_until": "2017-12-01"})
    return wide, Forecaster.load(model_dir)


wide, model = load()

st.title("Hawa (हावा)")
st.caption("Hour-by-hour PM2.5 forecasts for the Kathmandu Valley's government monitoring stations.")

c1, c2, c3 = st.columns(3)
day = c1.date_input(
    "Forecast issued on", value=date(2017, 12, 7), min_value=date(2016, 11, 8), max_value=date(2017, 12, 29)
)
hour = c2.selectbox("at", list(range(24)), index=23, format_func=lambda h: f"{h:02d}:00")
station = c3.selectbox(
    "Station", ["ratnapark", "pulchowk"], format_func=lambda s: {"ratnapark": "Ratnapark", "pulchowk": "Pulchowk"}[s]
)
st.caption("The model was trained on data up to 1 December 2017, so later hours are a true out-of-sample test.")

at = pd.Timestamp(datetime.combine(day, time(hour)))
fcs = {fc.station: fc for fc in forecast(wide.loc[:at], model, at)}
fc = fcs.get(station)
if fc is None:
    st.warning("No forecast for this station at that hour (too little recent data).")
    st.stop()

st.subheader("Alert")
if should_alert(fc):
    for text in message(fc).values():
        st.write(text)
else:
    st.success(f"No alert: the next 24 hours are unlikely to break the national limit of {STANDARD} µg/m³.")

d = fc.as_dict()
hourly = pd.DataFrame(d["hourly"]).assign(time=lambda x: pd.to_datetime(x["time"])).set_index("time")
actual = wide[station].reindex(hourly.index)
chart = pd.DataFrame(
    {
        "forecast (median)": hourly["p50"],
        "forecast (90th pct)": hourly["p90"],
        "what was measured": actual,
        "national standard": STANDARD,
    }
)
m1, m2, m3 = st.columns(3)
m1.metric("Next 24 h mean (median)", f"{d['next24h_mean_p50']} µg/m³")
m2.metric("Next 24 h mean (90th pct)", f"{d['next24h_mean_p90']} µg/m³")
m3.metric("Exceeds the standard", d["exceeds_standard"])
st.line_chart(chart, y_label="PM2.5 µg/m³")
if d.get("mode") != "primary" or d.get("note"):
    st.info(f"Mode: {d.get('mode')}. {d.get('note', '')}")
