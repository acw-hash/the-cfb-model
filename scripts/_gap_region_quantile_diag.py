"""Training-margin density in rating-gap regions + raw (unsorted) quantile heads.

Read-only. Uses:
  - data/tmp/epistemic_mix_coherence_w1.json (17 post-mix failures)
  - data/tmp/epistemic_mix_w1_features.parquet
  - champion quantile/margin head feature_bank + reduced_v3 realized margins

Usage:
  uv run python scripts/_gap_region_quantile_diag.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ncaa_quant.models.heads.quantile import QUANTILES, enforce_quantile_order, quantile_column
from ncaa_quant.registry.bundle import ENSEMBLE_FILENAME, load_production_ensemble

COHERENCE = ROOT / "data" / "tmp" / "epistemic_mix_coherence_w1.json"
FEATURES = ROOT / "data" / "tmp" / "epistemic_mix_w1_features.parquet"
PREDS = (
    ROOT
    / "data"
    / "backtests"
    / "task23_fundamental_reduced_v3"
    / "full"
    / "predictions.parquet"
)
OUT = ROOT / "data" / "tmp" / "gap_region_quantile_diag.json"

# Gap definition: signed off-EPA rating differential (primary monotone μ feature).
GAP_COL = "rating_diff_off_epa"
# Relative window: |gap - g0| / max(|g0|, floor) <= REL, also absolute floor ABS.
REL_TOL = 0.15
ABS_TOL = 0.04
N_MATCHED = 17


def _gap_window(g0: float) -> tuple[float, float]:
    half = max(ABS_TOL, REL_TOL * abs(g0))
    return g0 - half, g0 + half


def _margin_quantiles(y: np.ndarray) -> dict[str, float | int | None]:
    y = y[np.isfinite(y)]
    if y.size == 0:
        return {"n": 0}
    qs = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
    out: dict[str, float | int | None] = {"n": int(y.size)}
    out["min"] = float(np.min(y))
    out["max"] = float(np.max(y))
    out["mean"] = float(np.mean(y))
    for q in qs:
        out[f"p{int(q * 100):02d}"] = float(np.quantile(y, q))
    return out


def _build_train_frame(predictor: Any) -> pd.DataFrame:
    fb = predictor.margin_head._feature_bank  # noqa: SLF001
    rows = []
    for gid, feats in fb.items():
        row = {"game_id": str(int(gid)) if str(gid).isdigit() else str(gid)}
        row.update({k: feats.get(k) for k in feats})
        rows.append(row)
    feat = pd.DataFrame(rows)
    preds = pd.read_parquet(PREDS)
    preds["game_id"] = preds["game_id"].astype(str)
    keep = preds[["game_id", "season", "week", "realized_margin", "pred_margin"]].copy()
    # Also try staged games for any bank ids missing from backtest preds
    merged = feat.merge(keep, on="game_id", how="inner")
    merged[GAP_COL] = pd.to_numeric(merged[GAP_COL], errors="coerce")
    merged["realized_margin"] = pd.to_numeric(merged["realized_margin"], errors="coerce")
    merged = merged.dropna(subset=[GAP_COL, "realized_margin"])
    return merged


def _raw_quantile_matrix(predictor: Any, features: pd.DataFrame) -> tuple[np.ndarray, bool]:
    """Return (n, n_q) raw booster outputs before enforce_quantile_order."""
    head = predictor.quantile_margin_head
    x = head._align_features(features)  # noqa: SLF001
    mat = np.column_stack(
        [
            np.asarray(head._boosters[float(q)].predict(x), dtype=float)  # noqa: SLF001
            for q in head.quantiles
        ]
    )
    ordered, crossed = enforce_quantile_order(mat, quantiles=head.quantiles)
    del ordered
    return mat, crossed


def main() -> None:
    coherence = json.loads(COHERENCE.read_text(encoding="utf-8"))
    feats_w1 = pd.read_parquet(FEATURES)
    feats_w1["game_id"] = feats_w1["game_id"].astype(str)

    # Join post-mix results onto features
    res = pd.DataFrame(coherence["games_post_out"] + [
        g for g in coherence.get("games_flipped_by_mix", []) if False  # placeholder
    ])
    # All week-1 games from coherence file: rebuild from post_out + need coherent set
    # Load full per-game rows if present; else reconstruct from summary sides.
    # The JSON only keeps pre_out / post_out / flipped — rebuild from features + re-score?
    # Prefer: re-read by merging post_out list and building coherent from features
    # using the same inside flags stored... we only have failures in detail.
    # Recompute inside flags from the saved JSON fields on failures; for coherent
    # matching, load the full frame by re-deriving from previous script output.
    # Fallback: write all games into a sidecar if missing.
    all_path = ROOT / "data" / "tmp" / "epistemic_mix_coherence_w1_all.parquet"
    if not all_path.is_file():
        # Reconstruct minimal all-games table from JSON fragments + features.
        # post_out and we need coherent — re-run inside check using saved summary
        # by calling predict quickly on cached features.
        from ncaa_quant.registry.store import ModelRegistry
        from ncaa_quant.config import load_config

        cfg = load_config()
        reg = ModelRegistry(Path(cfg.paths.data_dir) / "registry", tracking_uri=None)
        champ = reg.resolve_champion()
        predictor = load_production_ensemble(Path(champ.artifact_dir) / ENSEMBLE_FILENAME)
        # Rebuild all-game table from coherence JSON keys that list only subsets:
        # Use features + mix results file if we expand. For now reconstruct via
        # the diagnostic values embedded... Actually write from features by
        # re-predicting pre/post quickly (features cached; mix ~few sec).
        point = predictor._predict_point(feats_w1)  # noqa: SLF001
        point["game_id"] = point["game_id"].astype(str)
        pre = point.set_index("game_id")["pred_margin"].astype(float)
        qpred = predictor.quantile_margin_head.predict(feats_w1)
        qpred["game_id"] = qpred["game_id"].astype(str)
        q = qpred.set_index("game_id")
        q10 = q[quantile_column("margin", 0.1)].astype(float)
        q90 = q[quantile_column("margin", 0.9)].astype(float)
        q_lo = pd.concat([q10, q90], axis=1).min(axis=1)
        q_hi = pd.concat([q10, q90], axis=1).max(axis=1)
        mix = predictor._epistemic_mix(  # noqa: SLF001
            feats_w1, rho=float(predictor._rho), seed=int(predictor.seed)  # noqa: SLF001
        )
        post = pd.Series(mix.params.mu_m, index=point["game_id"].to_numpy(), dtype=float)
        # filter_history flags from post_out / both_in rates — reload from JSON games
        post_out_ids = {g["game_id"] for g in coherence["games_post_out"]}
        fh_map = {g["game_id"]: g["both_in_fh"] for g in coherence["games_post_out"]}
        # For others, recompute from staged + filter_history
        from ncaa_quant.evaluation.backtest_runner import load_staged_games

        fh = pd.read_parquet(
            Path(cfg.paths.data_dir) / "artifacts" / "state_space" / "filter_history.parquet"
        )
        fh_ids = set(fh["team_id"].astype(int))
        sched = load_staged_games(Path(cfg.paths.staged_dir), (2026,))
        sched = sched.loc[sched["week"].astype(int) == 1].copy()
        sched["game_id"] = sched["game_id"].astype(str)
        sched["both_in_fh"] = sched["home_team_id"].astype(int).isin(fh_ids) & sched[
            "away_team_id"
        ].astype(int).isin(fh_ids)
        rows = []
        for gid in point["game_id"]:
            mu_pre = float(pre.loc[gid])
            mu_post = float(post.loc[gid])
            lo = float(q_lo.loc[gid])
            hi = float(q_hi.loc[gid])
            both = bool(sched.loc[sched["game_id"] == gid, "both_in_fh"].iloc[0])
            rows.append(
                {
                    "game_id": gid,
                    "home_team": str(sched.loc[sched["game_id"] == gid, "home_team"].iloc[0]),
                    "away_team": str(sched.loc[sched["game_id"] == gid, "away_team"].iloc[0]),
                    "both_in_fh": both,
                    "mu_pre": mu_pre,
                    "mu_post": mu_post,
                    "mix_shift": mu_post - mu_pre,
                    "q10": lo,
                    "q90": hi,
                    "pre_inside": bool(lo < mu_pre < hi),
                    "post_inside": bool(lo < mu_post < hi),
                }
            )
        all_games = pd.DataFrame(rows)
        all_games.to_parquet(all_path, index=False)
    else:
        all_games = pd.read_parquet(all_path)
        from ncaa_quant.config import load_config
        from ncaa_quant.registry.store import ModelRegistry

        cfg = load_config()
        reg = ModelRegistry(Path(cfg.paths.data_dir) / "registry", tracking_uri=None)
        champ = reg.resolve_champion()
        predictor = load_production_ensemble(Path(champ.artifact_dir) / ENSEMBLE_FILENAME)

    all_games["game_id"] = all_games["game_id"].astype(str)
    w1 = feats_w1.merge(all_games, on="game_id", how="inner")
    w1[GAP_COL] = pd.to_numeric(w1[GAP_COL], errors="coerce")
    # Also magnitude for reporting
    w1["rating_gap_mag"] = w1[GAP_COL].abs() + pd.to_numeric(
        w1["rating_diff_def_epa"], errors="coerce"
    ).abs()

    failures = w1.loc[~w1["post_inside"]].copy()
    coherent = w1.loc[w1["post_inside"]].copy()
    print(f"failures={len(failures)} coherent={len(coherent)}", flush=True)

    # Matched coherent: nearest neighbors in GAP_COL among coherent games
    matched_rows = []
    used = set()
    for _, fr in failures.iterrows():
        g0 = float(fr[GAP_COL])
        cand = coherent.loc[~coherent["game_id"].isin(used)].copy()
        cand["_d"] = (cand[GAP_COL] - g0).abs()
        if cand.empty:
            break
        pick = cand.nsmallest(1, "_d").iloc[0]
        used.add(pick["game_id"])
        matched_rows.append(pick)
    matched = pd.DataFrame(matched_rows)
    print(f"matched coherent={len(matched)}", flush=True)

    train = _build_train_frame(predictor)
    print(
        f"train bank joined n={len(train)} gap range "
        f"[{train[GAP_COL].min():.3f},{train[GAP_COL].max():.3f}]",
        flush=True,
    )

    def region_report(game_row: pd.Series) -> dict[str, Any]:
        g0 = float(game_row[GAP_COL])
        lo, hi = _gap_window(g0)
        sub = train.loc[(train[GAP_COL] >= lo) & (train[GAP_COL] <= hi)]
        y = sub["realized_margin"].to_numpy(dtype=float)
        dist = _margin_quantiles(y)
        q90 = float(game_row["q90"])
        # Where does predicted q90 sit in the empirical training margin CDF?
        if y.size:
            ecdf = float(np.mean(y <= q90))
            near_top = q90 >= float(np.quantile(y, 0.90)) if y.size >= 10 else None
            above_max = q90 > float(np.max(y))
            above_p95 = q90 >= float(np.quantile(y, 0.95)) if y.size >= 20 else None
        else:
            ecdf = near_top = above_max = above_p95 = None
        return {
            "game_id": str(game_row["game_id"]),
            "matchup": f"{game_row['home_team']} vs {game_row['away_team']}",
            "both_in_fh": bool(game_row["both_in_fh"]),
            "post_inside": bool(game_row["post_inside"]),
            "gap": g0,
            "gap_mag": float(game_row["rating_gap_mag"]),
            "gap_window": [lo, hi],
            "mu_post": float(game_row["mu_post"]),
            "q10": float(game_row["q10"]),
            "q90": q90,
            "train_margin_dist": dist,
            "q90_ecdf_in_train": ecdf,
            "q90_ge_train_p90": near_top,
            "q90_ge_train_p95": above_p95,
            "q90_gt_train_max": above_max,
            "mu_post_ecdf_in_train": float(np.mean(y <= float(game_row["mu_post"])))
            if y.size
            else None,
        }

    fail_reports = [region_report(r) for _, r in failures.iterrows()]
    match_reports = [region_report(r) for _, r in matched.iterrows()]

    # Aggregate summary
    def agg(reports: list[dict[str, Any]], label: str) -> dict[str, Any]:
        ns = [r["train_margin_dist"].get("n", 0) for r in reports]
        ecdfs = [r["q90_ecdf_in_train"] for r in reports if r["q90_ecdf_in_train"] is not None]
        near = [r["q90_ge_train_p90"] for r in reports if r["q90_ge_train_p90"] is not None]
        gt_max = [r["q90_gt_train_max"] for r in reports if r["q90_gt_train_max"] is not None]
        return {
            "label": label,
            "n_games": len(reports),
            "train_n_median": float(np.median(ns)) if ns else None,
            "train_n_min": int(min(ns)) if ns else None,
            "train_n_max": int(max(ns)) if ns else None,
            "mean_q90_ecdf": float(np.mean(ecdfs)) if ecdfs else None,
            "frac_q90_ge_train_p90": float(np.mean(near)) if near else None,
            "frac_q90_gt_train_max": float(np.mean(gt_max)) if gt_max else None,
            "mean_gap": float(np.mean([r["gap"] for r in reports])) if reports else None,
            "mean_q90": float(np.mean([r["q90"] for r in reports])) if reports else None,
            "mean_mu_post": float(np.mean([r["mu_post"] for r in reports])) if reports else None,
        }

    # Raw unsorted quantiles on the 17
    fail_feats = feats_w1.loc[feats_w1["game_id"].isin(failures["game_id"])].copy()
    # preserve order of failures
    fail_feats = fail_feats.set_index("game_id").loc[failures["game_id"].tolist()].reset_index()
    raw_mat, any_crossed = _raw_quantile_matrix(predictor, fail_feats)
    q_list = list(predictor.quantile_margin_head.quantiles)
    i10, i90 = q_list.index(0.1), q_list.index(0.9)
    raw_rows = []
    n_crossed_rows = 0
    for i, gid in enumerate(fail_feats["game_id"].tolist()):
        raw = raw_mat[i]
        # pairwise crossing: any q_i > q_{i+1}
        crossed = bool(np.any(raw[:-1] > raw[1:]))
        if crossed:
            n_crossed_rows += 1
        fr = failures.loc[failures["game_id"] == gid].iloc[0]
        raw_rows.append(
            {
                "game_id": gid,
                "matchup": f"{fr['home_team']} vs {fr['away_team']}",
                "raw_q10": float(raw[i10]),
                "raw_q90": float(raw[i90]),
                "sorted_q10": float(fr["q10"]),
                "sorted_q90": float(fr["q90"]),
                "raw_q10_gt_raw_q90": bool(raw[i10] > raw[i90]),
                "row_crossed": crossed,
                "raw_vector": {f"q{int(q*100):02d}": float(raw[j]) for j, q in enumerate(q_list)},
            }
        )

    summary = {
        "gap_col": GAP_COL,
        "rel_tol": REL_TOL,
        "abs_tol": ABS_TOL,
        "train_n_total": int(len(train)),
        "train_gap_p50": float(train[GAP_COL].median()),
        "train_gap_p90": float(train[GAP_COL].quantile(0.9)),
        "train_gap_p99": float(train[GAP_COL].quantile(0.99)),
        "train_gap_max": float(train[GAP_COL].max()),
        "failures_agg": agg(fail_reports, "post_mix_failures"),
        "matched_coherent_agg": agg(match_reports, "matched_coherent"),
        "failures": fail_reports,
        "matched_coherent": match_reports,
        "raw_quantiles_unsorted": {
            "n": len(raw_rows),
            "n_rows_crossed": n_crossed_rows,
            "cross_rate": n_crossed_rows / len(raw_rows) if raw_rows else None,
            "n_raw_q10_gt_q90": sum(1 for r in raw_rows if r["raw_q10_gt_raw_q90"]),
            "batch_crossing_flag": bool(any_crossed),
            "games": raw_rows,
        },
    }
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({
        "train_n_total": summary["train_n_total"],
        "failures_agg": summary["failures_agg"],
        "matched_coherent_agg": summary["matched_coherent_agg"],
        "raw_cross": {
            "n_rows_crossed": n_crossed_rows,
            "cross_rate": summary["raw_quantiles_unsorted"]["cross_rate"],
            "n_raw_q10_gt_q90": summary["raw_quantiles_unsorted"]["n_raw_q10_gt_q90"],
        },
    }, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
