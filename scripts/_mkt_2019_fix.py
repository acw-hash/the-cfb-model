"""MKT-2019-FIX — archive, regime metrics, equivalence, blast-radius ledger.

Does not tune. Does not widen the ATS guard band. Does not touch the lockbox.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.metrics import (
    AtsPlausibilityError,
    assert_prediction_ats_plausible,
    attach_metric_cis,
    ats_home_outcomes,
    ats_plausibility_band,
    binary_accuracy,
    compute_metric_suite,
    crps_gaussian,
    log_loss,
    mae,
)

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "docs" / "notes" / "_artifacts" / "mkt_2019_fix"
PRED_TUE = (
    ROOT / "data" / "backtests" / "task23_market_aware_reduced_v2_tue" / "full" / "predictions.parquet"
)
PRED_ARCHIVED = ART / "contaminated_tue_predictions.parquet"
A3_PRED = ROOT / "data" / "backtests" / "task23_a3_reduced_v2" / "A3_market_off" / "predictions.parquet"
FUND_PRED = (
    ROOT / "data" / "backtests" / "task23_fundamental_reduced_v2" / "full" / "predictions.parquet"
)
SEED = 42
VINTAGE = "RERUN_V2_MKT_2019_FIX"
SCOPE = "REDUCED_PER_ADR_0013"
FEATURE_TIME = "TUESDAY_DECISION"

# Published (struck) market-aware 2019 numbers.
STRUCK_2019: list[dict[str, Any]] = [
    {
        "label": "Tuesday-decision market-aware 2019 ATS",
        "value": "45.63%",
        "ci": "[42.9%, 48.6%]",
        "n": 743,
        "ll": 0.835,
        "mae": 14.59,
        "source": "docs/notes/week-align-fix.md",
        "run_id": "task23_market_aware_reduced_v2_tue",
        "feature_time": "TUESDAY_DECISION",
        "reason": "CONTAMINATED_2019_FEATURE_SOURCE",
    },
    {
        "label": "kick−5min market-aware 2019 ATS",
        "value": "47.51%",
        "ci": "[44.5%, 50.8%]",
        "n": 743,
        "ll": 0.961,
        "mae": 15.11,
        "source": "docs/notes/mkt-asof-fix.md",
        "run_id": "task23_market_aware_reduced_v2",
        "feature_time": "SLOT_CLOSE_KICK_MINUS_5M",
        "reason": "CONTAMINATED_2019_FEATURE_SOURCE",
    },
    {
        "label": "v1 market-aware 2019 ATS (ancestor)",
        "value": "CONTAMINATED_v1 (see 23-rerun-r1.md)",
        "source": "docs/notes/23-rerun-r1.md",
        "run_id": "task23_market_aware_reduced_v1",
        "reason": "CONTAMINATED_2019_FEATURE_SOURCE",
        "note": "v1 also used CFBD close as 2019 snapshot-config features; additionally CONTAMINATED_v1 on snapshot ATS grading.",
    },
]

SUPERSEDED_2021_2024_TUESDAY: dict[str, Any] = {
    "label": "Tuesday-decision snapshot 2021–2024 (prior run)",
    "ats": "51.42%",
    "n": 3491,
    "ll": 0.812,
    "mae": 14.32,
    "crps": 10.26,
    "ci": "[49.3%, 53.6%]",
    "source": "docs/notes/week-align-fix.md",
    "run_id": "task23_market_aware_reduced_v2_tue",
    "feature_time": "TUESDAY_DECISION",
    "disposition": "superseded-by-retrain",
    "note": "Odds-backed; not in the 2019 feature-source blast radius. Retrain required because 2019 null features change the expanding-window fit.",
}


def _headline(preds: pd.DataFrame) -> pd.DataFrame:
    if "exclude_from_headline" in preds.columns:
        return preds.loc[~preds["exclude_from_headline"].fillna(False).astype(bool)].copy()
    return preds.copy()


def regime_metrics(preds: pd.DataFrame, regime: str, mask: pd.Series) -> dict[str, Any] | None:
    sub = preds.loc[mask].copy()
    if sub.empty:
        return None
    suite = compute_metric_suite(sub)
    cis = attach_metric_cis(suite, sub, seed=SEED)
    ats_ci = cis.get("ats_accuracy")
    y = ats_home_outcomes(
        sub["realized_margin"].to_numpy(dtype=float),
        sub["spread_close"].to_numpy(dtype=float),
    )
    p = sub["p_ats_home"].to_numpy(dtype=float)
    ok = np.isfinite(y) & np.isfinite(p)
    rate = binary_accuracy(p, y) if np.any(ok) else float("nan")
    ll = log_loss(p[ok], y[ok]) if np.any(ok) else float("nan")
    mu = sub["pred_margin"].to_numpy(dtype=float)
    rm = sub["realized_margin"].to_numpy(dtype=float)
    ok_m = np.isfinite(mu) & np.isfinite(rm)
    mae_m = mae(rm[ok_m], mu[ok_m]) if np.any(ok_m) else float("nan")
    if "sigma_m" in sub.columns:
        sig = sub["sigma_m"].to_numpy(dtype=float)
        ok_c = ok_m & np.isfinite(sig) & (sig > 0)
        crps_m = crps_gaussian(rm[ok_c], mu[ok_c], sig[ok_c]) if np.any(ok_c) else float("nan")
    else:
        crps_m = float("nan")
    n = int(ok.sum())
    lo, hi = ats_plausibility_band(n)
    return {
        "regime": regime,
        "n": n,
        "ats": float(rate),
        "ats_pct": round(100.0 * float(rate), 2) if np.isfinite(rate) else None,
        "logloss_model": float(ll),
        "mae_margin": float(mae_m),
        "crps_margin": float(crps_m),
        "bootstrap_lo": float(ats_ci.ci_low) if ats_ci is not None else float("nan"),
        "bootstrap_hi": float(ats_ci.ci_high) if ats_ci is not None else float("nan"),
        "guard_lo": float(lo),
        "guard_hi": float(hi),
        "inside_guard": bool(np.isfinite(rate) and lo <= float(rate) <= hi),
    }


def summarize_run(preds: pd.DataFrame) -> dict[str, Any]:
    h = _headline(preds)
    regimes = []
    for label, mask in (
        ("cfbd_2019", h["season"].astype(int) == 2019),
        ("snapshots_2021_2024", h["season"].astype(int).between(2021, 2024)),
    ):
        r = regime_metrics(h, label, mask)
        if r is not None:
            regimes.append(r)
    return {
        "vintage": VINTAGE,
        "ensemble_scope": SCOPE,
        "feature_time": FEATURE_TIME,
        "run_id": "task23_market_aware_reduced_v2_tue",
        "n_predictions": int(len(preds)),
        "n_headline": int(len(h)),
        "regimes": regimes,
    }


def archive_contaminated() -> dict[str, Any]:
    ART.mkdir(parents=True, exist_ok=True)
    if not PRED_TUE.is_file():
        raise FileNotFoundError(PRED_TUE)
    if not PRED_ARCHIVED.is_file():
        shutil.copy2(PRED_TUE, PRED_ARCHIVED)
    preds = pd.read_parquet(PRED_ARCHIVED, columns=["season", "game_id"])
    n_2019 = int((preds["season"].astype(int) == 2019).sum())
    return {
        "archived_to": str(PRED_ARCHIVED.relative_to(ROOT)).replace("\\", "/"),
        "n_rows": int(len(preds)),
        "n_2019": n_2019,
    }


def guard_disposition(preds: pd.DataFrame) -> dict[str, Any]:
    try:
        assert_prediction_ats_plausible(preds)
        return {"disposition": "INSIDE_BAND_PUBLISHED", "error": None}
    except AtsPlausibilityError as exc:
        return {"disposition": "GUARD_TRIP_REFUSED", "error": str(exc)}


def vs_table(
    aware: dict[str, Any],
    other: dict[str, Any],
    other_name: str,
) -> list[dict[str, Any]]:
    by_a = {r["regime"]: r for r in aware.get("regimes", [])}
    by_o = {r["regime"]: r for r in other.get("regimes", [])}
    rows = []
    for regime in ("cfbd_2019", "snapshots_2021_2024"):
        a = by_a.get(regime)
        o = by_o.get(regime)
        if a is None or o is None:
            continue
        rows.append(
            {
                "regime": regime,
                "aware_ats": a["ats"],
                f"{other_name}_ats": o["ats"],
                "d_ats_pp": round(100.0 * (a["ats"] - o["ats"]), 2),
                "aware_mae": a["mae_margin"],
                f"{other_name}_mae": o["mae_margin"],
                "d_mae": round(a["mae_margin"] - o["mae_margin"], 2),
                "aware_n": a["n"],
                f"{other_name}_n": o["n"],
            }
        )
    return rows


def step_metrics() -> dict[str, Any]:
    ART.mkdir(parents=True, exist_ok=True)
    if not PRED_TUE.is_file():
        raise FileNotFoundError(PRED_TUE)
    preds = pd.read_parquet(PRED_TUE)
    summary = summarize_run(preds)
    guard = guard_disposition(preds)
    summary["guard"] = guard
    out: dict[str, Any] = {"aware": summary, "guard": guard}
    if A3_PRED.is_file():
        a3 = summarize_run(pd.read_parquet(A3_PRED))
        a3["run_id"] = "task23_a3_reduced_v2"
        out["a3"] = a3
        out["vs_a3"] = vs_table(summary, a3, "a3")
    if FUND_PRED.is_file():
        fund = summarize_run(pd.read_parquet(FUND_PRED))
        fund["run_id"] = "task23_fundamental_reduced_v2"
        out["fundamental_v2"] = fund
        out["vs_fundamental_v2"] = vs_table(summary, fund, "fund")
    (ART / "step4_rerun_metrics.json").write_text(
        json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return out


def step_equivalence() -> dict[str, Any]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_readout_addendum_check", ROOT / "scripts" / "_readout_addendum_check.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load scripts/_readout_addendum_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    report = mod.check_2019_mkt_equivalence()
    (ART / "2019_mkt_equivalence.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return report


def step_ledger() -> dict[str, Any]:
    ART.mkdir(parents=True, exist_ok=True)
    ledger = {
        "reason_code": "CONTAMINATED_2019_FEATURE_SOURCE",
        "struck_2019_market_aware": STRUCK_2019,
        "superseded_not_deleted_2021_2024_tuesday": SUPERSEDED_2021_2024_TUESDAY,
        "note": (
            "Struck numbers are retained in source memos. Do not delete. "
            "2021–2024 Tuesday snapshot figures were Odds-backed and are "
            "superseded-by-retrain, not in the 2019 feature-source blast radius."
        ),
    }
    (ART / "blast_radius_ledger.json").write_text(
        json.dumps(ledger, indent=2) + "\n", encoding="utf-8"
    )
    return ledger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=("archive", "metrics", "equivalence", "ledger", "all"),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.phase in {"archive", "all"}:
        print(json.dumps({"archive": archive_contaminated()}, indent=2))
    if args.phase in {"ledger", "all"}:
        print(json.dumps({"ledger": step_ledger()}, indent=2))
    if args.phase in {"metrics", "all"}:
        print(json.dumps({"metrics": step_metrics()}, indent=2, default=str))
    if args.phase in {"equivalence", "all"}:
        report = step_equivalence()
        print(json.dumps({k: report[k] for k in (
            "disposition", "ok", "n_2019_prediction_rows", "n_violations",
            "n_mkt_is_missing", "provenance_counts", "line_source_counts",
            "noise_claim_licensed",
        )}, indent=2, default=str))
        if not report["ok"]:
            print("STOP: 2019 mkt_* not null+is_missing after re-run.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
