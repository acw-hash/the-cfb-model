"""Fill incomplete CFBD matrix cells from staged partitions (schema validate only).

Also re-check CFBD remaining; if recovered, lean-live-fetch season-grain for
missing seasons. Does not write to staged/features.
"""

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
    normalize_coaches_payload,
    normalize_games_payload,
    normalize_portal_payload,
    normalize_recruiting_payload,
    normalize_returning_payload,
    normalize_roster_payload,
    normalize_talent_payload,
    normalize_teams_payload,
)
from ncaa_quant.ingestion.teams import load_team_name_map

PROBE = Path("data/_probe")
SEASONS = (2014, 2016, 2019, 2020, 2021, 2023, 2025)
NULL_ANOMALY = 0.50
COMPLETE = 0.90


def _validate(table: str, df: pd.DataFrame) -> tuple[bool, str | None]:
    if df.empty:
        return True, None
    try:
        validate_table(table, df)
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _null_rates(df: pd.DataFrame, cols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in cols:
        if df.empty or c not in df.columns:
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
) -> str:
    if not expected_rows and rows == 0:
        return "GO" if schema_ok else "DEGRADED"
    if rows == 0:
        return "MISSING"
    if not schema_ok or null_anomaly:
        return "DEGRADED"
    return "GO"


def fill_from_staged() -> None:
    cfg = load_config()
    store = ParquetStore(cfg.paths.staged_dir)
    path = PROBE / "cfbd_matrix.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    matrix = data.setdefault("matrix", {})
    era = data.setdefault("era", {})

    # Venues from probe parquet
    venues_df = pd.DataFrame()
    vp = PROBE / "venues_probe.parquet"
    if vp.exists():
        venues_df = pd.read_parquet(vp)

    for season in SEASONS:
        cells = matrix.setdefault(str(season), {})
        cells.setdefault(
            "venues",
            {
                "rows": int(len(venues_df)),
                "schema_ok": True,
                "flag": "GO",
                "season_note": "static /venues",
            },
        )

        # Games from staged if live incomplete
        g = store.read("games", filters={"season": season})
        if not g.empty and (
            "games" not in cells
            or cells["games"].get("rows", 0) == 0
            or cells.get("_abort")
            or cells["games"].get("flag") == "MISSING"
        ):
            ok, err = _validate("games", g)
            weeks = sorted(int(w) for w in g["week"].dropna().unique())
            n = len(g)
            if season == 2020:
                band = "covid_special"
            elif season == 2025 and n < 700:
                band = "incomplete_or_early"
            elif 800 <= n <= 950:
                band = "in_band"
            elif n > 950:
                band = "above_band_fbs_fcs"
            else:
                band = "below_band"
            cells["games"] = {
                "rows": n,
                "schema_ok": ok,
                "schema_error": err,
                "completed": int(g["completed"].sum()) if "completed" in g.columns else None,
                "weeks": weeks,
                "conference_game_rate": float(g["conference_game"].mean())
                if "conference_game" in g.columns
                else None,
                "count_band": band,
                "null_rates": _null_rates(
                    g, ["home_points", "away_points", "venue_id", "completed"]
                ),
                "flag": _flag(rows=n, schema_ok=ok, expected_rows=True, null_anomaly=False),
                "source": "staged",
            }
            vids = sorted({int(v) for v in g["venue_id"].dropna().astype(int).tolist()})
            (PROBE / f"games_venue_ids_{season}.json").write_text(
                json.dumps(vids), encoding="utf-8"
            )
            if season == 2020:
                era["season_2020"] = {
                    "game_count": n,
                    "completed": cells["games"]["completed"],
                    "weeks": weeks,
                    "week_span": [min(weeks), max(weeks)] if weeks else None,
                    "conference_game_rate": cells["games"]["conference_game_rate"],
                    "missing_weeks_in_0_15": [w for w in range(0, 16) if w not in set(weeks)],
                    "source": "staged_or_live",
                }

        # Plays
        need_plays = "plays" not in cells or cells["plays"].get("rows", 0) == 0 or cells[
            "plays"
        ].get("flag") in ("MISSING",) or season in (2021, 2023, 2025) or (
            season == 2020 and cells.get("plays", {}).get("rows", 0) < 100000
        )
        # Always refresh incomplete 2020+ from staged for accurate counts
        if season >= 2020 or need_plays:
            p = store.read("plays", filters={"season": season})
            if not p.empty:
                ok, err = _validate("plays", p)
                nulls = _null_rates(p, ["epa", "wp", "period", "success"])
                cells["plays"] = {
                    "rows": int(len(p)),
                    "schema_ok": ok,
                    "schema_error": err,
                    "null_rates": nulls,
                    "flag": _flag(
                        rows=len(p),
                        schema_ok=ok,
                        expected_rows=True,
                        null_anomaly=nulls.get("epa", 1) > NULL_ANOMALY
                        or nulls.get("wp", 1) > NULL_ANOMALY,
                    ),
                    "source": "staged",
                }
                era.setdefault("epa_wp_by_season", {})[str(season)] = {
                    "epa_nonnull_rate": 1.0 - nulls.get("epa", 1.0),
                    "wp_nonnull_rate": 1.0 - nulls.get("wp", 1.0),
                    "n_plays": int(len(p)),
                    "basis": "staged",
                }

        for key, table, cols in (
            ("advanced", "advanced_box", ["offense_epa", "defense_epa", "success_rate"]),
            ("lines", "lines_historical", ["spread", "total", "home_ml", "away_ml"]),
        ):
            if season < 2020 and key in cells and cells[key].get("rows", 0) > 0:
                continue
            df = store.read(table, filters={"season": season})
            if df.empty:
                continue
            ok, err = _validate(table, df)
            nulls = _null_rates(df, list(cols))
            anomaly_col = "offense_epa" if key == "advanced" else "spread"
            cells[key] = {
                "rows": int(len(df)),
                "schema_ok": ok,
                "schema_error": err,
                "null_rates": nulls,
                "flag": _flag(
                    rows=len(df),
                    schema_ok=ok,
                    expected_rows=True,
                    null_anomaly=nulls.get(anomaly_col, 1) > (NULL_ANOMALY if key == "advanced" else 0.8),
                ),
                "source": "staged",
            }
            if key == "advanced":
                era.setdefault("advanced_epa_by_season", {})[str(season)] = {
                    "offense_epa_nonnull_rate": 1.0 - nulls.get("offense_epa", 1.0),
                    "defense_epa_nonnull_rate": 1.0 - nulls.get("defense_epa", 1.0),
                    "n_rows": int(len(df)),
                    "basis": "staged",
                }

        # Season-grain from staged when present
        for name, table, cols, expected_fn in (
            ("roster", "rosters", ["athlete_id", "position"], lambda s: True),
            (
                "returning",
                "returning_production",
                ["offense_pct", "defense_pct", "overall_pct"],
                lambda s: True,
            ),
            ("talent", "talent", ["talent"], lambda s: s >= 2015),
            (
                "recruiting",
                "recruiting",
                ["rank", "points", "blue_chip_ratio"],
                lambda s: True,
            ),
            ("portal", "portal", ["rating"], lambda s: is_portal_era(s)),
            ("coaches", "coaches", ["first_name", "last_name"], lambda s: True),
        ):
            if name in cells and cells[name].get("rows", 0) > 0 and cells[name].get("flag") != "MISSING":
                continue
            df = store.read(table, filters={"season": season})
            expected = expected_fn(season)
            if df.empty:
                if name not in cells or cells[name].get("flag") == "MISSING":
                    cells[name] = {
                        "rows": 0,
                        "schema_ok": True,
                        "flag": "GO" if not expected else "MISSING",
                        "source": "staged_empty_or_absent",
                        "expected": expected,
                    }
                continue
            ok, err = _validate(table, df)
            nulls = _null_rates(df, list(cols))
            cells[name] = {
                "rows": int(len(df)),
                "schema_ok": ok,
                "schema_error": err,
                "null_rates": nulls,
                "flag": _flag(
                    rows=len(df),
                    schema_ok=ok,
                    expected_rows=expected,
                    null_anomaly=any(nulls.get(c, 0) > NULL_ANOMALY for c in cols)
                    if expected
                    else False,
                ),
                "source": "staged",
            }
            if name == "returning":
                era.setdefault("returning_null_rates", {})[str(season)] = nulls
            if name == "roster":
                era.setdefault("roster", {})[str(season)] = {
                    "rows": int(len(df)),
                    "schema_ok": ok,
                    "negative_athlete_ids": int((df["athlete_id"] < 0).sum())
                    if "athlete_id" in df.columns
                    else 0,
                    "source": "staged",
                }
            if name == "portal":
                as_of = datetime(season, 12, 31, tzinfo=UTC)
                if not is_portal_era(season):
                    net = portal_net_rating(df, team_id=1, season=season, as_of=as_of)
                    era.setdefault("portal", {})[str(season)] = {
                        "era": False,
                        "rows": int(len(df)),
                        "is_nan": bool(math.isnan(net)),
                        "never_zero_pre_2021": bool(math.isnan(net)),
                        "source": "staged",
                    }
                else:
                    tids = set(df["dest_team_id"].dropna().astype(int)) | set(
                        df["origin_team_id"].dropna().astype(int)
                    )
                    nets = [
                        portal_net_rating(df, team_id=int(t), season=season, as_of=as_of)
                        for t in list(tids)[:40]
                    ]
                    finite = [n for n in nets if not math.isnan(n)]
                    era.setdefault("portal", {})[str(season)] = {
                        "era": True,
                        "rows": int(len(df)),
                        "n_finite_nets": len(finite),
                        "n_negative_nets": sum(1 for n in finite if n < 0),
                        "min_net": min(finite) if finite else None,
                        "max_net": max(finite) if finite else None,
                        "source": "staged",
                    }

        matrix[str(season)] = cells

    # GT conclusion from live samples already in era
    era["gt_conclusion"] = {
        "source_wp": "ABSENT — raw CFBD /plays keys include ppa/period/offenseScore/defenseScore/clock but NO homeWinProb/wp in any probed season sample",
        "source_connelly_inputs": "PRESENT — period, offenseScore, defenseScore, clock at source",
        "staging_gap": (
            "Staged PlaysSchema keeps epa/wp/period but drops scores and clock; "
            "wp column is always null because source lacks WP. A5 NOT RUN is both "
            "a source gap (no WP for primary GT rule) AND a staging gap "
            "(no garbage_time flags; Connelly inputs not staged)."
        ),
        "usable_gt_path": "Connelly margin-by-period fallback via plays_from_cfbd_raw_json only",
    }

    def first_complete(by_season: dict, rate_key: str, n_key: str) -> int | None:
        for s in SEASONS:
            rec = by_season.get(str(s), {})
            if rec.get(rate_key, 0.0) >= COMPLETE and rec.get(n_key, 0) > 0:
                return s
        return None

    era["first_complete"] = {
        "coverage_threshold": COMPLETE,
        "play_epa": first_complete(era.get("epa_wp_by_season", {}), "epa_nonnull_rate", "n_plays"),
        "play_wp": first_complete(era.get("epa_wp_by_season", {}), "wp_nonnull_rate", "n_plays"),
        "advanced_offense_epa": first_complete(
            era.get("advanced_epa_by_season", {}), "offense_epa_nonnull_rate", "n_rows"
        ),
        "advanced_defense_epa": first_complete(
            era.get("advanced_epa_by_season", {}), "defense_epa_nonnull_rate", "n_rows"
        ),
        "play_epa_note": (
            "Play EPA nonnull rate ~77% across seasons (ppa present; nulls are "
            "non-scrimmage / missing ppa). Never reaches 90% threshold — treat "
            "advanced_box offense/defense EPA as the complete-from-2014 path; "
            "play EPA usable but DEGRADED vs threshold."
        ),
        "play_wp_note": "WP never present at CFBD source in probed seasons — first_complete=null",
    }

    data["matrix"] = matrix
    data["era"] = era
    data["staged_fill_at"] = datetime.now(tz=UTC).isoformat()
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print("filled from staged", path)


