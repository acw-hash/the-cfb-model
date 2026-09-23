"""rating_uncertainty definition, train split, ENet-without-uncertainty ablation.

Usage:
  uv run python scripts/_rating_uncertainty_ablation.py
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
from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.models.heads.elasticnet import ElasticNetMuHead
from ncaa_quant.models.heads.quantile import quantile_column
from ncaa_quant.registry.bundle import ENSEMBLE_FILENAME, load_production_ensemble
from ncaa_quant.registry.store import ModelRegistry

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
OUT = ROOT / "data" / "tmp" / "rating_uncertainty_ablation.json"


def _dist(s: pd.Series) -> dict[str, float | int]:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return {"n": 0}
    return {
        "n": int(len(s)),
        "mean": float(s.mean()),
        "std": float(s.std(ddof=0)),
        "p10": float(s.quantile(0.10)),
        "p25": float(s.quantile(0.25)),
        "p50": float(s.quantile(0.50)),
        "p75": float(s.quantile(0.75)),
        "p90": float(s.quantile(0.90)),
        "p95": float(s.quantile(0.95)),
        "p99": float(s.quantile(0.99)),
        "max": float(s.max()),
        "min": float(s.min()),
    }


def _coef_table(enet: ElasticNetMuHead) -> list[dict[str, Any]]:
    payload = enet._get_estimator()  # noqa: SLF001
    model = payload["model"]
    scaler = payload["scaler"]
    names = list(payload["selected_features"])
    coef = np.asarray(model.coef_, dtype=float)
    scale = np.asarray(scaler.scale_, dtype=float)
    rows = []
    for i, n in enumerate(names):
        rows.append(
            {
                "feature": n,
                "coef_standardized": float(coef[i]),
                "slope_raw": float(coef[i] / max(float(scale[i]), 1e-12)),
                "abs_coef_std": float(abs(coef[i])),
            }
        )
    rows.sort(key=lambda r: -r["abs_coef_std"])
    return rows


def main() -> None:
    cfg = load_config()
    reg = ModelRegistry(Path(cfg.paths.data_dir) / "registry", tracking_uri=None)
    predictor = load_production_ensemble(
        Path(reg.resolve_champion().artifact_dir) / ENSEMBLE_FILENAME
    )
    champ_enet = predictor.enet_margin
    weights = predictor.ensemble_weights
    w_l = float(weights["lgbm_mu_margin"])
    w_e = float(weights["enet_mu_margin"])
    w_sum = w_l + w_e
    w_l_n, w_e_n = w_l / w_sum, w_e / w_sum

    # --- definition excerpt (code-backed) ---
    definition = {
        "production_construction": (
            "compute_game_features: rating_uncertainty = "
            "rating_state[home:sd_off_epa] + rating_state[away:sd_off_epa], "
            "defaulting each missing side to 1.0"
        ),
        "sd_off_epa": (
            "state_snapshot: sd_{dim} = sqrt(max(P[i,i], 0)) for each Kalman "
            "posterior state dimension; off_epa is one state dim"
        ),
        "equivalent_form": (
            "sqrt(Var_home(off_epa)) + sqrt(Var_away(off_epa)) — sum of posterior "
            "SDs on offensive EPA, not a pooled SD"
        ),
        "code_refs": [
            "src/ncaa_quant/evaluation/production_stack.py:444-446",
            "src/ncaa_quant/evaluation/production_stack.py:240-246",
        ],
    }

    # --- training frame + filter_history split ---
    fb = predictor.margin_head._feature_bank  # noqa: SLF001
    feat_rows = [{"game_id": str(int(gid)), **feats} for gid, feats in fb.items()]
    feat = pd.DataFrame(feat_rows)
    preds = pd.read_parquet(PREDS)
    preds["game_id"] = preds["game_id"].astype(str)
    labels = preds[["game_id", "season", "week", "realized_margin"]].copy()
    train = feat.merge(labels, on="game_id", how="inner")
    train["realized_margin"] = pd.to_numeric(train["realized_margin"], errors="coerce")
    train = train.dropna(subset=["realized_margin", "rating_uncertainty"])

    fh = pd.read_parquet(
        Path(cfg.paths.data_dir) / "artifacts" / "state_space" / "filter_history.parquet"
    )
    fh_ids = set(fh["team_id"].astype(int))
    seasons = sorted({int(s) for s in train["season"].unique()})
    games = load_staged_games(Path(cfg.paths.staged_dir), seasons)
    games["game_id"] = games["game_id"].astype(str)
    games["home_in_fh"] = games["home_team_id"].astype(int).isin(fh_ids)
    games["away_in_fh"] = games["away_team_id"].astype(int).isin(fh_ids)
    games["both_in_fh"] = games["home_in_fh"] & games["away_in_fh"]
    games["opponent_absent"] = ~games["away_in_fh"]  # conventional: away is visitor/FCS
    games["either_absent"] = ~games["both_in_fh"]
    train = train.merge(
        games[
            [
                "game_id",
                "home_team_id",
                "away_team_id",
                "both_in_fh",
                "opponent_absent",
                "either_absent",
                "home_in_fh",
                "away_in_fh",
            ]
        ],
        on="game_id",
        how="left",
    )

    unc_dist = {
        "overall": _dist(train["rating_uncertainty"]),
        "both_in_fh": _dist(train.loc[train["both_in_fh"] == True, "rating_uncertainty"]),  # noqa: E712
        "either_absent": _dist(
            train.loc[train["either_absent"] == True, "rating_uncertainty"]  # noqa: E712
        ),
        "away_absent_opponent": _dist(
            train.loc[train["away_in_fh"] == False, "rating_uncertainty"]  # noqa: E712
        ),
        "home_absent": _dist(
            train.loc[train["home_in_fh"] == False, "rating_uncertainty"]  # noqa: E712
        ),
    }

    # --- ENet refit without rating_uncertainty ---
    feat_cols = list(predictor.margin_head.signature.names)  # type: ignore[union-attr]
    features_tr = train[["game_id", *feat_cols]].copy()
    labels_tr = train[["game_id", "season", "week", "realized_margin"]].copy()
    features_drop = features_tr.drop(columns=["rating_uncertainty"])

    enet_abl = ElasticNetMuHead(
        target="margin",
        model_version="enet-ablate-uncertainty",
        top_k=champ_enet.top_k,
        alpha=champ_enet.alpha,
        l1_ratio=champ_enet.l1_ratio,
        max_iter=champ_enet.max_iter,
        null_share_drop_threshold=champ_enet.null_share_drop_threshold,
        seed=int(champ_enet.seed),
        season_half_life=float(champ_enet.season_half_life),
    )
    print("fitting ENet without rating_uncertainty…", flush=True)
    enet_abl.fit(features_drop, labels_tr)
    coefs_abl = _coef_table(enet_abl)
    coefs_base = _coef_table(champ_enet)
    print(
        "selected ablated:",
        [c["feature"] for c in coefs_abl],
        "has_uncertainty",
        any(c["feature"] == "rating_uncertainty" for c in coefs_abl),
        flush=True,
    )

    # Align signature: ablated fit dropped the column, so predict must too.
    _enet_abl_predict = enet_abl.predict

    def _predict_without_unc(features: pd.DataFrame) -> pd.DataFrame:
        return _enet_abl_predict(features.drop(columns=["rating_uncertainty"], errors="ignore"))

    enet_abl.predict = _predict_without_unc  # type: ignore[method-assign]

    # Week-1 features
    feats_w1 = pd.read_parquet(FEATURES)
    feats_w1["game_id"] = feats_w1["game_id"].astype(str)
    if "expected_possessions" in feats_w1.columns:
        feats_w1["expected_possessions"] = pd.to_numeric(
            feats_w1["expected_possessions"], errors="coerce"
        ).fillna(0.0)
    feats_w1_drop = feats_w1.drop(columns=["rating_uncertainty"])

    # Member predictions
    lgbm = predictor.margin_head.predict(feats_w1)
    lgbm["game_id"] = lgbm["game_id"].astype(str)
    mu_l = lgbm.set_index("game_id")["pred_margin"].astype(float)

    enet0 = champ_enet.predict(feats_w1)
    enet0["game_id"] = enet0["game_id"].astype(str)
    mu_e0 = enet0.set_index("game_id")["pred_margin"].astype(float)

    enet1 = enet_abl.predict(feats_w1_drop)
    enet1["game_id"] = enet1["game_id"].astype(str)
    mu_e1 = enet1.set_index("game_id")["pred_margin"].astype(float)

    stack0 = w_l_n * mu_l + w_e_n * mu_e0
    stack1 = w_l_n * mu_l + w_e_n * mu_e1

    # Post-mix: swap ablated ENet into predictor (predict ignores non-selected cols)
    print("epistemic mix baseline…", flush=True)
    mix0 = predictor._epistemic_mix(  # noqa: SLF001
        feats_w1, rho=float(predictor._rho), seed=int(predictor.seed)  # noqa: SLF001
    )
    post0 = pd.Series(mix0.params.mu_m, index=feats_w1["game_id"].to_numpy(), dtype=float)

    orig_enet = predictor.enet_margin
    predictor.enet_margin = enet_abl
    try:
        print("epistemic mix ablated…", flush=True)
        mix1 = predictor._epistemic_mix(  # noqa: SLF001
            feats_w1, rho=float(predictor._rho), seed=int(predictor.seed)  # noqa: SLF001
        )
        post1 = pd.Series(mix1.params.mu_m, index=feats_w1["game_id"].to_numpy(), dtype=float)
    finally:
        predictor.enet_margin = orig_enet

    qpred = predictor.quantile_margin_head.predict(feats_w1)
    qpred["game_id"] = qpred["game_id"].astype(str)
    q = qpred.set_index("game_id")
    q_lo = pd.concat(
        [
            q[quantile_column("margin", 0.1)].astype(float),
            q[quantile_column("margin", 0.9)].astype(float),
        ],
        axis=1,
    ).min(axis=1)
    q_hi = pd.concat(
        [
            q[quantile_column("margin", 0.1)].astype(float),
            q[quantile_column("margin", 0.9)].astype(float),
        ],
        axis=1,
    ).max(axis=1)

    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    realized = {
        str(g["game_id"]): float(g["actual_margin"])
        for g in results["games"]
        if g.get("week") == 1 and g.get("grade_status") == "graded" and g.get("actual_margin") is not None
    }

    meta = pd.read_parquet(ALL_GAMES)
    meta["game_id"] = meta["game_id"].astype(str)
    fail_ids = meta.loc[~meta["post_inside"], "game_id"].tolist()

    def mae(pred: pd.Series) -> float:
        aes = []
        for gid, y in realized.items():
            if gid in pred.index:
                aes.append(abs(float(pred.loc[gid]) - y))
        return float(np.mean(aes))

    def coherent_count(pred: pd.Series, ids: list[str]) -> dict[str, Any]:
        n_in = 0
        rows = []
        for gid in ids:
            mu = float(pred.loc[gid])
            lo, hi = float(q_lo.loc[gid]), float(q_hi.loc[gid])
            inside = bool(lo < mu < hi)
            n_in += int(inside)
            m = meta.loc[meta["game_id"] == gid].iloc[0]
            rows.append(
                {
                    "game_id": gid,
                    "matchup": f"{m['home_team']} vs {m['away_team']}",
                    "mu_post_baseline": float(post0.loc[gid]),
                    "mu_post_ablated": mu,
                    "mu_enet_baseline": float(mu_e0.loc[gid]),
                    "mu_enet_ablated": float(mu_e1.loc[gid]),
                    "q10": lo,
                    "q90": hi,
                    "baseline_inside": bool(lo < float(post0.loc[gid]) < hi),
                    "ablated_inside": inside,
                }
            )
        return {"n_coherent": n_in, "n": len(ids), "games": rows}

    coh = coherent_count(post1, fail_ids)

    # ensemble_weight_dampen
    dampen = {
        "config_default": 0.7,
        "design_9_7": (
            "Ensemble weights | Monthly gate, damped (new = 0.7·old + 0.3·fit) | "
            "Adaptive weighting on small windows chases noise; damping bounds regret "
            "(standard online-learning practice)"
        ),
        "purpose": (
            "Temporal EMA damping of ensemble weight updates across monthly gates — "
            "not per-member dampening of an unconstrained/linear member vs tree member"
        ),
        "implemented_in_src": False,
        "config_field_only": "PipelineConfig.ensemble_weight_dampen in config.py",
        "matches_hypothesis_unconstrained_member_dampen": False,
    }

    out = {
        "definition": definition,
        "rating_uncertainty_train_distribution": unc_dist,
        "enet_baseline_coefficients": coefs_base,
        "enet_ablated_coefficients": coefs_abl,
        "enet_ablated_selected": [c["feature"] for c in coefs_abl],
        "enet_ablated_intercept": float(enet_abl._get_estimator()["model"].intercept_),  # noqa: SLF001
        "weights": {"lgbm": w_l_n, "enet": w_e_n},
        "week1_mae": {
            "n": len(realized),
            "postmix_baseline": mae(post0),
            "postmix_ablated": mae(post1),
            "stack_baseline": mae(stack0),
            "stack_ablated": mae(stack1),
            "lgbm_only": mae(mu_l),
            "enet_baseline": mae(mu_e0),
            "enet_ablated": mae(mu_e1),
        },
        "failures_coherence": {
            "baseline_coherent": sum(1 for g in coh["games"] if g["baseline_inside"]),
            "ablated_coherent": coh["n_coherent"],
            "n_failures": coh["n"],
            "games": coh["games"],
        },
        "ensemble_weight_dampen": dampen,
        "w1_unc_failures_vs_coherent": {
            "failures_mean_unc": float(
                feats_w1.loc[feats_w1["game_id"].isin(fail_ids), "rating_uncertainty"].mean()
            ),
            "coherent_mean_unc": float(
                feats_w1.loc[~feats_w1["game_id"].isin(fail_ids), "rating_uncertainty"].mean()
            ),
        },
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "unc_dist": unc_dist,
                "week1_mae": out["week1_mae"],
                "failures_coherence": {
                    k: out["failures_coherence"][k]
                    for k in ("baseline_coherent", "ablated_coherent", "n_failures")
                },
                "ablated_top_coefs": coefs_abl[:6],
                "dampen": dampen,
                "w1_unc": out["w1_unc_failures_vs_coherent"],
            },
            indent=2,
        )
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
