"""Post-hoc metrics for Task 23-RERUN-R1 REDUCED ensemble runs.

Run after all eight backtests complete. Writes
docs/notes/_artifacts/task23_reduced_v1/metrics_summary.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.metrics import (
    MetricsError,
    attach_metric_cis,
    compute_metric_suite,
    crps_gaussian,
    log_loss,
    mae,
    report_a2_components_by_basis,
    weekly_error_curve,
)
from ncaa_quant.utils.seeding import set_global_seed

ROOT = Path("data/backtests")
OUT = Path("docs/notes/_artifacts/task23_reduced_v1")
ENSEMBLE_SCOPE = "REDUCED_PER_ADR_0013"
COMPOSITION = (
    "margin: LGBM+ENet NNLS; total: single-LGBM stub; "
    "quantile: margin only; MC + epistemic active"
)

RUNS = [
    ("task23_fundamental_reduced_v1", "full", "fundamental"),
    ("task23_market_aware_reduced_v1", "full", "market_aware"),
    ("task23_a1_reduced_v1", "A1_league_mean", "fundamental"),
    ("task23_a2_reduced_v1", "A2_frozen_after_week_1", "fundamental"),
    ("task23_a3_reduced_v1", "A3_market_off", "market_aware"),
    ("task23_a4_reduced_v1", "A4_single_lgbm", "fundamental"),
    ("task23_a5_reduced_v1", "A5_gt_off", "fundamental"),
    ("task23_a6_reduced_v1", "A6_cfbd_open_close", "market_aware"),
]


def _stamp_manifest(path: Path) -> None:
    m = json.loads(path.read_text(encoding="utf-8"))
    extra = m.setdefault("extra", {})
    extra["ensemble_scope"] = ENSEMBLE_SCOPE
    extra["ensemble_composition"] = COMPOSITION
    path.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")


def _headline(preds: pd.DataFrame) -> pd.DataFrame:
    if "exclude_from_headline" in preds.columns:
        return preds.loc[~preds["exclude_from_headline"].astype(bool)].copy()
    return preds.copy()


def _regime_split(preds: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Split CFBD-only 2019 vs snapshot-backed 2021–2024; never pool."""
    out: dict[str, pd.DataFrame] = {}
    if "season" not in preds.columns:
        return out
    cfbd = preds.loc[preds["season"].astype(int) == 2019]
    snap = preds.loc[preds["season"].astype(int).isin([2021, 2022, 2023, 2024])]
    if not cfbd.empty:
        out["cfbd_2019"] = cfbd
    if not snap.empty:
        out["snapshots_2021_2024"] = snap
    return out


def _ats_mask(df: pd.DataFrame) -> pd.Series:
    return (
        df["spread_close"].notna()
        & df["p_ats_home"].notna()
        & df["realized_margin"].notna()
        if {"spread_close", "p_ats_home", "realized_margin"}.issubset(df.columns)
        else pd.Series(False, index=df.index)
    )


def _ou_mask(df: pd.DataFrame) -> pd.Series:
    return (
        df["total_close"].notna()
        & df["p_ou_over"].notna()
        & df["realized_total"].notna()
        if {"total_close", "p_ou_over", "realized_total"}.issubset(df.columns)
        else pd.Series(False, index=df.index)
    )


def _cover_hit(df: pd.DataFrame) -> np.ndarray:
    # Home covers when realized_margin + spread_close > 0 (spread is home perspective).
    margin = df["realized_margin"].to_numpy(dtype=float)
    spread = df["spread_close"].to_numpy(dtype=float)
    p = df["p_ats_home"].to_numpy(dtype=float)
    # Model picks home when p > 0.5
    pick_home = p > 0.5
    home_covers = (margin + spread) > 0
    push = np.isclose(margin + spread, 0.0)
    hits = np.where(push, np.nan, np.where(pick_home, home_covers, ~home_covers)).astype(float)
    return hits


def _ou_hit(df: pd.DataFrame) -> np.ndarray:
    total = df["realized_total"].to_numpy(dtype=float)
    line = df["total_close"].to_numpy(dtype=float)
    p = df["p_ou_over"].to_numpy(dtype=float)
    pick_over = p > 0.5
    over = total > line
    push = np.isclose(total, line)
    hits = np.where(push, np.nan, np.where(pick_over, over, ~over)).astype(float)
    return hits


