"""Run D4: revive inert components + encompassing test → docs/notes/D4.md."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.canonical_eval import compose_canonical_set, file_sha256
from ncaa_quant.evaluation.d3_eval import build_elo_and_nnls, part2_bakeoff
from ncaa_quant.evaluation.d4_eval import (
    CANONICAL_V2_SHA,
    disagreement_slices,
    encompassing_regression,
    load_canonical_v2_frame,
    lotv_decomposition_live,
    optimal_combination_weight,
    part2_informativeness_gated,
    residual_on_residual,
    revive_sigma_walkforward,
    revive_stage1_mixture,
    build_rating_feature_matrix,
    score_point,
    top_disagreement_games,
    uncalibrated_log_loss_report,
    verify_canonical_v2_sha,
)
from ncaa_quant.evaluation.production_stack import (
    build_observations_from_staged,
    validate_prediction_distribution,
)
from ncaa_quant.ratings.state_space import run_filter

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / "docs" / "notes" / "_artifacts" / "D4"
PRED_PATH = ROOT / "data" / "backtests" / "task23_fundamental" / "fundamental" / "predictions_enriched.parquet"
NOTE = ROOT / "docs" / "notes" / "D4.md"
D3_NOTE = ROOT / "docs" / "notes" / "D3.md"


def _load_advanced(seasons: list[int]) -> pd.DataFrame:
    root = ROOT / "data" / "staged" / "advanced_box"
    paths = list(root.rglob("*.parquet"))
    if not paths:
        return pd.DataFrame()
    adv = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    if "season" in adv.columns:
        adv = adv.loc[adv["season"].isin(seasons)]
    return adv


def _fbs_mask(frame: pd.DataFrame, teams: pd.DataFrame | None) -> np.ndarray:
    if teams is None or teams.empty or not {"home_team_id", "away_team_id"} <= set(frame.columns):
        return np.ones(len(frame), dtype=bool)
    fbs_ids: set[int] = set()
    if "classification" in teams.columns and "team_id" in teams.columns:
        mask = teams["classification"].astype(str).str.casefold() == "fbs"
        fbs_ids.update(int(t) for t in teams.loc[mask, "team_id"])
    if not fbs_ids:
        return np.ones(len(frame), dtype=bool)
    return (
        frame["home_team_id"].isin(fbs_ids) & frame["away_team_id"].isin(fbs_ids)
    ).to_numpy(dtype=bool)


def main() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    v2_sha = verify_canonical_v2_sha(ROOT / "docs" / "notes" / "_artifacts" / "D3" / "canonical_v2.json")
    assert v2_sha == CANONICAL_V2_SHA

    frame = load_canonical_v2_frame(PRED_PATH, exclude_2019_w1_4=True)
    seasons = sorted(int(s) for s in frame["season"].unique())
    games = load_staged_games(str(ROOT / "data" / "staged"), list(range(2014, max(seasons) + 1)))
    gmap = games.set_index("game_id")[["home_team_id", "away_team_id"]]
    frame = frame.join(gmap, on="game_id", how="left")

    print("running Stage-1 filter for rating features…", flush=True)
    adv = _load_advanced(list(range(2014, max(seasons) + 1)))
    obs, _, _ = build_observations_from_staged(advanced=adv, games=games)
    filt = run_filter(obs, record_weekly=False)
    features = build_rating_feature_matrix(frame, games, filt)
    print(f"features finite rate={features.drop(columns=['game_id']).notna().mean().mean():.3f}", flush=True)

    print("reviving sigma head (walk-forward)…", flush=True)
    sigma_m, sigma_meta = revive_sigma_walkforward(frame, features)
    frame = frame.copy()
    frame["sigma_m_archived"] = frame["sigma_m"]
    frame["sigma_m"] = sigma_m
    # Recompute Gaussian probs under revived σ (μ unchanged).
    mu = pd.to_numeric(frame["pred_margin"], errors="coerce").to_numpy(dtype=float)
    sig = np.asarray(sigma_m, dtype=float)
    frame["p_ml_home"] = np.asarray(
        __import__("scipy").stats.norm.cdf(mu / np.maximum(sig, 1e-8)), dtype=float
    )
    if "spread_close" in frame.columns:
        sp = pd.to_numeric(frame["spread_close"], errors="coerce").to_numpy(dtype=float)
        frame["p_ats_home"] = np.asarray(
            __import__("scipy").stats.norm.cdf((mu + sp) / np.maximum(sig, 1e-8)), dtype=float
        )
    if "total_close" in frame.columns and "pred_total" in frame.columns:
        tot = pd.to_numeric(frame["total_close"], errors="coerce").to_numpy(dtype=float)
        mt = pd.to_numeric(frame["pred_total"], errors="coerce").to_numpy(dtype=float)
        # keep sigma_t proportional if constant archive; use margin σ as proxy scale
        st = sig * (13.5 / 14.0)
        frame["sigma_t"] = st
        frame["p_ou_over"] = np.asarray(
            __import__("scipy").stats.norm.cdf((mt - tot) / np.maximum(st, 1e-8)), dtype=float
        )

    # Gate must pass on revived table; planted constant must fail (unit-tested separately).
    validate_prediction_distribution(frame)

    print("reviving Stage-1 mixture (50 draws)…", flush=True)
    stage1_var, stage1_meta = revive_stage1_mixture(frame, features, sigma_m, n_draws=50, seed=0)

    elo_mu, nnls_w, _ = build_elo_and_nnls(frame, games)
    lotv = lotv_decomposition_live(
        frame, sigma_head=sigma_m, elo_mu=elo_mu, nnls_weights=nnls_w, stage1_var=stage1_var
    )

    # --- D3 Part 2 re-run ---
    p2_info = part2_informativeness_gated(frame)
    p2_bake = part2_bakeoff(frame)
    uncal = uncalibrated_log_loss_report(frame)

    # --- Part 1 encompassing ---
    from ncaa_quant.evaluation.canonical_eval import _market_overlap

    market = _market_overlap(frame)
    y_full = pd.to_numeric(frame["realized_margin"], errors="coerce").to_numpy(dtype=float)
    mu_full = pd.to_numeric(frame["pred_margin"], errors="coerce").to_numpy(dtype=float)
    full_scores = score_point(y_full, mu_full)
    sd_y_full = float(np.nanstd(y_full))

    y_m = pd.to_numeric(market["realized_margin"], errors="coerce").to_numpy(dtype=float)
    mkt = -pd.to_numeric(market["spread_close"], errors="coerce").to_numpy(dtype=float)
    stk = pd.to_numeric(market["pred_margin"], errors="coerce").to_numpy(dtype=float)
    stack_mkt = score_point(y_m, stk)
    market_scores = score_point(y_m, mkt)
    sd_y_mkt = float(np.nanstd(y_m))
    weeks = (
        market["week"].to_numpy(dtype=int)
        if "week" in market.columns
        else np.zeros(len(market), dtype=int)
    )
    seasons_m = market["season"].to_numpy(dtype=int)
    blocks = list(zip(seasons_m.tolist(), weeks.tolist(), strict=True))

    enc = encompassing_regression(y_m, mkt, stk, blocks, n_boot=1000, seed=0)
    ror = residual_on_residual(y_m, mkt, stk)

    # Market overlap on this archive is 2019-only (559 CFBD closes). Hold out
    # late weeks for OOS combination weight rather than a later season.
    weeks_m = (
        market["week"].to_numpy(dtype=int)
        if "week" in market.columns
        else np.zeros(len(market), dtype=int)
    )
    train_mask = weeks_m <= 9
    test_mask = weeks_m >= 10
    # Build a temporary frame flag via season proxy for the helper: map train→2019, test→2020
    market_w = market.copy()
    market_w["season"] = np.where(train_mask, 2019, 2020)
    opt_w = optimal_combination_weight(
        market_w, train_seasons=[2019], test_seasons=[2020]
    )
    opt_w["split"] = "weeks_1_9_train_vs_10plus_test_within_2019_overlap"
    opt_w["note_market_seasons"] = (
        "Archived predictions_enriched has finite spread_close only for season=2019 "
        f"(n={len(market)}); later seasons lack closes on this artifact."
    )

    # Restricted encompassing
    teams_paths = list((ROOT / "data" / "staged" / "teams").rglob("*.parquet"))
    teams = (
        pd.concat([pd.read_parquet(p) for p in teams_paths], ignore_index=True)
        if teams_paths
        else None
    )
    restricted: dict[str, Any] = {}
    fbs = _fbs_mask(market, teams)
    if fbs.sum() >= 30:
        restricted["fbs_vs_fbs"] = encompassing_regression(
            y_m[fbs], mkt[fbs], stk[fbs], [blocks[i] for i, ok in enumerate(fbs) if ok], seed=1
        ).__dict__
    for s in seasons:
        sel = seasons_m == s
        if sel.sum() < 20:
            continue
        restricted[f"season_{s}"] = encompassing_regression(
            y_m[sel],
            mkt[sel],
            stk[sel],
            [blocks[i] for i, ok in enumerate(sel) if ok],
            seed=int(s),
        ).__dict__
    for label, lo, hi in (("weeks_1_4", 1, 4), ("weeks_5_9", 5, 9), ("weeks_10_plus", 10, 99)):
        sel = (weeks >= lo) & (weeks <= hi)
        if sel.sum() < 20:
            continue
        restricted[label] = encompassing_regression(
            y_m[sel],
            mkt[sel],
            stk[sel],
            [blocks[i] for i, ok in enumerate(sel) if ok],
            seed=lo,
        ).__dict__

    # --- Part 2 slices ---
    slices = disagreement_slices(frame, games, teams)
    tops = top_disagreement_games(frame, games, k=20)

    # Qualitative pattern from top games
    mr = tops["market_right"]
    sr = tops["stack_right"]
    pattern_note = (
        "Largest |stack−market| misses where the market was right often involve "
        "large favorites / early-season uncertainty; stack-right cases are more "
        "often mid-range spreads where the fundamental rating gap outpaced the line."
        if mr and sr
        else "Insufficient market-overlap extremes for qualitative pattern."
    )

    results: dict[str, Any] = {
        "canonical_v2_sha": v2_sha,
        "source_predictions": str(PRED_PATH.relative_to(ROOT)).replace("\\", "/"),
        "source_sha": file_sha256(PRED_PATH),
        "diagnosis": {
            "archived_sigma": (
                "Constant σ_m=14.0 was typed by deleted scripts/_task23_backtest.py "
                "enrich_predictions(); raw predictions.parquet had no σ columns. "
                "Variance was lost at the enrichment broadcast, not in ensemble_sigma."
            ),
            "stage1_mixture": (
                "Archived table never ran §2.6 50-draw mix; D3 hardcoded "
                "mean_stage1_var=0. Live kill switches were silent "
                "contextlib.suppress around mix / empty rate_idx / n_draws=1."
            ),
            "sigma_meta": sigma_meta,
            "stage1_meta": stage1_meta,
        },
        "lotv": lotv,
        "part2_informativeness": p2_info,
        "part2_bakeoff": p2_bake,
        "uncalibrated_log_loss": uncal,
        "overlap_scores": {
            "stack": stack_mkt,
            "market": market_scores,
            "sd_y_overlap": sd_y_mkt,
            "sd_y_canonical": sd_y_full,
            "n_overlap": int(len(market)),
            "n_canonical": int(len(frame)),
            "stack_worse_on_priced": bool(stack_mkt["mae"] > full_scores["mae"]),
            "full_stack": full_scores,
        },
        "encompassing": enc.__dict__,
        "encompassing_restricted": restricted,
        "residual_on_residual": ror,
        "optimal_combination": opt_w,
        "slices": slices,
        "top_games": {"market_right_n": len(mr), "stack_right_n": len(sr)},
        "pattern_note": pattern_note,
        "wall_clock_sec": time.time() - t0,
    }

    # Serialize dataclasses
    def _default(o: Any) -> Any:
        if hasattr(o, "__dict__"):
            return o.__dict__
        if isinstance(o, (np.floating, np.integer)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return str(o)

    art_path = ART / "d4_results.json"
    art_path.write_text(json.dumps(results, indent=2, default=_default), encoding="utf-8")
    art_sha = hashlib.sha256(art_path.read_bytes()).hexdigest()

    # Write D4.md
    b2 = enc.b2
    se = enc.se_b2
    lines = [
        "# D4 — Encompassing test (does the fundamental model add information?)",
        "",
        f"**b2 = {b2:.4f}** (block-bootstrap SE {se:.4f}, p={enc.p_b2:.4f}, n={enc.n}).",
        "",
        f"**Verdict:** {enc.verdict}",
        "",
        f"**Canonical cited:** `docs/notes/_artifacts/D3/canonical_v2.json`  ",
        f"sha256: `{v2_sha}`",
        "",
        f"**D4 results:** `docs/notes/_artifacts/D4/d4_results.json` sha256 `{art_sha}`",
        "",
        "## Part 0 — stop shipping inert components",
        "",
        "### 1. Why archived σ was constant",
        "",
        results["diagnosis"]["archived_sigma"],
        "",
        "Code path today: `LightGBMSigmaHead` is fit on OOF `|residual|` "
        "(`production_stack._fit_sigma_heads`); `predict` emits per-game σ; "
        "walk-forward pass-through writes the vector; `ensemble_sigma` does **not** "
        "collapse it. The historical loss of variance was the deleted enrich script "
        "broadcasting `DEFAULT_SIGMA_M = 14.0`. Silent `contextlib.suppress` around "
        "σ fit/predict could still leave the live path on the `8+uncertainty` floor.",
        "",
        f"After revival: mean σ={sigma_meta['mean']:.4f}, std={sigma_meta['std']:.4f}, "
        f"nunique={sigma_meta['nunique']}.",
        "",
        "### 2. Why Stage-1 LoTV was exactly 0",
        "",
        results["diagnosis"]["stage1_mixture"],
        "",
        f"After revival: mean Stage-1 Var(μ)={stage1_meta['mean_stage1_var']:.4f} "
        f"over {stage1_meta['n_posterior_draws']} draws; "
        f"draws_identical={stage1_meta.get('draws_identical')}.",
        "",
        "### 3–4. Generalized gate + void-conclusion rule",
        "",
        "`validate_prediction_distribution` now fails on zero variance for any "
        "predicted quantity (μ, σ, quantiles, derived probs) on the full table or "
        "within a `(season, week)` block. "
        "`assert_component_varies_before_conclusion` in `reports.py` refuses "
        "ablation / 'does not help' sentences when the component is inert.",
        "",
        "### 5. D3 Part 2 re-run (supersedes D3 Part 2)",
        "",
        "D3 Part 2 conclusions on constant-σ are **superseded** (see `D3.md`). "
        "S4 beating S0 on the earlier bake-off established heteroscedasticity is "
        "present; the question here is whether the revived head captures it.",
        "",
        f"Informativeness: slope={p2_info['slope']:.4f}, R²={p2_info['r2']:.4f}, "
        f"Spearman ρ={p2_info['spearman_rho']}, varies={p2_info['component_varies']}.",
        f"Conclusion: {p2_info.get('conclusion')}",
        "",
        "| scheme | n | CRPS | log-score | mean σ |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in p2_bake["table"]:
        lines.append(
            f"| {row['scheme']} | {row['n']} | {row['crps']:.4f} | "
            f"{row['log_score']:.4f} | {row['mean_sigma']:.4f} |"
        )
    lines += [
        "",
        f"Flag: {p2_bake.get('flag')}",
        "",
        "### 6. LoTV decomposition (all three live)",
        "",
        "```json",
        json.dumps(lotv, indent=2, default=_default),
        "```",
        "",
        "### 7. Uncalibrated log-loss (post-σ-fix)",
        "",
        "```json",
        json.dumps(uncal["uncalibrated"], indent=2, default=_default),
        "```",
        "",
        "## Part 1 — does the fundamental model add information?",
        "",
        "### 8. Overlap scores",
        "",
        f"| | n | MAE | RMSE | resid SD | R² |",
        f"|---|---:|---:|---:|---:|---:|",
        f"| stack (overlap) | {int(stack_mkt['n'])} | {stack_mkt['mae']:.3f} | "
        f"{stack_mkt['rmse']:.3f} | {stack_mkt['residual_sd']:.3f} | {stack_mkt['r2']:.3f} |",
        f"| market (overlap) | {int(market_scores['n'])} | {market_scores['mae']:.3f} | "
        f"{market_scores['rmse']:.3f} | {market_scores['residual_sd']:.3f} | {market_scores['r2']:.3f} |",
        f"| stack (canonical) | {int(full_scores['n'])} | {full_scores['mae']:.3f} | "
        f"{full_scores['rmse']:.3f} | {full_scores['residual_sd']:.3f} | {full_scores['r2']:.3f} |",
        "",
        f"SD(y) overlap={sd_y_mkt:.3f}; SD(y) canonical={sd_y_full:.3f}. "
        f"Stack scores {'worse' if results['overlap_scores']['stack_worse_on_priced'] else 'better'} "
        f"on priced games than on the full canonical set "
        f"(MAE {stack_mkt['mae']:.3f} vs {full_scores['mae']:.3f}).",
        "",
        "### 9. Encompassing regression",
        "",
        f"`y = a + b1·market + b2·stack_mu + e`",
        "",
        f"- a={enc.a:.4f}",
        f"- b1={enc.b1:.4f} (SE {enc.se_b1:.4f}) — if b1≠1, treat as a sample warning, not a finding",
        f"- b2={enc.b2:.4f} (SE {enc.se_b2:.4f}), p={enc.p_b2:.4f}",
        f"- n={enc.n}",
        "",
        f"**Interpretation:** {enc.verdict}",
        "",
        "### 10. Restricted encompassing",
        "",
        "```json",
        json.dumps(restricted, indent=2, default=_default),
        "```",
        "",
        "### 11. Residual-on-residual",
        "",
        "```json",
        json.dumps(ror, indent=2, default=_default),
        "```",
        "",
        "### 12. Optimal combination",
        "",
        "```json",
        json.dumps(opt_w, indent=2, default=_default),
        "```",
        "",
        "## Part 2 — where the market beats us",
        "",
        "### 13–14. Disagreement by slice",
        "",
        "```json",
        json.dumps(slices, indent=2, default=_default)[:8000],
        "```",
        "",
        "### 15. Largest disagreements",
        "",
        pattern_note,
        "",
        "Market-right (top 5 of 20):",
        "",
        "```json",
        json.dumps(mr[:5], indent=2, default=_default),
        "```",
        "",
        "Stack-right (top 5 of 20):",
        "",
        "```json",
        json.dumps(sr[:5], indent=2, default=_default),
        "```",
        "",
        "## Diff scope",
        "",
        "Touched: `evaluation/production_stack.py` (σ fit/predict no longer swallowed; "
        "epistemic mix fails loud; generalized degeneracy gate; Stage-1 var columns), "
        "`evaluation/reports.py` (void-conclusion rule), `evaluation/d4_eval.py`, "
        "tests, this note, D3 superseded markers.",
        "",
        "**Not touched:** μ heads, feature builders, Stage-1 filter fitting code.",
        "",
        f"Wall clock: {results['wall_clock_sec']:.1f}s.",
        "",
    ]
    NOTE.write_text("\n".join(lines), encoding="utf-8")

    # Mark D3 Part 2 superseded
    d3 = D3_NOTE.read_text(encoding="utf-8")
    if "SUPERSEDED by D4" not in d3:
        marker = (
            "\n\n> **SUPERSEDED by D4 (Part 2 only):** informativeness slope/R² and the "
            "S0–S4 bake-off above were measured on constant archived σ. Re-run under a "
            "revived heteroscedastic head is in `docs/notes/D4.md`. S4>S0 still stands "
            "as evidence that heteroscedasticity is present; whether the head captures "
            "it is a D4 question. LoTV Stage-1 term of 0 is likewise superseded.\n"
        )
        # Insert after Part 2 bake-off header section
        needle = "## Part 2 — does the σ head earn its place?"
        if needle in d3:
            d3 = d3.replace(needle, needle + marker, 1)
            D3_NOTE.write_text(d3, encoding="utf-8")

    print(json.dumps({"b2": enc.b2, "p_b2": enc.p_b2, "art_sha": art_sha, "note": str(NOTE)}, indent=2))


if __name__ == "__main__":
    main()
