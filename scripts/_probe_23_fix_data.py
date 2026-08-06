"""Task 23-FIX-DATA read-only probe. Scratch → data/_probe/. No staged/features writes.

Run: ``uv run python scripts/_probe_23_fix_data.py``
"""

from __future__ import annotations

import json
import math
import traceback
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.data.schemas import validate_table
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.features.builders.roster import is_portal_era, portal_net_rating
from ncaa_quant.ingestion.cfbd import (
    CFBDClient,
    ENDPOINT_SPECS,
    RateLimitBudgetError,
    normalize_advanced_payload,
    normalize_coaches_payload,
    normalize_games_payload,
    normalize_lines_payload,
    normalize_plays_payload,
    normalize_portal_payload,
    normalize_recruiting_payload,
    normalize_returning_payload,
    normalize_roster_payload,
    normalize_talent_payload,
    normalize_teams_payload,
    normalize_venues_payload,
)
from ncaa_quant.ingestion.odds_api import (
    BASE_URL as ODDS_BASE_URL,
    OddsAPIClient,
    OddsAPIError,
    estimate_historical_credits,
    plan_historical_units,
)
from ncaa_quant.ingestion.teams import load_team_name_map
from ncaa_quant.ingestion.weather import OpenMeteoClient

PROBE_DIR = Path("data/_probe")
SEASONS = (2014, 2016, 2019, 2020, 2021, 2023, 2025)
# Usable non-null coverage threshold for EPA/WP "complete" claims.
COMPLETE_COVERAGE = 0.90
# Hard Odds spend cap for this entire task.
ODDS_SPEND_CAP = 50
NULL_ANOMALY = 0.50  # flag when critical col null rate exceeds this


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _save(name: str, payload: Any) -> Path:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    path = PROBE_DIR / name
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path}", flush=True)
    return path


def _null_rates(df: pd.DataFrame, cols: Sequence[str]) -> dict[str, float]:
    if df.empty:
        return {c: 1.0 for c in cols if c in df.columns or True}
    out: dict[str, float] = {}
    for c in cols:
        if c not in df.columns:
            out[c] = 1.0
        else:
            out[c] = float(df[c].isna().mean())
    return out


def _flag(
    *,
    rows: int,
    schema_ok: bool,
    expected_rows: bool,
    null_anomaly: bool,
    error: str | None = None,
) -> str:
    if error:
        return "MISSING"
    if not expected_rows and rows == 0:
        # Legitimate empty (e.g. portal pre-2021) still GO if schema path ok.
        return "GO" if schema_ok else "DEGRADED"
    if rows == 0:
        return "MISSING"
    if not schema_ok:
        return "DEGRADED"
    if null_anomaly:
        return "DEGRADED"
    return "GO"


def _school_to_id(teams: pd.DataFrame) -> dict[str, int]:
    if teams.empty:
        return {}
    return {str(r.school): int(r.team_id) for r in teams.itertuples(index=False)}


def _game_starts(games: pd.DataFrame) -> dict[int, datetime]:
    out: dict[int, datetime] = {}
    for row in games.itertuples(index=False):
        ts = pd.Timestamp(row.start_date)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        out[int(row.game_id)] = ts.to_pydatetime()
    return out


def _validate(table: str, df: pd.DataFrame) -> tuple[bool, str | None]:
    if df.empty:
        # Empty frames often fail pandera dtypes; treat as pass with 0 rows.
        return True, None
    try:
        validate_table(table, df)
        return True, None
    except Exception as exc:  # noqa: BLE001 — probe records any schema failure
        return False, f"{type(exc).__name__}: {exc}"


def _inspect_raw_plays_gt(raw: bytes) -> dict[str, Any]:
    """Inspect raw CFBD play JSON for GT-relevant fields (source, not staged)."""
    data = json.loads(raw)
    if not isinstance(data, list) or not data:
        return {"n_raw": 0}
    sample = data[0] if isinstance(data[0], dict) else {}
    keys = set(sample.keys()) if isinstance(sample, dict) else set()
    n = len(data)
    wp_keys = ("homeWinProb", "wp", "winProbability", "home_win_prob")
    score_keys = (
        "offenseScore",
        "defenseScore",
        "homeScore",
        "awayScore",
        "offense_score",
        "defense_score",
    )
    clock_keys = ("clock", "time", "secondsRemaining", "seconds_remaining")
    counts = {
        "n_raw": n,
        "has_period": "period" in keys or any("period" in str(k).casefold() for k in keys),
        "sample_keys": sorted(keys)[:40],
    }
    for label, candidates in (
        ("wp", wp_keys),
        ("score", score_keys),
        ("clock", clock_keys),
    ):
        present = [k for k in candidates if k in keys]
        nonempty = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            if any(item.get(k) is not None for k in present):
                nonempty += 1
        counts[f"{label}_keys_present"] = present
        counts[f"{label}_nonnull_rate"] = (nonempty / n) if n else 0.0
    # Also scan nested / alternate names in a few rows.
    period_nn = sum(
        1 for item in data if isinstance(item, dict) and item.get("period") is not None
    )
    counts["period_nonnull_rate"] = period_nn / n if n else 0.0
    return counts


