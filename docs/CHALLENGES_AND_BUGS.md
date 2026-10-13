# Challenges and bugs

A log of what went wrong while building this, in roughly the order I hit it, with the evidence and the fix. Every number here can be reproduced from the repo.

## Data

### 1. pandas silently shifted every column one place left

**Symptom:** the first load crashed with `time data "54" doesn't match format "%m/%d/%Y %H"`. The `datetime` column contained PM2.5 values.

**Cause:** every data row in the DoE export ends with a trailing comma, so rows have five fields and the header has four. When that happens pandas assumes the first column is the index and shifts everything else left: `datetime` gets the Max values, `Max` gets Min, and so on. If the timestamps had been numeric, nothing would have crashed and the model would have trained on the wrong columns.

**Fix:** `pd.read_csv(..., index_col=False)`, plus a check that the columns are exactly the four expected names. **Test:** `test_trailing_comma_does_not_shift_columns`.

### 2. "No data" codes were averaged into real readings

**Symptom:** hourly averages like `-634.9`, `-104.7` and, more dangerously, `59.27` at Pulchowk on 13 June 2017 14:00, a value that looks completely normal.

**Cause:** the monitor logs several readings per hour and the portal averages them. When some readings are the `-999` "no data" code, the average includes them. An hour where `Min == -999` has a meaningless average even if it happens to come out positive (the 59.27 hour also had a Max of 2,926).

**Fix:** drop any hour whose `Min` is `-999`, not just hours whose `Average` is negative. That removed 828 hours at Pulchowk (8.8% of its rows) and 286 at Ratnapark. **Test:** `test_hour_containing_sentinel_is_dropped_even_if_average_looks_valid`.

### 3. Spikes above the instrument's range

Nine Ratnapark hours have a Max between 1,456 and 2,926 µg/m³ with an ordinary Min. That is a glitch, not air: the monitors' range tops out at 1,000. The hourly average is contaminated and can't be repaired, so those hours are dropped.

### 4. Long sensor outages

After cleaning, gaps are common and long: Pulchowk was silent for **26 days** in November 2016, Ratnapark for **12 days** in March 2017, and Pulchowk had only 244 valid hours in September 2017. Overall completeness is 90.9% (Ratnapark) and 83.7% (Pulchowk). This turned "what if a sensor dies?" from a hypothetical into a requirement, and is why the neighbour fallback exists ([FAILURE_MODES.md](FAILURE_MODES.md)).

### 5. Files end on 30 December

Both 2017 files stop at `12/30/2017 23`; 31 December is missing. The scraper that produced the mirror probably used an exclusive end date. Not fixable from here; documented so nobody thinks the model "can't forecast New Year's Eve".

## Modelling

### 6. I picked a target the model couldn't beat

My first target was "the mean PM2.5 over the next 24 hours". The simplest baseline, "the mean over the last 24 hours", scored MAE 5.0 µg/m³, and LightGBM scored 5.3 to 6.7 depending on the version. I spent a while tuning before looking at the target itself: a 24-hour mean, recomputed every hour, shares 23 of its 24 hours with the previous one. Day-to-day changes average about 6 µg/m³, and without weather data there's little information about which way tomorrow will go.

**Fix:** forecast each of the next 24 hours instead. That's more useful (it answers *when* the air will be cleanest), and naive baselines are much weaker on it. The daily alert is now derived from the hourly forecasts, and on that alert LightGBM does beat persistence (README, Results).

**Lesson:** check whether a naive baseline is already near the noise floor *before* tuning a model.

### 7. Trees can't extrapolate to the monsoon

**Symptom:** the first model predicted PM2.5 levels directly. Its June error was 9.1 µg/m³ against 3.5 for persistence, 2.6x worse.

**Cause:** in a walk-forward backtest, the June model was trained on November to May, the polluted half of the year. A tree's prediction is an average of training values in a leaf, so it can never predict below the lowest level it has seen. Monsoon rain washes the air clean, and the model couldn't follow.

