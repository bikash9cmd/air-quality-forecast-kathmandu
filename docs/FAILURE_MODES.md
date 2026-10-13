# What breaks, and what happens when it does

The question I designed around: *at 07:00 on a bad January morning, what has to go right for a parent in Kathmandu to get a correct warning, and what happens when each piece doesn't?*

## Measured: a sensor goes dark

Sensors in this network do go dark: Pulchowk was silent for 26 days in November 2016 and Ratnapark for 12 days in March 2017 (see [CHALLENGES_AND_BUGS.md](CHALLENGES_AND_BUGS.md)). So I simulated it: blank Ratnapark's sensor for a whole test month, then forecast every hour of that month with each strategy and score against what the sensor actually recorded.

| Month | Healthy sensor (normal case) | Broken sensor, no fallback | Neighbour-only fallback | Naive: repeat last known day |
|---|---:|---:|---:|---:|
| Nov 2017 | MAE 5.5 | no forecast for 75% of hours | MAE 11.1 | MAE 12.6 |
| Dec 2017 | MAE 7.8 | no forecast for 98% of hours | MAE 15.1 | MAE 36.7 |

Reading it: losing the station's own sensor roughly doubles the error, but the fallback still forecasts every hour and beats the naive alternative, by a lot in December when pollution was climbing and "repeat the last known day" froze at a stale level. Without the fallback the primary model simply has no inputs. Reproduce with `python -m hawa backtest` (`sensor_outage_experiment` in `artifacts/reports/backtest_summary.json`).

The fallback's p90 is its p50 x 1.77, the 90th percentile of observed / forecast in the backtest, so its alerts are calibrated too, just wider.

## Every failure mode I could think of

| What fails | How it shows up | How it's detected | What the system does | Residual risk |
|---|---|---|---|---|
| One station's sensor goes dark | Ingest gets no row for that station | `MissingReading` metric; `SensorDownAlarm` after 6 hours | Forecast switches to `mode: neighbour` (measured above); response says so in `note` | Error roughly doubles until fixed |
| Both sensors dark | No data at all | Same alarm; `/health` returns `ok: false` | `mode: stale`, no numbers. **Refusing beats inventing a forecast.** | No forecasts until data returns |
| Sensor returns garbage | `-999` codes, negative averages, spikes above 1,000, stuck values | Counted per rule in the data-quality report | Dropped by the cleaning rules in `hawa/data.py`, each tested | A plausible-looking wrong value (e.g. miscalibration drift) gets through |
| Upstream changes its file format | Columns renamed or shifted | `read_raw` raises on unexpected columns; ingest fails | Scheduler retries 3x, then the event goes to the DLQ; `DLQNotEmptyAlarm` | Hours of missing ingest; forecast falls back as above |
| Ingest runs late | Forecast at :15 runs before :05's data landed | `valid_24` < 24 in the forecast input | Features tolerate gaps (LightGBM handles NaN); anchor falls back to 24h mean | Slightly worse forecast for one hour |
| Forecast Lambda crashes | No new forecast item | `ForecastErrorsAlarm` (2 failed hours) | API keeps serving the last forecast, with its `issued_at` | Users see an older forecast; `/health` flags it after 2h |
| Schedule disabled or deleted | Nothing runs, **so nothing errors** | `ForecastNotRunningAlarm` on zero invocations for 3h (`TreatMissingData: breaching`) | n/a | This is the failure an error-only alarm misses |
| A retry races the next hour | Older forecast overwrites newer | Prevented, not detected | `ReservedConcurrentExecutions: 1` on the forecaster | None |
| Bad retrain (a week of bad data, a code bug) | New model worse than old | Promotion gate on the last 30 days | Candidate rejected; champion keeps serving; `RetrainGateAlarm` | Model gets staler until someone looks |
| Bad model gets past the gate | Forecasts get worse | Not automated yet (see "gaps" below) | Roll back: set `/hawa/champion-model-version` to the previous version | Hours to days of worse forecasts |
| Weather changes the model can't see | A clean day after polluted days is forecast as polluted (13-14 Dec 2017 in the [README chart](img/example_week.png)) | Visible in the backtest | Nothing yet; the model has no weather inputs | The biggest single source of error. Weather is the top item in "next steps". |
| Season the model hasn't seen | Training only on winter, then predicting monsoon | Found in development: a level-based model's June error was 2.6x that of plain persistence | Model predicts a log-ratio to an anchor, so it adapts to level shifts | One year of data can't teach the annual cycle; `doy_sin/cos` features are a known overfitting risk |
| Someone scrapes the API hard | Request spike | API Gateway metrics | HTTP API throttle (10 rps, burst 20); responses cacheable 5 min | None at this scale |
| Cost runaway | Bill grows | AWS Budget at 80% actual and 100% forecast | Email to ops | Budget alerts lag by hours |
| AWS region outage (ap-south-1) | Everything down | AWS Health | Nothing: single region by choice | For a free public-good service, a few hours of downtime per year is acceptable; multi-region would multiply cost and complexity for no user-visible gain |

## Known gaps (not built, deliberately listed)

1. **No live accuracy monitoring.** The system knows when it *didn't* forecast, but not when it forecast *badly*. The fix is a small daily job that joins yesterday's forecasts with the readings that arrived since and publishes a rolling MAE metric with an alarm against the backtest number. That is the next thing I would build.
2. **No canary for new models.** The gate checks history; a shadow period (new model forecasts logged but not served for a few days) would check the present.
3. **Live data source.** The deployment replays 2016-17 data; see "Going live" in [ARCHITECTURE.md](ARCHITECTURE.md).
