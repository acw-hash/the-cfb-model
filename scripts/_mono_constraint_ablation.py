"""Refit margin μ head with/without monotone constraints; compare on week-1.

Read-only diagnostic. Same champion HeadTrainConfig / seed / training frame;
only monotone_constraints differ.

Usage:
  uv run python scripts/_mono_constraint_ablation.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ncaa_quant.config import load_config
from ncaa_quant.models.heads.base import HeadTrainConfig, monotone_constraints_for
from ncaa_quant.models.heads.margin import LightGBMMuHead
from ncaa_quant.models.heads.quantile import quantile_column
from ncaa_quant.registry.bundle import ENSEMBLE_FILENAME, load_production_ensemble
from ncaa_quant.registry.store import ModelRegistry
import ncaa_quant.models.heads.margin as margin_mod

FEATURES = ROOT / "data" / "tmp" / "epistemic_mix_w1_features.parquet"
ALL_GAMES = ROOT / "data" / "tmp" / "epistemic_mix_coherence_w1_all.parquet"
RESULTS = ROOT / "data" / "results" / "latest_results_2026.json"
PREDS = (
    ROOT
    / "data"
    / "backtests"
    / "task23_fundamental_reduced_v3"
    / "full"
    / "predictions.parquet"
)
OUT = ROOT / "data" / "tmp" / "mono_constraint_ablation.json"
GAP_COL = "rating_diff_off_epa"


def _training_frame(predictor: Any) -> tuple[pd.DataFrame, pd.DataFrame]:
    fb = predictor.margin_head._feature_bank  # noqa: SLF001
    rows = []
    for gid, feats in fb.items():
        rows.append({"game_id": str(int(gid)), **feats})
    feat = pd.DataFrame(rows)
    preds = pd.read_parquet(PREDS)
    preds["game_id"] = preds["game_id"].astype(str)
    labels = preds[["game_id", "season", "week", "realized_margin"]].copy()
    labels["realized_margin"] = pd.to_numeric(labels["realized_margin"], errors="coerce")
    merged = feat.merge(labels, on="game_id", how="inner")
    merged = merged.dropna(subset=["realized_margin"])
    feat_cols = list(predictor.margin_head.signature.names)  # type: ignore[union-attr]
    features = merged[["game_id", *feat_cols]].copy()
    lab = merged[["game_id", "season", "week", "realized_margin"]].copy()
    return features, lab


def _fit_head(
    *,
    train_cfg: HeadTrainConfig,
    seed: int,
    features: pd.DataFrame,
    labels: pd.DataFrame,
    with_mono: bool,
) -> LightGBMMuHead:
    head = LightGBMMuHead(
        target="margin",
        model_version="ablation-mu",
        train=train_cfg,
        seed=seed,
    )
    if with_mono:
        head.fit(features, labels)
        return head

    # Patch the symbol LightGBMMuHead._fit_estimator closes over via margin module import.
    orig = margin_mod.monotone_constraints_for

    def _zeros(feature_names, target="margin"):  # type: ignore[no-untyped-def]
        del target
        return [0] * len(feature_names)

    margin_mod.monotone_constraints_for = _zeros  # type: ignore[assignment]
    try:
        head.fit(features, labels)
    finally:
        margin_mod.monotone_constraints_for = orig  # type: ignore[assignment]
    return head


def _decile_monotonicity(train: pd.DataFrame, gap_col: str) -> dict[str, Any]:
    d = train[[gap_col, "realized_margin"]].dropna().copy()
    d[gap_col] = pd.to_numeric(d[gap_col], errors="coerce")
    d["realized_margin"] = pd.to_numeric(d["realized_margin"], errors="coerce")
    d = d.dropna()
    d["decile"] = pd.qcut(d[gap_col], 10, labels=False, duplicates="drop")
    rows = []
    for dec, sub in d.groupby("decile"):
        rows.append(
            {
                "decile": int(dec) + 1,
                "n": int(len(sub)),
                "gap_mean": float(sub[gap_col].mean()),
                "gap_min": float(sub[gap_col].min()),
                "gap_max": float(sub[gap_col].max()),
                "margin_mean": float(sub["realized_margin"].mean()),
                "margin_median": float(sub["realized_margin"].median()),
                "margin_p90": float(sub["realized_margin"].quantile(0.9)),
            }
        )
    rows = sorted(rows, key=lambda r: r["decile"])
    # Top vs 9th
    top = rows[-1]
    ninth = rows[-2] if len(rows) >= 2 else None
    deltas = [
        rows[i]["margin_mean"] - rows[i - 1]["margin_mean"] for i in range(1, len(rows))
    ]
    return {
        "gap_col": gap_col,
        "deciles": rows,
        "top_minus_ninth_mean_margin": (
            float(top["margin_mean"] - ninth["margin_mean"]) if ninth else None
        ),
        "top_minus_ninth_mean_gap": (
            float(top["gap_mean"] - ninth["gap_mean"]) if ninth else None
        ),
        "mean_step_deciles_1_to_8": float(np.mean(deltas[:-1])) if len(deltas) > 1 else None,
        "step_8_to_9": float(deltas[-2]) if len(deltas) >= 2 else None,
        "step_9_to_10": float(deltas[-1]) if deltas else None,
        "flattens_at_top": (
            bool(deltas[-1] < 0.5 * np.mean(deltas[:-1])) if len(deltas) > 1 else None
        ),
    }


def main() -> None:
    cfg = load_config()
    reg = ModelRegistry(Path(cfg.paths.data_dir) / "registry", tracking_uri=None)
    champ = reg.resolve_champion()
    predictor = load_production_ensemble(Path(champ.artifact_dir) / ENSEMBLE_FILENAME)
    champ_head = predictor.margin_head
    train_cfg = champ_head.train
    seed = int(champ_head.seed)

    features_tr, labels_tr = _training_frame(predictor)
    print(
        f"train n={len(features_tr)} seed={seed} train_cfg={train_cfg} "
        f"champ_mono={champ_head.monotone_constraints}",
        flush=True,
    )

    print("fitting constrained…", flush=True)
    head_c = _fit_head(
        train_cfg=train_cfg,
        seed=seed,
        features=features_tr,
        labels=labels_tr,
        with_mono=True,
    )
    print("fitting unconstrained…", flush=True)
    head_u = _fit_head(
        train_cfg=train_cfg,
        seed=seed,
        features=features_tr,
        labels=labels_tr,
        with_mono=False,
    )
    print(
        f"refit mono={head_c.monotone_constraints} uncon_mono={head_u.monotone_constraints}",
        flush=True,
    )

    feats_w1 = pd.read_parquet(FEATURES)
    feats_w1["game_id"] = feats_w1["game_id"].astype(str)
    # Fill expected_possessions NaN like align may need — use train median
    if "expected_possessions" in feats_w1.columns:
        med = pd.to_numeric(features_tr["expected_possessions"], errors="coerce").median()
        if not np.isfinite(med):
            med = 0.0
        feats_w1["expected_possessions"] = pd.to_numeric(
            feats_w1["expected_possessions"], errors="coerce"
        ).fillna(med)

    pred_c = head_c.predict(feats_w1).set_index(
        head_c.predict(feats_w1)["game_id"].astype(str)
        if False
        else "game_id"
    )
    # avoid double predict
    pc = head_c.predict(feats_w1)
    pu = head_u.predict(feats_w1)
    pc["game_id"] = pc["game_id"].astype(str)
    pu["game_id"] = pu["game_id"].astype(str)
    pc = pc.set_index("game_id")["pred_margin"].astype(float)
    pu = pu.set_index("game_id")["pred_margin"].astype(float)

    # champion head (pickled) for reference
    p_champ = champ_head.predict(feats_w1)
    p_champ["game_id"] = p_champ["game_id"].astype(str)
    p_champ = p_champ.set_index("game_id")["pred_margin"].astype(float)

    qpred = predictor.quantile_margin_head.predict(feats_w1)
    qpred["game_id"] = qpred["game_id"].astype(str)
    q = qpred.set_index("game_id")
    q90 = q[quantile_column("margin", 0.9)].astype(float)
    q10 = q[quantile_column("margin", 0.1)].astype(float)
    q_lo = pd.concat([q10, q90], axis=1).min(axis=1)
    q_hi = pd.concat([q10, q90], axis=1).max(axis=1)

    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    realized = {
        str(g["game_id"]): g.get("actual_margin")
        for g in results["games"]
        if g.get("week") == 1 and g.get("grade_status") == "graded"
    }

    meta = pd.read_parquet(ALL_GAMES)
    meta["game_id"] = meta["game_id"].astype(str)
    failures = meta.loc[~meta["post_inside"]].copy()
    coherent = meta.loc[meta["post_inside"]].copy()
    # rematch on gap from features
    w1 = feats_w1.merge(meta, on="game_id")
    used: set[str] = set()
    matched_ids: list[str] = []
    for _, fr in failures.iterrows():
        g0 = float(w1.loc[w1["game_id"] == fr["game_id"], GAP_COL].iloc[0])
        cand = coherent.loc[~coherent["game_id"].isin(used)].copy()
        cand = cand.merge(w1[["game_id", GAP_COL]], on="game_id")
        cand["_d"] = (cand[GAP_COL] - g0).abs()
        pick = str(cand.nsmallest(1, "_d").iloc[0]["game_id"])
        used.add(pick)
        matched_ids.append(pick)

    def row_for(gid: str, group: str) -> dict[str, Any]:
        m = meta.loc[meta["game_id"] == gid].iloc[0]
        yc = float(pc.loc[gid])
        yu = float(pu.loc[gid])
        ych = float(p_champ.loc[gid])
        qq = float(q_hi.loc[gid])
        ql = float(q_lo.loc[gid])
        y = realized.get(gid)
        yf = float(y) if y is not None and np.isfinite(float(y)) else float("nan")
        return {
            "game_id": gid,
            "group": group,
            "matchup": f"{m['home_team']} vs {m['away_team']}",
            "both_in_fh": bool(m["both_in_fh"]),
            "mu_constrained": yc,
            "mu_unconstrained": yu,
            "mu_champion_head": ych,
            "delta_uncon_minus_con": yu - yc,
            "q90": qq,
            "q10": ql,
            "realized_margin": yf if np.isfinite(yf) else None,
            "ae_constrained": abs(yc - yf) if np.isfinite(yf) else None,
            "ae_unconstrained": abs(yu - yf) if np.isfinite(yf) else None,
            "con_inside": bool(ql < yc < qq),
            "uncon_inside": bool(ql < yu < qq),
        }

    focus_rows = [row_for(gid, "failure") for gid in failures["game_id"].tolist()]
    focus_rows += [row_for(gid, "matched_coherent") for gid in matched_ids]

    # All 99 MAE
    all_rows = []
    for gid in feats_w1["game_id"].tolist():
        y = realized.get(gid)
        if y is None or not np.isfinite(float(y)):
            continue
        yf = float(y)
        yc = float(pc.loc[gid])
        yu = float(pu.loc[gid])
        all_rows.append(
            {
                "game_id": gid,
                "realized_margin": yf,
                "mu_constrained": yc,
                "mu_unconstrained": yu,
                "ae_constrained": abs(yc - yf),
                "ae_unconstrained": abs(yu - yf),
                "ae_delta_uncon_minus_con": abs(yu - yf) - abs(yc - yf),
            }
        )
    all_df = pd.DataFrame(all_rows)
    mae_c = float(all_df["ae_constrained"].mean())
    mae_u = float(all_df["ae_unconstrained"].mean())

    # Train frame for decile analysis
    train_join = features_tr.merge(labels_tr, on="game_id")
    mono_off = _decile_monotonicity(train_join, GAP_COL)
    train_join["gap_mag"] = train_join[GAP_COL].abs() + pd.to_numeric(
        train_join["rating_diff_def_epa"], errors="coerce"
    ).abs()
    mono_mag = _decile_monotonicity(train_join, "gap_mag")

    # Focus summary
    fail = [r for r in focus_rows if r["group"] == "failure"]
    match = [r for r in focus_rows if r["group"] == "matched_coherent"]

    def focus_agg(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
        return {
            "label": label,
            "n": len(rows),
            "mean_mu_con": float(np.mean([r["mu_constrained"] for r in rows])),
            "mean_mu_uncon": float(np.mean([r["mu_unconstrained"] for r in rows])),
            "mean_delta": float(np.mean([r["delta_uncon_minus_con"] for r in rows])),
            "mean_q90": float(np.mean([r["q90"] for r in rows])),
            "mean_realized": float(
                np.nanmean([r["realized_margin"] for r in rows if r["realized_margin"] is not None])
            ),
            "mae_con": float(
                np.nanmean([r["ae_constrained"] for r in rows if r["ae_constrained"] is not None])
            ),
            "mae_uncon": float(
                np.nanmean(
                    [r["ae_unconstrained"] for r in rows if r["ae_unconstrained"] is not None]
                )
            ),
            "frac_con_inside": float(np.mean([r["con_inside"] for r in rows])),
            "frac_uncon_inside": float(np.mean([r["uncon_inside"] for r in rows])),
        }

    # Correlation: constrained head vs champion pickled head (should be near 1 if same data)
    corr = float(np.corrcoef(pc.to_numpy(), p_champ.to_numpy())[0, 1])
    mae_vs_champ = float((pc - p_champ).abs().mean())

    out = {
        "train_n": int(len(features_tr)),
        "head_train_config": train_cfg.__dict__,
        "seed": seed,
        "champ_mono": champ_head.monotone_constraints,
        "refit_constrained_mono": head_c.monotone_constraints,
        "refit_unconstrained_mono": head_u.monotone_constraints,
        "refit_vs_champion_head_corr": corr,
        "refit_vs_champion_head_mae": mae_vs_champ,
        "week1_n": int(len(all_df)),
        "week1_mae_constrained": mae_c,
        "week1_mae_unconstrained": mae_u,
        "week1_mae_delta_uncon_minus_con": mae_u - mae_c,
        "week1_median_ae_constrained": float(all_df["ae_constrained"].median()),
        "week1_median_ae_unconstrained": float(all_df["ae_unconstrained"].median()),
        "week1_uncon_wins": int((all_df["ae_unconstrained"] < all_df["ae_constrained"]).sum()),
        "week1_con_wins": int((all_df["ae_constrained"] < all_df["ae_unconstrained"]).sum()),
        "focus_failures_agg": focus_agg(fail, "failures"),
        "focus_matched_agg": focus_agg(match, "matched_coherent"),
        "focus_games": focus_rows,
        "monotonicity_signed_off_epa": mono_off,
        "monotonicity_gap_mag": mono_mag,
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "refit_vs_champion_head_corr": corr,
                "refit_vs_champion_head_mae": mae_vs_champ,
                "week1_mae_constrained": mae_c,
                "week1_mae_unconstrained": mae_u,
                "week1_mae_delta": mae_u - mae_c,
                "failures": out["focus_failures_agg"],
                "matched": out["focus_matched_agg"],
                "mono_off_top_steps": {
                    "step_9_to_10": mono_off["step_9_to_10"],
                    "mean_step_1_to_8": mono_off["mean_step_deciles_1_to_8"],
                    "flattens": mono_off["flattens_at_top"],
                    "top_minus_ninth": mono_off["top_minus_ninth_mean_margin"],
                },
                "mono_mag_top_steps": {
                    "step_9_to_10": mono_mag["step_9_to_10"],
                    "mean_step_1_to_8": mono_mag["mean_step_deciles_1_to_8"],
                    "flattens": mono_mag["flattens_at_top"],
                    "top_minus_ninth": mono_mag["top_minus_ninth_mean_margin"],
                },
            },
            indent=2,
        )
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
