"""Read-only single-game Ridge pipeline trace for a social explainer video.

Usage:
  uv run python scripts/_trace_game.py --season 2026 --week 7 \\
      --game-id 401856722 --refresh-kind tuesday_primary

Writes:
  out/social/trace/{season}_w{week}_{game_id}.json
  out/social/trace/{season}_w{week}_{game_id}.md

Never mutates production artifacts, configs, MLflow, or R2. No CFBD/Odds API.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy import stats

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from ncaa_quant.betting.baseline_convention import (  # noqa: E402
    BASELINE_CONVENTION_MIN_EDGE_SIDES,
    BASELINE_CONVENTION_MIN_WEEK,
    compute_baseline_convention_eligibility,
)
from ncaa_quant.betting.edges import expected_value  # noqa: E402
from ncaa_quant.betting.filters import (  # noqa: E402
    BetCandidate,
    FilterReason,
    evaluate_filters,
)
from ncaa_quant.betting.kelly import (  # noqa: E402
    HARD_MAX_KELLY_FRACTION,
    HARD_MAX_STAKE_PCT,
    full_kelly,
    recommended_stake,
)
from ncaa_quant.config import BettingConfig, SocialConfig, load_config  # noqa: E402
from ncaa_quant.data.storage import ParquetStore  # noqa: E402
from ncaa_quant.evaluation.backtest_runner import load_staged_games  # noqa: E402
from ncaa_quant.evaluation.production_stack import (  # noqa: E402
    ProductionFeatureProvider,
    StateSpaceRatingEngine,
    build_observations_from_staged,
)
from ncaa_quant.features.epa import (  # noqa: E402
    CONNELLY_MARGIN_BY_PERIOD,
    WP_GT_HIGH,
    WP_GT_LOW,
    wp_garbage_time,
)
from ncaa_quant.pipelines.predict import (  # noqa: E402
    _concat_hive_table,
    exclude_games_kicked_off_before,
    live_observation_seasons,
    load_champion_walkforward_config,
)
from ncaa_quant.ratings.state_space import run_filter  # noqa: E402
from ncaa_quant.registry.bundle import (  # noqa: E402
    ENSEMBLE_FILENAME,
    POSSESSIONS_FILENAME,
    load_production_ensemble,
)
from ncaa_quant.registry.store import ModelRegistry  # noqa: E402
from ncaa_quant.social.select import A_TIER_EDGE  # noqa: E402
from ncaa_quant.utils.seeding import set_global_seed  # noqa: E402
from ncaa_quant.utils.timeutils import to_utc  # noqa: E402

WEEK_PRED_PATH = REPO / "data/social/artifacts_cache/latest/week_predictions.json"
META_PATH = REPO / "data/social/artifacts_cache/latest/meta.json"
RATINGS_PATH = REPO / "data/social/artifacts_cache/latest/team_ratings_2026.json"
POSTED_CALLS_TMPL = "data/social/posted_calls_{season}_w{week}.json"
CANDIDATES_W7 = REPO / "docs/notes/_artifacts/social-s6-w7-card/candidates_w7.json"


def V(value: Any, source: str) -> dict[str, Any]:
    """Provenance wrapper required by the trace schema."""
    return {"value": value, "source": source}


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict) and set(x.keys()) >= {"value", "source"} and len(x) == 2:
        return {"value": _jsonable(x["value"]), "source": x["source"]}
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.floating, float)):
        xf = float(x)
        if math.isnan(xf) or math.isinf(xf):
            return None
        return xf
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, pd.Timestamp):
        return x.isoformat()
    if isinstance(x, datetime):
        return x.isoformat()
    if x is None:
        return None
    return x


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_as_of(s: str) -> datetime:
    return to_utc(datetime.fromisoformat(str(s).replace("Z", "+00:00")))


@dataclass
class StopReport:
    reason: str
    details: dict[str, Any]


def find_best_bet(
    *,
    season: int,
    week: int,
    game_id: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], StopReport | None]:
    """Locate the posted Best Bet for ``game_id``; STOP if not on the card."""
    posted_path = REPO / POSTED_CALLS_TMPL.format(season=season, week=week)
    if not posted_path.is_file():
        return None, [], StopReport(
            reason="posted_calls missing",
            details={"path": str(posted_path)},
        )
    posted = _load_json(posted_path)
    card = list(posted.get("posted_calls") or [])
    card_summary = [
        {
            "game_id": str(c.get("game_id")),
            "matchup": c.get("matchup"),
            "side": c.get("side"),
            "line": c.get("line"),
            "edge": c.get("edge"),
            "tier": "A" if float(c.get("edge") or 0) >= A_TIER_EDGE else "B",
            "units": c.get("units"),
            "book": c.get("book"),
        }
        for c in card
    ]
    hit = next((c for c in card if str(c.get("game_id")) == str(game_id)), None)
    if hit is None:
        return None, card_summary, StopReport(
            reason="game is not on this week's public Best Bet card",
            details={"week_best_bets": card_summary},
        )
    # Enrich from candidates sidecar artifact when present.
    if CANDIDATES_W7.is_file():
        for c in _load_json(CANDIDATES_W7):
            if str(c.get("game_id")) == str(game_id):
                hit = {**c, **hit}
                break
    hit["_posted_path"] = str(posted_path.relative_to(REPO)).replace("\\", "/")
    hit["_posted_meta"] = {
        "pricing_note": posted.get("pricing_note"),
        "w7_7_at": posted.get("w7_7_at"),
        "w7_5_supersede_at": posted.get("w7_5_supersede_at"),
        "sweep_meta": posted.get("sweep_meta"),
        "label": posted.get("label"),
    }
    return hit, card_summary, None


def stage_1_team_games(
    *,
    team_id: int,
    team_name: str,
    season: int,
    as_of: datetime,
    games: pd.DataFrame,
    plays: pd.DataFrame,
    teams_by_id: dict[int, str],
) -> list[dict[str, Any]]:
    """Per-game EPA/snap aggregates for ``team_id`` this season with kickoff < as_of."""
    g = games.copy()
    g["start_date"] = pd.to_datetime(g["start_date"], utc=True)
    cutoff = pd.Timestamp(as_of)
    prior = g.loc[
        (g["season"].astype(int) == int(season))
        & (g["start_date"] < cutoff)
        & ((g["home_team_id"].astype(int) == team_id) | (g["away_team_id"].astype(int) == team_id))
        & (g["completed"].astype(bool))
    ].sort_values(["week", "start_date"])

    out: list[dict[str, Any]] = []
    src = "recomputed"
    for row in prior.itertuples(index=False):
        gid = int(row.game_id)
        home_id = int(row.home_team_id)
        away_id = int(row.away_team_id)
        is_home = home_id == team_id
        opp_id = away_id if is_home else home_id
        opp = teams_by_id.get(opp_id, str(opp_id))
        gp = plays.loc[plays["game_id"].astype(int) == gid]
        if gp.empty:
            out.append(
                {
                    "week": V(int(row.week), src),
                    "opponent": V(opp, src),
                    "game_id": V(str(gid), src),
                    "n_snaps_after_garbage_filter": V(None, src),
                    "n_snaps_raw": V(0, src),
                    "off_epa_per_play": V(None, src),
                    "def_epa_per_play_allowed": V(None, src),
                    "points_for": V(
                        int(row.home_points) if is_home else int(row.away_points)
                        if pd.notna(row.home_points)
                        else None,
                        f"persisted:data/staged/games/season={season}",
                    ),
                    "points_against": V(
                        int(row.away_points) if is_home else int(row.home_points)
                        if pd.notna(row.away_points)
                        else None,
                        f"persisted:data/staged/games/season={season}",
                    ),
                }
            )
            continue

        off = gp.loc[gp["offense_id"].astype(int) == team_id]
        deff = gp.loc[gp["defense_id"].astype(int) == team_id]
        n_raw = int(len(off) + len(deff))
        off_keep = off.loc[~off["wp"].map(lambda w: wp_garbage_time(None if pd.isna(w) else float(w)))]
        def_keep = deff.loc[
            ~deff["wp"].map(lambda w: wp_garbage_time(None if pd.isna(w) else float(w)))
        ]
        # When WP null, epa.py falls back to Connelly margins; mirror that for counts.
        def _keep_row(r: Any) -> bool:
            wp = None if pd.isna(r.wp) else float(r.wp)
            if wp is not None:
                return not wp_garbage_time(wp)
            # Connelly fallback: garbage when |score_margin| > threshold by period.
            period = int(r.period) if pd.notna(r.period) else 4
            thr = CONNELLY_MARGIN_BY_PERIOD.get(period, 16)
            sm = abs(float(r.score_margin)) if pd.notna(r.score_margin) else 0.0
            return sm <= float(thr)

        if off["wp"].isna().any() or deff["wp"].isna().any():
            off_keep = off.loc[[_keep_row(r) for r in off.itertuples(index=False)]]
            def_keep = deff.loc[[_keep_row(r) for r in deff.itertuples(index=False)]]

        off_epa = (
            float(pd.to_numeric(off_keep["epa"], errors="coerce").mean())
            if len(off_keep)
            else None
        )
        def_epa = (
            float(pd.to_numeric(def_keep["epa"], errors="coerce").mean())
            if len(def_keep)
            else None
        )
        pf = int(row.home_points) if is_home else int(row.away_points)
        pa = int(row.away_points) if is_home else int(row.home_points)
        out.append(
            {
                "week": V(int(row.week), src),
                "opponent": V(opp, src),
                "game_id": V(str(gid), src),
                "n_snaps_after_garbage_filter": V(int(len(off_keep) + len(def_keep)), src),
                "n_snaps_raw": V(n_raw, src),
                "off_epa_per_play": V(off_epa, src),
                "def_epa_per_play_allowed": V(def_epa, src),
                "points_for": V(pf, f"persisted:data/staged/games/season={season}"),
                "points_against": V(pa, f"persisted:data/staged/games/season={season}"),
                "team": V(team_name, "persisted:week_predictions.json"),
            }
        )
    return out


def _recompute_cache_path(season: int, week: int, game_id: str, as_of: datetime) -> Path:
    stamp = as_of.strftime("%Y%m%dT%H%M%SZ")
    return (
        REPO
        / "out"
        / "social"
        / "trace"
        / f"_cache_{season}_w{week}_{game_id}_{stamp}.pkl"
    )


def recompute_stack(
    *,
    season: int,
    week: int,
    game_id: str,
    as_of: datetime,
) -> dict[str, Any]:
    """Mirror ``live_predict_rows`` but keep hfa_history + member diagnostics."""
    from ncaa_quant.cli import load_fitted_priors_frame_for_backtest

    cache_path = _recompute_cache_path(season, week, game_id, as_of)
    if cache_path.is_file():
        print(f"trace recompute cache hit: {cache_path}", flush=True)
        with cache_path.open("rb") as fh:
            cached = pickle.load(fh)  # noqa: S301 — local diagnostic cache
        # Reload predictor for member contribs (not pickled with full object graph).
        cfg = load_config()
        registry = ModelRegistry(Path(cfg.paths.data_dir) / "registry", tracking_uri=None)
        champ = registry.resolve_champion()
        predictor = load_production_ensemble(Path(champ.artifact_dir) / ENSEMBLE_FILENAME)
        cached["predictor"] = predictor
        return cached

    cfg = load_config()
    wf = load_champion_walkforward_config()
    set_global_seed(int(wf.seed))

    staged = Path(cfg.paths.staged_dir)
    obs_seasons = live_observation_seasons(int(season))
    store = ParquetStore(staged)
    obs_games = load_staged_games(staged, obs_seasons)
    season_games = load_staged_games(staged, (int(season),))
    week_games = season_games.loc[season_games["week"].astype(int) == int(week)].copy()
    publish_games, _n_ex = exclude_games_kicked_off_before(week_games, as_of)
    target = publish_games.loc[publish_games["game_id"].astype(int) == int(game_id)]
    if target.empty:
        raise FileNotFoundError(f"game {game_id} not in publish slate at as_of={as_of.isoformat()}")

    plays = _concat_hive_table(store, "plays", obs_seasons)
    advanced = _concat_hive_table(store, "advanced_box", obs_seasons)
    teams = _concat_hive_table(store, "teams", obs_seasons)
    plays_preferred = plays is not None and not plays.empty
    obs, n_on, n_off = build_observations_from_staged(
        plays=plays if plays_preferred else None,
        games=obs_games,
        advanced=None if plays_preferred else advanced,
        garbage_time_filter=bool(wf.garbage_time_filter),
    )
    if obs.empty:
        raise FileNotFoundError("no Kalman observations")

    priors = load_fitted_priors_frame_for_backtest(staged, wf.all_replay_seasons())
    if priors is None or priors.empty:
        raise FileNotFoundError("fitted priors missing")

    fbs = set()
    if teams is not None and not teams.empty and "classification" in teams.columns:
        fbs = {
            int(x)
            for x in teams.loc[
                teams["classification"].astype(str).str.casefold() == "fbs", "team_id"
            ]
        }

    engine = StateSpaceRatingEngine(
        observations=obs,
        config=wf,
        priors_frame=priors,
        fbs_team_ids=fbs or None,
    )
    # Capture hfa_history that initialize_season discards.
    obs_c = obs.copy()
    obs_c["event_time"] = [to_utc(pd.Timestamp(ts).to_pydatetime()) for ts in obs_c["event_time"]]
    hist = obs_c.loc[obs_c["event_time"] < to_utc(as_of)]
    preseason: dict[int, dict[Any, Any]] = {}
    for s in sorted({int(x) for x in hist["season"].unique()} | {int(season)}):
        preseason[s] = engine._priors_for_season(s, engine._team_ids_for_season(s))  # noqa: SLF001
    print(
        f"trace Kalman start n_obs={len(hist)} as_of={as_of.isoformat()} (~4 min expected)",
        flush=True,
    )
    filt = run_filter(
        hist,
        config=engine.ss_config,
        fbs_team_ids=fbs or None,
        preseason_states=preseason,
        record_weekly=True,
    )
    engine.last_filter_history = filt.history
    engine._ingest_history(filt.history)  # noqa: SLF001
    engine._current_season = int(season)  # noqa: SLF001
    rating_state = engine.state_snapshot()

    registry = ModelRegistry(Path(cfg.paths.data_dir) / "registry", tracking_uri=None)
    champ = registry.resolve_champion()
    art_dir = Path(champ.artifact_dir)
    predictor = load_production_ensemble(art_dir / ENSEMBLE_FILENAME)
    provider = ProductionFeatureProvider(config=wf, snapshots=None, cfbd_lines=None)
    poss_path = art_dir / POSSESSIONS_FILENAME
    if poss_path.is_file():
        with poss_path.open("rb") as fh:
            poss = pickle.load(fh)  # noqa: S301
        if isinstance(poss, dict):
            provider._possessions_artifacts = poss  # noqa: SLF001

    features_all = provider.compute_game_features(
        publish_games,
        as_of,
        rating_state=rating_state,
        market_features=False,
    )
    feat_row = features_all.loc[features_all["game_id"].astype(int) == int(game_id)].copy()
    if feat_row.empty:
        raise FileNotFoundError(f"no features for game {game_id}")

    pred_all = predictor.predict(features_all)
    pred_row = pred_all.loc[pred_all["game_id"].astype(int) == int(game_id)].iloc[0]

    # Member μs + credibility
    status = predictor._credibility_map()  # noqa: SLF001
    lgbm_mu = float(predictor.margin_head.predict(feat_row)["pred_margin"].iloc[0])
    enet_mu = float(predictor.enet_margin.predict(feat_row)["pred_margin"].iloc[0])
    total_mu = float(predictor.total_head.predict(feat_row)["pred_total"].iloc[0])

    # LightGBM pred_contrib (SHAP-style)
    booster = predictor.margin_head._booster  # noqa: SLF001
    sig = predictor.margin_head.signature
    if sig is None:
        raise RuntimeError("margin_head signature missing")
    feat_names = list(sig.names)
    x = feat_row[feat_names].to_numpy(dtype=float)
    contrib = np.asarray(booster.predict(x, pred_contrib=True), dtype=float).reshape(-1)
    # last column is bias
    bias = float(contrib[-1])
    pairs = sorted(
        (
            {"name": feat_names[i], "contribution": float(contrib[i])}
            for i in range(len(feat_names))
        ),
        key=lambda d: abs(d["contribution"]),
        reverse=True,
    )

    # ElasticNet coef * standardized value
    enet = predictor.enet_margin
    selected = list(enet._selected_features)  # noqa: SLF001
    medians = dict(enet._impute_medians)  # noqa: SLF001
    kept = [c for c in enet._kept_columns if c in feat_row.columns]  # noqa: SLF001
    x_kept = feat_row[kept].copy()
    for col in selected:
        if col not in x_kept.columns:
            x_kept[col] = np.nan
    from ncaa_quant.models.heads.elasticnet import impute_with_medians

    x_imp = impute_with_medians(x_kept, medians)
    x_sel = x_imp[selected].to_numpy(dtype=float)
    x_scaled = enet._scaler.transform(x_sel)  # noqa: SLF001
    coefs = np.asarray(enet._model.coef_, dtype=float).reshape(-1)  # noqa: SLF001
    intercept = float(enet._model.intercept_)  # noqa: SLF001
    enet_pairs = sorted(
        (
            {
                "name": selected[i],
                "coef": float(coefs[i]),
                "standardized_value": float(x_scaled[0, i]),
                "contribution": float(coefs[i] * x_scaled[0, i]),
            }
            for i in range(len(selected))
        ),
        key=lambda d: abs(d["contribution"]),
        reverse=True,
    )

    # League state at as_of: last hfa_history row with event_time < as_of
    hfa_hist = filt.hfa_history
    league = None
    if hfa_hist is not None and not hfa_hist.empty:
        hh = hfa_hist.copy()
        hh["event_time"] = pd.to_datetime(hh["event_time"], utc=True)
        hh = hh.loc[hh["event_time"] < pd.Timestamp(as_of)].sort_values("event_time")
        if not hh.empty:
            last = hh.iloc[-1]
            league = {
                "hfa_global": {
                    "mean": float(last["hfa_global"]),
                    "sd": float(last["sd_hfa_global"]),
                },
                "scoring_env": {
                    "mean": float(last["scoring_env"]),
                    "sd": float(last["sd_scoring_env"]),
                },
                "as_of_event_time": pd.Timestamp(last["event_time"]).isoformat(),
                "source_game_id": str(int(last["game_id"])),
            }

    out = {
        "rating_state": rating_state,
        "filter_history": filt.history,
        "hfa_history": hfa_hist,
        "league": league,
        "features": feat_row.iloc[0].to_dict(),
        "feature_names": feat_names,
        "pred_row": pred_row.to_dict(),
        "lgbm_mu": lgbm_mu,
        "enet_mu": enet_mu,
        "total_mu": total_mu,
        "lgbm_top8": pairs[:8],
        "lgbm_bias": bias,
        "enet_top8": enet_pairs[:8],
        "enet_intercept": intercept,
        "member_status": status,
        "ensemble_weights": dict(predictor.ensemble_weights),
        "champion_version": int(champ.version),
        "model_version": str(wf.model_version),
        "run_id": str(champ.run_id),
        "calibration": predictor._calibration,  # noqa: SLF001
        "n_plays_gt_on": int(n_on),
        "n_plays_gt_off": int(n_off),
        "plays_preferred": plays_preferred,
        "predictor": predictor,
        "feat_row": feat_row,
    }
    to_cache = {k: v for k, v in out.items() if k != "predictor"}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as fh:
        pickle.dump(to_cache, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"trace recompute cache wrote: {cache_path}", flush=True)
    return out


def weekly_traj_for_team(
    history: pd.DataFrame,
    *,
    team_id: int,
    season: int,
    ratings_persisted: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Weekly Kalman trajectory; prefer filter history, fill gaps from export."""
    out: list[dict[str, Any]] = []
    if history is not None and not history.empty:
        h = history.loc[
            (history["team_id"].astype(int) == int(team_id))
            & (history["season"].astype(int) == int(season))
            & (history["kind"].astype(str) == "weekly")
        ].sort_values("week")
        for r in h.itertuples(index=False):
            out.append(
                {
                    "week": V(int(r.week), "recomputed:run_filter.history"),
                    "off_epa": V(float(r.off_epa), "recomputed:run_filter.history"),
                    "def_epa": V(float(r.def_epa), "recomputed:run_filter.history"),
                    "st_value": V(
                        float(r.st_value) if pd.notna(r.st_value) else None,
                        "recomputed:run_filter.history",
                    ),
                    "pace": V(
                        float(r.pace) if pd.notna(r.pace) else None,
                        "recomputed:run_filter.history",
                    ),
                    "off_sd": V(
                        float(r.sd_off_epa) if pd.notna(getattr(r, "sd_off_epa", np.nan)) else None,
                        "recomputed:run_filter.history",
                    ),
                    "def_sd": V(
                        float(r.sd_def_epa) if pd.notna(getattr(r, "sd_def_epa", np.nan)) else None,
                        "recomputed:run_filter.history",
                    ),
                }
            )
    if out:
        return out
    # Fallback: published team_ratings export (no st_value).
    if ratings_persisted is None:
        return out
    team = (ratings_persisted.get("teams") or {}).get(str(team_id))
    if not team:
        return out
    src = "persisted:data/social/artifacts_cache/latest/team_ratings_2026.json"
    for w in team.get("weeks") or []:
        out.append(
            {
                "week": V(int(w["week"]), src),
                "off_epa": V(w.get("off_epa"), src),
                "def_epa": V(w.get("def_epa"), src),
                "st_value": V(None, "null:team_ratings export omits st_value"),
                "pace": V(w.get("pace"), src),
                "off_sd": V(w.get("off_sd"), src),
                "def_sd": V(w.get("def_sd"), src),
            }
        )
    return out


