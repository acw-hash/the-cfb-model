"""Decompose published μ on the 17 coherence failures into stack + mix parts.

Usage:
  uv run python scripts/_mu_decomposition_failures.py
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
from ncaa_quant.models.heads.quantile import quantile_column
from ncaa_quant.registry.bundle import ENSEMBLE_FILENAME, load_production_ensemble
from ncaa_quant.registry.store import ModelRegistry

FEATURES = ROOT / "data" / "tmp" / "epistemic_mix_w1_features.parquet"
ALL_GAMES = ROOT / "data" / "tmp" / "epistemic_mix_coherence_w1_all.parquet"
OUT = ROOT / "data" / "tmp" / "mu_decomposition_failures.json"

RATING_DIFF_FEATURES = ("rating_diff_off_epa", "rating_diff_def_epa", "off_epa_diff", "def_epa_diff")


def _enet_coef_table(enet: Any) -> pd.DataFrame:
    payload = enet._get_estimator()  # noqa: SLF001
    model = payload["model"]
    scaler = payload["scaler"]
    names = list(payload["selected_features"])
    coef = np.asarray(model.coef_, dtype=float)
    scale = np.asarray(scaler.scale_, dtype=float)
    mean = np.asarray(scaler.mean_, dtype=float)
    # Prediction: intercept + coef · ((x - mean) / scale)
    # Unstandardized slope on raw x: coef / scale
    raw_slope = coef / np.maximum(scale, 1e-12)
    return pd.DataFrame(
        {
            "feature": names,
            "coef_standardized": coef,
            "scaler_mean": mean,
            "scaler_scale": scale,
            "slope_raw": raw_slope,
            "abs_coef_std": np.abs(coef),
        }
    ).sort_values("abs_coef_std", ascending=False)


def _enet_feature_contributions(enet: Any, features: pd.DataFrame) -> pd.DataFrame:
    """Per-row linear contributions in the ENet standardized space."""
    payload = enet._get_estimator()  # noqa: SLF001
    model = payload["model"]
    scaler = payload["scaler"]
    names = list(payload["selected_features"])
    medians = payload["impute_medians"]
    x = features.copy()
    for col, med in medians.items():
        if col in x.columns:
            x[col] = pd.to_numeric(x[col], errors="coerce").fillna(float(med))
    mat = x[names].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    z = scaler.transform(mat)
    coef = np.asarray(model.coef_, dtype=float)
    contrib = z * coef[None, :]
    out = pd.DataFrame(contrib, columns=[f"contrib__{n}" for n in names])
    out["game_id"] = features["game_id"].astype(str).to_numpy()
    out["enet_intercept"] = float(model.intercept_)
    out["enet_from_features"] = contrib.sum(axis=1)
    out["enet_reconstructed"] = out["enet_intercept"] + out["enet_from_features"]
    return out


def main() -> None:
    cfg = load_config()
    reg = ModelRegistry(Path(cfg.paths.data_dir) / "registry", tracking_uri=None)
    predictor = load_production_ensemble(
        Path(reg.resolve_champion().artifact_dir) / ENSEMBLE_FILENAME
    )
    weights = predictor.ensemble_weights
    w_l = float(weights.get("lgbm_mu_margin", 0.0))
    w_e = float(weights.get("enet_mu_margin", 0.0))
    w_sum = w_l + w_e
    w_l_n = w_l / w_sum
    w_e_n = w_e / w_sum

    feats = pd.read_parquet(FEATURES)
    feats["game_id"] = feats["game_id"].astype(str)
    if "expected_possessions" in feats.columns:
        feats["expected_possessions"] = pd.to_numeric(
            feats["expected_possessions"], errors="coerce"
        ).fillna(0.0)

    meta = pd.read_parquet(ALL_GAMES)
    meta["game_id"] = meta["game_id"].astype(str)
    failures = meta.loc[~meta["post_inside"]].copy()
    fail_ids = failures["game_id"].tolist()
    feat_f = feats.loc[feats["game_id"].isin(fail_ids)].copy()
    feat_f = feat_f.set_index("game_id").loc[fail_ids].reset_index()

    lgbm = predictor.margin_head.predict(feat_f)
    lgbm["game_id"] = lgbm["game_id"].astype(str)
    mu_l = lgbm.set_index("game_id")["pred_margin"].astype(float)

    enet = predictor.enet_margin.predict(feat_f)
    enet["game_id"] = enet["game_id"].astype(str)
    mu_e = enet.set_index("game_id")["pred_margin"].astype(float)

    point = predictor._predict_point(feat_f)  # noqa: SLF001
    point["game_id"] = point["game_id"].astype(str)
    mu_stack = point.set_index("game_id")["pred_margin"].astype(float)

    print(f"epistemic mix ({predictor.n_epistemic_draws} draws)…", flush=True)
    mix = predictor._epistemic_mix(  # noqa: SLF001
        feat_f, rho=float(predictor._rho), seed=int(predictor.seed)  # noqa: SLF001
    )
    mu_post = pd.Series(mix.params.mu_m, index=point["game_id"].to_numpy(), dtype=float)

    qpred = predictor.quantile_margin_head.predict(feat_f)
    qpred["game_id"] = qpred["game_id"].astype(str)
    q = qpred.set_index("game_id")
    q90 = pd.concat(
        [
            q[quantile_column("margin", 0.1)].astype(float),
            q[quantile_column("margin", 0.9)].astype(float),
        ],
        axis=1,
    ).max(axis=1)

    coef_table = _enet_coef_table(predictor.enet_margin)
    contrib = _enet_feature_contributions(predictor.enet_margin, feat_f).set_index("game_id")

    rows: list[dict[str, Any]] = []
    for gid in fail_ids:
        m = failures.loc[failures["game_id"] == gid].iloc[0]
        ml = float(mu_l.loc[gid])
        me = float(mu_e.loc[gid])
        ms = float(mu_stack.loc[gid])
        # Manual stack with renormalized weights (matches production when both finite)
        ms_manual = w_l_n * ml + w_e_n * me
        mp = float(mu_post.loc[gid])
        qq = float(q90.loc[gid])
        excess_stack = ms - ml
        excess_post = mp - ml
        # Who contributes excess over LGBM in the stack?
        # stacked - lgbm = w_e_n * (enet - lgbm) when weights normalized on both
        pull_enet = w_e_n * (me - ml)
        mix_shift = mp - ms
        c = contrib.loc[gid]
        rating_diff_names = [n for n in RATING_DIFF_FEATURES if f"contrib__{n}" in c.index]
        rating_diff_contrib = float(sum(float(c[f"contrib__{n}"]) for n in rating_diff_names))
        fr = feat_f.loc[feat_f["game_id"] == gid].iloc[0]
        # Linear explanation from raw slopes * gaps
        slopes = coef_table.set_index("feature")["slope_raw"]
        explained_raw = 0.0
        gap_terms = {}
        for n in ("rating_diff_off_epa", "rating_diff_def_epa"):
            if n in slopes.index and n in fr.index:
                term = float(slopes.loc[n]) * float(fr[n])
                gap_terms[n] = {"gap": float(fr[n]), "slope_raw": float(slopes.loc[n]), "term": term}
                explained_raw += term

        rows.append(
            {
                "game_id": gid,
                "matchup": f"{m['home_team']} vs {m['away_team']}",
                "mu_lgbm": ml,
                "mu_enet": me,
                "w_lgbm": w_l_n,
                "w_enet": w_e_n,
                "mu_stacked": ms,
                "mu_stacked_manual": ms_manual,
                "stack_minus_manual": ms - ms_manual,
                "mu_pre_mix": ms,
                "mu_post_mix": mp,
                "mix_shift": mix_shift,
                "q90": qq,
                "excess_stack_over_lgbm": excess_stack,
                "excess_post_over_lgbm": excess_post,
                "enet_pull_in_stack": pull_enet,
                "enet_minus_lgbm": me - ml,
                "dominant_excess_source": (
                    "enet_in_stack"
                    if abs(pull_enet) >= abs(mix_shift) and abs(pull_enet) >= 0.5 * abs(excess_post)
                    else (
                        "epistemic_mix"
                        if abs(mix_shift) > abs(pull_enet)
                        else "mixed"
                    )
                ),
                "rating_diff_off_epa": float(fr["rating_diff_off_epa"]),
                "rating_diff_def_epa": float(fr["rating_diff_def_epa"]),
                "enet_intercept": float(c["enet_intercept"]),
                "enet_rating_diff_contrib_std": rating_diff_contrib,
                "enet_reconstructed": float(c["enet_reconstructed"]),
                "enet_gap_linear_raw_terms": gap_terms,
                "enet_gap_linear_raw_sum": explained_raw,
                # After stack weight: how much of excess can rating-diff slopes explain?
                "enet_gap_linear_raw_x_w_enet": explained_raw * w_e_n,
                "post_minus_q90": mp - qq,
            }
        )

    # Aggregate
    def mean(key: str) -> float:
        return float(np.mean([r[key] for r in rows]))

    # Classify dominant source more carefully per game
    for r in rows:
        # excess_post = (stack - lgbm) + (post - stack) = enet_pull + mix_shift
        parts = {
            "enet_in_stack": r["enet_pull_in_stack"],
            "epistemic_mix": r["mix_shift"],
        }
        # residual if stack != manual (should be ~0)
        r["excess_identity_check"] = r["excess_post_over_lgbm"] - (
            r["enet_pull_in_stack"] + r["mix_shift"]
        )
        r["dominant_excess_source"] = max(parts, key=lambda k: abs(parts[k]))

    coef_records = coef_table.to_dict(orient="records")
    rating_coef = coef_table.loc[
        coef_table["feature"].isin(
            ["rating_diff_off_epa", "rating_diff_def_epa", "off_epa_diff", "def_epa_diff"]
        )
    ].to_dict(orient="records")

    summary = {
        "n": len(rows),
        "weights_raw": {"lgbm_mu_margin": w_l, "enet_mu_margin": w_e},
        "weights_normalized": {"lgbm": w_l_n, "enet": w_e_n},
        "mean_mu_lgbm": mean("mu_lgbm"),
        "mean_mu_enet": mean("mu_enet"),
        "mean_mu_stacked": mean("mu_stacked"),
        "mean_mu_post_mix": mean("mu_post_mix"),
        "mean_q90": mean("q90"),
        "mean_excess_stack_over_lgbm": mean("excess_stack_over_lgbm"),
        "mean_excess_post_over_lgbm": mean("excess_post_over_lgbm"),
        "mean_enet_pull_in_stack": mean("enet_pull_in_stack"),
        "mean_mix_shift": mean("mix_shift"),
        "mean_enet_minus_lgbm": mean("enet_minus_lgbm"),
        "mean_post_minus_q90": mean("post_minus_q90"),
        "mean_enet_gap_linear_raw_sum": mean("enet_gap_linear_raw_sum"),
        "mean_enet_gap_linear_raw_x_w_enet": mean("enet_gap_linear_raw_x_w_enet"),
        "n_dominant_enet": sum(1 for r in rows if r["dominant_excess_source"] == "enet_in_stack"),
        "n_dominant_mix": sum(1 for r in rows if r["dominant_excess_source"] == "epistemic_mix"),
        "enet_coefficients": coef_records,
        "enet_rating_diff_coefficients": rating_coef,
        "games": rows,
    }
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: summary[k] for k in summary if k not in ("games", "enet_coefficients")},
            indent=2,
        )
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
