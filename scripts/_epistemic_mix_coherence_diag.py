"""Read-only: week-1 pre/post epistemic-mix μ vs q10/q90 vs filter_history.

Captures pred_margin immediately before and after the epistemic-mix block,
plus pred_margin_q10/q90 from the quantile head on the same features.
Does not publish. Skips MC (not needed for this gate).

Usage:
  uv run python scripts/_epistemic_mix_coherence_diag.py
"""

from __future__ import annotations

import json
import pickle
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.production_stack import (
    ProductionFeatureProvider,
    StateSpaceRatingEngine,
    build_observations_from_staged,
)
from ncaa_quant.models.heads.quantile import quantile_column
from ncaa_quant.pipelines.predict import (
    _concat_hive_table,
    exclude_games_kicked_off_before,
    live_observation_seasons,
    load_champion_walkforward_config,
)
from ncaa_quant.registry.bundle import (
    ENSEMBLE_FILENAME,
    POSSESSIONS_FILENAME,
    load_production_ensemble,
)
from ncaa_quant.registry.store import ModelRegistry
from ncaa_quant.utils.seeding import set_global_seed

# Graded week-1 rows are dominated by Aug 27 daily_refresh (kind precedence).
AS_OF = datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)
SEASON = 2026
WEEK = 1
OUT_JSON = ROOT / "data" / "tmp" / "epistemic_mix_coherence_w1.json"
FEATURES_CACHE = ROOT / "data" / "tmp" / "epistemic_mix_w1_features.parquet"
META_CACHE = ROOT / "data" / "tmp" / "epistemic_mix_w1_meta.json"


def _fbs_team_ids(teams: pd.DataFrame | None) -> set[int]:
    if teams is None or teams.empty or "classification" not in teams.columns:
        return set()
    cls = teams["classification"].astype(str).str.lower()
    mask = cls.eq("fbs")
    if "id" in teams.columns:
        return {int(x) for x in teams.loc[mask, "id"].dropna()}
    if "team_id" in teams.columns:
        return {int(x) for x in teams.loc[mask, "team_id"].dropna()}
    return set()


def _filter_history_ids(path: Path) -> set[int]:
    fh = pd.read_parquet(path)
    col = "team_id" if "team_id" in fh.columns else "id"
    return {int(x) for x in fh[col].dropna().unique()}


def _inside(mu: float, q10: float, q90: float) -> bool:
    return bool(np.isfinite(mu) and np.isfinite(q10) and np.isfinite(q90) and q10 < mu < q90)


