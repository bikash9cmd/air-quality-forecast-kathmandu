# Data

## Source

Hourly PM2.5 from two Department of Environment (Government of Nepal) monitoring stations in the Kathmandu Valley, originally published on [pollution.gov.np](http://pollution.gov.np) and mirrored by [hbvj99/NPGovAirPollution](https://github.com/hbvj99/NPGovAirPollution). The mirror's README states the data "is available to the general public with no cost".

`python -m hawa download` fetches the four files below from the mirror **at commit `5574cf9`** and checks each against its SHA-256. Raw files are not committed to this repository.

| File | Station | Period | Rows |
|---|---|---|---:|
| `Ratnapark, Kathmandu_PM2.5 Inst_2016-11-01_2016-12-30_Hourly.csv` | Ratnapark, central Kathmandu | Nov-Dec 2016 | 1,431 |
| `Ratnapark, Kathmandu_PM2.5 Inst_2017-01-01_2017-12-30_Hourly.csv` | Ratnapark | 2017 | 8,135 |
| `Pulchowk, Lalitpur_PM2.5 Inst_2016-11-01_2016-12-30_Hourly.csv` | Pulchowk, Lalitpur | Nov-Dec 2016 | 646 |
| `Pulchowk, Lalitpur_PM2.5 Inst_2017-01-01_2017-12-30_Hourly.csv` | Pulchowk | 2017 | 8,721 |

## Format

```text
datetime, Max, Min, Average
01/01/2017 00, 116.900001526, 77.8000030518, 90.36333312987666,
```

- `datetime`: `MM/DD/YYYY HH`, Nepal local time (UTC+05:45), hour-beginning.
- `Max`, `Min`, `Average`: of the sub-hourly readings in that hour, µg/m³. `-999` means no reading.
- Every data row ends with a trailing comma (see [bug #1](../docs/CHALLENGES_AND_BUGS.md#1-pandas-silently-shifted-every-column-one-place-left)).

## Cleaning rules (`hawa/data.py`)

| Rule | Ratnapark | Pulchowk |
|---|---:|---:|
| Hour contains a `-999` reading (`Min <= -999`) | 286 | 828 |
| Negative average (after the rule above) | 0 | 0 |
| Max above the instrument's 1,000 µg/m³ range | 9 | 0 |
| Duplicate timestamp | 0 | 0 |
| Stuck sensor (6+ identical hours) | 0 | 0 |
| **Valid hours / hours in period** | **9,271 / 10,200 (90.9%)** | **8,539 / 10,200 (83.7%)** |

`python -m hawa quality` reproduces this table.