**Fix:** predict the log-ratio between the target and an anchor (the recent level for that clock hour), then multiply back. The model learns "how different from usual", which transfers between seasons.

### 8. The anchor turned out to be most of the model

Looking for a better anchor, I tried *damped-anomaly persistence*: start from the usual level for the target hour, add how far the latest hour sits from *its* usual level, and fade that anomaly with horizon (`exp(-h/9)`). With no training at all it scores MAE 7.51, better than every naive baseline (best: 8.45) and close to LightGBM (7.18).

I kept it as both a baseline and the anchor. It makes the honest headline smaller: most of the improvement over naive forecasts comes from this one line of statistics, and LightGBM adds about 4% on top. Where LightGBM clearly earns its place is the alert: of 2,822 next-24-hour exceedances it misses 324, against 433 for the anchor alone, at the same precision (see README).

### 9. Bigger trees made it worse

| `num_leaves` | MAE on the 4 tuning months |
|---:|---:|
| 31 (600 trees) | 8.44 |
| 15 | 8.30 |
| 7 | 7.85 |
| **4** | **7.70** |

Once the anchor explains most of the variance, what's left is a weak signal in a lot of noise, and deeper trees fit the noise. A tree of 4 leaves can still capture "mornings after a bad night are worse".

### 10. I tuned on the test months, so I report both

The hyperparameters above were chosen on 4 of the 10 backtest months (Apr, Jul, Oct, Dec), which makes those months optimistic. On the 6 months I didn't tune on, LightGBM ties the damped-anomaly anchor and ridge regression (all 6.72) and beats the best naive baseline (weekly profile, 7.63) by 12%. Both numbers are in the README. A third, held-back year of data would settle it properly.

### 11. Overlapping training rows wasted most of the training time

The first full backtest took over 8 minutes. Consecutive issue hours share 23 of their 24 input hours, so they're near-duplicates. Training on every 3rd issue hour took the 4-fold experiment from 81 s to 32 s and the error went *down* slightly (8.43 to 8.30), probably from less overfitting to near-duplicates.

### 12. Leakage through the label, designed out

A forecast issued at 23:00 on 28 February for 22:00 on 1 March has its label inside March. Splitting train and test by *issue* time would put that label in training for the March test fold. The split filters training rows by *label* time instead. **Test:** `test_no_training_label_falls_inside_the_test_month`, which also asserts that the leaky rows exist (so the test can't pass vacuously).

## Infrastructure

### 13. An alarm that could never fire

The ingest Lambda emitted `MissingReading` through CloudWatch Embedded Metric Format with a `station` dimension. The `SensorDownAlarm` in the template watched `MissingReading` with no dimensions. In CloudWatch those are **different metrics**, so the alarm would have sat in `INSUFFICIENT_DATA` forever. The same per-station, per-mode dimensions created 11 custom metrics at $0.30 each, which were 75% of the projected monthly bill.

**Fix:** emit dimensionless metrics (the station name goes into the log line as a property, still searchable in Logs Insights). 5 custom metrics; the projected bill dropped from $4.37 to $2.57 per month. Found while building the cost model, which is a good argument for building one.

### 14. LightGBM needs OpenMP, which slim images don't ship

`ldd lib_lightgbm.so` shows a dependency on the system `libgomp.so.1`. `python:3.12-slim` doesn't include it, so the import fails at runtime, not at `pip install`. Both Dockerfiles install it explicitly (`libgomp1` on Debian, `libgomp` on the Lambda base image).

### 15. Nepal is UTC+05:45

EventBridge Scheduler cron expressions here are in UTC. Saturday 02:00 Nepal time (the quietest hour of Nepal's one-day weekend) is **Friday 20:15 UTC**. All data timestamps are Nepal local time, and Nepal has no daylight saving, so the offset never changes.
