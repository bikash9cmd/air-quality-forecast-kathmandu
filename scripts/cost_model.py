"""Monthly AWS cost estimate for Hawa, from explicit assumptions. Prints a markdown table.

    python scripts/cost_model.py

Prices are on-demand list prices (USD) as I understood them in October 2026 and are
deliberately rounded up; check them in the AWS Pricing Calculator for your region before
quoting. The free tier is ignored, so the totals are what a new account pays after year one.
"""

from __future__ import annotations

HOURS = 730  # per month

PRICE = {
    "lambda_gb_s_arm": 0.0000133334,
    "lambda_req": 0.20 / 1e6,
    "ddb_wru": 0.625 / 1e6,
    "ddb_rru": 0.125 / 1e6,
    "s3_put": 0.005 / 1000,
    "s3_get": 0.0004 / 1000,
    "s3_gb": 0.025,
    "httpapi_req": 1.00 / 1e6,
    "logs_gb": 0.50,
    "custom_metric": 0.30,
    "alarm": 0.10,
    "ecr_gb": 0.10,
    "sagemaker_ml_t3_medium_hr": 0.05,
    "ec2_t4g_small_hr": 0.0168,
    # SMS to Nepal (see docs/COST.md for sources): international gateways ~USD 0.10-0.30,
    # local Nepali aggregators ~NPR 0.5-1.4 (about USD 0.004-0.01).
    "sms_international": 0.10,
    "sms_local_np": 0.01,
}

ASSUME = {
    "stations": 2,
    "ingest_seconds": 3, "ingest_gb": 0.5,
    "forecast_seconds": 10, "forecast_gb": 1.0,
    "retrain_seconds": 180, "retrain_gb": 4.0, "retrains": 4.3,
    "api_requests": 100_000, "api_seconds": 0.15, "api_gb": 0.5,
    "forecast_item_kb": 3,
    "custom_metrics": 5, "alarms": 5,
    "image_gb": 0.45, "logs_gb": 0.2,
    "raw_archive_objects": 2 * 24 * 365,  # one year of hourly objects, read by each retrain
    "subscribers": 1000, "alert_days": 12,  # a bad winter month in Kathmandu
}


def serverless() -> dict[str, float]:
    p, a = PRICE, ASSUME
    lam_gbs = (HOURS * a["ingest_seconds"] * a["ingest_gb"]
               + HOURS * a["forecast_seconds"] * a["forecast_gb"]
               + a["retrains"] * a["retrain_seconds"] * a["retrain_gb"]
               + a["api_requests"] * a["api_seconds"] * a["api_gb"])
    lam_req = HOURS * 2 + a["retrains"] + a["api_requests"]
    wru = HOURS * a["stations"] * (1 + a["forecast_item_kb"])
    rru = HOURS * a["stations"] * 6 + a["api_requests"]
    return {
        "Lambda (compute + requests)": lam_gbs * p["lambda_gb_s_arm"] + lam_req * p["lambda_req"],
        "DynamoDB on-demand": wru * p["ddb_wru"] + rru * p["ddb_rru"],
        "S3 (archive writes, retrain reads, storage)": HOURS * a["stations"] * p["s3_put"]
        + a["retrains"] * a["raw_archive_objects"] * p["s3_get"] + 0.01 * p["s3_gb"],
        "API Gateway HTTP API": a["api_requests"] * p["httpapi_req"],
        "CloudWatch (logs, custom metrics, alarms)": a["logs_gb"] * p["logs_gb"]
        + a["custom_metrics"] * p["custom_metric"] + a["alarms"] * p["alarm"],
        "ECR image storage": a["image_gb"] * p["ecr_gb"],
        "EventBridge Scheduler, SSM parameter, SNS email": 0.0,
    }


def main() -> None:
    rows = serverless()
    total = sum(rows.values())
    print("| Item | USD / month |\n|---|---:|")
    for k, v in rows.items():
        print(f"| {k} | {v:.2f} |")
    print(f"| **Total (platform)** | **{total:.2f}** |\n")

    a, p = ASSUME, PRICE
    sms = a["subscribers"] * a["alert_days"]
    print("Alternatives for the same workload (compute only):\n")
    print("| Design | USD / month |\n|---|---:|")
    print(f"| This design (serverless, scales to zero) | {total:.2f} |")
    print(f"| One EC2 t4g.small running 24/7 (cron + API) | {HOURS * p['ec2_t4g_small_hr']:.2f} |")
    print(f"| SageMaker real-time endpoint, ml.t3.medium 24/7 (endpoint only) | "
          f"{HOURS * p['sagemaker_ml_t3_medium_hr']:.2f} |\n")
    print(f"Alerts: {a['subscribers']} SMS subscribers x {a['alert_days']} alert days = {sms:,} messages\n")
    print("| Channel | USD / month |\n|---|---:|")
    print(f"| International SMS gateway (e.g. SNS SMS) at ~$0.10 | {sms * p['sms_international']:.2f} |")
    print(f"| Local Nepali SMS aggregator at ~$0.01 | {sms * p['sms_local_np']:.2f} |")
    print("| Email via SNS, or app push / Viber | ~0 |")


if __name__ == "__main__":
    main()