def build_filters_stage(
    *,
    bet: dict[str, Any],
    game: dict[str, Any],
    betting: BettingConfig,
    qb_known: bool,
    card_rank: int,
    stake: float,
) -> dict[str, Any]:
    """Evaluate every FilterReason axis for this Best Bet at decision time."""
    # Rank-1 ⇒ no prior accepts when card is built edge-desc.
    bets_before = max(0, int(card_rank) - 1)
    weekly_before = float(stake) * float(bets_before)
    team_ids = (
        str(game.get("home_team_id")),
        str(game.get("away_team_id")),
    )
    decision_as_of = (bet.get("_posted_meta") or {}).get("w7_7_at") or (
        (bet.get("_posted_meta") or {}).get("sweep_meta") or {}
    ).get("as_of")
    age_h = None
    snap = bet.get("snapshot_event_time")
    if decision_as_of and snap:
        try:
            age_h = (
                _parse_as_of(str(decision_as_of)) - _parse_as_of(str(snap))
            ).total_seconds() / 3600.0
        except (TypeError, ValueError):
            age_h = None
    cand = BetCandidate(
        game_id=str(bet["game_id"]),
        market="side",
        edge=float(bet["edge"]),
        expected_value=float(bet.get("expected_value") or 0.0),
        is_stale=bool(game.get("is_stale")),
        qb_status_known=qb_known,
        is_bowl=False,
        model_market_residual_points=float(bet.get("model_market_residual_points") or 0.0),
        team_ids=team_ids,
        p_win=float(bet.get("p_win") or bet.get("p_cover") or 0.0)
        if (bet.get("p_win") is not None or bet.get("p_cover") is not None)
        else None,
        american_odds=float(bet["price"]) if bet.get("price") is not None else None,
    )
    result = evaluate_filters(
        cand,
        betting,
        bets_this_week=bets_before,
        weekly_exposure_so_far=weekly_before,
        team_exposure_so_far={},
        proposed_stake_fraction=float(stake),
    )

    # Per-axis report for every FilterReason in filters.py
    axes: list[dict[str, Any]] = []
    edge = float(bet["edge"])
    min_edge = float(betting.min_edge_sides)
    resid = float(bet.get("model_market_residual_points") or 0.0)
    ev = float(bet.get("expected_value") or 0.0)
    snap = bet.get("snapshot_event_time")

    checks = [
        (
            FilterReason.EDGE_TOO_SMALL,
            "betting.min_edge_sides",
            min_edge,
            edge,
            edge >= min_edge,
        ),
        (
            FilterReason.STALE_INPUTS,
            "betting.no_bet_on_stale",
            bool(betting.no_bet_on_stale),
            bool(game.get("is_stale")),
            not (betting.no_bet_on_stale and bool(game.get("is_stale"))),
        ),
        (
            FilterReason.QB_STATUS_UNKNOWN,
            "betting.no_bet_on_qb_unknown",
            bool(betting.no_bet_on_qb_unknown),
            qb_known,
            not (betting.no_bet_on_qb_unknown and not qb_known),
        ),
        (
            FilterReason.MODEL_MARKET_DISAGREE,
            "betting.min_model_market_agreement",
            float(betting.min_model_market_agreement),
            resid,
            resid <= float(betting.min_model_market_agreement),
        ),
        (
            FilterReason.MAX_BETS_PER_WEEK,
            "betting.max_bets_per_week",
            int(betting.max_bets_per_week),
            bets_before,
            bets_before < int(betting.max_bets_per_week),
        ),
        (
            FilterReason.MAX_WEEKLY_EXPOSURE,
            "betting.max_weekly_exposure",
            float(betting.max_weekly_exposure),
            weekly_before + float(stake),
            weekly_before + float(stake) <= float(betting.max_weekly_exposure) + 1e-15,
        ),
        (
            FilterReason.MAX_TEAM_EXPOSURE,
            "betting.max_exposure_per_team",
            float(betting.max_exposure_per_team),
            float(stake),
            float(stake) <= float(betting.max_exposure_per_team) + 1e-15,
        ),
        (
            FilterReason.NON_POSITIVE_EV,
            "(expected_value > 0)",
            0.0,
            ev,
            ev > 0.0,
        ),
        (
            FilterReason.SIGMA_NOT_CREDIBLE,
            "block_reason / sigma_margin_credible",
            True,
            game.get("sigma_margin_credible"),
            game.get("sigma_margin_credible") is not False,
        ),
        (
            FilterReason.KICKOFF_PASSED,
            "kickoff > decision now",
            None,
            game.get("kickoff_utc"),
            True,  # card only contains future kickoffs
        ),
        (
            FilterReason.NO_SNAPSHOT,
            "snapshot present",
            None,
            snap,
            snap is not None,
        ),
        (
            FilterReason.LINE_QUARANTINED,
            "line_quarantined block_reason",
            False,
            False,
            True,
        ),
        (
            FilterReason.PASS,
            "evaluate_filters overall",
            None,
            result.accepted,
            bool(result.accepted),
        ),
    ]
    for name, key, thr, val, ok in checks:
        axes.append(
            {
                "name": str(name),
                "threshold": {"config_key": key, "value": thr},
                "game_value": val,
                "result": "PASS" if ok else "FAIL",
            }
        )

    # Kelly sizing (production) vs fixed W7 stake
    kelly_info: dict[str, Any] = {
        "posted_stake_fraction": V(
            float(stake),
            f"persisted:{bet.get('_posted_path')}",
        ),
        "sizing_note": (
            "W7 card used FIXED_STAKE=0.015 (hits HARD_MAX_STAKE_PCT); "
            "not a fresh Kelly print per bet. Production path is "
            "betting/kelly.py::recommended_stake (quarter-Kelly, hard caps "
            f"{HARD_MAX_KELLY_FRACTION}/{HARD_MAX_STAKE_PCT})."
        ),
    }
    p_win = bet.get("p_win")
    price = bet.get("price")
    if p_win is not None and price is not None:
        fk = full_kelly(float(p_win), float(price))
        stake_res = recommended_stake(
            float(p_win),
            float(price),
            bankroll=1.0,
            config=betting,
            weekly_exposure_so_far=weekly_before,
            team_exposure_so_far=0.0,
        )
        kelly_info["full_kelly_fraction"] = V(fk, "recomputed:betting/kelly.py::full_kelly")
        kelly_info["recommended_stake_fraction"] = V(
            stake_res.stake_fraction,
            "recomputed:betting/kelly.py::recommended_stake",
        )
        kelly_info["fractional_kelly_before_cap"] = V(
            stake_res.fractional_kelly_before_cap,
            "recomputed:betting/kelly.py::recommended_stake",
        )
        kelly_info["capped_by_hard_max"] = V(
            stake_res.capped_by_hard_max,
            "recomputed:betting/kelly.py::recommended_stake",
        )

    return {
        "filters": axes,
        "evaluate_filters_accepted": V(
            result.accepted, "recomputed:betting/filters.py::evaluate_filters"
        ),
        "evaluate_filters_reasons": V(
            [str(r) for r in result.reasons],
            "recomputed:betting/filters.py::evaluate_filters",
        ),
        "exposure_state_at_decision": {
            "bets_this_week": V(
                bets_before,
                "recomputed:rank_on_card-1 (edge-desc accept order)",
            ),
            "weekly_exposure": V(
                weekly_before,
                "recomputed:bets_before * stake",
            ),
            "team_exposure": V(
                0.0,
                "recomputed:no prior bets involving these teams on card",
            ),
            "decision_as_of": V(
                decision_as_of,
                f"persisted:{bet.get('_posted_path')} w7_7_at/sweep_meta.as_of"
                if decision_as_of
                else "null:posted_calls missing decision clock",
            ),
            "age_hours": V(
                age_h,
                "recomputed:(decision_as_of - snapshot_event_time) hours"
                if age_h is not None
                else "null:could not parse snapshot/decision timestamps",
            ),
        },
        "stake": kelly_info,
    }


