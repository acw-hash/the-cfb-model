"""W9-A — regrade new v3/A2 predictions and emit metric tables.

Points at ``task23_fundamental_reduced_v3`` and ``task23_a2_reduced_v2`` only.
Never writes under ``data/backtests/task23_fundamental_reduced_v2/``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.metrics import (
    AtsPlausibilityError,
    attach_metric_cis,
    ats_home_outcomes,
    binary_accuracy,
    compute_metric_suite,
    log_loss,
    naive_proportion_ci,
    ou_over_outcomes,
    rate_ci_block,
    report_a2_components_by_basis,
    weekly_error_curve,
)
from ncaa_quant.evaluation.walkforward import WalkForwardConfig
from ncaa_quant.registry.champion_serialize import hash_isolation_paths, isolation_state_paths
from ncaa_quant.registry.w9a_revalidate import (
    ADR_0014_OOD_BLOCKS,
    CHAMPION3_ROOT,
    hash_tree,
    n_season,
    ungradable_blocks,
)

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "data" / "staged"
BACKTESTS = ROOT / "data" / "backtests"
OUT = ROOT / "docs" / "notes" / "_artifacts" / "webapp-w9a"

RUNS: tuple[tuple[str, str, str], ...] = (
    ("task23_fundamental_reduced_v3", "full", "fundamental"),
    ("task23_a2_reduced_v2", "A2_frozen_after_week_1", "a2"),
)
GRADE_SEASONS = [2019, 2021, 2022, 2023, 2024]
SNAP_SEASONS = [2021, 2022, 2023, 2024]


def _load_regrade() -> Any:
    path = ROOT / "scripts" / "_ats_regrade.py"
    spec = importlib.util.spec_from_file_location("ats_regrade_w9a", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ats_regrade_w9a"] = mod
    spec.loader.exec_module(mod)
    return mod


def _headline(preds: pd.DataFrame) -> pd.DataFrame:
    if "exclude_from_headline" in preds.columns:
        return preds.loc[~preds["exclude_from_headline"].fillna(False).astype(bool)].copy()
    return preds.copy()


def _ci_payload(obj: Any) -> dict[str, float] | None:
    if obj is None:
        return None
    return {
        "point": float(getattr(obj, "rate", getattr(obj, "estimate", float("nan")))),
        "lo": float(obj.ci_low),
        "hi": float(obj.ci_high),
    }


def _regime_ou(frame: pd.DataFrame, regime: str, mask: pd.Series) -> dict[str, Any] | None:
    sub = frame.loc[mask].copy()
    if sub.empty or not {"total_close", "p_ou_over", "realized_total", "week"} <= set(sub.columns):
        return None
    y = ou_over_outcomes(
        sub["realized_total"].to_numpy(dtype=float),
        sub["total_close"].to_numpy(dtype=float),
    )
    p = sub["p_ou_over"].to_numpy(dtype=float)
    mask_y = np.isfinite(y) & np.isfinite(p)
    if not np.any(mask_y):
        return None
    rate = binary_accuracy(p, y)
    weeks = [int(w) for w, keep in zip(sub["week"].tolist(), mask_y, strict=True) if keep]
    hits = ((p[mask_y] >= 0.5).astype(float) == y[mask_y]).astype(float)
    boot = rate_ci_block(hits, weeks, n_boot=1000, alpha=0.05, seed=23, label="OU")
    naive = naive_proportion_ci(hits, label="OU naive", alpha=0.05)
    return {
        "regime": regime,
        "n": int(mask_y.sum()),
        "ou": float(rate),
        "bootstrap_lo": float(boot.ci_low),
        "bootstrap_hi": float(boot.ci_high),
        "naive_lo": float(naive.ci_low),
        "naive_hi": float(naive.ci_high),
    }


def _pct(x: float) -> float:
    return float(round(100.0 * x, 1))


def measure(graded: pd.DataFrame, *, run_id: str, role: str, regrade: Any) -> dict[str, Any]:
    head = _headline(graded)
    test = head.loc[head["season"].astype(int).isin(GRADE_SEASONS)].copy()
    blocks = sorted(ungradable_blocks(graded))
    extra = set(blocks) - ADR_0014_OOD_BLOCKS
    suite = compute_metric_suite(head)
    cis = attach_metric_cis(suite, head, seed=23)
    a2_basis = [
        {
            "metric": rec.metric,
            "value": rec.value,
            "n": rec.n,
            "basis": rec.basis,
            "seasons": list(rec.seasons),
        }
        for rec in report_a2_components_by_basis(head)
    ]
    curve = weekly_error_curve(head, target="margin")
    week4 = (
        float(curve.loc[curve["week"] == 4, "mae"].mean()) if (curve["week"] == 4).any() else float("nan")
    )
    week10 = (
        float(curve.loc[curve["week"] == 10, "mae"].mean())
        if (curve["week"] == 10).any()
        else float("nan")
    )
    ats = []
    ou = []
    ll = []
    for label, mask in [
        ("cfbd_2019", head["season"].astype(int) == 2019),
        ("snapshots_2021_2024", head["season"].astype(int).isin(SNAP_SEASONS)),
    ]:
        r = regrade._regime_ats(head, label, mask)
        if r is not None:
            ats.append(asdict(r))
        o = _regime_ou(head, label, mask)
        if o is not None:
            ou.append(o)
        sub = head.loc[mask]
        if not sub.empty and {"p_ats_home", "spread_close", "realized_margin"} <= set(sub.columns):
            y = ats_home_outcomes(
                sub["realized_margin"].to_numpy(dtype=float),
                sub["spread_close"].to_numpy(dtype=float),
            )
            p = sub["p_ats_home"].to_numpy(dtype=float)
            m = np.isfinite(y) & np.isfinite(p)
            ll.append(
                {
                    "regime": label,
                    "n": int(m.sum()),
                    "logloss_model": float(log_loss(p[m], y[m])) if np.any(m) else float("nan"),
                    "logloss_market": float(np.log(2.0)),
                }
            )
    mae_all = next((x for x in a2_basis if x["metric"] == "mae_margin"), None)
    crps_all = next((x for x in a2_basis if x["metric"] == "crps_margin"), None)
    return {
        "run_id": run_id,
        "role": role,
        "n_predictions": int(len(graded)),
        "n_headline": int(len(head)),
        "n_test_seasons": int(len(test)),
        "n_2025": n_season(graded, 2025),
        "ungradable_blocks": [list(b) for b in blocks],
        "ungradable_extra_beyond_adr_0014": [list(b) for b in sorted(extra)],
        "suite": {
            "mae_margin": float(suite.mae_margin),
            "crps_margin": None if suite.crps_margin is None else float(suite.crps_margin.model),
            "ats_accuracy": float(suite.ats_accuracy),
            "ou_accuracy": float(suite.ou_accuracy),
            "logloss_ats": None if suite.logloss_ats is None else float(suite.logloss_ats.model),
            "n_games": int(suite.n_games),
        },
        "cis": {k: _ci_payload(v) for k, v in cis.items()},
        "a2_components_by_basis": a2_basis,
        "ats_regimes": ats,
        "ou_regimes": ou,
        "logloss_ats_regimes": ll,
        "mae_all_seasons": mae_all,
        "crps_all_seasons": crps_all,
        "weekly_mae": curve.to_dict(orient="records"),
        "weekly_mae_week4": week4,
        "weekly_mae_week10": week10,
        "weekly_mae_week10_minus_week4": (
            week10 - week4 if np.isfinite(week10) and np.isfinite(week4) else float("nan")
        ),
        "ats_pct": {r["regime"]: {"ats_pct": _pct(r["ats"]), "n": r["n"]} for r in ats},
        "ou_pct": {r["regime"]: {"ou_pct": _pct(r["ou"]), "n": r["n"]} for r in ou},
    }


def main() -> None:
    if CHAMPION3_ROOT.resolve() == (BACKTESTS / "task23_fundamental_reduced_v2").resolve():
        c3 = hash_tree(CHAMPION3_ROOT)
        print(f"champion3_hash_before_grade={c3}")
    regrade = _load_regrade()
    seasons = GRADE_SEASONS
    print("loading staged games/lines/snaps…")
    games = load_staged_games(STAGED, seasons)
    lines = regrade._read_hive(STAGED / "lines_historical", seasons)
    snaps = regrade._read_hive(STAGED / "odds_snapshots", SNAP_SEASONS)
    if "event_time" in snaps.columns:
        snaps["event_time"] = pd.to_datetime(snaps["event_time"], utc=True)
    if "event_time" in games.columns:
        games["event_time"] = pd.to_datetime(games["event_time"], utc=True)
    cfg = WalkForwardConfig()
    summary: dict[str, Any] = {"grade_version": "v2", "vintage": "W9A_CURRENT_CODE", "runs": {}}
    for run_id, subdir, role in RUNS:
        src = BACKTESTS / run_id / subdir / "predictions.parquet"
        if not src.is_file():
            raise FileNotFoundError(src)
        print(f"regrading {run_id}/{subdir} n_2025 check…")
        preds = pd.read_parquet(src)
        n25 = n_season(preds, 2025)
        print(f"N_2025={n25} path={src}")
        if n25 != 0:
            raise RuntimeError(f"N_2025={n25} in {src}")
        extra_blocks = set(ungradable_blocks(preds)) - ADR_0014_OOD_BLOCKS
        if extra_blocks:
            raise RuntimeError(f"{run_id} ungradable outside ADR 0014: {extra_blocks}")
        out_dir = BACKTESTS / run_id / subdir / "grade_v2"
        if CHAMPION3_ROOT.resolve() in out_dir.resolve().parents:
            raise RuntimeError("refusing to write grade into champion 3")
        graded_path = out_dir / "predictions.parquet"
        plausibility: dict[str, Any] | None = None
        if graded_path.is_file():
            print(f"reusing existing {graded_path}")
            graded = pd.read_parquet(graded_path)
        else:
            graded = regrade.regrade_predictions(
                preds, games=games, snapshots=snaps, cfbd_lines=lines, config=cfg
            )
            try:
                regrade.assert_prediction_ats_plausible(graded)
                plausibility = {"tripped": False}
            except AtsPlausibilityError as exc:
                # A2 frozen is expected worse; record the trip and still measure.
                plausibility = {"tripped": True, "message": str(exc)}
                print(f"W9-A ATS_PLAUSIBILITY recorded not fatal: {exc}")
            out_dir.mkdir(parents=True, exist_ok=True)
            graded.to_parquet(graded_path, index=False)
        measured = measure(graded, run_id=run_id, role=role, regrade=regrade)
        if plausibility is not None:
            measured["ats_plausibility"] = plausibility
        summary["runs"][run_id] = measured
        print(json.dumps({run_id: {
            "n_2025": measured["n_2025"],
            "mae": measured["mae_all_seasons"],
            "crps": measured["crps_all_seasons"],
            "ats": measured["ats_pct"],
            "ou": measured["ou_pct"],
            "ungradable_extra": measured["ungradable_extra_beyond_adr_0014"],
        }}, indent=2))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "metrics_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    c3_after = hash_tree(CHAMPION3_ROOT)
    iso = hash_isolation_paths(isolation_state_paths(ROOT))
    (OUT / "champion3_hash_after_grade.txt").write_text(c3_after + "\n", encoding="utf-8")
    (OUT / "isolation_after_grade.json").write_text(
        json.dumps(iso, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("champion3_hash_after_grade", c3_after)
    print("wrote", OUT / "metrics_summary.json")


if __name__ == "__main__":
    main()
