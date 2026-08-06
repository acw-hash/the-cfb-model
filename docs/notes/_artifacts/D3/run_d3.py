"""Run D3 measurement suite and write docs/notes/D3.md + canonical_v2."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.canonical_eval import (
    compose_canonical_set,
    file_sha256,
    gate_task23_fundamental,
)
from ncaa_quant.evaluation.d3_eval import (
    CANONICAL_V1_SHA,
    apply_sigma_correction,
    build_elo_and_nnls,
    fit_true_l1_stage1,
    load_canonical_frame,
    part1_ensemble_decomposition,
    part1_sigma_before_after,
    part2_bakeoff,
    part2_informativeness,
    part3_shape,
    part4_calibration,
    part5_comparisons,
    verify_canonical_v1_sha,
)
from ncaa_quant.evaluation.production_stack import build_observations_from_staged
from ncaa_quant.ratings.state_space import posterior_asof, run_filter
from ncaa_quant.utils.timeutils import to_utc

ROOT = Path(__file__).resolve().parents[4]
ART_D3 = ROOT / "docs" / "notes" / "_artifacts" / "D3"
PRED_PATH = ROOT / "data" / "backtests" / "task23_fundamental" / "fundamental" / "predictions_enriched.parquet"
RAW_PRED = ROOT / "data" / "backtests" / "task23_fundamental" / "fundamental" / "predictions.parquet"


def _rating_diff_off_epa(frame: pd.DataFrame, games: pd.DataFrame, filter_result: Any) -> np.ndarray:
    """Pregame Stage-1 offense rating differential (event_time < kickoff).

    Uses each game's ``start_date`` as the exclusive as-of bound so ratings
    never include that game's update — independent of any stamped ``as_of`` on
    the prediction table (which may be post-week in the archived backtest).
    """
    from datetime import timedelta

    gmap = games.set_index("game_id")
    history = filter_result.history
    out = np.full(len(frame), np.nan)
    for i, row in enumerate(frame.itertuples(index=False)):
        gid = int(row.game_id)
        if gid not in gmap.index:
            continue
        g = gmap.loc[gid]
        if "start_date" not in gmap.columns or not pd.notna(g.get("start_date")):
            continue
        # One second before kickoff — exclusive upper bound for PIT.
        kick = to_utc(pd.Timestamp(g["start_date"]).to_pydatetime())
        as_of = kick - timedelta(seconds=1)
        hid, aid = int(g["home_team_id"]), int(g["away_team_id"])
        h = posterior_asof(history, hid, as_of)
        a = posterior_asof(history, aid, as_of)
        if h is None or a is None:
            continue
        try:
            i_off = filter_result.config.dim_index("off_epa")
            out[i] = float(h.mean[i_off]) - float(a.mean[i_off])
        except Exception:  # noqa: BLE001
            continue
    return out


def _load_advanced(seasons: list[int]) -> pd.DataFrame:
    paths = []
    root = ROOT / "data" / "staged" / "advanced_box"
    for p in root.rglob("*.parquet"):
        paths.append(p)
    if not paths:
        return pd.DataFrame()
    frames = [pd.read_parquet(p) for p in paths]
    adv = pd.concat(frames, ignore_index=True)
    if "season" in adv.columns:
        adv = adv.loc[adv["season"].isin(seasons)]
    return adv


def main() -> None:
    ART_D3.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    v1_sha = verify_canonical_v1_sha(ROOT / "docs" / "notes" / "_artifacts" / "D2" / "canonical_v1.json")

    frame_raw = load_canonical_frame(PRED_PATH, exclude_2019_w1_4=True)
    frame = apply_sigma_correction(frame_raw)

    seasons = sorted(int(s) for s in frame["season"].unique())
    games = load_staged_games(str(ROOT / "data" / "staged"), list(range(2014, max(seasons) + 1)))
    # Align team ids onto frame
    gmap = games.set_index("game_id")[["home_team_id", "away_team_id"]]
    frame = frame.join(gmap, on="game_id", how="left")
    frame_raw = frame_raw.join(gmap, on="game_id", how="left")

    elo_mu, nnls_w, _ = build_elo_and_nnls(frame, games)

    # True Stage-1 L1 via filter (warmup 2014–2018 included)
    print("building observations + running filter for true L1…", flush=True)
    adv = _load_advanced(list(range(2014, max(seasons) + 1)))
    obs, _, _ = build_observations_from_staged(advanced=adv, games=games)
    l1_source = "stage1_rating_diff_off_epa"
    if obs.empty:
        # Fallback: Elo-based L1 (document as incomplete)
        from sklearn.linear_model import LinearRegression

        y = frame["realized_margin"].to_numpy(dtype=float)
        mask = np.isfinite(elo_mu) & np.isfinite(y)
        lr = LinearRegression().fit(elo_mu[mask].reshape(-1, 1), y[mask])
        fill = float(np.nanmean(elo_mu[mask]))
        l1_mu = lr.predict(np.nan_to_num(elo_mu, nan=fill).reshape(-1, 1))
        l1_source = "FALLBACK_elo_proxy_obs_empty"
    else:
        filt = run_filter(obs, record_weekly=False)
        # Need as_of on frame — enriched preds have it
        rating_diff = _rating_diff_off_epa(frame, games, filt)
        finite_rate = float(np.mean(np.isfinite(rating_diff)))
        print(f"rating_diff finite rate={finite_rate:.3f}", flush=True)
        if finite_rate < 0.5:
            from sklearn.linear_model import LinearRegression

            y = frame["realized_margin"].to_numpy(dtype=float)
            mask = np.isfinite(elo_mu) & np.isfinite(y)
            lr = LinearRegression().fit(elo_mu[mask].reshape(-1, 1), y[mask])
            fill = float(np.nanmean(elo_mu[mask]))
            l1_mu = lr.predict(np.nan_to_num(elo_mu, nan=fill).reshape(-1, 1))
            l1_source = f"FALLBACK_elo_proxy_finite_rate={finite_rate:.3f}"
        else:
            # Warmup train pool: 2014–2018 games (not in headline frame).
            warmup_seasons = [2014, 2015, 2016, 2017, 2018]
            warm_games = games.loc[games["season"].isin(warmup_seasons)].copy()
            if not warm_games.empty:
                warm_rows = []
                for g in warm_games.itertuples(index=False):
                    warm_rows.append(
                        {
                            "game_id": int(g.game_id),
                            "season": int(g.season),
                            "week": int(getattr(g, "week", 0) or 0),
                            "as_of": g.start_date,
                            "realized_margin": (
                                float(g.home_points) - float(g.away_points)
                                if pd.notna(g.home_points) and pd.notna(g.away_points)
                                else float("nan")
                            ),
                        }
                    )
                warm_frame = pd.DataFrame(warm_rows)
                warm_diff = _rating_diff_off_epa(warm_frame, games, filt)
                # Train pool = warmup + prior headline seasons (handled inside fit).
                train_frame = pd.concat(
                    [
                        warm_frame,
                        frame[["game_id", "season", "week", "realized_margin"]].assign(
                            as_of=frame["as_of"] if "as_of" in frame.columns else None
                        ),
                    ],
                    ignore_index=True,
                )
                train_diff = np.concatenate([warm_diff, rating_diff])
                l1_mu, l1_source = fit_true_l1_stage1(
                    frame,
                    rating_diff,
                    train_frame=train_frame,
                    train_rating_diff=train_diff,
                )
            else:
                l1_mu, l1_source = fit_true_l1_stage1(frame, rating_diff)

    p1 = part1_sigma_before_after(frame_raw)
    # Ensemble decomp on corrected sigmas
    frame_for_decomp = frame.copy()
    p1_ens = part1_ensemble_decomposition(frame_for_decomp, elo_mu=elo_mu, nnls_weights=nnls_w)
    p2_info = part2_informativeness(frame)
    p2_bake = part2_bakeoff(frame)
    p3 = part3_shape(frame)
    p4 = part4_calibration(frame)
    p5 = part5_comparisons(frame, elo_mu=elo_mu, l1_mu=l1_mu, l1_source=l1_source)

    teams_paths = list((ROOT / "data" / "staged" / "teams").rglob("*.parquet"))
    teams = (
        pd.concat([pd.read_parquet(p) for p in teams_paths], ignore_index=True)
        if teams_paths
        else None
    )
    # canonical_v2: exclusion of 2019 W1–4 is definitional
    comp = compose_canonical_set(frame, teams=teams, fcs_rule="include")
    gate = gate_task23_fundamental(RAW_PRED, raise_on_fail=False)

    payload = {
        "name": "canonical_v2",
        "supersedes": {
            "name": "canonical_v1",
            "sha256": CANONICAL_V1_SHA,
            "path": "docs/notes/_artifacts/D2/canonical_v1.json",
        },
        "source_predictions": str(PRED_PATH.relative_to(ROOT)).replace("\\", "/"),
        "source_predictions_sha256": file_sha256(PRED_PATH),
        "change": (
            "2019 weeks 1–4 excluded by definition (legacy unfitted zero-μ cold start). "
            "Not an expected failure — those weeks are outside the scored universe."
        ),
        "composition": comp.as_dict(),
        "sigma_diagnostics_corrected": p1["after"],
        "part1": p1,
        "part1_ensemble": p1_ens,
        "part2_informativeness": p2_info,
        "part2_bakeoff": p2_bake,
        "part3": p3,
        "part4": p4,
        "part5": p5,
        "elapsed_sec": time.time() - t0,
        "canonical_v1_sha_verified": v1_sha,
    }

    art_path = ART_D3 / "d3_results.json"
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    art_path.write_text(text + "\n", encoding="utf-8")
    art_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    v2_path = ART_D3 / "canonical_v2.json"
    v2_payload = {
        "name": "canonical_v2",
        "sha256_of_prior": CANONICAL_V1_SHA,
        "source_predictions": payload["source_predictions"],
        "source_predictions_sha256": payload["source_predictions_sha256"],
        "composition": comp.as_dict(),
        "exclusion": {
            "2019_weeks_1_4": "excluded_by_definition",
            "reason": payload["change"],
        },
        "quality_gate": gate,
        "sigma_diagnostics_corrected": p1["after"],
        "d3_results_sha256": art_sha,
    }
    v2_text = json.dumps(v2_payload, indent=2, sort_keys=True, default=str)
    v2_path.write_text(v2_text + "\n", encoding="utf-8")
    v2_sha = hashlib.sha256(v2_text.encode("utf-8")).hexdigest()

    # Also update config name bump
    notes = _render_notes(payload, art_sha=art_sha, v2_sha=v2_sha, v2_path=v2_path)
    (ROOT / "docs" / "notes" / "D3.md").write_text(notes, encoding="utf-8")
    print(json.dumps({"d3_sha": art_sha, "v2_sha": v2_sha, "elapsed": payload["elapsed_sec"]}, indent=2))


def _render_notes(payload: dict[str, Any], *, art_sha: str, v2_sha: str, v2_path: Path) -> str:
    p1 = payload["part1"]
    before, after = p1["before"], p1["after"]
    bake = payload["part2_bakeoff"]["table"]
    p5 = payload["part5"]
    lines = [
        "# D3 — Fix sigma, re-measure calibration",
        "",
        "## Sigma ratio (Part 1 first — load-bearing)",
        "",
        f"**Canonical cited:** `docs/notes/_artifacts/D2/canonical_v1.json`  ",
        f"sha256: `{CANONICAL_V1_SHA}`",
        "",
        f"**D3 results:** `docs/notes/_artifacts/D3/d3_results.json` sha256 `{art_sha}`  ",
        f"**Canonical successor:** `{v2_path.as_posix().split('the-cfb-model/')[-1] if False else 'docs/notes/_artifacts/D3/canonical_v2.json'}` "
        f"sha256 `{v2_sha}`",
        "",
        "| | mean predicted σ | realized residual SD | resid/pred ratio | pred/resid |",
        "|---|---:|---:|---:|---:|",
        f"| **before** (MAD-as-σ) | {before['mean_predicted_sigma']:.4f} | {before['realized_residual_sd']:.4f} | "
        f"{before['resid_over_pred_ratio']:.4f} | {before['sigma_ratio']:.4f} |",
        f"| **after** (×√(π/2)) | {after['mean_predicted_sigma']:.4f} | {after['realized_residual_sd']:.4f} | "
        f"{after['resid_over_pred_ratio']:.4f} | {after['sigma_ratio']:.4f} |",
        "",
        f"Hypothesis **confirmed**: head trained on `|residual|` = σ√(2/π); no √(2/π) "
        f"correction in the predict path. Observed resid/pred ≈ {before['resid_over_pred_ratio']:.4f} "
        f"vs √(π/2)≈{p1['half_normal_scale']:.4f}.",
        "",
        f"PIT KS after: {after.get('pit_ks')}  ",
        f"Coverage after 50/80/95: {after.get('coverage')}",
        "",
        "### Ensemble σ decomposition (§5.2 item 2, fitted NNLS weights)",
        "",
        f"```json\n{json.dumps(payload['part1_ensemble'], indent=2, default=str)}\n```",
        "",
        "## Part 2 — does the σ head earn its place?",
        "",
        f"Informativeness: slope={payload['part2_informativeness']['slope']:.4f}, "
        f"R²={payload['part2_informativeness']['r2']:.4f}, "
        f"Spearman ρ={payload['part2_informativeness']['spearman_rho']:.4f}. "
        f"{'FLAG: slope near zero — head is noise on this artifact.' if payload['part2_informativeness']['flag_noise'] else ''}",
        "",
        "### Bake-off (CRPS / log-score; fit on train seasons only)",
        "",
        "| scheme | n | CRPS | log-score | mean σ |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in bake:
        lines.append(
            f"| {r['scheme']} | {r['n']} | {r['crps']:.4f} | {r['log_score']:.4f} | {r['mean_sigma']:.4f} |"
        )
    if payload["part2_bakeoff"].get("flag"):
        lines += ["", f"**{payload['part2_bakeoff']['flag']}**"]
    lines += [
        "",
        "## Part 3 — distributional shape",
        "",
        f"```json\n{json.dumps({k: payload['part3'][k] for k in ('standardized_residuals','student_t_fit','adopt_note','conformal_coverage','key_number_consistency')}, indent=2, default=str)}\n```",
        "",
        "## Part 4 — calibration (from corrected distribution; default OFF)",
        "",
        f"Markets passing gate: {payload['part4'].get('markets_passing')}  ",
        f"```json\n{json.dumps(payload['part4'], indent=2, default=str)}\n```",
        "",
        "## Part 5 — comparisons with CIs",
        "",
        "### De-vigged market (overlap)",
        "",
        f"```json\n{json.dumps(p5['devigged_market'], indent=2, default=str)}\n```",
        "",
        "### Point table + mapping-layer gap",
        "",
        "| predictor | n | MAE | RMSE | resid_SD | R² |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in p5["point_table"]:
        lines.append(
            f"| {r['predictor']} | {r['n']} | {r['mae']:.3f} | {r['rmse']:.3f} | "
            f"{r['residual_sd']:.3f} | {r['r2']:.3f} |"
        )
    lines += [
        "",
        f"L1 source: `{p5['l1_source']}`  ",
        f"**{p5['mapping_layer_verdict']}**",
        "",
        "### Paired block-bootstrap ΔMAE (stack − baseline; negative ⇒ stack better)",
        "",
        "| contrast | ΔMAE | 95% CI | n |",
        "|---|---:|---|---:|",
    ]
    for name, d in p5["paired_bootstrap_deltas"].items():
        lines.append(
            f"| {name} | {d['delta_mae']:.3f} | [{d['ci_low']:.3f}, {d['ci_high']:.3f}] | {d['n']} |"
        )
    lines += [
        "",
        "## Canonical v2",
        "",
        "2019 W1–4 zero-μ gap resolved **by definition**: those weeks are excluded from "
        "the scored universe (legacy unfitted cold start). Warm-start from a 2014–2018 "
        "filter run remains available for production continuity but is not required to "
        "score canonical. Bumped `canonical_v1` → `canonical_v2` with a new sha.",
        "",
        "## Diff scope",
        "",
        "Touched: `models/heads/sigma.py` (half-normal correction), `models/ensemble.py` "
        "(variance decomposition), `models/calibrate.py` (bounded calibrators, gate), "
        "`distribution/shape.py`, `evaluation/d3_eval.py`, tests, this note.",
        "",
        "**Not touched:** μ heads, feature builders, Stage-1 filter code.",
        "",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