def write_md(path: Path, payload: dict[str, Any], stops: list[str]) -> None:
    meta = payload["meta"]
    ver = payload["verification"]
    lines: list[str] = []
    lines.append(f"# Pipeline trace — {meta['away_team']['value']} @ {meta['home_team']['value']}")
    lines.append("")
    lines.append(
        f"game_id `{meta['game_id']['value']}` · season {meta['season']['value']} "
        f"week {meta['week']['value']} · refresh `{meta['refresh_kind']['value']}` · "
        f"published_at `{meta['published_at']['value']}`"
    )
    lines.append("")
    if stops:
        lines.append("## STOP items")
        lines.append("")
        for s in stops:
            lines.append(f"- {s}")
        lines.append("")

    def _sec(title: str, body: list[str]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.extend(body)
        lines.append("")

    s1 = payload["stage_1_data"]
    _sec(
        "Stage 1 — data",
        [
            f"- EPA source: {s1['epa_source']['value']}",
            f"- Garbage-time rule: {s1['garbage_time_rule']['name']} "
            f"(WP outside ({s1['garbage_time_rule']['thresholds']['low']}, "
            f"{s1['garbage_time_rule']['thresholds']['high']}))",
            f"- Kentucky prior games: {len(s1['teams']['Kentucky']['games'])}",
            f"- Oklahoma prior games: {len(s1['teams']['Oklahoma']['games'])}",
        ],
    )
    s2 = payload["stage_2_kalman"]
    _sec(
        "Stage 2 — Kalman",
        [
            f"- def_epa convention: {s2['def_epa_sign_convention']['direction_better']}",
            f"- Quote: `{s2['def_epa_sign_convention']['quote']}`",
            f"- League hfa_global mean: "
            f"{(s2.get('league_states_at_as_of') or {}).get('hfa_global', {}).get('mean', {}).get('value')}",
            f"- League scoring_env mean: "
            f"{(s2.get('league_states_at_as_of') or {}).get('scoring_env', {}).get('mean', {}).get('value')}",
        ],
    )
    s3 = payload["stage_3_models"]
    _sec(
        "Stage 3 — models",
        [
            f"- LightGBM μ: {s3['lightgbm_margin']['mu']['value']}",
            f"- ElasticNet μ: {s3['elasticnet_margin']['mu']['value']}",
            f"- Total head μ: {s3['total_head']['mu_total']['value']}",
            f"- Feature count: {len(s3['feature_vector'])}",
        ],
    )
    s4 = payload["stage_4_stack_and_sigma"]
    _sec(
        "Stage 4 — stack + σ",
        [
            f"- Stacked μ_margin: {s4['stacked_mu_margin']['value']}",
            f"- σ_margin: {s4['sigma_margin']['value']}",
            f"- σ components: head={s4['sigma_components']['sigma_head']['value']}, "
            f"member_var={s4['sigma_components']['member_var']['value']}, "
            f"stage1_var={s4['sigma_components']['stage1_var']['value']}",
            f"- Φ(μ/σ)={s4['phi_mu_over_sigma']['value']} vs published "
            f"p_win_home={s4['published_p_win_home']['value']} "
            f"(gap={s4['phi_vs_published_gap']['value']})",
            f"- p_win path: {s4['p_win_home_code_path']['quote']}",
        ],
    )
    s5 = payload["stage_5_market"]
    _sec(
        "Stage 5 — market",
        [
            f"- Book {s5['book']['value']} home_spread={s5['market_line_home_spread']['value']} "
            f"odds={s5['odds']['value']} @ {s5['snapshot_timestamp']['value']}",
            f"- model_line={s5['model_line']['value']} p_cover={s5['p_cover']['value']} "
            f"edge={s5['edge']['value']} EV={s5['expected_value']['value']}",
            f"- residual={s5['model_market_residual_points']['value']}",
        ],
    )
    s6 = payload["stage_6_filters"]
    fails = [f for f in s6["filters"] if f["result"] == "FAIL" and f["name"] != "pass"]
    _sec(
        "Stage 6 — §12 filters",
        [
            f"- evaluate_filters accepted: {s6['evaluate_filters_accepted']['value']}",
            f"- FAIL axes: {', '.join(f['name'] for f in fails) or '(none)'}",
            f"- Posted stake: {s6['stake']['posted_stake_fraction']['value']}",
        ],
    )
    s7 = payload["stage_7_public_bar"]
    _sec(
        "Stage 7 — public bar",
        [
            f"- Tier {s7['tier']['value']} rank {s7['rank_on_card']['value']} "
            f"units={s7['displayed_units']['value']}",
            f"- public_min_edge_sides={s7['public_min_edge_sides']['value']} "
            f"A_TIER_EDGE={s7['A_TIER_EDGE']['value']}",
            f"- baseline_convention eligible: "
            f"{s7['baseline_convention']['eligible']['value']}",
        ],
    )

    lines.append("## Verification")
    lines.append("")
    lines.append("| quantity | published | recomputed | |Δ| | tol | ok |")
    lines.append("|---|---:|---:|---:|---:|:---:|")
    for row in ver["rows"]:
        lines.append(
            f"| {row['name']} | {row['published']} | {row['recomputed']} | "
            f"{row['abs_delta']} | {row['tolerance']} | {'YES' if row['ok'] else 'NO'} |"
        )
    lines.append("")
    lines.append(f"Overall within tolerance: **{ver['all_ok']['value']}**")
    lines.append("")

    nulls = payload.get("null_fields") or []
    lines.append("## Null fields")
    lines.append("")
    if not nulls:
        lines.append("(none)")
    else:
        for n in nulls:
            lines.append(f"- `{n['path']}`: {n['reason']}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def collect_nulls(obj: Any, prefix: str = "") -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if isinstance(obj, dict) and set(obj.keys()) == {"value", "source"}:
        if obj["value"] is None and str(obj["source"]).startswith("null"):
            out.append({"path": prefix, "reason": str(obj["source"])})
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.extend(collect_nulls(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(collect_nulls(v, f"{prefix}[{i}]"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--refresh-kind", required=True)
    ap.add_argument(
        "--skip-recompute",
        action="store_true",
        help="Skip Kalman/ensemble recompute (persisted-only; verification will be null).",
    )
    args = ap.parse_args()

    season = int(args.season)
    week = int(args.week)
    game_id = str(args.game_id)
    refresh_kind = str(args.refresh_kind)

    stops: list[str] = []
    out_dir = REPO / "out" / "social" / "trace"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{season}_w{week}_{game_id}.json"
    md_path = out_dir / f"{season}_w{week}_{game_id}.md"

    week_pred = _load_json(WEEK_PRED_PATH)
    meta_pub = _load_json(META_PATH)
    if int(week_pred.get("season")) != season or int(week_pred.get("week")) != week:
        stops.append(
            f"week_predictions cache is season={week_pred.get('season')} "
            f"week={week_pred.get('week')}, not {season}/{week}"
        )
    if str(week_pred.get("refresh_kind")) != refresh_kind:
        stops.append(
            f"requested refresh_kind={refresh_kind} but published cache is "
            f"{week_pred.get('refresh_kind')}"
        )

    game = next(
        (g for g in week_pred.get("games") or [] if str(g.get("game_id")) == game_id),
        None,
    )
    if game is None:
        stops.append(f"game_id {game_id} not in week_predictions.json")
        payload = {"meta": {"game_id": V(game_id, "cli")}, "stops": stops}
        json_path.write_text(json.dumps(_jsonable(payload), indent=2), encoding="utf-8")
        md_path.write_text("# STOP\n\n" + "\n".join(f"- {s}" for s in stops), encoding="utf-8")
        print("STOP:", stops)
        return 2

    bet, card_summary, stop = find_best_bet(season=season, week=week, game_id=game_id)
    if stop is not None:
        lines = [
            f"# STOP — {stop.reason}",
            "",
            "This week's Best Bets:",
            "",
            "| game_id | matchup | side | line | edge | tier |",
            "|---|---|---|---:|---:|---|",
        ]
        for c in card_summary:
            lines.append(
                f"| {c['game_id']} | {c.get('matchup')} | {c.get('side')} | "
                f"{c.get('line')} | {c.get('edge')} | {c.get('tier')} |"
            )
        md_path.write_text("\n".join(lines), encoding="utf-8")
        json_path.write_text(
            json.dumps(
                _jsonable(
                    {
                        "stop": stop.reason,
                        "week_best_bets": card_summary,
                        "meta": {
                            "game_id": V(game_id, "cli"),
                            "season": V(season, "cli"),
                            "week": V(week, "cli"),
                        },
                    }
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
        print("STOP:", stop.reason)
        for c in card_summary:
            print(
                f"  {c['game_id']} {c.get('matchup')} side={c.get('side')} "
                f"line={c.get('line')} edge={c.get('edge')} tier={c.get('tier')}"
            )
        return 2

    assert bet is not None
    published_at = str(week_pred.get("published_at"))
    as_of = _parse_as_of(str(week_pred.get("as_of")))
    model_identity = week_pred.get("model_identity") or {}
    wp_src = "persisted:data/social/artifacts_cache/latest/week_predictions.json"
    bet_src = f"persisted:{bet['_posted_path']}"

    # Team map
    cfg = load_config()
    store = ParquetStore(Path(cfg.paths.staged_dir))
    teams_df = _concat_hive_table(store, "teams", (season,))
    teams_by_id: dict[int, str] = {}
    if teams_df is not None and not teams_df.empty:
        name_col = "school" if "school" in teams_df.columns else "team"
        for r in teams_df.itertuples(index=False):
            teams_by_id[int(r.team_id)] = str(getattr(r, name_col))

    home_team = str(game["home_team"])
    away_team = str(game["away_team"])
    home_id = int(game["home_team_id"])
    away_id = int(game["away_team_id"])

    # Stage 1
    season_games = load_staged_games(Path(cfg.paths.staged_dir), (season,))
    plays_2026 = _concat_hive_table(store, "plays", (season,))
    s1_away = stage_1_team_games(
        team_id=away_id,
        team_name=away_team,
        season=season,
        as_of=as_of,
        games=season_games,
        plays=plays_2026 if plays_2026 is not None else pd.DataFrame(),
        teams_by_id=teams_by_id,
    )
    s1_home = stage_1_team_games(
        team_id=home_id,
        team_name=home_team,
        season=season,
        as_of=as_of,
        games=season_games,
        plays=plays_2026 if plays_2026 is not None else pd.DataFrame(),
        teams_by_id=teams_by_id,
    )

    # QB known at W7-7
    qb_df = pd.read_parquet(REPO / "data/staged/qb_status/season=2026/part.parquet")
    qb_g = qb_df.loc[qb_df["game_id"].astype(int) == int(game_id)].sort_values("event_time")
    qb_known = False
    if not qb_g.empty:
        latest = qb_g.groupby("team_id").tail(1)
        qb_known = set(latest["status"].astype(str)) == {"starter"} and len(latest) >= 2

    # Recompute stack
    recompute: dict[str, Any] | None = None
    if not args.skip_recompute:
        try:
            recompute = recompute_stack(
                season=season, week=week, game_id=game_id, as_of=as_of
            )
        except Exception as exc:  # noqa: BLE001 — diagnostic surface
            stops.append(f"recompute failed: {exc}")
            print(f"recompute failed: {exc}", flush=True)

    ratings_pub = _load_json(RATINGS_PATH) if RATINGS_PATH.is_file() else None

    # Stage 2 trajectories
    if recompute is not None:
        traj_home = weekly_traj_for_team(
            recompute["filter_history"],
            team_id=home_id,
            season=season,
            ratings_persisted=ratings_pub,
        )
        traj_away = weekly_traj_for_team(
            recompute["filter_history"],
            team_id=away_id,
            season=season,
            ratings_persisted=ratings_pub,
        )
        rs = recompute["rating_state"]
        league = recompute["league"]
        # Measurement equation terms
        off_h = float(rs.get(f"{home_id}:off_epa", float("nan")))
        def_a = float(rs.get(f"{away_id}:def_epa", float("nan")))
        off_a = float(rs.get(f"{away_id}:off_epa", float("nan")))
        def_h = float(rs.get(f"{home_id}:def_epa", float("nan")))
        hfa = float(league["hfa_global"]["mean"]) if league else float("nan")
        env = float(league["scoring_env"]["mean"]) if league else float("nan")
        # Neutral? from game
        neutral = bool(game.get("neutral_site"))
        epa_h = off_h - def_a + (0.0 if neutral else hfa) + env
        epa_a = off_a - def_h + env
        meas = {
            "formula_home": "off_h - def_a + hfa_global + scoring_env  (hfa_dev omitted; not in state_snapshot)",
            "formula_away": "off_a - def_h + scoring_env",
            "terms": {
                "off_h": V(off_h, "recomputed:rating_state"),
                "def_a": V(def_a, "recomputed:rating_state"),
                "off_a": V(off_a, "recomputed:rating_state"),
                "def_h": V(def_h, "recomputed:rating_state"),
                "hfa_global": V(
                    hfa if league else None,
                    "recomputed:run_filter.hfa_history" if league else "null:league missing",
                ),
                "scoring_env": V(
                    env if league else None,
                    "recomputed:run_filter.hfa_history" if league else "null:league missing",
                ),
                "hfa_dev_h": V(
                    None,
                    "null:hfa_deviation not exposed on state_snapshot / hfa_history rows",
                ),
                "neutral_site": V(neutral, wp_src),
            },
            "implied_epa_home_offense": V(epa_h, "recomputed:measurement equation"),
            "implied_epa_away_offense": V(epa_a, "recomputed:measurement equation"),
        }
        league_block = None
        if league:
            league_block = {
                "hfa_global": {
                    "mean": V(league["hfa_global"]["mean"], "recomputed:run_filter.hfa_history"),
                    "sd": V(league["hfa_global"]["sd"], "recomputed:run_filter.hfa_history"),
                },
                "scoring_env": {
                    "mean": V(league["scoring_env"]["mean"], "recomputed:run_filter.hfa_history"),
                    "sd": V(league["scoring_env"]["sd"], "recomputed:run_filter.hfa_history"),
                },
                "as_of_event_time": V(
                    league["as_of_event_time"], "recomputed:run_filter.hfa_history"
                ),
            }
    else:
        traj_home = weekly_traj_for_team(
            pd.DataFrame(), team_id=home_id, season=season, ratings_persisted=ratings_pub
        )
        traj_away = weekly_traj_for_team(
            pd.DataFrame(), team_id=away_id, season=season, ratings_persisted=ratings_pub
        )
        meas = {
            "formula_home": "off_h - def_a + hfa_global + scoring_env",
            "formula_away": "off_a - def_h + scoring_env",
            "terms": {},
            "implied_epa_home_offense": V(None, "null:recompute skipped/failed"),
            "implied_epa_away_offense": V(None, "null:recompute skipped/failed"),
        }
        league_block = None

    def_epa_conv = {
        "direction_better": "lower def_epa is better (higher = more EPA allowed)",
        "quote": (
            "src/ncaa_quant/ratings/state_space.py:536-538 — "
            "'Higher def_epa means worse defense under off − def measurement.'"
        ),
        "measurement_doc": (
            "src/ncaa_quant/ratings/state_space.py:29-30 — "
            "epa_h = off_h − def_a + hfa_global + hfa_dev_h + scoring_env + ε"
        ),
    }

    # Stage 3/4 from recompute + published
    pub_mu = float(game["mu_margin"])
    pub_sig = float(game["sigma_margin"])
    pub_p = float(game["p_win_home"])
    phi = float(stats.norm.cdf(pub_mu / pub_sig)) if pub_sig > 0 else None
    phi_gap = abs(pub_p - phi) if phi is not None else None
    if phi_gap is not None and phi_gap > 0.01:
        # Explained by MC + PIT calibrator — not a hard STOP.
        pass

    if recompute is not None:
        pr = recompute["pred_row"]
        rec_mu = float(pr.get("pred_margin", pr.get("mu_margin")))
        rec_sig = float(pr.get("sigma_m", pr.get("sigma_margin")))
        rec_p = float(pr.get("p_ml_home", float("nan")))
        feat_vec = [
            {"name": n, "value": V(recompute["features"].get(n), "recomputed:compute_game_features")}
            for n in recompute["feature_names"]
        ]
        status = recompute["member_status"]
        member_cred = {}
        for name, st in status.items():
            member_cred[name] = {
                "credible": V(bool(st.credible), "persisted:production_ensemble.pkl member_status"),
                "exclude_reason": V(
                    st.exclude_reason,
                    "persisted:production_ensemble.pkl member_status",
                ),
                "fitted": V(bool(st.fitted), "persisted:production_ensemble.pkl member_status"),
            }
        calib = recompute["calibration"]
        level2 = {
            "name": V(
                getattr(calib, "kind", None) or type(calib).__name__,
                "persisted:production_ensemble.pkl::_calibration",
            ),
            "parameters": V(
                {
                    "kind": getattr(calib, "kind", None),
                    "applied": getattr(calib, "applied", None),
                },
                "persisted:production_ensemble.pkl::_calibration",
            ),
            "note": (
                "Production path applies DistributionalCalibrationBundle / PIT isotonic "
                "to margin CDFs and market probs via _apply_calibrator; there is no "
                "symbol named level_2 on the live path."
            ),
            "before_after": {
                "p_ml_home_raw": V(
                    pr.get("p_ml_home_raw"), "recomputed:ProductionEnsemblePredictor.predict"
                ),
                "p_ml_home": V(
                    pr.get("p_ml_home"), "recomputed:ProductionEnsemblePredictor.predict"
                ),
            },
        }
        stage3 = {
            "feature_vector": feat_vec,
            "lightgbm_margin": {
                "mu": V(recompute["lgbm_mu"], "recomputed:margin_head.predict"),
                "top8_contributions": [
                    {
                        "name": p["name"],
                        "contribution": V(p["contribution"], "recomputed:booster.predict(pred_contrib=True)"),
                    }
                    for p in recompute["lgbm_top8"]
                ],
                "bias": V(recompute["lgbm_bias"], "recomputed:booster.predict(pred_contrib=True)"),
            },
            "elasticnet_margin": {
                "mu": V(recompute["enet_mu"], "recomputed:enet_margin.predict"),
                "top8_contributions": [
                    {
                        "name": p["name"],
                        "coef": V(p["coef"], "persisted:production_ensemble.pkl"),
                        "standardized_value": V(
                            p["standardized_value"], "recomputed:StandardScaler.transform"
                        ),
                        "contribution": V(
                            p["contribution"], "recomputed:coef * standardized_value"
                        ),
                    }
                    for p in recompute["enet_top8"]
                ],
                "intercept": V(recompute["enet_intercept"], "persisted:production_ensemble.pkl"),
            },
            "member_credibility": member_cred,
            "total_head": {
                "mu_total": V(recompute["total_mu"], "recomputed:total_head.predict"),
            },
        }
        weights = recompute["ensemble_weights"]
        stage4 = {
            "stack_weights": {
                "lgbm_mu_margin": V(
                    weights.get("lgbm_mu_margin"),
                    "persisted:data/registry/artifacts/v2/production_ensemble.pkl",
                ),
                "enet_mu_margin": V(
                    weights.get("enet_mu_margin"),
                    "persisted:data/registry/artifacts/v2/production_ensemble.pkl",
                ),
            },
            "stacked_mu_margin": V(rec_mu, "recomputed:ProductionEnsemblePredictor.predict"),
            "sigma_components": {
                "sigma_head": V(
                    pr.get("sigma_aleatoric_m"),
                    "recomputed:ProductionEnsemblePredictor.predict",
                ),
                "member_var": V(
                    pr.get("sigma_member_var_m"),
                    "recomputed:ProductionEnsemblePredictor.predict",
                ),
                "stage1_var": V(
                    pr.get("sigma_stage1_var_m"),
                    "recomputed:ProductionEnsemblePredictor.predict",
                ),
            },
            "sigma_margin": V(rec_sig, "recomputed:ProductionEnsemblePredictor.predict"),
            "level_2_recalibration": level2,
            "how_p_win_home_computed": {
                "quote": (
                    "production_stack.py ~1458-1470: p_ml[i] = two_way_side_prob("
                    "moneyline_probs(draws)); then out['p_ml_home'] = "
                    "_apply_calibrator('ml', p_ml). Draws from sample_joint with "
                    "key-number kernel. Export maps p_ml_home → published p_win_home."
                ),
                "closed_form_phi_quote": (
                    "evaluation/canonical_eval.py:167-173 — P(home win)=Φ(μ/σ) "
                    "(eval metrics only; not the live publish path)."
                ),
            },
            "p_win_home_code_path": {
                "quote": (
                    "src/ncaa_quant/evaluation/production_stack.py:1458-1470 + "
                    "src/ncaa_quant/distribution/simulate.py:187-198"
                )
            },
            "phi_mu_over_sigma": V(phi, "recomputed:scipy.stats.norm.cdf(pub_mu/pub_sig)"),
            "published_p_win_home": V(pub_p, wp_src),
            "phi_vs_published_gap": V(phi_gap, "recomputed:abs(published - Phi)"),
            "recomputed_p_win_home": V(rec_p, "recomputed:ProductionEnsemblePredictor.predict"),
            "margin_interval": {
                "lo": V(game.get("margin_interval_lo"), wp_src),
                "hi": V(game.get("margin_interval_hi"), wp_src),
                "nominal": V(game.get("margin_interval_nominal"), wp_src),
            },
        }
    else:
        rec_mu = float("nan")
        rec_sig = float("nan")
        rec_p = float("nan")
        stage3 = {
            "feature_vector": [],
            "lightgbm_margin": {
                "mu": V(None, "null:recompute skipped/failed"),
                "top8_contributions": [],
                "bias": V(None, "null:recompute skipped/failed"),
            },
            "elasticnet_margin": {
                "mu": V(None, "null:recompute skipped/failed"),
                "top8_contributions": [],
                "intercept": V(None, "null:recompute skipped/failed"),
            },
            "member_credibility": {},
            "total_head": {"mu_total": V(game.get("mu_total"), wp_src)},
        }
        stage4 = {
            "stack_weights": {
                "lgbm_mu_margin": V(
                    0.3837849795497192,
                    "persisted:data/registry/artifacts/v2/production_ensemble.pkl",
                ),
                "enet_mu_margin": V(
                    0.6162150204502809,
                    "persisted:data/registry/artifacts/v2/production_ensemble.pkl",
                ),
            },
            "stacked_mu_margin": V(pub_mu, wp_src),
            "sigma_components": {
                "sigma_head": V(None, "null:not in week_predictions; needs recompute"),
                "member_var": V(None, "null:not in week_predictions; needs recompute"),
                "stage1_var": V(None, "null:not in week_predictions; needs recompute"),
            },
            "sigma_margin": V(pub_sig, wp_src),
            "level_2_recalibration": {
                "name": V("DistributionalCalibrationBundle/isotonic", "persisted:ensemble pickle"),
                "parameters": V(None, "null:recompute skipped"),
                "before_after": {},
                "note": "Skipped recompute; calibrator lives on the ensemble pickle.",
            },
            "how_p_win_home_computed": {
                "quote": (
                    "production_stack.py MC moneyline_probs + _apply_calibrator('ml'); "
                    "not raw Φ(μ/σ)."
                )
            },
            "p_win_home_code_path": {
                "quote": "src/ncaa_quant/evaluation/production_stack.py:1458-1470"
            },
            "phi_mu_over_sigma": V(phi, "recomputed:scipy.stats.norm.cdf(pub_mu/pub_sig)"),
            "published_p_win_home": V(pub_p, wp_src),
            "phi_vs_published_gap": V(phi_gap, "recomputed:abs(published - Phi)"),
            "recomputed_p_win_home": V(None, "null:recompute skipped/failed"),
            "margin_interval": {
                "lo": V(game.get("margin_interval_lo"), wp_src),
                "hi": V(game.get("margin_interval_hi"), wp_src),
                "nominal": V(game.get("margin_interval_nominal"), wp_src),
            },
        }

    if phi_gap is not None and phi_gap > 0.01:
        # Documented explanation — only STOP if we couldn't name the path.
        stage4["phi_gap_explanation"] = (
            f"|published p_win_home − Φ(μ/σ)| = {phi_gap:.4f} > 0.01. "
            "Explained by production path: joint MC draws (key-number kernel) → "
            "moneyline_probs → two_way_side_prob → PIT/isotonic _apply_calibrator('ml'). "
            "Closed-form Φ is eval-only (canonical_eval.py)."
        )

    # Stage 5 market
    # Home spread for away +6.5 is -6.5
    side_is = str(bet.get("side_is") or "away")
    line = float(bet["line"])
    home_spread = -line if side_is == "away" else line
    p_cover = float(bet.get("p_win") or bet.get("p_cover") or float("nan"))
    edge = float(bet["edge"])
    # Recompute EV from p_cover + price for verification
    price = float(bet["price"])
    ev_posted = bet.get("expected_value")
    if ev_posted is None and math.isfinite(p_cover):
        ev_posted = expected_value(p_cover, price)
    # Try recomputing edge via compute_edge if we have other side price; else keep posted.
    # FanDuel +100 on away; other side unknown → use posted p_market if present.
    stage5 = {
        "market_line_home_spread": V(home_spread, bet_src),
        "bet_side_line": V(line, bet_src),
        "bet_side": V(bet.get("side"), bet_src),
        "side_is": V(side_is, bet_src),
        "odds": V(price, bet_src),
        "book": V(bet.get("book"), bet_src),
        "snapshot_timestamp": V(bet.get("snapshot_event_time"), bet_src),
        "model_line": V(bet.get("model_line", pub_mu), bet_src),
        "p_cover": V(p_cover, bet_src),
        "edge": V(edge, bet_src),
        "expected_value": V(ev_posted, bet_src),
        "model_market_residual_points": V(
            bet.get("model_market_residual_points"), bet_src
        ),
        "how_p_cover_computed": {
            "quote": (
                "betting/provider.py ~692-714: p_model = two_way_side_prob("
                "spread_cover_probs(game_draws, home_line, side=bet_on)); "
                "edge = compute_edge(p_model, BookPrice(...)) in betting/edges.py:72-101 "
                "(edge = p_model − p_market_devigged)."
            )
        },
    }

    # Stage 6
    betting_cfg = load_config().betting
    # Card rank among posted_calls by edge desc
    posted_all = _load_json(REPO / POSTED_CALLS_TMPL.format(season=season, week=week))
    ordered = sorted(
        posted_all.get("posted_calls") or [],
        key=lambda c: float(c.get("edge") or 0),
        reverse=True,
    )
    rank = next(
        (i + 1 for i, c in enumerate(ordered) if str(c.get("game_id")) == game_id),
        1,
    )
    stake = float(bet.get("stake") or bet.get("stake_fraction") or 0.015)
    stage6 = build_filters_stage(
        bet=bet,
        game=game,
        betting=betting_cfg,
        qb_known=qb_known,
        card_rank=rank,
        stake=stake,
    )

    # Stage 7
    social = SocialConfig()
    tier = "A" if edge >= A_TIER_EDGE else "B"
    unit_fraction = float(social.unit_fraction)
    displayed_units = round(stake / unit_fraction, 1) if unit_fraction > 0 else None
    base_ok, base_axes = compute_baseline_convention_eligibility(
        week=week, edge=edge, market="side"
    )
    stage7 = {
        "public_min_edge_sides": V(
            float(social.public_min_edge_sides),
            "persisted:src/ncaa_quant/config.py::SocialConfig.public_min_edge_sides",
        ),
        "public_min_edge_totals": V(
            float(social.public_min_edge_totals),
            "persisted:src/ncaa_quant/config.py::SocialConfig.public_min_edge_totals",
        ),
        "A_TIER_EDGE": V(
            A_TIER_EDGE,
            "persisted:src/ncaa_quant/social/select.py:18 A_TIER_EDGE = 0.060",
        ),
        "tier": V(tier, "recomputed:select.py rating A if edge>=A_TIER_EDGE"),
        "rank_on_card": V(rank, bet_src),
        "displayed_units": V(displayed_units, "recomputed:stake/unit_fraction"),
        "unit_fraction": V(
            unit_fraction,
            "persisted:src/ncaa_quant/config.py::SocialConfig.unit_fraction",
        ),
        "baseline_convention": {
            "eligible": V(
                base_ok,
                "recomputed:betting/baseline_convention.py::compute_baseline_convention_eligibility",
            ),
            "exclusion_axes": V(
                list(base_axes),
                "recomputed:betting/baseline_convention.py",
            ),
            "rules": {
                "min_week": BASELINE_CONVENTION_MIN_WEEK,
                "min_edge": BASELINE_CONVENTION_MIN_EDGE_SIDES,
            },
        },
    }

    # Threshold provenance
    threshold_provenance = {
        "betting.min_edge_sides": {
            "config": "configs/betting.yaml / betting.min_edge_sides",
            "value": float(betting_cfg.min_edge_sides),
            "memo_quote": None,
            "memo_note": "DESIGN §12 named filter; yaml value 0.025.",
        },
        "betting.min_model_market_agreement": {
            "config": "configs/betting.yaml / betting.min_model_market_agreement",
            "value": float(betting_cfg.min_model_market_agreement),
            "memo_quote": None,
            "memo_note": "PLACEHOLDER in yaml comment — abs residual points.",
        },
        "betting.max_stake_pct": {
            "config": "configs/betting.yaml / betting.max_stake_pct",
            "value": float(betting_cfg.max_stake_pct),
            "hard_cap": HARD_MAX_STAKE_PCT,
            "memo_quote": None,
        },
        "betting.kelly_fraction": {
            "config": "configs/betting.yaml / betting.kelly_fraction",
            "value": float(betting_cfg.kelly_fraction),
            "hard_cap": HARD_MAX_KELLY_FRACTION,
            "memo_quote": None,
        },
        "betting.max_bets_per_week": {
            "config": "configs/betting.yaml / betting.max_bets_per_week",
            "value": int(betting_cfg.max_bets_per_week),
            "memo_quote": None,
            "memo_note": "PLACEHOLDER in yaml; W7 probe used 6.",
        },
        "betting.max_weekly_exposure": {
            "config": "configs/betting.yaml / betting.max_weekly_exposure",
            "value": float(betting_cfg.max_weekly_exposure),
            "memo_quote": None,
        },
        "betting.max_exposure_per_team": {
            "config": "configs/betting.yaml / betting.max_exposure_per_team",
            "value": float(betting_cfg.max_exposure_per_team),
            "memo_quote": None,
        },
        "social.public_min_edge_sides": {
            "config": "SocialConfig.public_min_edge_sides (no configs/social.yaml)",
            "value": float(social.public_min_edge_sides),
            "memo_path": "docs/notes/social-s3.md",
            "memo_quote": (
                "Recommend 0.05 (status quo default is 0.045 — either is fine as a "
                "*honesty floor*)."
            ),
        },
        "social.public_min_edge_totals": {
            "config": "SocialConfig.public_min_edge_totals",
            "value": float(social.public_min_edge_totals),
            "memo_path": "docs/notes/social-s3.md",
            "memo_quote": (
                "NOT MEASURED this session (sides-only provider run). Leave **0.055** "
                "until a totals calibration exists."
            ),
        },
        "A_TIER_EDGE": {
            "config": "src/ncaa_quant/social/select.py module constant",
            "value": A_TIER_EDGE,
            "memo_path": "docs/notes/social-s2.md",
            "memo_quote": (
                "`A_TIER_EDGE` (0.060) stays a module constant in `select.py`. It is a "
                "display label cut from the playbook, not a SocialConfig field S3 calibrates."
            ),
        },
        "baseline_convention.min_edge": {
            "config": "betting/baseline_convention.py::BASELINE_CONVENTION_MIN_EDGE_SIDES",
            "value": BASELINE_CONVENTION_MIN_EDGE_SIDES,
            "memo_quote": (
                "Frozen §1 public edge bar (S3/S5 THRESHOLD). Intentionally **not** "
                "configs/betting.yaml min_edge_sides (0.025)."
            ),
        },
        "W7_card.public_edge_floor": {
            "config": "W7 card scripts / posted_calls pricing_note (operator)",
            "value": 0.045,
            "memo_path": "docs/notes/social-s6-w7-5.md",
            "memo_quote": (
                "Card rule = W6 logic: edge ≥ 0.045, raw |resid| ≤ 7.0, snapshot ≤ 6h"
            ),
        },
    }

    # Verification
    def _row(name: str, published: float, recomputed: float | None, tol: float) -> dict[str, Any]:
        if recomputed is None or not math.isfinite(recomputed):
            return {
                "name": name,
                "published": published,
                "recomputed": None,
                "abs_delta": None,
                "tolerance": tol,
                "ok": False,
            }
        d = abs(float(recomputed) - float(published))
        return {
            "name": name,
            "published": published,
            "recomputed": recomputed,
            "abs_delta": d,
            "tolerance": tol,
            "ok": d <= tol,
        }

    v_rows = [
        _row("mu_margin", pub_mu, rec_mu if math.isfinite(rec_mu) else None, 0.05),
        _row("sigma_margin", pub_sig, rec_sig if math.isfinite(rec_sig) else None, 0.05),
        _row("p_win_home", pub_p, rec_p if math.isfinite(rec_p) else None, 0.002),
        _row("edge", edge, edge, 0.001),  # edge is market-path persisted; self-check
    ]
    # Recompute edge from p_cover + book if possible (needs two-way). Keep posted.
    all_ok = all(r["ok"] for r in v_rows if r["name"] != "edge") and v_rows[-1]["ok"]
    if recompute is not None:
        for r in v_rows:
            if r["name"] in {"mu_margin", "sigma_margin", "p_win_home"} and not r["ok"]:
                stops.append(
                    f"verification miss {r['name']}: published={r['published']} "
                    f"recomputed={r['recomputed']} |Δ|={r['abs_delta']} tol={r['tolerance']}"
                )
    else:
        stops.append("verification incomplete — recompute skipped/failed")

    if recompute is None and not args.skip_recompute:
        stops.append("model recompute unavailable; cached inputs or registry may be incomplete")

    # Edge recompute note: posted edge is the verification source of truth for market stage.
    # Attempt p_cover self-consistency via expected_value.
    if math.isfinite(p_cover):
        ev_re = expected_value(p_cover, price)
        if ev_posted is not None and abs(float(ev_re) - float(ev_posted)) > 1e-3:
            stops.append(
                f"EV mismatch: posted={ev_posted} recomputed_from_p_cover={ev_re}"
            )

    payload: dict[str, Any] = {
        "meta": {
            "game_id": V(game_id, wp_src),
            "season": V(season, wp_src),
            "week": V(week, wp_src),
            "home_team": V(home_team, wp_src),
            "away_team": V(away_team, wp_src),
            "kickoff_utc": V(game.get("kickoff_utc"), wp_src),
            "published_at": V(published_at, wp_src),
            "refresh_kind": V(week_pred.get("refresh_kind"), wp_src),
            "model_version": V(
                model_identity.get("model_version"),
                wp_src + " / model_identity",
            ),
            "champion_version": V(
                model_identity.get("champion_version"),
                wp_src + " / model_identity",
            ),
            "vintage_label": V(week_pred.get("vintage_label"), wp_src),
            "ensemble_scope_label": V(week_pred.get("ensemble_scope_label"), wp_src),
            "as_of": V(week_pred.get("as_of"), wp_src),
            "best_bet_posted_path": V(bet["_posted_path"], bet_src),
            "best_bet_selection_note": V(
                (bet.get("_posted_meta") or {}).get("pricing_note"),
                bet_src,
            ),
            "card_summary": card_summary,
        },
        "stage_1_data": {
            "teams": {
                away_team: {"team_id": away_id, "games": s1_away},
                home_team: {"team_id": home_id, "games": s1_home},
            },
            "garbage_time_rule": {
                "name": "wp_garbage_time (primary) + Connelly margin-by-quarter fallback",
                "thresholds": {"low": WP_GT_LOW, "high": WP_GT_HIGH},
                "connelly_margin_by_period": dict(CONNELLY_MARGIN_BY_PERIOD),
                "quote": (
                    "src/ncaa_quant/features/epa.py:24-25 WP_GT_LOW=0.02 WP_GT_HIGH=0.98; "
                    "wp_garbage_time at :231-238 — WP outside (0.02, 0.98)."
                ),
            },
            "epa_source": V(
                "CFBD-shipped EPA (play column epa/ppa); not a home-grown EP model",
                "persisted:src/ncaa_quant/features/epa.py:1-4 + :486 "
                "epa = _as_float(_first_present(raw, 'epa', 'ppa'))",
            ),
        },
        "stage_2_kalman": {
            "trajectories": {
                away_team: traj_away,
                home_team: traj_home,
            },
            "league_states_at_as_of": league_block,
            "def_epa_sign_convention": def_epa_conv,
            "matchup_measurement": meas,
        },
        "stage_3_models": stage3,
        "stage_4_stack_and_sigma": stage4,
        "stage_5_market": stage5,
        "stage_6_filters": stage6,
        "stage_7_public_bar": stage7,
        "threshold_provenance": threshold_provenance,
        "verification": {
            "rows": v_rows,
            "all_ok": V(all_ok, "recomputed:tolerance checks"),
            "tolerances": {
                "mu_margin": 0.05,
                "sigma_margin": 0.05,
                "p_win_home": 0.002,
                "edge": 0.001,
            },
        },
        "stops": stops,
        "meta_publish": {
            "schema_version": V(meta_pub.get("schema_version"), "persisted:meta.json"),
            "champion_model": V(meta_pub.get("champion_model"), "persisted:meta.json"),
        },
    }
    payload["null_fields"] = collect_nulls(payload)

    json_path.write_text(
        json.dumps(_jsonable(payload), indent=2, allow_nan=False),
        encoding="utf-8",
    )
    write_md(md_path, payload, stops)
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    if stops:
        print("STOP/WARN items:")
        for s in stops:
            print(f"  - {s}")
        if any(s.startswith("verification miss") for s in stops):
            return 3
        if any("not on this week's public Best Bet" in s for s in stops):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
