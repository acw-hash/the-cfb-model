"""Summarize + resume CFBD probe for remaining seasons (lean week sample)."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.data.schemas import validate_table
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.features.builders.roster import is_portal_era, portal_net_rating
from ncaa_quant.ingestion.cfbd import (
    CFBDClient,
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
)
from ncaa_quant.ingestion.teams import load_team_name_map

PROBE = Path("data/_probe")
COMPLETE_COVERAGE = 0.90
NULL_ANOMALY = 0.50
# Lean: sample these weeks only for plays/advanced/lines (+ first available).
SAMPLE_WEEKS = (1, 5, 10)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _null_rates(df: pd.DataFrame, cols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in cols:
        if df.empty or c not in df.columns:
            out[c] = 1.0
        else:
            out[c] = float(df[c].isna().mean())
    return out


def _validate(table: str, df: pd.DataFrame) -> tuple[bool, str | None]:
    if df.empty:
        return True, None
    try:
        validate_table(table, df)
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


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
        return "GO" if schema_ok else "DEGRADED"
    if rows == 0:
        return "MISSING"
    if not schema_ok or null_anomaly:
        return "DEGRADED"
    return "GO"


def _inspect_raw_plays_gt(raw: bytes) -> dict:
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
    out: dict = {
        "n_raw": n,
        "sample_keys": sorted(keys)[:40],
        "period_nonnull_rate": sum(
            1 for i in data if isinstance(i, dict) and i.get("period") is not None
        )
        / n,
    }
    for label, candidates in (("wp", wp_keys), ("score", score_keys)):
        present = [k for k in candidates if k in keys]
        nonempty = sum(
            1
            for i in data
            if isinstance(i, dict) and any(i.get(k) is not None for k in present)
        )
        out[f"{label}_keys_present"] = present
        out[f"{label}_nonnull_rate"] = nonempty / n
    return out


def summarize() -> None:
    d = json.loads((PROBE / "cfbd_matrix.json").read_text(encoding="utf-8"))
    flags = {
        s: {k: v.get("flag") for k, v in cells.items() if isinstance(v, dict) and "flag" in v}
        for s, cells in d.get("matrix", {}).items()
    }
    rows = {
        s: {k: v.get("rows") for k, v in cells.items() if isinstance(v, dict) and "rows" in v}
        for s, cells in d.get("matrix", {}).items()
    }
    nulls = {
        s: {
            k: v.get("null_rates")
            for k, v in cells.items()
            if isinstance(v, dict) and v.get("null_rates")
        }
        for s, cells in d.get("matrix", {}).items()
    }
    summary = {
        "auth": d.get("auth"),
        "api_calls": d.get("api_calls"),
        "flags": flags,
        "rows": rows,
        "nulls": nulls,
        "era_first": d.get("era", {}).get("first_complete"),
        "epa_wp": d.get("era", {}).get("epa_wp_by_season"),
        "adv": d.get("era", {}).get("advanced_epa_by_season"),
        "gt": {
            k: {kk: vv for kk, vv in v.items() if kk != "sample_keys"}
            for k, v in d.get("era", {}).get("gt_raw_by_season", {}).items()
        },
        "portal": d.get("era", {}).get("portal"),
        "returning": d.get("era", {}).get("returning_null_rates"),
        "roster": d.get("era", {}).get("roster"),
        "s2020": d.get("era", {}).get("season_2020"),
    }
    (PROBE / "_summary_cfbd.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2)[:12000])


def staged_week_counts(store: ParquetStore, season: int, table: str) -> int:
    try:
        df = store.read(table, filters={"season": season})
        return int(len(df))
    except Exception:  # noqa: BLE001
        return -1


def resume_lean(seasons: list[int]) -> None:
    secrets = load_secrets()
    cfg = load_config()
    key = secrets.cfbd_api_key.get_secret_value()
    team_map = load_team_name_map(cfg.data.team_names_path)
    store = ParquetStore(cfg.paths.staged_dir)
    path = PROBE / "cfbd_matrix.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    matrix = data.setdefault("matrix", {})
    era = data.setdefault("era", {})
    api_calls = int(data.get("api_calls") or 0)
    now = _now()

    # Venues cell from prior
    venues_cell = None
    for s, cells in matrix.items():
        if isinstance(cells.get("venues"), dict) and cells["venues"].get("rows"):
            venues_cell = cells["venues"]
            break

    with CFBDClient(key, requests_per_second=cfg.data.cfbd_requests_per_second) as client:
        # Probe remaining with a tiny call
        try:
            client.fetch_teams(2023)
            api_calls += 1
            print(f"CFBD remaining at resume: {client.remaining_requests}", flush=True)
        except RateLimitBudgetError as exc:
            print(f"still budget-blocked: {exc}", flush=True)
            data["resume_blocked"] = str(exc)
            path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            return

        for season in seasons:
            print(f"=== RESUME lean season {season} ===", flush=True)
            cells: dict = {
                "venues": {**(venues_cell or {}), "season_note": "static /venues"},
                "probe_mode": "lean_sample_weeks",
            }
            try:
                tbody = client.fetch_teams(season)
                api_calls += 1
                teams = normalize_teams_payload(
                    tbody, season=season, ingested_at=now, team_map=team_map
                )
                school_to_id = {
                    str(r.school): int(r.team_id) for r in teams.itertuples(index=False)
                }

                game_frames = []
                for stype in ("regular", "postseason"):
                    gbody = client.fetch_games(season, season_type=stype, classification="fbs")
                    api_calls += 1
                    game_frames.append(normalize_games_payload(gbody, ingested_at=now))
                games = pd.concat(game_frames, ignore_index=True).drop_duplicates(
                    subset=["game_id"], keep="last"
                )
                gok, gerr = _validate("games", games)
                weeks = sorted(int(w) for w in games["week"].dropna().unique())
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
                cells["games"] = {
                    "rows": n_games,
                    "schema_ok": gok,
                    "schema_error": gerr,
                    "completed": int(games["completed"].sum()),
                    "weeks": weeks,
                    "conference_game_rate": float(games["conference_game"].mean()),
                    "count_band": band,
                    "null_rates": _null_rates(
                        games, ["home_points", "away_points", "venue_id", "completed"]
                    ),
                    "flag": _flag(
                        rows=n_games, schema_ok=gok, expected_rows=True, null_anomaly=False
                    ),
                }
                if season == 2020:
                    era["season_2020"] = {
                        "game_count": n_games,
                        "completed": cells["games"]["completed"],
                        "weeks": weeks,
                        "week_span": [min(weeks), max(weeks)] if weeks else None,
                        "conference_game_rate": cells["games"]["conference_game_rate"],
                        "missing_weeks_in_0_15": [w for w in range(0, 16) if w not in set(weeks)],
                    }

                # Persist venue ids
                vids = sorted(
                    {int(v) for v in games["venue_id"].dropna().astype(int).tolist()}
                )
                (PROBE / f"games_venue_ids_{season}.json").write_text(
                    json.dumps(vids), encoding="utf-8"
                )

                game_start_by_id = {}
                for row in games.itertuples(index=False):
                    ts = pd.Timestamp(row.start_date)
                    if ts.tzinfo is None:
                        ts = ts.tz_localize("UTC")
                    game_start_by_id[int(row.game_id)] = ts.to_pydatetime()

                # Sample weeks that exist
                sample = [w for w in SAMPLE_WEEKS if w in set(weeks)]
                if not sample and weeks:
                    sample = [weeks[len(weeks) // 2]]

                play_frames, adv_frames, line_frames = [], [], []
                gt_sample = None
                for week in sample:
                    stype = "regular"
                    pbody = client.fetch_plays(
                        season, week, season_type=stype, classification="fbs"
                    )
                    api_calls += 1
                    if gt_sample is None and pbody and pbody != b"[]":
                        gt_sample = _inspect_raw_plays_gt(pbody)
                        gt_sample["sample_week"] = week
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

                plays = pd.concat(play_frames, ignore_index=True) if play_frames else pd.DataFrame()
                advanced = (
                    pd.concat(adv_frames, ignore_index=True) if adv_frames else pd.DataFrame()
                )
                lines = pd.concat(line_frames, ignore_index=True) if line_frames else pd.DataFrame()

                pok, perr = _validate("plays", plays)
                play_nulls = _null_rates(plays, ["epa", "wp", "period", "success"])
                staged_plays = staged_week_counts(store, season, "plays")
                cells["plays"] = {
                    "rows_sample": int(len(plays)),
                    "rows": staged_plays if staged_plays >= 0 else int(len(plays)),
                    "rows_source": "staged" if staged_plays >= 0 else "live_sample",
                    "sample_weeks": sample,
                    "schema_ok": pok,
                    "schema_error": perr,
                    "null_rates": play_nulls,
                    "null_rates_basis": "live_sample_weeks",
                    "flag": _flag(
                        rows=len(plays) if len(plays) else staged_plays,
                        schema_ok=pok,
                        expected_rows=True,
                        null_anomaly=play_nulls.get("epa", 1) > NULL_ANOMALY
                        or play_nulls.get("wp", 1) > NULL_ANOMALY,
                    ),
                }
                era.setdefault("epa_wp_by_season", {})[str(season)] = {
                    "epa_nonnull_rate": 1.0 - play_nulls.get("epa", 1.0),
                    "wp_nonnull_rate": 1.0 - play_nulls.get("wp", 1.0),
                    "n_plays": int(len(plays)),
                    "basis": "lean_sample",
                }
                if gt_sample:
                    era.setdefault("gt_raw_by_season", {})[str(season)] = gt_sample

                aok, aerr = _validate("advanced_box", advanced)
                adv_nulls = _null_rates(
                    advanced, ["offense_epa", "defense_epa", "success_rate", "explosiveness"]
                )
                staged_adv = staged_week_counts(store, season, "advanced_box")
                cells["advanced"] = {
                    "rows_sample": int(len(advanced)),
                    "rows": staged_adv if staged_adv >= 0 else int(len(advanced)),
                    "rows_source": "staged" if staged_adv >= 0 else "live_sample",
                    "sample_weeks": sample,
                    "schema_ok": aok,
                    "schema_error": aerr,
                    "null_rates": adv_nulls,
                    "flag": _flag(
                        rows=len(advanced) if len(advanced) else staged_adv,
                        schema_ok=aok,
                        expected_rows=True,
                        null_anomaly=adv_nulls.get("offense_epa", 1) > NULL_ANOMALY,
                    ),
                }
                era.setdefault("advanced_epa_by_season", {})[str(season)] = {
                    "offense_epa_nonnull_rate": 1.0 - adv_nulls.get("offense_epa", 1.0),
                    "defense_epa_nonnull_rate": 1.0 - adv_nulls.get("defense_epa", 1.0),
                    "n_rows": int(len(advanced)),
                    "basis": "lean_sample",
                }

                lok, lerr = _validate("lines_historical", lines)
                line_nulls = _null_rates(lines, ["spread", "total", "home_ml", "away_ml"])
                staged_lines = staged_week_counts(store, season, "lines_historical")
                cells["lines"] = {
                    "rows_sample": int(len(lines)),
                    "rows": staged_lines if staged_lines >= 0 else int(len(lines)),
                    "rows_source": "staged" if staged_lines >= 0 else "live_sample",
                    "sample_weeks": sample,
                    "schema_ok": lok,
                    "schema_error": lerr,
                    "null_rates": line_nulls,
                    "flag": _flag(
                        rows=len(lines) if len(lines) else staged_lines,
                        schema_ok=lok,
                        expected_rows=True,
                        null_anomaly=line_nulls.get("spread", 1) > 0.8,
                    ),
                }

                # Season grain
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
                        season >= 2015,
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
                    nulls = _null_rates(frame, list(cols))
                    cells[name] = {
                        "rows": int(len(frame)),
                        "schema_ok": ok,
                        "schema_error": err,
                        "null_rates": nulls,
                        "flag": _flag(
                            rows=len(frame),
                            schema_ok=ok,
                            expected_rows=expected,
                            null_anomaly=any(nulls.get(c, 0) > NULL_ANOMALY for c in cols)
                            if expected and len(frame)
                            else False,
                        ),
                    }
                    if name == "returning":
                        era.setdefault("returning_null_rates", {})[str(season)] = nulls
                        negs = {
                            c: int((frame[c] < 0).sum())
                            for c in ("offense_pct", "defense_pct", "overall_pct")
                            if c in frame.columns and not frame.empty
                        }
                        cells[name]["negative_counts"] = negs
                    if name == "roster":
                        era.setdefault("roster", {})[str(season)] = {
                            "rows": int(len(frame)),
                            "schema_ok": ok,
                            "negative_athlete_ids": int((frame["athlete_id"] < 0).sum())
                            if not frame.empty
                            else 0,
                        }

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
                as_of = datetime(season, 12, 31, tzinfo=UTC)
                if not expected_portal:
                    net = portal_net_rating(portal, team_id=1, season=season, as_of=as_of)
                    era.setdefault("portal", {})[str(season)] = {
                        "era": False,
                        "rows": int(len(portal)),
                        "portal_net_sample": net,
                        "is_nan": bool(math.isnan(net)),
                        "never_zero_pre_2021": bool(math.isnan(net)),
                    }
                else:
                    team_ids = set()
                    if not portal.empty:
                        team_ids = set(portal["dest_team_id"].dropna().astype(int)) | set(
                            portal["origin_team_id"].dropna().astype(int)
                        )
                    nets = [
                        portal_net_rating(portal, team_id=int(tid), season=season, as_of=as_of)
                        for tid in list(team_ids)[:40]
                    ]
                    finite = [n for n in nets if not math.isnan(n)]
                    era.setdefault("portal", {})[str(season)] = {
                        "era": True,
                        "rows": int(len(portal)),
                        "n_finite_nets": len(finite),
                        "n_negative_nets": sum(1 for n in finite if n < 0),
                        "min_net": min(finite) if finite else None,
                        "max_net": max(finite) if finite else None,
                    }

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
                        rows=len(coaches), schema_ok=ok, expected_rows=True, null_anomaly=False
                    ),
                }

            except RateLimitBudgetError as exc:
                cells["_abort"] = str(exc)
                matrix[str(season)] = cells
                print(f"budget abort: {exc}", flush=True)
                break

            matrix[str(season)] = cells
            print(f"  done {season}; remaining={client.remaining_requests}", flush=True)

        data["auth"]["cfbd_remaining_end"] = client.remaining_requests

    # Recompute first_complete
    def first_complete(by_season: dict, rate_key: str, n_key: str) -> int | None:
        for s in (2014, 2016, 2019, 2020, 2021, 2023, 2025):
            rec = by_season.get(str(s), {})
            if rec.get(rate_key, 0.0) >= COMPLETE_COVERAGE and rec.get(n_key, 0) > 0:
                return s
        return None

    era["first_complete"] = {
        "coverage_threshold": COMPLETE_COVERAGE,
        "play_epa": first_complete(era.get("epa_wp_by_season", {}), "epa_nonnull_rate", "n_plays"),
        "play_wp": first_complete(era.get("epa_wp_by_season", {}), "wp_nonnull_rate", "n_plays"),
        "advanced_offense_epa": first_complete(
            era.get("advanced_epa_by_season", {}), "offense_epa_nonnull_rate", "n_rows"
        ),
        "advanced_defense_epa": first_complete(
            era.get("advanced_epa_by_season", {}), "defense_epa_nonnull_rate", "n_rows"
        ),
        "note": (
            "Play-level rates from full-season pulls where available (2014/2016/2019); "
            "lean week samples for later seasons after CFBD budget abort."
        ),
    }
    data["api_calls"] = api_calls
    data["era"] = era
    data["matrix"] = matrix
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print("updated", path)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "summarize":
        summarize()
    else:
        # Complete 2020 week-grain if partial, then 2021/2023/2025 lean.
        resume_lean([2020, 2021, 2023, 2025])
        summarize()