def probe_cfbd() -> dict[str, Any]:
    secrets = load_secrets()
    cfg = load_config()
    key = secrets.cfbd_api_key.get_secret_value()
    auth = {"cfbd_key_present": bool(key), "auth_ok": False, "auth_error": None}
    if not key:
        auth["auth_error"] = "CFBD_API_KEY empty"
        _save("cfbd_auth.json", auth)
        return {"auth": auth, "matrix": {}, "era": {}, "error": "no key"}

    team_map = load_team_name_map(cfg.data.team_names_path)
    matrix: dict[str, dict[str, Any]] = {}
    era: dict[str, Any] = {
        "epa_wp_by_season": {},
        "advanced_epa_by_season": {},
        "gt_raw_by_season": {},
        "season_2020": {},
        "portal": {},
        "returning_null_rates": {},
        "roster": {},
    }
    api_calls = 0
    now = _now()

    with CFBDClient(
        key,
        requests_per_second=cfg.data.cfbd_requests_per_second,
    ) as client:
        try:
            body = client.fetch_teams(2023)
            api_calls += 1
            teams_probe = normalize_teams_payload(
                body, season=2023, ingested_at=now, team_map=team_map
            )
            auth["auth_ok"] = len(teams_probe) > 0
            auth["teams_2023_rows"] = int(len(teams_probe))
            auth["cfbd_remaining"] = client.remaining_requests
        except Exception as exc:  # noqa: BLE001
            auth["auth_error"] = f"{type(exc).__name__}: {exc}"
            _save("cfbd_auth.json", auth)
            return {"auth": auth, "matrix": {}, "era": {}, "api_calls": api_calls}

        _save("cfbd_auth.json", auth)

        # Venues once (static).
        venues_cell: dict[str, Any]
        try:
            vbody = client.fetch_venues()
            api_calls += 1
            venues_df = normalize_venues_payload(vbody, season=2023, ingested_at=now)
            vok, verr = _validate("venues", venues_df)
            missing_coords = 0
            if not venues_df.empty:
                missing_coords = int(
                    venues_df["latitude"].isna().sum() + venues_df["longitude"].isna().sum()
                )
            venues_cell = {
                "rows": int(len(venues_df)),
                "schema_ok": vok,
                "schema_error": verr,
                "null_rates": _null_rates(venues_df, ["latitude", "longitude", "dome"]),
                "venues_missing_either_coord_rows": missing_coords,
                "flag": _flag(
                    rows=len(venues_df),
                    schema_ok=vok,
                    expected_rows=True,
                    null_anomaly=False,
                ),
                "venues_df_path": None,
            }
            # Keep venue id→coords for Part D cross-check (parquet scratch).
            if not venues_df.empty:
                vpath = PROBE_DIR / "venues_probe.parquet"
                PROBE_DIR.mkdir(parents=True, exist_ok=True)
                venues_df.to_parquet(vpath, index=False)
                venues_cell["venues_df_path"] = str(vpath)
        except Exception as exc:  # noqa: BLE001
            venues_cell = {
                "rows": 0,
                "schema_ok": False,
                "schema_error": f"{type(exc).__name__}: {exc}",
                "flag": "MISSING",
            }

        for season in SEASONS:
            print(f"=== CFBD season {season} ===", flush=True)
            cells: dict[str, Any] = {"venues": {**venues_cell, "season_note": "static /venues"}}
            try:
                # Teams for school_to_id
                tbody = client.fetch_teams(season)
                api_calls += 1
                teams = normalize_teams_payload(
                    tbody, season=season, ingested_at=now, team_map=team_map
                )
                school_to_id = _school_to_id(teams)

                # Games regular + postseason
                game_frames: list[pd.DataFrame] = []
                for stype in ("regular", "postseason"):
                    gbody = client.fetch_games(season, season_type=stype, classification="fbs")
                    api_calls += 1
                    game_frames.append(normalize_games_payload(gbody, ingested_at=now))
                games = (
                    pd.concat(game_frames, ignore_index=True)
                    if game_frames
                    else pd.DataFrame()
                )
                if not games.empty:
                    games = games.drop_duplicates(subset=["game_id"], keep="last")
                gok, gerr = _validate("games", games)
                cells["games"] = {
                    "rows": int(len(games)),
                    "schema_ok": gok,
                    "schema_error": gerr,
                    "completed": int(games["completed"].sum()) if not games.empty else 0,
                    "weeks": sorted(int(w) for w in games["week"].dropna().unique())
                    if not games.empty
                    else [],
                    "conference_game_rate": float(games["conference_game"].mean())
                    if not games.empty
                    else None,
                    "null_rates": _null_rates(
                        games, ["home_points", "away_points", "venue_id", "completed"]
                    ),
                    "flag": _flag(
                        rows=len(games),
                        schema_ok=gok,
                        expected_rows=True,
                        null_anomaly=False,
                        error=gerr if not gok and len(games) == 0 else None,
                    ),
                }
                # Flag game-count band
                n_games = len(games)
                if season == 2020:
                    band = "covid_special"
                elif season == 2025 and n_games < 700:
                    band = "incomplete_or_early"
                elif 800 <= n_games <= 950:
                    band = "in_band"
                elif n_games > 950:
                    band = "above_band_fbs_fcs"
                else:
                    band = "below_band"
                cells["games"]["count_band"] = band

                if season == 2020 and not games.empty:
                    weeks = cells["games"]["weeks"]
                    era["season_2020"] = {
                        "game_count": n_games,
                        "completed": cells["games"]["completed"],
                        "weeks": weeks,
                        "week_span": [min(weeks), max(weeks)] if weeks else None,
                        "conference_game_rate": cells["games"]["conference_game_rate"],
                        "missing_weeks_in_0_15": [
                            w for w in range(0, 16) if w not in set(weeks)
                        ],
                        "note": (
                            "DESIGN §7.2 item 5: include for Stage-1 continuity, "
                            "exclude from mapping loss and headline metrics"
                        ),
                    }

                game_start_by_id = _game_starts(games)
                # Week tuples from games
                week_jobs: list[tuple[int, str]] = []
                if not games.empty:
                    for stype in ("regular", "postseason"):
                        sub = games[games["season_type"] == stype]
                        for w in sorted(sub["week"].dropna().unique()):
                            week_jobs.append((int(w), stype))

                # Plays / advanced / lines — all weeks for accurate counts
                play_frames: list[pd.DataFrame] = []
                adv_frames: list[pd.DataFrame] = []
                line_frames: list[pd.DataFrame] = []
                gt_sample: dict[str, Any] | None = None
                plays_error: str | None = None
                adv_error: str | None = None
                lines_error: str | None = None

                for week, stype in week_jobs:
                    try:
                        pbody = client.fetch_plays(
                            season, week, season_type=stype, classification="fbs"
                        )
                        api_calls += 1
                        if gt_sample is None and pbody and pbody != b"[]":
                            gt_sample = _inspect_raw_plays_gt(pbody)
                            gt_sample["sample_week"] = week
                            gt_sample["sample_season_type"] = stype
                        play_frames.append(
                            normalize_plays_payload(
                                pbody,
                                season=season,
                                week=week,
                                ingested_at=now,
                                school_to_id=school_to_id,
                                team_map=team_map,
                                game_start_by_id=game_start_by_id,
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        plays_error = f"{type(exc).__name__}: {exc}"
                        print(f"  plays fail {season} w{week} {stype}: {exc}", flush=True)

                    try:
                        abody = client.fetch_advanced(season, week, season_type=stype)
                        api_calls += 1
                        adv_frames.append(
                            normalize_advanced_payload(
                                abody,
                                season=season,
                                week=week,
                                ingested_at=now,
                                school_to_id=school_to_id,
                                team_map=team_map,
                                game_start_by_id=game_start_by_id,
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        adv_error = f"{type(exc).__name__}: {exc}"

                    try:
                        lbody = client.fetch_lines(season, week, season_type=stype)
                        api_calls += 1
                        line_frames.append(
                            normalize_lines_payload(
                                lbody,
                                season=season,
                                week=week,
                                ingested_at=now,
                                game_start_by_id=game_start_by_id,
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        lines_error = f"{type(exc).__name__}: {exc}"

                plays = (
                    pd.concat(play_frames, ignore_index=True) if play_frames else pd.DataFrame()
                )
                advanced = (
                    pd.concat(adv_frames, ignore_index=True) if adv_frames else pd.DataFrame()
                )
                lines = (
                    pd.concat(line_frames, ignore_index=True) if line_frames else pd.DataFrame()
                )

                pok, perr = _validate("plays", plays)
                play_nulls = _null_rates(plays, ["epa", "wp", "period", "success"])
                cells["plays"] = {
                    "rows": int(len(plays)),
                    "schema_ok": pok,
                    "schema_error": perr or plays_error,
                    "null_rates": play_nulls,
                    "weeks_fetched": len(week_jobs),
                    "flag": _flag(
                        rows=len(plays),
                        schema_ok=pok,
                        expected_rows=True,
                        null_anomaly=play_nulls.get("epa", 1.0) > NULL_ANOMALY
                        or play_nulls.get("wp", 1.0) > NULL_ANOMALY,
                        error=plays_error if len(plays) == 0 else None,
                    ),
                }
                era["epa_wp_by_season"][str(season)] = {
                    "epa_nonnull_rate": 1.0 - play_nulls.get("epa", 1.0),
                    "wp_nonnull_rate": 1.0 - play_nulls.get("wp", 1.0),
                    "n_plays": int(len(plays)),
                }
                if gt_sample is not None:
                    era["gt_raw_by_season"][str(season)] = gt_sample

                aok, aerr = _validate("advanced_box", advanced)
                adv_nulls = _null_rates(
                    advanced, ["offense_epa", "defense_epa", "success_rate", "explosiveness"]
                )
                cells["advanced"] = {
                    "rows": int(len(advanced)),
                    "schema_ok": aok,
                    "schema_error": aerr or adv_error,
                    "null_rates": adv_nulls,
                    "flag": _flag(
                        rows=len(advanced),
                        schema_ok=aok,
                        expected_rows=True,
                        null_anomaly=adv_nulls.get("offense_epa", 1.0) > NULL_ANOMALY,
                        error=adv_error if len(advanced) == 0 else None,
                    ),
                }
                era["advanced_epa_by_season"][str(season)] = {
                    "offense_epa_nonnull_rate": 1.0 - adv_nulls.get("offense_epa", 1.0),
                    "defense_epa_nonnull_rate": 1.0 - adv_nulls.get("defense_epa", 1.0),
                    "n_rows": int(len(advanced)),
                }

                lok, lerr = _validate("lines_historical", lines)
                line_nulls = _null_rates(lines, ["spread", "total", "home_ml", "away_ml"])
                cells["lines"] = {
                    "rows": int(len(lines)),
                    "schema_ok": lok,
                    "schema_error": lerr or lines_error,
                    "null_rates": line_nulls,
                    "n_open": int((lines["line_type"] == "open").sum())
                    if not lines.empty and "line_type" in lines.columns
                    else 0,
                    "n_close": int((lines["line_type"] == "close").sum())
                    if not lines.empty and "line_type" in lines.columns
                    else 0,
                    "flag": _flag(
                        rows=len(lines),
                        schema_ok=lok,
                        expected_rows=True,
                        null_anomaly=line_nulls.get("spread", 1.0) > 0.8,
                        error=lines_error if len(lines) == 0 else None,
                    ),
                }

                # Season-grain endpoints (api_calls tracked)
                for name, table, fetch, norm, cols, expected in (
                    (
                        "roster",
                        "rosters",
                        client.fetch_roster,
                        normalize_roster_payload,
                        ["athlete_id", "position"],
                        True,
                    ),
                    (
                        "returning",
                        "returning_production",
                        client.fetch_returning,
                        normalize_returning_payload,
                        ["offense_pct", "defense_pct", "overall_pct"],
                        True,
                    ),
                    (
                        "talent",
                        "talent",
                        client.fetch_talent,
                        normalize_talent_payload,
                        ["talent"],
                        season >= 2015,  # DESIGN: talent 2015+
                    ),
                    (
                        "recruiting",
                        "recruiting",
                        client.fetch_recruiting,
                        normalize_recruiting_payload,
                        ["rank", "points", "blue_chip_ratio"],
                        True,
                    ),
                ):
                    try:
                        raw = fetch(season)
                        api_calls += 1
                        frame = norm(
                            raw,
                            season=season,
                            ingested_at=now,
                            school_to_id=school_to_id,
                            team_map=team_map,
                        )
                        ok, err = _validate(table, frame)
                        nulls = _null_rates(frame, cols)
                        cells[name] = {
                            "rows": int(len(frame)),
                            "schema_ok": ok,
                            "schema_error": err,
                            "null_rates": nulls,
                            "flag": _flag(
                                rows=len(frame),
                                schema_ok=ok,
                                expected_rows=expected,
                                null_anomaly=(
                                    any(nulls.get(c, 0.0) > NULL_ANOMALY for c in cols)
                                    if expected and len(frame) > 0
                                    else False
                                ),
                            ),
                        }
                        if name == "returning":
                            era["returning_null_rates"][str(season)] = nulls
                            # Negatives present?
                            negs = {}
                            for c in ("offense_pct", "defense_pct", "overall_pct"):
                                if c in frame.columns and not frame.empty:
                                    negs[c] = int((frame[c] < 0).sum())
                            cells[name]["negative_counts"] = negs
                        if name == "roster":
                            era["roster"][str(season)] = {
                                "rows": int(len(frame)),
                                "schema_ok": ok,
                                "negative_athlete_ids": int((frame["athlete_id"] < 0).sum())
                                if not frame.empty and "athlete_id" in frame.columns
                                else 0,
                            }
                    except Exception as exc:  # noqa: BLE001
                        cells[name] = {
                            "rows": 0,
                            "schema_ok": False,
                            "schema_error": f"{type(exc).__name__}: {exc}",
                            "flag": "MISSING" if expected else "GO",
                        }

                # Portal
                try:
                    raw = client.fetch_portal(season)
                    api_calls += 1
                    portal = normalize_portal_payload(
                        raw,
                        season=season,
                        ingested_at=now,
                        school_to_id=school_to_id,
                        team_map=team_map,
                    )
                    ok, err = _validate("portal", portal)
                    expected_portal = is_portal_era(season)
                    cells["portal"] = {
                        "rows": int(len(portal)),
                        "schema_ok": ok,
                        "schema_error": err,
                        "portal_era": expected_portal,
                        "flag": _flag(
                            rows=len(portal),
                            schema_ok=ok,
                            expected_rows=expected_portal,
                            null_anomaly=False,
                        ),
                    }
                    # portal_net_rating probe
                    as_of = datetime(season, 12, 31, tzinfo=UTC)
                    if not expected_portal:
                        net = portal_net_rating(
                            portal, team_id=1, season=season, as_of=as_of
                        )
                        era.setdefault("portal", {})[str(season)] = {
                            "era": False,
                            "rows": int(len(portal)),
                            "portal_net_sample": net,
                            "is_nan": bool(math.isnan(net)),
                            "never_zero_pre_2021": bool(math.isnan(net)),
                        }
                    else:
                        nets: list[float] = []
                        team_ids = (
                            set(portal["dest_team_id"].dropna().astype(int))
                            | set(portal["origin_team_id"].dropna().astype(int))
                            if not portal.empty
                            else set()
                        )
                        for tid in list(team_ids)[:40]:
                            nets.append(
                                portal_net_rating(
                                    portal, team_id=int(tid), season=season, as_of=as_of
                                )
                            )
                        finite = [n for n in nets if not math.isnan(n)]
                        era.setdefault("portal", {})[str(season)] = {
                            "era": True,
                            "rows": int(len(portal)),
                            "n_teams_sampled": len(nets),
                            "n_finite_nets": len(finite),
                            "n_negative_nets": sum(1 for n in finite if n < 0),
                            "min_net": min(finite) if finite else None,
                            "max_net": max(finite) if finite else None,
                            "negatives_accepted": True,
                        }
                except Exception as exc:  # noqa: BLE001
                    cells["portal"] = {
                        "rows": 0,
                        "schema_ok": False,
                        "schema_error": f"{type(exc).__name__}: {exc}",
                        "flag": "MISSING",
                    }

                # Coaches
                try:
                    raw = client.fetch_coaches(season)
                    api_calls += 1
                    coaches = normalize_coaches_payload(
                        raw,
                        season=season,
                        ingested_at=now,
                        school_to_id=school_to_id,
                        team_map=team_map,
                    )
                    ok, err = _validate("coaches", coaches)
                    cells["coaches"] = {
                        "rows": int(len(coaches)),
                        "schema_ok": ok,
                        "schema_error": err,
                        "flag": _flag(
                            rows=len(coaches),
                            schema_ok=ok,
                            expected_rows=True,
                            null_anomaly=False,
                        ),
                    }
                except Exception as exc:  # noqa: BLE001
                    cells["coaches"] = {
                        "rows": 0,
                        "schema_ok": False,
                        "schema_error": f"{type(exc).__name__}: {exc}",
                        "flag": "MISSING",
                    }

                # Persist games venue ids for Part D
                if not games.empty and "venue_id" in games.columns:
                    vpath = PROBE_DIR / f"games_venue_ids_{season}.json"
                    vids = sorted(
                        {int(v) for v in games["venue_id"].dropna().astype(int).tolist()}
                    )
                    vpath.write_text(json.dumps(vids), encoding="utf-8")

            except RateLimitBudgetError as exc:
                cells["_abort"] = str(exc)
                matrix[str(season)] = cells
                print(f"CFBD budget abort: {exc}", flush=True)
                break
            except Exception as exc:  # noqa: BLE001
                cells["_error"] = f"{type(exc).__name__}: {exc}"
                cells["_trace"] = traceback.format_exc()
                print(f"season {season} error: {exc}", flush=True)

            matrix[str(season)] = cells
            _save("cfbd_matrix_partial.json", {"matrix": matrix, "api_calls": api_calls})
            print(
                f"  season {season} done; remaining={client.remaining_requests}",
                flush=True,
            )

        auth["cfbd_remaining_end"] = client.remaining_requests

    # First complete EPA/WP / advanced seasons
    def _first_complete(by_season: dict[str, Any], rate_key: str) -> int | None:
        for s in SEASONS:
            rec = by_season.get(str(s), {})
            if rec.get(rate_key, 0.0) >= COMPLETE_COVERAGE and rec.get(
                "n_plays", rec.get("n_rows", 0)
            ) > 0:
                return s
        return None

    era["first_complete"] = {
        "coverage_threshold": COMPLETE_COVERAGE,
        "play_epa": _first_complete(era["epa_wp_by_season"], "epa_nonnull_rate"),
        "play_wp": _first_complete(era["epa_wp_by_season"], "wp_nonnull_rate"),
        "advanced_offense_epa": _first_complete(
            era["advanced_epa_by_season"], "offense_epa_nonnull_rate"
        ),
        "advanced_defense_epa": _first_complete(
            era["advanced_epa_by_season"], "defense_epa_nonnull_rate"
        ),
    }

    # GT source vs staging summary
    gt_ok_seasons = []
    for s, rec in era["gt_raw_by_season"].items():
        wp_ok = rec.get("wp_nonnull_rate", 0) >= 0.5
        period_ok = rec.get("period_nonnull_rate", 0) >= 0.5
        score_ok = rec.get("score_nonnull_rate", 0) >= 0.5
        if wp_ok and period_ok:
            gt_ok_seasons.append(
                {
                    "season": s,
                    "wp_ok": wp_ok,
                    "period_ok": period_ok,
                    "score_ok": score_ok,
                    "wp_keys": rec.get("wp_keys_present"),
                    "score_keys": rec.get("score_keys_present"),
                }
            )
    era["gt_conclusion"] = {
        "seasons_with_wp_and_period_at_source": gt_ok_seasons,
        "staging_gap_note": (
            "Staged PlaysSchema keeps wp/period/epa but drops clock and score "
            "columns. Task 8 Connelly fallback needs score_margin from raw "
            "(plays_from_cfbd_raw_json). A5 NOT RUN was a staging/feature-flag "
            "gap (no garbage_time column on staged plays), not necessarily a "
            "CFBD source gap — see gt_raw_by_season."
        ),
    }

    result = {
        "auth": auth,
        "matrix": matrix,
        "era": era,
        "api_calls": api_calls,
        "complete_coverage_threshold": COMPLETE_COVERAGE,
    }
    _save("cfbd_matrix.json", result)
    return result


def probe_odds() -> dict[str, Any]:
    secrets = load_secrets()
    cfg = load_config()
    key = secrets.odds_api_key.get_secret_value()
    spend = 0
    out: dict[str, Any] = {
        "odds_key_present": bool(key),
        "spend_cap": ODDS_SPEND_CAP,
        "credits_spent_this_task": 0,
        "quota": {},
        "historical": {},
        "ladder": {},
        "live_2026": {},
        "reconciliation": {},
    }
    if not key:
        out["error"] = "ODDS_API_KEY empty"
        _save("odds_probe.json", out)
        return out

    # 10. Quota FIRST via sports list (not on OddsAPIClient — raw httpx, no client edit).
    print("Odds: sports list quota probe", flush=True)
    with httpx.Client(base_url=ODDS_BASE_URL, timeout=30.0) as http:
        resp = http.get("/sports", params={"apiKey": key})
        remaining = resp.headers.get("x-requests-remaining")
        used = resp.headers.get("x-requests-used")
        last = resp.headers.get("x-requests-last")
        # Sports list is typically free; if last is present, count it.
        last_i = int(last) if last is not None and str(last).isdigit() else 0
        spend += last_i
        out["quota"] = {
            "endpoint": "GET /v4/sports",
            "status_code": resp.status_code,
            "x_requests_remaining": int(remaining) if remaining and remaining.isdigit() else remaining,
            "x_requests_used": int(used) if used and str(used).isdigit() else used,
            "x_requests_last": last_i,
            "client_note": (
                "OddsAPIClient has no sports-list helper; probe used raw httpx "
                "without editing the client."
            ),
        }
        if resp.status_code >= 400:
            out["error"] = f"sports list failed: {resp.status_code}"
            out["credits_spent_this_task"] = spend
            _save("odds_probe.json", out)
            return out

    remaining_i = out["quota"]["x_requests_remaining"]
    used_i = out["quota"]["x_requests_used"]
    out["reconciliation"] = {
        "design_monthly_quota": 20000,
        "design_source": "DESIGN.md §3.2 / TASKS.md (docs only, not in config)",
        "config_historical_ceiling": cfg.data.odds_historical_credit_ceiling,
        "config_source": "configs/data.yaml odds_historical_credit_ceiling",
        "notes_23_claimed_used": 24,
        "live_x_requests_used": used_i,
        "live_x_requests_remaining": remaining_i,
        "wrong_number_analysis": (
            "16,000 is the historical spend CEILING in configs/data.yaml "
            "(odds_historical_credit_ceiling), not the monthly plan quota. "
            "20,000 is the DESIGN/TASKS monthly plan figure and is not stored "
            "in config — spend guards therefore enforce 16k, not 20k. "
            "Task 23 notes conflated ceiling with quota when saying "
            "'16,000 ceiling against an actual budget of 20,000'."
        ),
        "used_24_status": (
            "matches live"
            if used_i == 24
            else f"STALE — notes said 24 used; live x-requests-used={used_i}"
        ),
    }

    # Abort if historical call would breach task cap.
    projected_unit = cfg.data.odds_historical_credits_per_call
    if spend + projected_unit > ODDS_SPEND_CAP:
        out["historical"]["aborted"] = (
            f"projected spend {spend + projected_unit} exceeds task cap {ODDS_SPEND_CAP}"
        )
        out["credits_spent_this_task"] = spend
        _save("odds_probe.json", out)
        return out

    # Also respect live reserve: remaining - unit >= reserve
    if isinstance(remaining_i, int) and remaining_i - projected_unit < cfg.data.odds_rate_limit_reserve:
        out["historical"]["aborted"] = (
            f"remaining {remaining_i} - {projected_unit} < live reserve "
            f"{cfg.data.odds_rate_limit_reserve}"
        )
        out["credits_spent_this_task"] = spend
        _save("odds_probe.json", out)
        return out

    # 11–14. ONE historical call
    print("Odds: ONE historical snapshot", flush=True)
    probe_ts = datetime(2024, 10, 12, 16, 0, 0, tzinfo=UTC)
    hist: dict[str, Any] = {"requested_at": probe_ts.isoformat()}
    try:
        with OddsAPIClient(
            key,
            books=cfg.data.odds_books,
            markets=cfg.data.odds_markets,
            regions=cfg.data.odds_regions,
            rate_limit_reserve=cfg.data.odds_rate_limit_reserve,
            budget_kind="historical",
            historical_credit_ceiling=cfg.data.odds_historical_credit_ceiling,
            credits_per_historical_call=cfg.data.odds_historical_credits_per_call,
            force_ceiling=True,  # single probe; ceiling is for bulk backfill
        ) as client:
            # Seed remaining from sports-list so budget guard works.
            client._remaining = remaining_i if isinstance(remaining_i, int) else None  # noqa: SLF001
            before_used = used_i if isinstance(used_i, int) else None
            resp = client.fetch_historical_odds(probe_ts)
            unit = client.last_requests_last
            if unit is None:
                unit = projected_unit
            spend += int(unit)
            hist.update(
                {
                    "status": "ok",
                    "measured_unit_cost_x_requests_last": unit,
                    "credits_spent_client": client.credits_spent,
                    "remaining_after": client.remaining_requests,
                    "envelope_timestamp": resp.timestamp.isoformat(),
                    "previous_timestamp": (
                        resp.previous_timestamp.isoformat()
                        if resp.previous_timestamp
                        else None
                    ),
                    "next_timestamp": (
                        resp.next_timestamp.isoformat() if resp.next_timestamp else None
                    ),
                    "n_events": len(resp.data),
                    "timestamp_distinct_from_request": resp.timestamp != probe_ts,
                }
            )
            # Payload suitability for bet layer
            books: set[str] = set()
            sides_ok = 0
            events_with_books = 0
            for event in resp.data[:20]:
                if not isinstance(event, dict):
                    continue
                bms = event.get("bookmakers") or []
                if bms:
                    events_with_books += 1
                for bm in bms:
                    if not isinstance(bm, dict):
                        continue
                    books.add(str(bm.get("key") or bm.get("title") or "?"))
                    for market in bm.get("markets") or []:
                        if not isinstance(market, dict):
                            continue
                        outcomes = market.get("outcomes") or []
                        if len(outcomes) >= 2:
                            prices = [
                                o.get("price")
                                for o in outcomes
                                if isinstance(o, dict) and o.get("price") is not None
                            ]
                            if len(prices) >= 2:
                                sides_ok += 1
            hist["payload_check"] = {
                "books_seen": sorted(books),
                "n_books": len(books),
                "events_with_books_in_sample": events_with_books,
                "markets_with_both_side_prices": sides_ok,
                "suitable_for_bet_time_vs_close": bool(
                    resp.timestamp
                    and books
                    and sides_ok > 0
                    and resp.previous_timestamp is not None
                ),
                "note": (
                    "Historical envelope carries snapshot timestamp + prev/next "
                    "navigation; outcomes include both sides' American prices. "
                    "This is a point-in-time board, not CFBD close-only."
                ),
            }
            if before_used is not None and client.remaining_requests is not None:
                # used delta ≈ unit cost
                hist["used_delta_approx"] = unit
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        spend_last = exc.response.headers.get("x-requests-last")
        if spend_last and str(spend_last).isdigit():
            spend += int(spend_last)
        hist["status"] = "http_error"
        hist["status_code"] = code
        hist["body_preview"] = exc.response.text[:500]
        if code in (401, 403):
            hist["task_5b_answer"] = (
                "Historical endpoint not available on this plan — Task 5B blocked."
            )
        out["historical"] = hist
        out["credits_spent_this_task"] = spend
        _save("odds_probe.json", out)
        return out
    except (OddsAPIError, Exception) as exc:  # noqa: BLE001
        hist["status"] = "error"
        hist["error"] = f"{type(exc).__name__}: {exc}"
        out["historical"] = hist
        out["credits_spent_this_task"] = spend
        _save("odds_probe.json", out)
        return out

    out["historical"] = hist
    measured = int(hist.get("measured_unit_cost_x_requests_last") or projected_unit)

    # 13. Recompute ladder from measured unit cost (no further network for odds).
    store = ParquetStore(cfg.paths.staged_dir)
    ladder: dict[str, Any] = {"measured_unit_cost": measured, "scopes": []}

    def _scope(
        label: str,
        seasons: Sequence[int],
        decision_points: Sequence[str] | None,
    ) -> dict[str, Any]:
        plan = plan_historical_units(
            store, seasons, decision_points=decision_points, config=cfg
        )
        recomputed = plan.total_requests * measured
        config_est = plan.total_requests * cfg.data.odds_historical_credits_per_call
        return {
            "label": label,
            "seasons": list(seasons),
            "decision_points": list(
                decision_points or cfg.data.odds_historical_decision_points
            ),
            "total_requests": plan.total_requests,
            "credits_at_config_30": config_est,
            "credits_at_measured": recomputed,
            "book_filter_note": (
                "Estimator meters 10×markets×regions; book count does NOT "
                "reduce credits. 'One book' scopes are identical cost to all books."
            ),
            "fits_ceiling_16k": recomputed <= cfg.data.odds_historical_credit_ceiling,
            "fits_monthly_20k": recomputed <= 20000,
        }

    baseline = _scope(
        "2021-2025 all DPs (baseline)",
        list(range(2021, 2026)),
        None,
    )
    ladder["scopes"].append(baseline)
    ladder["baseline_54090_check"] = {
        "notes_claim_requests": 1803,
        "notes_claim_credits": 54090,
        "actual_requests": baseline["total_requests"],
        "actual_at_30": baseline["credits_at_config_30"],
        "actual_at_measured": baseline["credits_at_measured"],
        "agrees_with_54090": baseline["credits_at_config_30"] == 54090,
        "measured_vs_estimator": (
            "agree"
            if measured == cfg.data.odds_historical_credits_per_call
            else f"DISAGREE measured={measured} config={cfg.data.odds_historical_credits_per_call}"
        ),
    }
    ladder["scopes"].append(
        _scope("one season (2024) slot_close only (~1 DP/game)", [2024], ["slot_close"])
    )
    ladder["scopes"].append(
        _scope(
            "two seasons (2024-2025) slot_close only",
            [2024, 2025],
            ["slot_close"],
        )
    )
    ladder["scopes"].append(
        _scope(
            "2024-2025 tuesday_0600_et + slot_close",
            [2024, 2025],
            ["tuesday_0600_et", "slot_close"],
        )
    )
    # Also print estimator lines for baseline
    _plan, lines = estimate_historical_credits(
        store, list(range(2021, 2026)), config=cfg, remaining_quota=remaining_i
        if isinstance(remaining_i, int)
        else None
    )
    ladder["estimator_lines_baseline"] = lines
    out["ladder"] = ladder

    # 15. Live 2026 from staged only
    try:
        live = store.read("odds_snapshots", filters={"season": 2026})
        if live.empty:
            out["live_2026"] = {"rows": 0, "note": "no staged 2026 snapshots"}
        else:
            caps = pd.to_datetime(live["captured_at"], utc=True)
            out["live_2026"] = {
                "rows": int(len(live)),
                "captured_at_min": str(caps.min()),
                "captured_at_max": str(caps.max()),
                "snapshot_sources": live["snapshot_source"].value_counts().to_dict()
                if "snapshot_source" in live.columns
                else {},
                "n_unique_capture_minutes": int(caps.dt.floor("min").nunique()),
            }
    except Exception as exc:  # noqa: BLE001
        out["live_2026"] = {"error": f"{type(exc).__name__}: {exc}"}

    out["credits_spent_this_task"] = spend
    _save("odds_probe.json", out)
    # Also dump a small raw sample of historical for inspection
    if hist.get("status") == "ok":
        _save(
            "odds_historical_sample.json",
            {
                "envelope_timestamp": hist.get("envelope_timestamp"),
                "n_events": hist.get("n_events"),
                "payload_check": hist.get("payload_check"),
            },
        )
    return out


def probe_weather_and_venues(cfbd: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"open_meteo": {}, "venues": {}}
    # Open-Meteo archive for a known outdoor stadium (Michigan Stadium approx)
    lat, lon = 42.2658, -83.7487
    print("Open-Meteo archive probe 2014-09-06", flush=True)
    try:
        with OpenMeteoClient(requests_per_second=2.0) as client:
            body = client.fetch_archive(
                latitude=lat,
                longitude=lon,
                local_date="2014-09-06",
                timezone_name="America/Detroit",
            )
            data = json.loads(body)
            hourly = data.get("hourly") or {}
            times = hourly.get("time") or []
            out["open_meteo"] = {
                "reachable": True,
                "url": "https://archive-api.open-meteo.com/v1/archive",
                "probe_date": "2014-09-06",
                "lat": lat,
                "lon": lon,
                "n_hourly_points": len(times),
                "has_temp": bool(hourly.get("temperature_2m")),
                "has_wind": bool(hourly.get("wind_speed_10m")),
                "has_precip": bool(hourly.get("precipitation")),
                "coverage_back_to_2014": len(times) > 0,
            }
    except Exception as exc:  # noqa: BLE001
        out["open_meteo"] = {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}

    # Venue coord coverage vs probed games
    venues_path = PROBE_DIR / "venues_probe.parquet"
    missing: list[dict[str, Any]] = []
    covered_seasons: dict[str, Any] = {}
    venues_df = pd.read_parquet(venues_path) if venues_path.exists() else pd.DataFrame()
    coord_by_id: dict[int, tuple[Any, Any]] = {}
    if not venues_df.empty:
        for row in venues_df.itertuples(index=False):
            coord_by_id[int(row.venue_id)] = (row.latitude, row.longitude)

    # Also check staged venues + overrides path existence
    cfg = load_config()
    staged = ParquetStore(cfg.paths.staged_dir)
    staged_venue_seasons = []
    for season in SEASONS:
        try:
            sv = staged.read("venues", filters={"season": season})
            if not sv.empty:
                staged_venue_seasons.append(season)
                for row in sv.itertuples(index=False):
                    vid = int(row.venue_id)
                    if vid not in coord_by_id:
                        coord_by_id[vid] = (row.latitude, row.longitude)
        except Exception:  # noqa: BLE001
            pass

    for season in SEASONS:
        vipath = PROBE_DIR / f"games_venue_ids_{season}.json"
        if not vipath.exists():
            covered_seasons[str(season)] = {"status": "no_games_venue_list"}
            continue
        vids = json.loads(vipath.read_text(encoding="utf-8"))
        miss = []
        for vid in vids:
            lat_v, lon_v = coord_by_id.get(int(vid), (None, None))
            if lat_v is None or lon_v is None or (isinstance(lat_v, float) and math.isnan(lat_v)):
                miss.append(int(vid))
                missing.append({"season": season, "venue_id": int(vid)})
        covered_seasons[str(season)] = {
            "n_venues_in_games": len(vids),
            "n_missing_coords": len(miss),
            "missing_venue_ids": miss[:30],
        }

    out["venues"] = {
        "overrides_path": cfg.data.venues_overrides_path,
        "staged_venue_seasons_present": staged_venue_seasons,
        "probe_venues_rows": int(len(venues_df)),
        "by_season": covered_seasons,
        "missing_coord_pairs": missing[:50],
        "n_missing_total": len(missing),
    }
    _save("weather_venues_probe.json", out)
    return out


def estimate_wall_clock(cfbd: dict[str, Any] | None) -> dict[str, Any]:
    """Estimate full 2014-2025 CFBD backfill wall clock at configured 2 QPS."""
    cfg = load_config()
    rps = cfg.data.cfbd_requests_per_second
    seasons = list(range(2014, 2026))
    # Approximate weeks: regular 0-15 + a few postseason ≈ 18 week-slots
    weeks_approx = 18
    season_grain = [
        n
        for n, s in ENDPOINT_SPECS.items()
        if s["grain"] == "season" and n != "venues"
    ]
    week_grain = [n for n, s in ENDPOINT_SPECS.items() if s["grain"] == "season_week"]
    # venues once; teams once per season; games often 2 season-types without week
    # Backfill actually loops weeks for games too — match ENDPOINT_SPECS usage.
    calls_per_season_week = len(week_grain)  # incl games, plays, drives, advanced, lines, games_teams
    # From run_cfbd_backfill pattern: week endpoints × weeks × season_types roughly
    # Conservative: regular weeks ~16 + postseason ~4
    calls_week = len(seasons) * weeks_approx * calls_per_season_week
    calls_season = len(seasons) * len(season_grain)
    calls_venues = 1
    total_calls = calls_week + calls_season + calls_venues
    seconds = total_calls / rps if rps > 0 else float("inf")
    # +20% retry/headroom
    seconds_headed = seconds * 1.2
    observed = {
        "probe_api_calls": (cfbd or {}).get("api_calls"),
        "probe_seasons": list(SEASONS),
    }
    out = {
        "cfbd_requests_per_second": rps,
        "rate_limit_reserve": 10,
        "seasons": seasons,
        "week_slots_assumed": weeks_approx,
        "week_grain_endpoints": week_grain,
        "season_grain_endpoints": season_grain,
        "estimated_get_calls": total_calls,
        "seconds_at_rps": round(seconds, 1),
        "hours_at_rps": round(seconds / 3600, 2),
        "hours_with_20pct_headroom": round(seconds_headed / 3600, 2),
        "observed_probe": observed,
        "retry_tests": {
            "file": "tests/unit/test_cfbd.py",
            "retry_recovery": "test_retry_on_500_then_success",
            "idempotency": "test_resumability_skips_completed_partition",
            "budget_guard": "test_rate_limit_budget_guard",
            "finding": (
                "429 is retryable in CFBDClient._is_retryable but there is no "
                "dedicated unit test for 429→success recovery (only 500)."
            ),
        },
    }
    _save("wall_clock_estimate.json", out)
    return out


def main() -> None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    print("Part A/B: CFBD probe", flush=True)
    cfbd = probe_cfbd()
    print("Part C: Odds probe", flush=True)
    odds = probe_odds()
    print("Part D: Weather/venues", flush=True)
    weather = probe_weather_and_venues(cfbd)
    print("Part E: Wall clock", flush=True)
    wall = estimate_wall_clock(cfbd)
    summary = {
        "finished_at": _now().isoformat(),
        "cfbd_api_calls": cfbd.get("api_calls"),
        "odds_credits_spent": odds.get("credits_spent_this_task"),
        "odds_unit_cost": (odds.get("historical") or {}).get(
            "measured_unit_cost_x_requests_last"
        ),
        "first_complete": (cfbd.get("era") or {}).get("first_complete"),
    }
    _save("probe_summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
