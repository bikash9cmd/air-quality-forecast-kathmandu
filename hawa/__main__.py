"""Command line: python -m hawa <command>. Run with --help for the list."""

from __future__ import annotations

import argparse
import json
import logging
import sys

import pandas as pd

from hawa import config, data


def cmd_download(_: argparse.Namespace) -> None:
    for p in data.download():
        print(f"checksum OK  {p.name}")


def cmd_quality(_: argparse.Namespace) -> None:
    _, reports = data.load_clean()
    report = [r.as_dict() for r in reports]
    out = config.ARTIFACT_DIR / "reports" / "data_quality.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def cmd_backtest(_: argparse.Namespace) -> None:
    from hawa.report import write_backtest_report

    wide, _ = data.load_clean()
    summary = write_backtest_report(wide, config.ARTIFACT_DIR / "reports")
    print(json.dumps(summary, indent=2))


def cmd_train(args: argparse.Namespace) -> None:
    from hawa.models import Forecaster
    from hawa.pipeline import train_with_gate

    wide, _ = data.load_clean()
    if args.until:
        wide = wide.loc[: args.until]
    model_dir = config.ARTIFACT_DIR / "model"
    champion = Forecaster.load(model_dir) if (model_dir / "forecaster.joblib").exists() and not args.force else None
    model, gate = train_with_gate(wide, champion)
    print(json.dumps(gate.as_dict(), indent=2))
    if model is None:
        print("GATE FAILED: candidate not promoted", file=sys.stderr)
        sys.exit(1)
    model.save(model_dir, {"gate": gate.as_dict(), "data_until": str(wide.index.max())})
    print(f"promoted model to {model_dir}")


def cmd_forecast(args: argparse.Namespace) -> None:
    from hawa.forecast import forecast
    from hawa.models import Forecaster

    wide, _ = data.load_clean()
    model = Forecaster.load(config.ARTIFACT_DIR / "model")
    at = pd.Timestamp(args.at) if args.at else wide.index.max()
    for fc in forecast(wide.loc[:at], model, at):
        d = fc.as_dict()
        d["hourly"] = d["hourly"][:6] + [f"... {len(d['hourly']) - 6} more hours"]
        print(json.dumps(d, ensure_ascii=False, indent=2))


def cmd_replay(args: argparse.Namespace) -> None:
    from hawa.models import Forecaster
    from hawa.pipeline import replay
    from hawa.store import LocalStore

    wide, _ = data.load_clean()
    model = Forecaster.load(config.ARTIFACT_DIR / "model")
    out = config.ARTIFACT_DIR / "reports" / f"replay_{args.start[:10]}_{args.end[:10]}.csv"
    log = replay(wide, model, LocalStore(), args.start, args.end, out)
    print(log.groupby(["station", "mode", "exceeds_standard"]).size().to_string())
    print(f"wrote {out}")


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("hawa.api:app", host=args.host, port=args.port)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    p = argparse.ArgumentParser(prog="hawa", description="Kathmandu Valley PM2.5 early warning")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("download", help="fetch raw DoE files and verify checksums").set_defaults(fn=cmd_download)
    sub.add_parser("quality", help="data quality report").set_defaults(fn=cmd_quality)
    sub.add_parser("backtest", help="walk-forward backtest, writes artifacts/reports").set_defaults(fn=cmd_backtest)
    t = sub.add_parser("train", help="train with the promotion gate")
    t.add_argument("--until", help="only use data up to this time (simulates an earlier retrain)")
    t.add_argument("--force", action="store_true", help="ignore the current champion")
    t.set_defaults(fn=cmd_train)
    f = sub.add_parser("forecast", help="one forecast at a given hour")
    f.add_argument("--at")
    f.set_defaults(fn=cmd_forecast)
    r = sub.add_parser("replay", help="run the hourly job over a past period")
    r.add_argument("--start", required=True)
    r.add_argument("--end", required=True)
    r.set_defaults(fn=cmd_replay)
    s = sub.add_parser("serve", help="run the HTTP API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