def main() -> None:
    from ncaa_quant.cli import load_fitted_priors_frame_for_backtest

    cfg = load_config()
    wf = load_champion_walkforward_config()
    set_global_seed(int(wf.seed))
    replay = wf.all_replay_seasons()

    staged = Path(cfg.paths.staged_dir)
    store = ParquetStore(staged)
    season_games = load_staged_games(staged, (SEASON,))
    week_games = season_games.loc[season_games["week"].astype(int) == WEEK].copy()
    publish_games, n_excluded = exclude_games_kicked_off_before(week_games, AS_OF)
    print(
        f"slate week={WEEK} as_of={AS_OF.isoformat()} "
        f"n_week={len(week_games)} n_excluded={n_excluded} n_publish={len(publish_games)}",
        flush=True,
    )

    reg = ModelRegistry(Path(cfg.paths.data_dir) / "registry", tracking_uri=None)
    champ = reg.resolve_champion()
    art_dir = Path(champ.artifact_dir)
    predictor = load_production_ensemble(art_dir / ENSEMBLE_FILENAME)
    n_ep = int(predictor.n_epistemic_draws)
    print(f"n_epistemic_draws={n_ep} mix_runs={n_ep >= 2}", flush=True)

    if FEATURES_CACHE.is_file() and META_CACHE.is_file():
        meta = json.loads(META_CACHE.read_text(encoding="utf-8"))
        if meta.get("as_of") == AS_OF.isoformat() and meta.get("n_games") == len(publish_games):
            print(f"loading cached features {FEATURES_CACHE}", flush=True)
            features = pd.read_parquet(FEATURES_CACHE)
        else:
            features = None
    else:
        features = None

    if features is None:
        obs_seasons = live_observation_seasons(SEASON)
        obs_games = load_staged_games(staged, obs_seasons)
        plays = _concat_hive_table(store, "plays", obs_seasons)
        advanced = _concat_hive_table(store, "advanced_box", obs_seasons)
        teams = _concat_hive_table(store, "teams", obs_seasons)
        plays_preferred = plays is not None and not plays.empty
        obs, _, _ = build_observations_from_staged(
            plays=plays if plays_preferred else None,
            games=obs_games,
            advanced=None if plays_preferred else advanced,
            garbage_time_filter=bool(wf.garbage_time_filter),
        )
        priors = load_fitted_priors_frame_for_backtest(staged, replay)
        if priors is None or priors.empty:
            raise SystemExit("fitted priors missing")

        engine = StateSpaceRatingEngine(
            observations=obs,
            config=wf,
            priors_frame=priors,
            fbs_team_ids=_fbs_team_ids(teams) or None,
        )
        print("Kalman start…", flush=True)
        engine.initialize_season(SEASON, AS_OF)
        rating_state = engine.state_snapshot()
        print(f"Kalman done n_keys={len(rating_state)}", flush=True)

        provider = ProductionFeatureProvider(config=wf, snapshots=None, cfbd_lines=None)
        poss_path = art_dir / POSSESSIONS_FILENAME
        if poss_path.is_file():
            with poss_path.open("rb") as fh:
                poss = pickle.load(fh)  # noqa: S301
            if isinstance(poss, dict):
                provider._possessions_artifacts = poss  # noqa: SLF001

        features = provider.compute_game_features(
            publish_games,
            AS_OF,
            rating_state=rating_state,
            market_features=False,
        )
        FEATURES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        features.to_parquet(FEATURES_CACHE, index=False)
        META_CACHE.write_text(
            json.dumps({"as_of": AS_OF.isoformat(), "n_games": len(publish_games)}),
            encoding="utf-8",
        )
        print(f"cached features -> {FEATURES_CACHE}", flush=True)

    print(f"features n={len(features)} cols={len(features.columns)}", flush=True)
    features = features.copy()
    features["game_id"] = features["game_id"].astype(str)

    # --- capture: pre-mix μ, post-mix μ, q10/q90 (same feature frame) ---
    point = predictor._predict_point(features)  # noqa: SLF001
    point["game_id"] = point["game_id"].astype(str)
    pre = point.set_index("game_id")["pred_margin"].astype(float)
    print(
        f"pre_mu finite={int(pre.apply(np.isfinite).sum())}/{len(pre)} "
        f"mean={float(pre.mean()):.3f}",
        flush=True,
    )

    qpred = predictor.quantile_margin_head.predict(features)
    qpred["game_id"] = qpred["game_id"].astype(str)
    q = qpred.set_index("game_id")
    q10_col = quantile_column("margin", 0.1)
    q90_col = quantile_column("margin", 0.9)
    q10 = q[q10_col].astype(float)
    q90 = q[q90_col].astype(float)
    # Match export: sort crossed heads
    q_lo = pd.concat([q10, q90], axis=1).min(axis=1)
    q_hi = pd.concat([q10, q90], axis=1).max(axis=1)
    print(
        f"q10 finite={int(q_lo.apply(np.isfinite).sum())}/{len(q_lo)} "
        f"q90 finite={int(q_hi.apply(np.isfinite).sum())}/{len(q_hi)}",
        flush=True,
    )

    if n_ep >= 2:
        print(f"epistemic mix ({n_ep} draws)…", flush=True)
        mix = predictor._epistemic_mix(  # noqa: SLF001
            features, rho=float(predictor._rho), seed=int(predictor.seed)  # noqa: SLF001
        )
        post = pd.Series(
            mix.params.mu_m, index=point["game_id"].astype(str).to_numpy(), dtype=float
        )
        mix_ran = True
        print(
            f"post_mu finite={int(post.apply(np.isfinite).sum())}/{len(post)} "
            f"mean={float(post.mean()):.3f} "
            f"mean_abs_shift={float((post - pre).abs().mean()):.4f}",
            flush=True,
        )
    else:
        post = pre.copy()
        mix_ran = False

    fh_ids = _filter_history_ids(
        Path(cfg.paths.data_dir) / "artifacts" / "state_space" / "filter_history.parquet"
    )

    # Schedule team ids
    sched = publish_games.copy()
    id_home = "home_id" if "home_id" in sched.columns else "home_team_id"
    id_away = "away_id" if "away_id" in sched.columns else "away_team_id"
    sched["game_id"] = sched["game_id"].astype(str)
    sched["_home_id"] = sched[id_home].astype(int)
    sched["_away_id"] = sched[id_away].astype(int)
    sched["home_in_fh"] = sched["_home_id"].isin(fh_ids)
    sched["away_in_fh"] = sched["_away_id"].isin(fh_ids)
    sched["both_in_fh"] = sched["home_in_fh"] & sched["away_in_fh"]
    sched["either_absent"] = ~sched["both_in_fh"]

    rows: list[dict[str, Any]] = []
    for gid in point["game_id"].astype(str):
        mu_pre = float(pre.loc[gid])
        mu_post = float(post.loc[gid])
        lo = float(q_lo.loc[gid])
        hi = float(q_hi.loc[gid])
        meta = sched.loc[sched["game_id"] == gid].iloc[0]
        shift = mu_post - mu_pre
        rows.append(
            {
                "game_id": gid,
                "home_team": str(meta.get("home_team", "")),
                "away_team": str(meta.get("away_team", "")),
                "both_in_fh": bool(meta["both_in_fh"]),
                "mu_pre": mu_pre,
                "mu_post": mu_post,
                "mix_shift": float(shift),
                "q10": lo,
                "q90": hi,
                "pre_inside": _inside(mu_pre, lo, hi),
                "post_inside": _inside(mu_post, lo, hi),
            }
        )

    frame = pd.DataFrame(rows)

    def crosstab(label: str, mask: pd.Series) -> dict[str, Any]:
        sub = frame.loc[mask]
        n = len(sub)
        if n == 0:
            return {"label": label, "n": 0}
        return {
            "label": label,
            "n": n,
            "mean_abs_shift": float(sub["mix_shift"].abs().mean()),
            "median_abs_shift": float(sub["mix_shift"].abs().median()),
            "max_abs_shift": float(sub["mix_shift"].abs().max()),
            "pre_inside_n": int(sub["pre_inside"].sum()),
            "pre_inside_rate": float(sub["pre_inside"].mean()),
            "post_inside_n": int(sub["post_inside"].sum()),
            "post_inside_rate": float(sub["post_inside"].mean()),
            "pre_out_post_in": int((~sub["pre_inside"] & sub["post_inside"]).sum()),
            "pre_in_post_out": int((sub["pre_inside"] & ~sub["post_inside"]).sum()),
            "both_out": int((~sub["pre_inside"] & ~sub["post_inside"]).sum()),
            "both_in": int((sub["pre_inside"] & sub["post_inside"]).sum()),
        }

    summary = {
        "as_of": AS_OF.isoformat(),
        "season": SEASON,
        "week": WEEK,
        "n_epistemic_draws": n_ep,
        "mix_ran": mix_ran,
        "n_games": len(frame),
        "overall": crosstab("overall", pd.Series(True, index=frame.index)),
        "both_in_fh": crosstab("both_in_fh", frame["both_in_fh"]),
        "either_absent": crosstab("either_absent", ~frame["both_in_fh"]),
        "games_pre_out": frame.loc[~frame["pre_inside"]].to_dict(orient="records"),
        "games_post_out": frame.loc[~frame["post_inside"]].to_dict(orient="records"),
        "games_flipped_by_mix": frame.loc[
            frame["pre_inside"] != frame["post_inside"]
        ].to_dict(orient="records"),
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in (
        "n_epistemic_draws", "mix_ran", "n_games", "overall", "both_in_fh", "either_absent"
    )}, indent=2))
    print(f"wrote {OUT_JSON}")
    print(f"flipped_by_mix n={len(summary['games_flipped_by_mix'])}")
    print(f"pre_out n={len(summary['games_pre_out'])} post_out n={len(summary['games_post_out'])}")


if __name__ == "__main__":
    main()
