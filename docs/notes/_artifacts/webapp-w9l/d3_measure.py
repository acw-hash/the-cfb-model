"""W9-L Amendment 1 D3: hang diagnosis + 2024 week-5 rating-source delta.

No fit, no R2, no real hysteresis/ledger write, no predict_fn rewire.
"""

from __future__ import annotations

import json
import pickle
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.cli import load_fitted_priors_frame_for_backtest
from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.evaluation.backtest_runner import (
    load_backtest_config,
    load_staged_games,
    walkforward_config_from_mapping,
)
from ncaa_quant.evaluation.production_stack import (
    ProductionFeatureProvider,
    StateSpaceRatingEngine,
    build_game_observations_from_advanced,
    build_observations_from_staged,
)
from ncaa_quant.evaluation.walkforward import WeekDecisionCalendar, week_decision_as_of
from ncaa_quant.features.epa import apply_garbage_time
from ncaa_quant.registry.bundle import ENSEMBLE_FILENAME, load_production_ensemble
from ncaa_quant.registry.store import ModelRegistry
from ncaa_quant.ratings.state_space import run_filter
from ncaa_quant.utils.timeutils import to_utc
from ncaa_quant.webapp.export import compute_p_favored, raw_tier_from_p_favored

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent
AS_OF_2024_W5 = datetime(2024, 9, 24, 10, 0, tzinfo=UTC)
HIST_PATH = ROOT / "data" / "artifacts" / "state_space" / "filter_history.parquet"
PLAYS_OBS_CACHE = ART / "plays_obs_2019_2024.parquet"
ADV_OBS_CACHE = ART / "advanced_obs_2019_2025.parquet"


def _log(msg: str) -> None:
    now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"D3 {now} {msg}", flush=True)


def _timed(label: str, fn: Any) -> tuple[Any, float]:
    t0 = time.perf_counter()
    out = fn()
    dt = time.perf_counter() - t0
    _log(f"TIMED {label}={dt:.3f}s")
    return out, dt


