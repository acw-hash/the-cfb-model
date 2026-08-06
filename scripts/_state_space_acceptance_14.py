"""One-off Task 14 acceptance: Kalman filter 2014-2025.

Not part of the package surface — run with
``uv run python scripts/_state_space_acceptance_14.py``.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.ingestion.cfbd import (
    CFBDClient,
    normalize_advanced_payload,
    normalize_games_payload,
    normalize_teams_payload,
)
from ncaa_quant.ingestion.teams import load_team_name_map
from ncaa_quant.ratings.diagnostics import filter_health_stats
from ncaa_quant.ratings.state_space import (
    StateSpaceConfig,
    build_game_observations_from_advanced,
    end_of_season_ratings,
    parameter_recovery_coverage,
    run_filter,
    team_sd_trajectory,
    tune_process_noise,
)

CACHE = Path("data/tmp/state_space_acceptance_14")
ELO_CACHE = Path("data/tmp/elo_acceptance_13")
SEASONS = list(range(2014, 2026))  # 2014–2025 inclusive


def _client() -> CFBDClient:
    secrets = load_secrets()
    cfg = load_config()
    return CFBDClient(
        secrets.cfbd_api_key.get_secret_value(),
        requests_per_second=cfg.data.cfbd_requests_per_second,
        rate_limit_reserve=5,
    )


def _load_or_fetch_games(client: CFBDClient) -> pd.DataFrame:
    path = CACHE / "games.parquet"
    if path.exists():
        return pd.read_parquet(path)
    # Reuse Elo cache for overlapping seasons when present.
    frames: list[pd.DataFrame] = []
    elo_path = ELO_CACHE / "games.parquet"
    if elo_path.exists():
        elo_games = pd.read_parquet(elo_path)
        frames.append(elo_games)
        have = set(int(s) for s in elo_games["season"].unique())
    else:
        have = set()
    now = datetime.now(tz=UTC)
    for season in SEASONS:
        if season in have:
            continue
        for stype in ("regular", "postseason"):
            print(f"fetch games {season} {stype}", flush=True)
            body = client.fetch_games(season, season_type=stype, classification="fbs")
            frames.append(
                normalize_games_payload(body, ingested_at=now, source_version="acceptance14")
            )
    games = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    games = games.drop_duplicates(subset=["game_id"], keep="last")
    path.parent.mkdir(parents=True, exist_ok=True)
    games.to_parquet(path, index=False)
    return games


def _load_or_fetch_teams(client: CFBDClient) -> pd.DataFrame:
    path = CACHE / "teams.parquet"
    if path.exists():
        return pd.read_parquet(path)
    elo_path = ELO_CACHE / "teams.parquet"
    frames: list[pd.DataFrame] = []
    have: set[int] = set()
    if elo_path.exists():
        elo_teams = pd.read_parquet(elo_path)
        frames.append(elo_teams)
        have = set(int(s) for s in elo_teams["season"].unique())
    cfg = load_config()
    team_map = load_team_name_map(cfg.data.team_names_path)
    now = datetime.now(tz=UTC)
    for season in SEASONS:
        if season in have:
            continue
        print(f"fetch teams {season}", flush=True)
        body = client.fetch_teams(season)
        frames.append(
            normalize_teams_payload(
                body, season=season, ingested_at=now, team_map=team_map
            )
        )
    teams = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    path.parent.mkdir(parents=True, exist_ok=True)
    teams.to_parquet(path, index=False)
    return teams


def _school_to_id(teams: pd.DataFrame, season: int) -> dict[str, int]:
    sub = teams.loc[teams["season"] == season]
    return {str(r.school): int(r.team_id) for r in sub.itertuples(index=False)}


def _load_or_fetch_advanced(client: CFBDClient, games: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    path = CACHE / "advanced.parquet"
    if path.exists():
        return pd.read_parquet(path)

    cfg = load_config()
    team_map = load_team_name_map(cfg.data.team_names_path)
    starts = {
        int(r.game_id): pd.Timestamp(r.start_date).to_pydatetime()
        for r in games.itertuples(index=False)
    }
    now = datetime.now(tz=UTC)
    frames: list[pd.DataFrame] = []

    # Prefer year-level pull (fewer round-trips); fall back to week loop.
    for season in SEASONS:
        print(f"fetch advanced {season}", flush=True)
        school_map = _school_to_id(teams, season)
        try:
            body = client.get("/stats/game/advanced", {"year": season})
            frame = normalize_advanced_payload(
                body,
                season=season,
                week=0,
                ingested_at=now,
                school_to_id=school_map,
                team_map=team_map,
                game_start_by_id=starts,
                source_version="acceptance14",
            )
            if frame.empty:
                raise RuntimeError("empty year pull")
            # Fill week from games when normalizer got week=0.
            week_map = {
                int(r.game_id): int(r.week) for r in games.loc[games["season"] == season].itertuples()
            }
            frame["week"] = frame["game_id"].map(week_map).fillna(frame["week"]).astype(int)
            frames.append(frame)
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"  year pull failed ({exc}); week loop", flush=True)

        weeks = sorted(
            int(w) for w in games.loc[games["season"] == season, "week"].dropna().unique()
        )
        for week in weeks:
            for stype in ("regular", "postseason"):
                body = client.fetch_advanced(season, week, season_type=stype)
                frames.append(
                    normalize_advanced_payload(
                        body,
                        season=season,
                        week=week,
                        ingested_at=now,
                        school_to_id=school_map,
                        team_map=team_map,
                        game_start_by_id=starts,
                        source_version="acceptance14",
                    )
                )

    advanced = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    advanced = advanced.drop_duplicates(subset=["game_id", "team_id"], keep="last")
    path.parent.mkdir(parents=True, exist_ok=True)
    advanced.to_parquet(path, index=False)
    return advanced


def _fbs_ids_for_season(teams: pd.DataFrame, season: int) -> set[int]:
    sub = teams.loc[teams["season"] == season]
    if "classification" in sub.columns:
        mask = sub["classification"].astype(str).str.casefold() == "fbs"
        return set(int(x) for x in sub.loc[mask, "team_id"])
    return set(int(x) for x in sub["team_id"])


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)

    # --- Parameter recovery (always, no network) ---
    recovery = parameter_recovery_coverage(n_teams=16, n_weeks=10, seed=7)
    print("PARAMETER_RECOVERY", json.dumps(recovery), flush=True)

    client = _client()
    games = _load_or_fetch_games(client)
    teams = _load_or_fetch_teams(client)
    advanced = _load_or_fetch_advanced(client, games, teams)

    # Completed FBS games only.
    g = games.loc[games["completed"].astype(bool)].copy()
    g = g.loc[g["home_points"].notna() & g["away_points"].notna()]
    obs = build_game_observations_from_advanced(advanced, g)
    obs = obs.loc[obs["season"].isin(SEASONS)].copy()

    # Mark FCS sides using per-season FBS sets (union is fine for is_fcs check).
    fbs_all: set[int] = set()
    for season in SEASONS:
        fbs_all |= _fbs_ids_for_season(teams, season)
    obs["home_is_fcs"] = ~obs["home_team_id"].isin(fbs_all)
    obs["away_is_fcs"] = ~obs["away_team_id"].isin(fbs_all)

    print(
        f"obs_games={len(obs)} advanced_rows={len(advanced)} games={len(g)}",
        flush=True,
    )

    # Tune Q on a subset of seasons for speed (2018–2022), then full run.
    tune_obs = obs.loc[obs["season"].between(2018, 2022)]
    print(f"tuning Q on {len(tune_obs)} games (2018-2022)...", flush=True)
    tuned_cfg, best_q, best_ll = tune_process_noise(
        tune_obs,
        q_scales=[0.5, 1.0, 2.0, 4.0],
        fbs_team_ids=fbs_all,
        dims=(),  # global scale scan only (fast); per-dim left for offseason
    )
    print("FITTED_Q", json.dumps(best_q), flush=True)
    print(f"TUNE_LOG_LIK={best_ll:.2f}", flush=True)

    t0 = time.perf_counter()
    result = run_filter(obs, config=tuned_cfg, fbs_team_ids=fbs_all, record_weekly=True)
    wall = time.perf_counter() - t0
    print(f"FILTER_WALL_CLOCK_SEC={wall:.3f}", flush=True)
    print(f"FILTER_LOG_LIK={result.log_likelihood:.2f}", flush=True)
    print(f"history_rows={len(result.history)} innov_rows={len(result.innovations)}", flush=True)

    health = filter_health_stats(result.innovations)
    print(f"FILTER_HEALTH {health.summary()}", flush=True)

    # End-of-2023 top 15 by off and def.
    eos = end_of_season_ratings(result.history, 2023, config=tuned_cfg, kind="weekly")
    school_map_2023 = {
        int(r.team_id): str(r.school)
        for r in teams.loc[teams["season"] == 2023].itertuples(index=False)
    }
    eos["school"] = eos["team_id"].map(school_map_2023)

    top_off = eos.sort_values("off_epa", ascending=False).head(15)
    # Higher def_epa = more EPA suppressed = better defense (off − def convention).
    top_def = eos.sort_values("def_epa", ascending=False).head(15)

    print("\nTOP_15_OFF_EPA season=2023", flush=True)
    print(top_off[["school", "off_epa", "sd_off_epa", "week"]].to_string(index=False), flush=True)
    print("\nTOP_15_DEF_EPA season=2023 (higher better)", flush=True)
    print(top_def[["school", "def_epa", "sd_def_epa", "week"]].to_string(index=False), flush=True)

    # SD trajectory for Michigan (or top off team) across 2023.
    focus_id = int(top_off.iloc[0]["team_id"])
    focus_school = school_map_2023.get(focus_id, str(focus_id))
    traj = team_sd_trajectory(result.history, focus_id, 2023, dim="off_epa", kind="postgame")
    print(f"\nSD_TRAJECTORY team={focus_school} season=2023 dim=off_epa", flush=True)
    print(traj.to_string(index=False), flush=True)
    if len(traj) >= 2:
        print(
            f"SD_SHRINK first={traj['sd_off_epa'].iloc[0]:.4f} "
            f"last={traj['sd_off_epa'].iloc[-1]:.4f}",
            flush=True,
        )

    # Persist artifacts for notes.
    summary = {
        "parameter_recovery": recovery,
        "fitted_q": best_q,
        "tune_log_lik": best_ll,
        "filter_wall_clock_sec": wall,
        "filter_log_lik": result.log_likelihood,
        "filter_health": {
            "mean_z": health.mean_z,
            "var_z": health.var_z,
            "n": health.n,
            "misspecified": health.misspecified,
        },
        "n_obs": len(obs),
        "focus_team": focus_school,
    }
    (CACHE / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    result.history.to_parquet(CACHE / "history.parquet", index=False)
    result.innovations.to_parquet(CACHE / "innovations.parquet", index=False)
    print(f"\nwrote {CACHE}", flush=True)


if __name__ == "__main__":
    main()
