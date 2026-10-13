# Cost

**Short version:** the platform costs about **$2.60 a month** on AWS. The model is a rounding error. If alerts go out by SMS, *the SMS bill* is the cost that matters, and choosing a local Nepali gateway over an international one cuts it about 10x.

All numbers come from [`scripts/cost_model.py`](../scripts/cost_model.py), which states every assumption. Prices are on-demand list prices, rounded up, with the free tier ignored (so this is what you'd pay after the first year). Check them in the [AWS Pricing Calculator](https://calculator.aws/) for your region before quoting them.

## Monthly estimate

Assumptions: 2 stations, hourly ingest and forecast, weekly retrain, 100,000 API requests a month.

| Item | USD / month |
|---|---:|
| Lambda (compute + requests) | 0.27 |
| DynamoDB on-demand | 0.02 |
| S3 (archive writes, retrain reads, storage) | 0.04 |
| API Gateway HTTP API | 0.10 |
| CloudWatch (logs, 5 custom metrics, 5 alarms) | 2.10 |
| ECR image storage | 0.05 |
| EventBridge Scheduler, SSM parameter, SNS email | 0.00 |
| **Total** | **2.57** |

The largest line is monitoring, not ML. That's normal for small serverless systems and worth knowing.

## Against the alternatives

| Design | USD / month | Why not |
|---|---:|---|
| **This design** (serverless, scales to zero) | **2.57** | |
| One EC2 t4g.small running 24/7 (cron + API) | 12.26 | 5x the cost, and now I patch an OS, and one instance is one failure domain |
| SageMaker real-time endpoint, ml.t3.medium 24/7 (endpoint alone) | 36.50 | Pays for 730 hours of a server to answer 2 batch jobs an hour |

## Alerts: the cost that actually scales

1,000 SMS subscribers x 12 alert days in a bad winter month = 12,000 messages:

| Channel | USD / month |
|---|---:|
| International SMS gateway (SNS SMS and similar) at ~$0.10 per message | ~1,200 |
| Local Nepali SMS aggregator at ~$0.01 per message | ~120 |
| Email through SNS, app push, or Viber | ~0 |

International rates to Nepal run about $0.10 to $0.30 per message, against NPR 0.50 to 1.40 through local providers ([sent.dm, May 2025](https://www.sent.dm/en/resources/sms-pricing/nepal-sms-pricing)). The design keeps SNS as the fan-out point and would add one subscriber Lambda that calls a local aggregator.

## Cost decisions in the code

| Decision | Where | Effect |
|---|---|---|
| Batch predictions every hour instead of a model endpoint | `forecast_handler`, ADR-005 | No idle compute: ~$36/month saved vs the smallest SageMaker endpoint |
| Train in Lambda instead of SageMaker Training | `RetrainFunction`, ADR-004 | Training takes about 20 s locally; no instance spin-up to pay for |
| Train on every 3rd issue hour | `config.TRAIN_EVERY_N_HOURS` | 2.5x less training compute, no loss in accuracy |
| Graviton (`arm64`) Lambdas | `Globals` in `template.yaml` | About 20% cheaper per GB-second than x86 |
| HTTP API instead of REST API | `HttpApi` | About 70% cheaper per request; I don't need REST API features |
| DynamoDB on-demand + TTL | both tables | Pay per request at this tiny volume; old items delete themselves for free |
| No S3 lifecycle transition for raw data | `DataBucket` | Objects are ~200 bytes, below the 128 KB billing minimum for Infrequent Access, so transitions would *raise* the bill |
| Dimensionless custom metrics | `_metric` in `handlers.py` | 11 metrics down to 5; fixed an alarm that could never fire ([bug #13](CHALLENGES_AND_BUGS.md#13-an-alarm-that-could-never-fire)) |
| Log retention 7-30 days | `*Logs` resources | CloudWatch keeps logs forever by default |
| API throttling and cache headers | `HttpApi`, `api_handler` | A scraper can't run up the bill; CloudFront can cache for 5 minutes |
| AWS Budget with email at 80% / forecast 100% | `MonthlyBudget` | A mistake shows up in hours, not on next month's invoice |
