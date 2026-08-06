"""One-off Task 13 acceptance: Elo 2014-2024 vs SP+ / closing lines.

Not part of the package surface — run with ``uv run python scripts/_elo_acceptance_13.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.ingestion.cfbd import (
    CFBDClient,
    normalize_games_payload,
    normalize_lines_payload,
    normalize_teams_payload,
)
from ncaa_quant.ratings.elo_baseline import (
    ats_accuracy_vs_closing,
    end_of_season_ratings,
    one_step_log_loss,
    run_elo,
    spearman_rank_corr,
    tune_elo_params,
)
from ncaa_quant.ingestion.teams import load_team_name_map

CACHE = Path("data/tmp/elo_acceptance_13")
SEASONS = list(range(2014, 2025))
REPORT_SEASONS = (2019, 2023)


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
    frames: list[pd.DataFrame] = []
    now = datetime.now(tz=UTC)
    for season in SEASONS:
        for stype in ("regular", "postseason"):
            print(f"fetch games {season} {stype}", flush=True)
            body = client.fetch_games(season, season_type=stype, classification="fbs")
            frames.append(
                normalize_games_payload(body, ingested_at=now, source_version="acceptance13")
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
    cfg = load_config()
    team_map = load_team_name_map(cfg.data.team_names_path)
    frames: list[pd.DataFrame] = []
    now = datetime.now(tz=UTC)
    for season in SEASONS:
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


def _load_or_fetch_lines(client: CFBDClient, games: pd.DataFrame) -> pd.DataFrame:
    path = CACHE / "lines.parquet"
    if path.exists():
        return pd.read_parquet(path)
    starts = {
        int(r.game_id): pd.Timestamp(r.start_date).to_pydatetime()
        for r in games.itertuples(index=False)
    }
    frames: list[pd.DataFrame] = []
    now = datetime.now(tz=UTC)
    for season in SEASONS:
        print(f"fetch lines {season}", flush=True)
        body = client.get("/lines", {"year": season})
        # week unused when payload carries week; normalizer falls back to arg.
        frames.append(
            normalize_lines_payload(
                body,
                season=season,
                week=0,
                ingested_at=now,
                game_start_by_id=starts,
                source_version="acceptance13",
            )
        )
    lines = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines.to_parquet(path, index=False)
    return lines


def _load_or_fetch_sp(client: CFBDClient, teams: pd.DataFrame) -> dict[int, pd.DataFrame]:
    path = CACHE / "sp_plus.json"
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {int(k): pd.DataFrame(v) for k, v in raw.items()}

    out: dict[int, pd.DataFrame] = {}
    for season in REPORT_SEASONS:
        print(f"fetch sp+ {season}", flush=True)
        body = client.get("/ratings/sp", {"year": season})
        rows = json.loads(body)
        school_to_id = {
            str(r.school): int(r.team_id)
            for r in teams.loc[teams["season"] == season].itertuples(index=False)
        }
        parsed: list[dict[str, object]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            school = str(item.get("team") or item.get("school") or "")
            tid = school_to_id.get(school)
            rating = item.get("rating")
            if tid is None or rating is None:
                continue
            parsed.append(
                {
                    "team_id": tid,
                    "school": school,
                    "sp_rating": float(rating),
                    "sp_rank": item.get("ranking"),
                }
            )
        out[season] = pd.DataFrame(parsed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({str(k): v.to_dict(orient="records") for k, v in out.items()}),
        encoding="utf-8",
    )
    return out


def main() -> None:
    with _client() as client:
        games = _load_or_fetch_games(client)
        teams = _load_or_fetch_teams(client)
        lines = _load_or_fetch_lines(client, games)
        sp_by_season = _load_or_fetch_sp(client, teams)

    print(f"games={len(games)} teams={len(teams)} lines={len(lines)}", flush=True)

    best, scan = tune_elo_params(
        games,
        teams=teams,
        k_grid=(10.0, 15.0, 20.0, 25.0, 30.0),
        mov_factor_grid=(1.5, 2.2, 2.8),
        fbs_only=True,
    )
    print("TUNE_SCAN", flush=True)
    print(scan.to_string(index=False), flush=True)
    print(
        f"BEST k={best.k_factor} mov_factor={best.mov_factor} "
        f"hfa={best.hfa} season_regression={best.season_regression}",
        flush=True,
    )

    game_log, history, _ = run_elo(games, config=best, teams=teams, fbs_only=True)
    ll = one_step_log_loss(game_log)
    ats = ats_accuracy_vs_closing(game_log, lines)
    print(f"ONE_STEP_LOG_LOSS={ll:.6f} n_games={len(game_log)}", flush=True)
    print(
        f"ATS_ACCURACY={ats['ats_accuracy']:.6f} n_ats={ats['n_ats']:.0f} "
        f"n_push={ats['n_push']:.0f}",
        flush=True,
    )

    school_lookup = {
        (int(r.season), int(r.team_id)): str(r.school)
        for r in teams.itertuples(index=False)
    }

    for season in REPORT_SEASONS:
        eos = end_of_season_ratings(history, season=season, teams=teams)
        top = eos.head(15).copy()
        top["school"] = [
            school_lookup.get((season, int(t)), str(t)) for t in top["team_id"]
        ]
        print(f"\nTOP_15_ELO season={season}", flush=True)
        print(top[["school", "elo", "week"]].to_string(index=False), flush=True)

        sp = sp_by_season[season]
        elo_map = {int(r.team_id): float(r.elo) for r in eos.itertuples(index=False)}
        sp_map = {int(r.team_id): float(r.sp_rating) for r in sp.itertuples(index=False)}
        corr = spearman_rank_corr(elo_map, sp_map)
        print(
            f"SPEARMAN_ELO_vs_SP season={season} corr={corr:.4f} "
            f"n_common={len(set(elo_map) & set(sp_map))}",
            flush=True,
        )


if __name__ == "__main__":
    main()