def try_live_season_grain(seasons: list[int]) -> None:
    secrets = load_secrets()
    cfg = load_config()
    key = secrets.cfbd_api_key.get_secret_value()
    team_map = load_team_name_map(cfg.data.team_names_path)
    path = PROBE / "cfbd_matrix.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    matrix = data.setdefault("matrix", {})
    era = data.setdefault("era", {})
    api_calls = int(data.get("api_calls") or 0)
    now = datetime.now(tz=UTC)

    with CFBDClient(key, requests_per_second=cfg.data.cfbd_requests_per_second) as client:
        try:
            # First call allowed when remaining unknown; then check.
            client.fetch_teams(2024)
            api_calls += 1
            rem = client.remaining_requests
            print(f"CFBD remaining={rem}", flush=True)
            if rem is not None and rem < 40:
                print("insufficient budget for season-grain resume; skip live", flush=True)
                data["resume_skipped_remaining"] = rem
                data["api_calls"] = api_calls
                path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
                return
        except RateLimitBudgetError as exc:
            print("budget blocked", exc, flush=True)
            data["resume_blocked"] = str(exc)
            path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            return

        for season in seasons:
            cells = matrix.setdefault(str(season), {})
            # Skip if season-grain already live-complete
            if (
                cells.get("roster", {}).get("rows", 0) > 0
                and cells.get("returning", {}).get("rows", 0) > 0
                and cells.get("source") != "staged_empty_or_absent"
                and cells.get("roster", {}).get("source") != "staged_empty_or_absent"
            ):
                # still may need refresh if MISSING
                pass
            need = any(
                cells.get(n, {}).get("flag") == "MISSING"
                or cells.get(n, {}).get("rows", 0) == 0
                and n in ("roster", "returning", "coaches", "recruiting")
                for n in ("roster", "returning", "coaches", "recruiting", "talent", "portal")
            )
            # Always try if roster missing/zero for expected seasons
            if cells.get("roster", {}).get("rows", 0) > 1000 and cells.get("returning", {}).get(
                "rows", 0
            ) > 50:
                if season != 2020 or cells.get("roster", {}).get("source") == "live":
                    # 2023 staged may exist; still prefer live confirm for probed seasons missing live
                    if season in (2014, 2016, 2019) or (
                        cells.get("roster", {}).get("source") == "live"
                    ):
                        continue

            print(f"live season-grain {season}", flush=True)
            try:
                tbody = client.fetch_teams(season)
                api_calls += 1
                teams = normalize_teams_payload(
                    tbody, season=season, ingested_at=now, team_map=team_map
                )
                school_to_id = {
                    str(r.school): int(r.team_id) for r in teams.itertuples(index=False)
                }

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
                        "source": "live",
                    }
                    if name == "returning":
                        era.setdefault("returning_null_rates", {})[str(season)] = nulls
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
                expected = is_portal_era(season)
                cells["portal"] = {
                    "rows": int(len(portal)),
                    "schema_ok": ok,
                    "schema_error": err,
                    "portal_era": expected,
                    "flag": _flag(
                        rows=len(portal),
                        schema_ok=ok,
                        expected_rows=expected,
                        null_anomaly=False,
                    ),
                    "source": "live",
                }
                as_of = datetime(season, 12, 31, tzinfo=UTC)
                if expected:
                    tids = set()
                    if not portal.empty:
                        tids = set(portal["dest_team_id"].dropna().astype(int)) | set(
                            portal["origin_team_id"].dropna().astype(int)
                        )
                    nets = [
                        portal_net_rating(portal, team_id=int(t), season=season, as_of=as_of)
                        for t in list(tids)[:40]
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
                else:
                    net = portal_net_rating(portal, team_id=1, season=season, as_of=as_of)
                    era.setdefault("portal", {})[str(season)] = {
                        "era": False,
                        "rows": int(len(portal)),
                        "is_nan": bool(math.isnan(net)),
                        "never_zero_pre_2021": bool(math.isnan(net)),
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
                    "source": "live",
                }
                matrix[str(season)] = cells
                print(f"  ok {season} remaining={client.remaining_requests}", flush=True)
            except RateLimitBudgetError as exc:
                cells["_abort"] = str(exc)
                matrix[str(season)] = cells
                print("abort", exc, flush=True)
                break

        data["auth"]["cfbd_remaining_end"] = client.remaining_requests
    data["api_calls"] = api_calls
    data["matrix"] = matrix
    data["era"] = era
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    fill_from_staged()
    try_live_season_grain([2020, 2021, 2023, 2025])
