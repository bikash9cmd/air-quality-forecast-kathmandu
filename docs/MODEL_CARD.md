# Model card: Hawa PM2.5 forecaster

| | |
|---|---|
| **What it predicts** | PM2.5 (µg/m³) at each of the next 24 hours for one monitoring station, as a median (p50) and a 90th percentile (p90) |
| **Derived output** | Whether the next 24-hour mean is *likely* (p50 > 40) or *possibly* (p90 > 40) above Nepal's 24-hour standard of 40 µg/m³ (NAAQS 2012) |
| **Model** | LightGBM, 3 models: p50, p90 (quantile loss), and a neighbour-only p50 for sensor outages. Trees of 4 leaves, 300 rounds. Target: `log1p(y) - log1p(anchor)`, where the anchor is damped-anomaly persistence (`features.own_anchor`) |
| **Training data** | Nepal Department of Environment, hourly PM2.5 at Ratnapark (Kathmandu) and Pulchowk (Lalitpur), 1 Nov 2016 to 30 Dec 2017, after the cleaning rules in `hawa/data.py` |
| **Evaluation** | Walk-forward by month, Mar to Dec 2017, training on all labels before each test month |

## Performance (Mar to Dec 2017, both stations, 283,078 hourly forecasts)

| Model | MAE (µg/m³) |
|---|---:|
| Latest hour, repeated | 11.23 |
| Same hour yesterday | 8.45 |
| 7-day same-hour average | 8.79 |
| Damped-anomaly anchor (no training) | 7.51 |
| Ridge regression | 7.32 |
| **LightGBM p50** | **7.18** |

p90 coverage: 90.3% of observed values fell at or below the p90 forecast (target 90%).

Hyperparameters were chosen on 4 of the 10 months. On the other 6 months LightGBM ties ridge and the anchor (6.72) and beats the best naive baseline by 12%.

## Intended use

Public information: when in the next day the air is likely to be cleanest, and whether tomorrow is likely to exceed the national standard. Planning outdoor school activities, exercise, and when to wear a mask.

## Not intended for

- Regulatory reporting or compliance decisions.
- Locations away from the two stations. PM2.5 varies across the valley (over hours when both reported, Ratnapark averaged 1.4x Pulchowk); the forecast is for the station, not "Kathmandu".
- Medical decisions for individuals.

## Limitations

- **No weather inputs.** Rain, wind and the winter inversion drive PM2.5 and the model can't see them coming. Its largest misses are sudden clean-ups after polluted spells.
- **One year of history.** It can't learn the annual cycle; `doy_sin`/`doy_cos` rank high in importance, which with one year likely means partial memorisation of 2017's calendar.
- **Two stations, 2016-17.** Pollution sources (vehicle fleet, brick kilns, construction) have changed since. Retrain on recent data before trusting it for current conditions.
- **Sensor quality.** The cleaning rules remove the faults I found; slow calibration drift would pass through.

## Fairness and impact

Errors are not symmetric in cost: a missed bad-air day exposes children, the elderly and people with respiratory illness, while a false alarm costs some inconvenience. The alert design reflects this with a "possible" tier on p90 (recall 99.5%, precision 48%) alongside "likely" on p50 (recall 88.5%, precision 81%). Only two stations exist in this data, both in the urban core, so communities on the valley edges are not represented.
