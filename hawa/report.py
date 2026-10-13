"""Backtest report: metrics JSON + the figures used in the README."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from hawa import backtest as B  # noqa: E402
from hawa import config  # noqa: E402
from hawa import features as F  # noqa: E402
from hawa.models import Forecaster  # noqa: E402

INK, MUTED, GRID = "#1f2937", "#6b7280", "#e5e7eb"
COLORS = {"lgbm_p50": "#2563eb", "weekly_profile": "#9ca3af", "same_hour_yesterday": "#d1d5db",
          "persistence": "#f59e0b", "ridge": "#10b981", "cross_station": "#a855f7"}
LABELS = {"lgbm_p50": "LightGBM (ours)", "weekly_profile": "7-day same-hour average",
          "same_hour_yesterday": "same hour yesterday", "persistence": "latest hour",
          "ridge": "ridge regression", "cross_station": "neighbour-only fallback"}


def _style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(GRID)
    ax.tick_params(colors=MUTED)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _fig_mae_by_horizon(res: pd.DataFrame, path: Path) -> None:
    m = B.hourly_metrics(res, "horizon")
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    for name in ["persistence", "same_hour_yesterday", "weekly_profile", "ridge", "lgbm_p50"]:
        lw = 2.6 if name == "lgbm_p50" else 1.6
        ax.plot(m.index, m[f"{name}_mae"], color=COLORS[name], lw=lw, label=LABELS[name])
    _style(ax)
    ax.set_xlabel("hours ahead", color=MUTED)
    ax.set_ylabel("mean absolute error (µg/m³)", color=MUTED)
    ax.set_title("Forecast error by horizon, Mar-Dec 2017 walk-forward", color=INK, loc="left")
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_example_week(wide: pd.DataFrame, res: pd.DataFrame, path: Path) -> None:
    """A week of 24h-ahead forecasts against what happened (Ratnapark, December 2017)."""
    r = res[(res["station"] == "ratnapark") & (res["horizon"] == 24)].set_index("target_time").sort_index()
    w = r.loc["2017-12-10":"2017-12-17"]
    fig, ax = plt.subplots(figsize=(9, 3.8), dpi=150)
    ax.fill_between(w.index, w["lgbm_p50"], w["lgbm_p90"], color=COLORS["lgbm_p50"], alpha=0.15,
                    label="p50 to p90 band", lw=0)
    ax.plot(w.index, w["target"], color=INK, lw=1.4, label="observed")
    ax.plot(w.index, w["lgbm_p50"], color=COLORS["lgbm_p50"], lw=1.8, label="forecast made 24h earlier")
    ax.axhline(config.NEPAL_PM25_24H_STANDARD, color="#dc2626", lw=1, ls="--")
    ax.text(w.index[2], config.NEPAL_PM25_24H_STANDARD + 3, "Nepal 24h standard (40)", color="#dc2626", fontsize=8)
    _style(ax)
    ax.set_ylabel("PM2.5 (µg/m³)", color=MUTED)
    ax.set_title("Ratnapark, 10-17 Dec 2017: hourly PM2.5 forecast a day ahead", color=INK, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_importance(wide: pd.DataFrame, path: Path) -> None:
    model = Forecaster.fit(F.build(wide.loc[:"2017-11-30"]))
    imp = model.importance().head(12)[::-1]
    imp = imp / imp.sum()
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=150)
    ax.barh(imp.index, imp.values, color=COLORS["lgbm_p50"])
    _style(ax)
    ax.xaxis.grid(True, color=GRID)
    ax.yaxis.grid(False)
    ax.set_xlabel("share of total gain (top 12 features)", color=MUTED)
    ax.set_title("What the model leans on", color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def write_backtest_report(wide: pd.DataFrame, out_dir: Path, img_dir: Path | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir = img_dir or (config.ROOT / "docs" / "img")
    img_dir.mkdir(parents=True, exist_ok=True)

    res = B.run(wide)
    res.to_parquet(out_dir / "backtest_predictions.parquet")
    overall = B.hourly_metrics(res).iloc[0]
    by_month = B.hourly_metrics(res, "month")
    by_h = B.hourly_metrics(res, "horizon")
    daily = B.daily_forecasts(res)
    alerts = B.alert_metrics(daily)
    outage = B.outage_experiment(wide)

    summary = {
        "hourly_mae": {m: round(float(overall[f"{m}_mae"]), 2) for m in B.HOURLY_MODELS},
        "hourly_rows": int(overall["n"]),
        "skill_vs_best_baseline": round(float(overall["skill_vs_best_baseline"]), 3),
        "p90_coverage": round(B.p90_coverage(res), 3),
        "mae_by_month": by_month[[f"{m}_mae" for m in B.HOURLY_MODELS]].round(2).to_dict(orient="index"),
        "mae_by_horizon": by_h[["persistence_mae", "weekly_profile_mae", "lgbm_p50_mae"]].round(2)
        .loc[[1, 3, 6, 12, 24]].to_dict(orient="index"),
        "daily_alert": {"exceedances": alerts.attrs["exceedances"], "forecasts": alerts.attrs["forecasts"],
                        **{k: {kk: round(float(vv), 3) for kk, vv in v.items()}
                           for k, v in alerts.to_dict(orient="index").items()}},
        "sensor_outage_experiment": outage.round(2).to_dict(orient="records"),
    }
    (out_dir / "backtest_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    _fig_mae_by_horizon(res, img_dir / "mae_by_horizon.png")
    _fig_example_week(wide, res, img_dir / "example_week.png")
    _fig_importance(wide, img_dir / "feature_importance.png")
    return summary