def _ci_block(hits: np.ndarray, weeks: list[Any], *, seed: int) -> dict[str, Any]:
    from ncaa_quant.evaluation.metrics import naive_proportion_ci, rate_ci_block

    mask = np.isfinite(hits)
    h = hits[mask]
    w = [weeks[i] for i, m in enumerate(mask) if m]
    if h.size < 2:
        return {"n": int(h.size), "rate": float(np.nanmean(h)) if h.size else float("nan")}
    boot = rate_ci_block(h, w, n_boot=1000, alpha=0.05, seed=seed, label="rate")
    naive = naive_proportion_ci(h, label="rate (naive)", alpha=0.05)
    return {
        "n": int(h.size),
        "rate": float(np.mean(h)),
        "bootstrap": {
            "point": float(boot.rate),
            "lo": float(boot.ci_low),
            "hi": float(boot.ci_high),
        },
        "naive": {
            "point": float(naive.rate),
            "lo": float(naive.ci_low),
            "hi": float(naive.ci_high),
        },
    }


def _possessions_null_share(preds: pd.DataFrame) -> dict[str, Any]:
    """Infer expected_possessions null share by week-band (drives only staged for 2023).

    Explicit is_missing column is absent from the provider; values are NaN until
    a PIT artifact exists and the game has pace inputs. Only 2023 drives are
    staged, so non-nulls can only appear for 2023 week>=5 games in the training
    frame. Report structural null shares for OU caveats.
    """
    rows: list[dict[str, Any]] = []
    for season in sorted({int(s) for s in preds["season"]}):
        sub = preds.loc[preds["season"].astype(int) == season]
        for band, mask in (
            ("weeks_1_4", sub["week"].astype(int) <= 4),
            ("weeks_5_plus", sub["week"].astype(int) >= 5),
        ):
            part = sub.loc[mask]
            n = int(len(part))
            # Structural: only 2023 w>=5 can be non-null given staged drives.
            if season == 2023 and band == "weeks_5_plus":
                # Upper bound on non-null: games that could have been in training.
                structural_null = "partial — only 2023 drives staged; week>=5 after retrain"
                null_share_structural = None
            else:
                structural_null = "all null (no drives staged / before first PIT retrain)"
                null_share_structural = 1.0
            rows.append(
                {
                    "season": season,
                    "week_band": band,
                    "n": n,
                    "null_share_structural": null_share_structural,
                    "note": structural_null,
                }
            )
    return {
        "explicit_is_missing_column": False,
        "zero_filled": False,
        "lgbm_native_nan": True,
        "drives_staged_seasons": [2023],
        "by_season_week_band": rows,
    }


