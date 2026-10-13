# Alternatives considered

Each decision below is written as a short architecture decision record: what I chose, what I rejected, and what would make me change my mind. The last line of each is the part I'd expect an interviewer to push on.

---

## ADR-001: Which Nepal problem

**Chosen:** hour-by-hour PM2.5 forecasts and next-day exceedance alerts for the Kathmandu Valley.

**Considered:**

| Problem | Why it's real | Why not (for this project) |
|---|---|---|
| Traffic congestion in Kathmandu | Everyone in the valley lives it | No public, machine-readable traffic data. A project built on synthetic traffic data proves nothing. |
| Waste segregation (image classifier) | Sisdol landfill crisis, source segregation rules | The public datasets (TrashNet etc.) are generic and the classifier is a common tutorial; it would look like a clone. |
| Waste collection route optimisation | KMC collection is irregular | Needs fill-level or truck GPS data that isn't public. |
| **Air quality** | Kathmandu regularly ranks among the most polluted cities in the world in winter and spring; schools have closed for it | **Real, hourly, government data is public**, and pollution is driven by traffic, brick kilns and waste burning, so it connects to the other two problems. |

**Would change if:** traffic data became available (Pathao or the Traffic Police opening a feed). It would also be the best new *feature* for this model.

---

## ADR-002: What to predict

**Chosen:** PM2.5 for each of the next 24 hours, plus "will the next 24-hour mean exceed Nepal's standard of 40 µg/m³", derived from the hourly forecasts.

**Considered:**

- **Only the next-24h mean.** I built this first. It is almost perfectly predicted by "the last 24 hours' mean" (persistence MAE 5.0 µg/m³ against daily changes of about 6), and no model I tried beat persistence on it. That is a property of the target, not the model: a 24-hour average of a slow-moving quantity barely changes hour to hour. It also isn't actionable on its own: a parent wants to know *when* to send a child outside, not the daily average.
- **AQI category instead of concentration.** Categories hide the margin ("unhealthy" at 56 and at 149 look the same) and turn a regression problem into an ordinal one with less data per class. AQI is easy to compute from the forecast at display time.

**Would change if:** users asked for a single daily answer; then I'd forecast the calendar-day mean issued at 18:00, which is a harder and more useful target than a rolling mean.

---

## ADR-003: Model

**Chosen:** LightGBM, trained on the *log-ratio to a damped-anomaly anchor*, with very small trees (4 leaves), one model for all stations and horizons; plus a p90 quantile model and a neighbour-only fallback model.

**Considered:**

| Option | Result / reason |
|---|---|
| Weekly profile (same hour, last 7 days) | Strong baseline, no training. Kept as a baseline and as part of the anchor. |
| Damped-anomaly persistence (statistical) | Even stronger with no training. Became the anchor the model corrects. |
| Ridge regression on the same features | Within a few percent of LightGBM. Kept as a baseline: it is the honest answer to "do you even need trees?" |
| LightGBM on raw levels | Failed in the monsoon: trees can't predict below the lowest value seen in training. |
| Deeper LightGBM (15-31 leaves) | Worse than 4 leaves. The residual after the anchor is mostly noise and big trees fit it. |
| One model per horizon (24 models) | 24x the artefacts and training time; a single model with `horizon` as a feature shares data across horizons. |
| LSTM / Temporal Fusion Transformer | 14 months of data from two sensors is far too little. It would be harder to explain, slower to train, and I'd expect it to lose to the anchor. |
| Prophet / SARIMA per station | Handles seasonality well, but one year of history can't estimate an annual cycle, and per-station fitting doesn't share information between stations. |

**Would change if:** several years of data and weather inputs became available. Then a gradient-boosted model with weather features is the next step, and a sequence model becomes worth testing.

---

## ADR-004: Where training runs

**Chosen:** a Lambda function (4 GB, 15-minute limit), weekly.

**Considered:** SageMaker Training Jobs; SageMaker Pipelines; an EC2 instance on a cron.

Training takes about a minute on one core. A SageMaker training job would spend longer provisioning an instance than training, and SageMaker Pipelines adds a second orchestration system for a two-step workflow. Lambda keeps the whole system in one deployment model with one IAM story.

**Would change if:** training approached the 15-minute Lambda limit (many stations, years of data, hyperparameter search). Then SageMaker Training Jobs with managed spot instances, started by the same weekly schedule through Step Functions.

---

## ADR-005: How predictions are served

**Chosen:** batch. Forecasts are computed hourly and written to DynamoDB; the API only reads.

**Considered:** a SageMaker real-time endpoint; SageMaker Serverless Inference; computing the forecast inside the API request.

Inputs change once an hour, so every request in the same hour would compute the same answer. Precomputing means the API is a key-value read (milliseconds, no model in the request path) and the model can be down without the API being down. A real-time endpoint would cost about $36/month idle; this costs cents.

**Would change if:** forecasts depended on user input (for example a personal exposure forecast from a route). Then SageMaker Serverless Inference or the model inside a Lambda.

---

## ADR-006: Orchestration

**Chosen:** EventBridge Scheduler directly invoking Lambdas, with retries and a dead-letter queue.

**Considered:** Step Functions; Airflow (MWAA); Kinesis for ingestion.

Two independent hourly jobs and one weekly job do not need a workflow engine. MWAA starts at roughly $350/month. Kinesis is for thousands of events per second; this is two per hour.

**Would change if:** the weekly job grew steps (compaction, training, evaluation, human approval). Step Functions is the next step, not Airflow.

---

## ADR-007: Infrastructure as code

**Chosen:** AWS SAM (CloudFormation).

**Considered:** Terraform; AWS CDK.

Everything here is serverless and AWS-only, which is what SAM is built for: short syntax for functions and schedules, and `sam local invoke` for testing handlers. CDK would be my choice for a larger system with shared constructs; Terraform for a multi-cloud estate or a team that already uses it.

---

## ADR-008: Alert channel

**Chosen:** SNS topic, with email subscriptions out of the box.

**Considered:** SNS SMS directly; a local Nepali SMS aggregator; app push; Viber.

SMS to Nepal through international gateways costs roughly $0.10 to $0.30 per message, against NPR 0.5 to 1.4 (about $0.004 to $0.01) through local aggregators ([source](https://www.sent.dm/en/resources/sms-pricing/nepal-sms-pricing)). At 1,000 subscribers that is the difference between ~$1,200 and ~$120 in a bad month, which dwarfs everything else this system costs (see [COST.md](COST.md)). The SNS topic stays the fan-out point; a small subscriber Lambda would call the local aggregator's HTTP API.