def _load_table(store: ParquetStore, table: str, seasons: tuple[int, ...]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for season in seasons:
        for path in store._matching_paths(table, {"season": int(season)}):  # noqa: SLF001
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def snapshot_from_filter_history(hist: pd.DataFrame, as_of: datetime) -> dict[str, float]:
    work = hist.copy()
    work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    work = work.loc[work["event_time"] < cutoff]
    if work.empty:
        return {}
    if "kind" in work.columns:
        post = work.loc[work["kind"].astype(str) == "postgame"]
        if not post.empty:
            work = post
    latest = work.sort_values("event_time").groupby("team_id", sort=False).tail(1)
    dims = ("off_epa", "def_epa", "st_value", "pace")
    out: dict[str, float] = {}
    for r in latest.itertuples(index=False):
        tid = str(int(r.team_id))
        for dim in dims:
            out[f"{tid}:{dim}"] = float(getattr(r, dim))
            sd = getattr(r, f"sd_{dim}", None)
            if sd is not None and pd.notna(sd):
                out[f"{tid}:sd_{dim}"] = float(sd)
    return out


def _fbs_ids(teams: pd.DataFrame) -> set[int]:
    if teams.empty or "classification" not in teams.columns:
        return set()
    fbs = teams.loc[teams["classification"].astype(str).str.casefold() == "fbs"]
    return {int(x) for x in fbs["team_id"]}


def _abs_stats(series: pd.Series) -> dict[str, float | None]:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {"n": 0, "min": None, "median": None, "p90": None, "max": None}
    arr = s.to_numpy(dtype=float)
    return {
        "n": int(len(arr)),
        "min": float(np.min(arr)),
        "median": float(np.median(arr)),
        "p90": float(np.percentile(arr, 90)),
        "max": float(np.max(arr)),
    }


def _tier_from_row(mu: float, p_ml: float) -> str:
    p_fav = compute_p_favored(mu, p_ml)
    if p_fav is None:
        return "null"
    return str(raw_tier_from_p_favored(float(p_fav)))


def diagnose_initialize_season(
    *,
    label: str,
    engine: StateSpaceRatingEngine,
    season: int,
    as_of: datetime,
) -> dict[str, Any]:
    """Peel initialize_season into timed steps (do not refit; no src edit)."""
    timings: dict[str, float] = {}
    t_all = time.perf_counter()
    engine._current_season = int(season)  # noqa: SLF001
    engine._week1_freeze = None  # noqa: SLF001
    engine._week1_snapshot = None  # noqa: SLF001
    team_ids = engine._team_ids_for_season(season)  # noqa: SLF001
    t0 = time.perf_counter()
    states = engine._priors_for_season(season, team_ids)  # noqa: SLF001
    timings["priors_target_season"] = time.perf_counter() - t0
    engine._states = {str(k): v for k, v in states.items()}  # noqa: SLF001

    if engine.observations.empty:
        timings["total"] = time.perf_counter() - t_all
        return {"label": label, "timings_s": timings, "n_hist": 0}

    t0 = time.perf_counter()
    obs = engine.observations.copy()
    obs["event_time"] = [to_utc(pd.Timestamp(ts).to_pydatetime()) for ts in obs["event_time"]]
    timings["python_event_time_loop"] = time.perf_counter() - t0
    timings["n_obs_rows"] = float(len(obs))

    hist = obs.loc[obs["event_time"] < to_utc(as_of)]
    timings["n_hist_rows"] = float(len(hist))
    if hist.empty:
        timings["total"] = time.perf_counter() - t_all
        return {"label": label, "timings_s": timings, "n_hist": 0}

    t0 = time.perf_counter()
    preseason: dict[int, dict[Any, Any]] = {}
    seasons = sorted({int(x) for x in hist["season"].unique()} | {int(season)})
    for s in seasons:
        preseason[s] = engine._priors_for_season(s, engine._team_ids_for_season(s))  # noqa: SLF001
    timings["preseason_states_all_seasons"] = time.perf_counter() - t0
    timings["n_preseason_seasons"] = float(len(seasons))

    t0 = time.perf_counter()
    result = run_filter(
        hist,
        config=engine.ss_config,
        fbs_team_ids=engine.fbs_team_ids,
        preseason_states=preseason,
        record_weekly=True,
    )
    timings["run_filter_record_weekly_true"] = time.perf_counter() - t0
    timings["n_history_rows"] = float(len(result.history))

    t0 = time.perf_counter()
    engine._ingest_history(result.history)  # noqa: SLF001
    timings["_ingest_history"] = time.perf_counter() - t0
    timings["total"] = time.perf_counter() - t_all
    _log(f"initialize_season_steps {label} " + json.dumps(timings))
    return {
        "label": label,
        "timings_s": timings,
        "n_hist": int(len(hist)),
        "n_history_rows": int(len(result.history)),
        "n_states": int(len(engine._states)),  # noqa: SLF001
    }


def predict_week5(
    *,
    games: pd.DataFrame,
    as_of: datetime,
    rating_state: dict[str, float],
    provider: ProductionFeatureProvider,
    predictor: Any,
) -> pd.DataFrame:
    features = provider.compute_game_features(
        games,
        as_of,
        rating_state=rating_state,
        market_features=False,
    )
    pred = predictor.predict(features)
    out = pred.copy()
    if "game_id" not in out.columns and "game_id" in features.columns:
        out["game_id"] = features["game_id"].to_numpy()
    return out


def main() -> None:
    started = datetime.now(tz=UTC)
    _log(f"start={started.isoformat()}")
    cfg_app = load_config()
    staged = Path(cfg_app.paths.staged_dir)
    store = ParquetStore(staged)
    payload = load_backtest_config("task23_fundamental_full_reduced_v3")
    wf = walkforward_config_from_mapping(payload)
    timings: dict[str, float] = {}
    notes: list[str] = []

    seasons_adv = (2019, 2020, 2021, 2022, 2023, 2024, 2025)
    seasons_plays = (2019, 2020, 2021, 2022, 2023, 2024)

    games_all, dt = _timed(
        "load_games_2019_2026",
        lambda: load_staged_games(staged, seasons_adv + (2026,)),
    )
    timings["load_games"] = dt
    teams, dt = _timed("load_teams_2019_2025", lambda: _load_table(store, "teams", seasons_adv))
    timings["load_teams"] = dt
    fbs_ids = _fbs_ids(teams)
    _log(f"n_fbs_ids={len(fbs_ids)} n_games={len(games_all)}")

    # --- Advanced-box obs (Task 14-like; W9-I hang suspect #2) ---
    if ADV_OBS_CACHE.is_file():
        adv_obs = pd.read_parquet(ADV_OBS_CACHE)
        timings["advanced_obs_build"] = 0.0
        notes.append(f"loaded advanced obs cache {ADV_OBS_CACHE}")
        _log(f"advanced_obs_cache n={len(adv_obs)}")
    else:
        advanced, dt = _timed(
            "load_advanced_box_2019_2025",
            lambda: _load_table(store, "advanced_box", seasons_adv),
        )
        timings["load_advanced"] = dt
        g_completed = games_all.loc[games_all["season"].astype(int).isin(seasons_adv)].copy()
        adv_obs, dt = _timed(
            "build_game_observations_from_advanced",
            lambda: build_game_observations_from_advanced(advanced, g_completed),
        )
        timings["advanced_obs_build"] = dt
        ADV_OBS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        adv_obs.to_parquet(ADV_OBS_CACHE, index=False)
        _log(f"wrote {ADV_OBS_CACHE} n={len(adv_obs)}")

    t0 = time.perf_counter()
    run_filter(adv_obs, record_weekly=False, fbs_team_ids=fbs_ids)
    timings["run_filter_advanced_weekly_false"] = time.perf_counter() - t0
    _log(f"TIMED run_filter_advanced_weekly_false={timings['run_filter_advanced_weekly_false']:.3f}s")

    t0 = time.perf_counter()
    run_filter(adv_obs, record_weekly=True, fbs_team_ids=fbs_ids)
    timings["run_filter_advanced_weekly_true"] = time.perf_counter() - t0
    _log(f"TIMED run_filter_advanced_weekly_true={timings['run_filter_advanced_weekly_true']:.3f}s")

    priors, dt = _timed(
        "load_fitted_priors",
        lambda: load_fitted_priors_frame_for_backtest(staged, seasons_plays),
    )
    timings["load_priors"] = dt
    _log(f"priors_rows={0 if priors is None else len(priors)}")

    engine_adv = StateSpaceRatingEngine(
        observations=adv_obs,
        config=wf,
        priors_frame=priors,
        fbs_team_ids=fbs_ids,
    )
    # W9-I hang: initialize_season(2026) with 2025 observations.
    live_2026 = games_all.loc[
        (games_all["season"].astype(int) == 2026) & (games_all["week"].astype(int) == 1)
    ].copy()
    cal_2026 = live_2026.copy()
    if "start_date" in cal_2026.columns:
        cal_2026["event_time"] = pd.to_datetime(cal_2026["start_date"], utc=True)
    calendar_2026 = WeekDecisionCalendar.from_games(cal_2026)
    as_of_2026_w1 = week_decision_as_of(2026, 1, wf, calendar=calendar_2026)
    prior_2026 = as_of_2026_w1 - timedelta(seconds=1)
    _log(f"initialize_season(2026) prior_as_of={prior_2026.isoformat()} n_adv_obs={len(adv_obs)}")
    hang_2026 = diagnose_initialize_season(
        label="advanced_init_2026",
        engine=engine_adv,
        season=2026,
        as_of=prior_2026,
    )

    # --- Plays obs (champion method; W9-I hang suspect #1) ---
    if PLAYS_OBS_CACHE.is_file():
        plays_obs = pd.read_parquet(PLAYS_OBS_CACHE)
        timings["plays_obs_build"] = 0.0
        notes.append(f"loaded plays obs cache {PLAYS_OBS_CACHE}")
        _log(f"plays_obs_cache n={len(plays_obs)}")
    else:
        plays, dt = _timed(
            "load_plays_2019_2024",
            lambda: _load_table(store, "plays", seasons_plays),
        )
        timings["load_plays"] = dt
        _log(f"n_plays={len(plays)}")
        t0 = time.perf_counter()
        if "garbage_time" not in plays.columns:
            apply_garbage_time(plays)
        timings["apply_garbage_time_copy"] = time.perf_counter() - t0
        _log(f"TIMED apply_garbage_time={timings['apply_garbage_time_copy']:.3f}s")
        g_plays = games_all.loc[games_all["season"].astype(int).isin(seasons_plays)].copy()
        t0 = time.perf_counter()
        plays_obs, n_on, n_off = build_observations_from_staged(
            plays=plays,
            games=g_plays,
            advanced=None,
            garbage_time_filter=True,
        )
        timings["build_observations_from_staged_plays"] = time.perf_counter() - t0
        _log(
            f"TIMED plays_obs_build={timings['build_observations_from_staged_plays']:.3f}s "
            f"n_obs={len(plays_obs)} n_on={n_on} n_off={n_off}"
        )
        plays_obs.to_parquet(PLAYS_OBS_CACHE, index=False)
        _log(f"wrote {PLAYS_OBS_CACHE}")

    t0 = time.perf_counter()
    run_filter(plays_obs, record_weekly=False, fbs_team_ids=fbs_ids)
    timings["run_filter_plays_weekly_false"] = time.perf_counter() - t0
    _log(f"TIMED run_filter_plays_weekly_false={timings['run_filter_plays_weekly_false']:.3f}s")

    t0 = time.perf_counter()
    run_filter(plays_obs, record_weekly=True, fbs_team_ids=fbs_ids)
    timings["run_filter_plays_weekly_true"] = time.perf_counter() - t0
    _log(f"TIMED run_filter_plays_weekly_true={timings['run_filter_plays_weekly_true']:.3f}s")

    # Champion-method reconstruction for 2024 week 5
    games_2024 = games_all.loc[games_all["season"].astype(int) == 2024].copy()
    cal_2024 = WeekDecisionCalendar.from_games(games_2024)
    as_of_w1_2024 = week_decision_as_of(2024, 1, wf, calendar=cal_2024)
    prior_2024 = as_of_w1_2024 - timedelta(seconds=1)
    as_of_w5 = week_decision_as_of(2024, 5, wf, calendar=cal_2024)
    _log(
        f"2024 calendar w1={as_of_w1_2024.isoformat()} w5={as_of_w5.isoformat()} "
        f"expected_w5={AS_OF_2024_W5.isoformat()} match={as_of_w5 == AS_OF_2024_W5}"
    )

    engine_ch = StateSpaceRatingEngine(
        observations=plays_obs,
        config=wf,
        priors_frame=priors,
        fbs_team_ids=fbs_ids,
    )
    hang_2024 = diagnose_initialize_season(
        label="plays_init_2024",
        engine=engine_ch,
        season=2024,
        as_of=prior_2024,
    )
    t0 = time.perf_counter()
    for week in (1, 2, 3, 4):
        wg = games_2024.loc[games_2024["week"].astype(int) == int(week)].copy()
        engine_ch.update_after_games(wg)
        _log(f"update_after_games 2024 week={week} n={len(wg)}")
    timings["update_after_games_w1_w4"] = time.perf_counter() - t0
    champ_state = engine_ch.state_snapshot()
    _log(f"champion_state n_keys={len(champ_state)}")

    # Task 14 filter_history snapshot
    hist = pd.read_parquet(HIST_PATH)
    t14_state = snapshot_from_filter_history(hist, AS_OF_2024_W5)
    _log(f"filter_history_state n_keys={len(t14_state)} n_hist_rows={len(hist)}")

    w5_games = games_2024.loc[games_2024["week"].astype(int) == 5].copy()
    _log(f"n_2024_week5_games={len(w5_games)}")

    registry = ModelRegistry(ROOT / "data" / "registry", tracking_uri=None)
    champ = registry.resolve_champion()
    art_dir = Path(champ.artifact_dir)
    predictor = load_production_ensemble(art_dir / ENSEMBLE_FILENAME)
    provider = ProductionFeatureProvider(config=wf, snapshots=None, cfbd_lines=None)
    poss_path = art_dir / "possessions_artifacts.pkl"
    if poss_path.is_file():
        with poss_path.open("rb") as fh:
            poss = pickle.load(fh)  # noqa: S301
        if isinstance(poss, dict):
            provider._possessions_artifacts = poss  # noqa: SLF001
            _log(f"attached possessions_artifacts n={len(poss)}")

    pred_t14 = predict_week5(
        games=w5_games,
        as_of=AS_OF_2024_W5,
        rating_state=t14_state,
        provider=provider,
        predictor=predictor,
    )
    pred_ch = predict_week5(
        games=w5_games,
        as_of=AS_OF_2024_W5,
        rating_state=champ_state,
        provider=provider,
        predictor=predictor,
    )

    def _std(pred: pd.DataFrame) -> pd.DataFrame:
        out = pred.copy()
        out["game_id"] = out["game_id"].astype("int64")
        if "mu_margin" not in out.columns:
            out["mu_margin"] = out.get("pred_margin")
        if "sigma_margin" not in out.columns:
            out["sigma_margin"] = out.get("sigma_m")
        if "p_ml_home" not in out.columns:
            out["p_ml_home"] = out.get("p_win_home")
        return out[["game_id", "mu_margin", "sigma_margin", "p_ml_home"]].copy()

    a = _std(pred_t14).rename(
        columns={
            "mu_margin": "mu_t14",
            "sigma_margin": "sigma_t14",
            "p_ml_home": "p_t14",
        }
    )
    b = _std(pred_ch).rename(
        columns={
            "mu_margin": "mu_ch",
            "sigma_margin": "sigma_ch",
            "p_ml_home": "p_ch",
        }
    )
    merged = a.merge(b, on="game_id", how="inner")
    merged["d_mu"] = (merged["mu_t14"] - merged["mu_ch"]).abs()
    merged["d_sigma"] = (merged["sigma_t14"] - merged["sigma_ch"]).abs()
    merged["d_p"] = (merged["p_t14"] - merged["p_ch"]).abs()
    merged["tier_t14"] = [
        _tier_from_row(float(r.mu_t14), float(r.p_t14)) for r in merged.itertuples(index=False)
    ]
    merged["tier_ch"] = [
        _tier_from_row(float(r.mu_ch), float(r.p_ch)) for r in merged.itertuples(index=False)
    ]
    n_agree = int((merged["tier_t14"] == merged["tier_ch"]).sum())
    delta = {
        "n_games": int(len(merged)),
        "n_t14": int(len(a)),
        "n_champ": int(len(b)),
        "as_of": AS_OF_2024_W5.isoformat(),
        "mu_margin_abs_delta": _abs_stats(merged["d_mu"]),
        "sigma_margin_abs_delta": _abs_stats(merged["d_sigma"]),
        "p_ml_home_abs_delta": _abs_stats(merged["d_p"]),
        "conviction_tier_agreement": {
            "n_agree": n_agree,
            "n": int(len(merged)),
            "frac": (n_agree / len(merged)) if len(merged) else None,
            "t14_counts": merged["tier_t14"].value_counts().to_dict(),
            "champ_counts": merged["tier_ch"].value_counts().to_dict(),
        },
        "sources": {
            "task14": str(HIST_PATH.as_posix()),
            "champion": "StateSpaceRatingEngine.initialize_season(2024)+update_after_games(weeks 1-4); plays-first GT obs; fitted priors; default StateSpaceConfig",
        },
    }
    print("D3_DELTA=" + json.dumps(delta, indent=2, default=str), flush=True)

    report = {
        "timings_s": timings,
        "hang_initialize_season_2026_advanced": hang_2026,
        "hang_initialize_season_2024_plays": hang_2024,
        "notes": notes,
        "delta": delta,
        "as_of_2024_w5_calendar": as_of_w5.isoformat(),
        "as_of_2026_w1_for_hang": as_of_2026_w1.isoformat(),
    }
    (ART / "d3_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    merged.to_csv(ART / "d3_week5_pair.csv", index=False)
    _log(f"wrote {ART / 'd3_report.json'}")
    ended = datetime.now(tz=UTC)
    _log(f"end={ended.isoformat()} elapsed_sec={(ended - started).total_seconds():.3f}")


if __name__ == "__main__":
    main()