def score_run(run_id: str, ablation_id: str, stack: str) -> dict[str, Any]:
    d = ROOT / run_id / ablation_id
    preds_path = d / "predictions.parquet"
    man_path = d / "manifest.json"
    if not preds_path.is_file():
        return {"run_id": run_id, "status": "MISSING", "ensemble_scope": ENSEMBLE_SCOPE}
    _stamp_manifest(man_path)
    man = json.loads(man_path.read_text(encoding="utf-8"))
    preds = pd.read_parquet(preds_path)
    head = _headline(preds)
    set_global_seed(42)

    regimes = _regime_split(head)
    regime_metrics: dict[str, Any] = {}
    for name, rdf in regimes.items():
        entry: dict[str, Any] = {"n": int(len(rdf))}
        ats = rdf.loc[_ats_mask(rdf)]
        if not ats.empty:
            hits = _cover_hit(ats)
            weeks = list(ats["week"].tolist())
            entry["ats"] = _ci_block(hits, weeks, seed=11)
        ou = rdf.loc[_ou_mask(rdf)]
        if not ou.empty:
            hits = _ou_hit(ou)
            weeks = list(ou["week"].tolist())
            entry["ou"] = _ci_block(hits, weeks, seed=22)
        # CRPS / log-loss vs market when probs present
        if {"realized_margin", "pred_margin", "sigma_m"}.issubset(rdf.columns):
            m = rdf.dropna(subset=["realized_margin", "pred_margin", "sigma_m"])
            if len(m) >= 10:
                entry["crps_margin"] = float(
                    crps_gaussian(
                        m["realized_margin"].to_numpy(dtype=float),
                        m["pred_margin"].to_numpy(dtype=float),
                        m["sigma_m"].to_numpy(dtype=float),
                    )
                )
                entry["mae_margin"] = float(
                    mae(
                        m["realized_margin"].to_numpy(dtype=float),
                        m["pred_margin"].to_numpy(dtype=float),
                    )
                )
        if {"realized_total", "pred_total", "sigma_t"}.issubset(rdf.columns):
            t = rdf.dropna(subset=["realized_total", "pred_total", "sigma_t"])
            if len(t) >= 10:
                entry["crps_total"] = float(
                    crps_gaussian(
                        t["realized_total"].to_numpy(dtype=float),
                        t["pred_total"].to_numpy(dtype=float),
                        t["sigma_t"].to_numpy(dtype=float),
                    )
                )
                entry["mae_total"] = float(
                    mae(
                        t["realized_total"].to_numpy(dtype=float),
                        t["pred_total"].to_numpy(dtype=float),
                    )
                )
        if {"p_ats_home", "realized_margin", "spread_close"}.issubset(rdf.columns):
            a = rdf.loc[_ats_mask(rdf)]
            if len(a) >= 10:
                margin = a["realized_margin"].to_numpy(dtype=float)
                spread = a["spread_close"].to_numpy(dtype=float)
                y = ((margin + spread) > 0).astype(float)
                push = np.isclose(margin + spread, 0.0)
                y = y[~push]
                p = a["p_ats_home"].to_numpy(dtype=float)[~push]
                if len(y) >= 10:
                    entry["logloss_ats"] = float(log_loss(y, p))
        regime_metrics[name] = entry

    a2 = [
        {
            "metric": r.metric,
            "value": r.value,
            "seasons": list(r.seasons),
            "n": r.n,
            "basis": r.basis,
        }
        for r in report_a2_components_by_basis(head)
    ]

    curve = weekly_error_curve(head, target="margin")
    week4 = float(curve.loc[curve["week"] == 4, "mae"].mean()) if (curve["week"] == 4).any() else float("nan")
    week10 = (
        float(curve.loc[curve["week"] == 10, "mae"].mean()) if (curve["week"] == 10).any() else float("nan")
    )

    clv_status = "NOT COMPUTED"
    clv_reason = "no bets.parquet / settle path in this runner output"
    bets_path = d / "bets.parquet"
    if bets_path.is_file():
        try:
            bets = pd.read_parquet(bets_path)
            suite = compute_metric_suite(head, bets=bets)
            cis = attach_metric_cis(suite, head, bets=bets)
            clv_status = "COMPUTED"
            clv_reason = ""
            clv_payload = {
                "mean_clv": suite.mean_clv,
                "n_clv": suite.n_clv,
                "cis": {
                    k: {
                        "point": float(getattr(v, "rate", getattr(v, "estimate", float("nan")))),
                        "lo": float(getattr(v, "ci_low", float("nan"))),
                        "hi": float(getattr(v, "ci_high", float("nan"))),
                    }
                    for k, v in cis.items()
                },
            }
        except MetricsError as exc:
            clv_status = "NOT COMPUTED"
            clv_reason = str(exc)
            clv_payload = None
    else:
        clv_payload = None

    return {
        "run_id": run_id,
        "ablation_id": ablation_id,
        "stack": stack,
        "status": "OK",
        "ensemble_scope": ENSEMBLE_SCOPE,
        "ensemble_composition": COMPOSITION,
        "n_predictions": int(len(preds)),
        "n_headline": int(len(head)),
        "wall_clock_sec": man.get("extra", {}).get("wall_clock_sec"),
        "label": man.get("extra", {}).get("label"),
        "regimes": regime_metrics,
        "a2_components_by_basis": a2,
        "weekly_mae_week4": week4,
        "weekly_mae_week10": week10,
        "weekly_mae_week10_minus_week4": (
            week10 - week4 if np.isfinite(week10) and np.isfinite(week4) else float("nan")
        ),
        "clv_status": clv_status,
        "clv_reason": clv_reason,
        "clv": clv_payload,
        "possessions_null": _possessions_null_share(head),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results = [score_run(r, a, s) for r, a, s in RUNS]
    # Ablation deltas vs fundamental full
    by_id = {r["run_id"]: r for r in results}
    fund = by_id.get("task23_fundamental_reduced_v1")
    deltas: dict[str, Any] = {}
    if fund and fund.get("status") == "OK":

        def _mae(r: dict[str, Any]) -> float | None:
            # Prefer snapshots regime MAE if present else all-season a2
            for rec in r.get("a2_components_by_basis") or []:
                if rec["metric"] == "mae_margin" and rec["basis"] == "all_seasons":
                    return float(rec["value"])
            return None

        base_mae = _mae(fund)
        for rid, label in (
            ("task23_a1_reduced_v1", "A1"),
            ("task23_a2_reduced_v1", "A2"),
            ("task23_a4_reduced_v1", "A4"),
            ("task23_a5_reduced_v1", "A5"),
        ):
            other = by_id.get(rid)
            if not other or other.get("status") != "OK" or base_mae is None:
                continue
            om = _mae(other)
            if om is None:
                continue
            deltas[label] = {
                "base_mae_margin_all_seasons": base_mae,
                "ablation_mae_margin_all_seasons": om,
                "delta_ablation_minus_base": om - base_mae,
                "ensemble_scope": ENSEMBLE_SCOPE,
            }
        # A3 vs market-aware
        mkt = by_id.get("task23_market_aware_reduced_v1")
        a3 = by_id.get("task23_a3_reduced_v1")
        if mkt and a3 and mkt.get("status") == "OK" and a3.get("status") == "OK":
            bm, am = _mae(mkt), _mae(a3)
            if bm is not None and am is not None:
                deltas["A3"] = {
                    "base_mae_margin_all_seasons": bm,
                    "ablation_mae_margin_all_seasons": am,
                    "delta_ablation_minus_base": am - bm,
                    "base": "market_aware_full",
                    "ensemble_scope": ENSEMBLE_SCOPE,
                }
        a6 = by_id.get("task23_a6_reduced_v1")
        if mkt and a6 and mkt.get("status") == "OK" and a6.get("status") == "OK":
            # Compare snapshot-regime ATS if available
            mkt_ats = (mkt.get("regimes") or {}).get("snapshots_2021_2024", {}).get("ats")
            a6_ats = (a6.get("regimes") or {}).get("snapshots_2021_2024", {}).get("ats")
            deltas["A6"] = {
                "market_aware_ats": mkt_ats,
                "a6_ats": a6_ats,
                "note": "A6 is cfbd_open_close on 2021-2024 only; compare ATS rates, not pooled",
                "ensemble_scope": ENSEMBLE_SCOPE,
            }
        # A4 framing: reduced-ensemble increment over single LGBM, margin vs total
        a4 = by_id.get("task23_a4_reduced_v1")
        if a4 and a4.get("status") == "OK":
            def _total_mae(r: dict[str, Any]) -> float | None:
                for name, block in (r.get("regimes") or {}).items():
                    if "mae_total" in block:
                        return float(block["mae_total"])
                return None

            deltas["A4_framing"] = {
                "margin": {
                    "reduced_ensemble_mae": base_mae,
                    "single_lgbm_mae": _mae(a4),
                    "increment_reduced_minus_single": (
                        None
                        if base_mae is None or _mae(a4) is None
                        else float(base_mae) - float(_mae(a4))
                    ),
                    "note": "reduced-ensemble increment over single LGBM (margin: LGBM+ENet vs LGBM)",
                },
                "total": {
                    "reduced_ensemble_mae": _total_mae(fund),
                    "single_lgbm_mae": _total_mae(a4),
                    "note": (
                        "total-side A4 is stub-vs-single and measures nothing about §5.2 ensembling"
                    ),
                },
                "ensemble_scope": ENSEMBLE_SCOPE,
            }

    # Determinism: re-read fundamental preds hash
    fund_path = ROOT / "task23_fundamental_reduced_v1" / "full" / "predictions.parquet"
    det = None
    if fund_path.is_file():
        import hashlib

        det = {
            "path": str(fund_path),
            "sha256": hashlib.sha256(fund_path.read_bytes()).hexdigest(),
            "note": "byte hash of first completed run; second pass not re-executed this session",
        }

    payload = {
        "ensemble_scope": ENSEMBLE_SCOPE,
        "ensemble_composition": COMPOSITION,
        "runs": results,
        "ablation_deltas": deltas,
        "determinism": det,
    }
    out = OUT / "metrics_summary.json"
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print("wrote", out)
    print("runs", [(r["run_id"], r["status"]) for r in results])


if __name__ == "__main__":
    main()
