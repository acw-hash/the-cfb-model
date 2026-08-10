"""TASK 5B-PATCH — zero-API name-map repair + archive replay driver.

Replays historical odds normalization and crosswalk resolution from
``data/raw/odds_api_historical/`` only. Refuses to proceed if any archive is
missing (that would imply live/historical API spend).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.odds_api import (
    preview_crosswalk_game_key_regression,
    replay_historical_from_archives,
)
from ncaa_quant.ingestion.teams import load_team_name_map
from ncaa_quant.utils.logging import configure_logging

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "data" / "staged"
RAW = ROOT / "data" / "raw" / "odds_api_historical"
SEASONS_ALL = (2021, 2022, 2023, 2024, 2025)
SEASONS_EVAL = (2021, 2022, 2023, 2024)
LOCKBOX = 2025


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _snapshot_matched_keys(store: ParquetStore, seasons: tuple[int, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for season in seasons:
        cw = store.read("odds_cfbd_game_crosswalk", filters={"season": int(season)})
        if cw.empty:
            continue
        matched = cw[(cw["match_status"] == "matched") & cw["game_id"].notna()]
        for row in matched.itertuples(index=False):
            out[str(row.odds_event_id)] = str(row.game_key)
    return out


def _crosswalk_stats(store: ParquetStore, season: int) -> dict[str, Any]:
    cw = store.read("odds_cfbd_game_crosswalk", filters={"season": int(season)})
    if cw.empty:
        return {
            "events": 0,
            "matched": 0,
            "match_pct": 0.0,
            "fbs_fbs_unmatched": 0,
            "residuals": [],
        }
    ev = cw.drop_duplicates(subset=["odds_event_id"])
    matched = ev[ev["match_status"] == "matched"]
    n = len(ev)
    n_matched = len(matched)
    teams = store.read("teams", filters={"season": int(season)})
    fbs = teams[teams["classification"].astype(str).str.lower() == "fbs"]
    fbs_ids = set(int(x) for x in fbs["team_id"].dropna())
    id_to = {int(r.team_id): str(r.school) for r in teams.itertuples(index=False)}
    games = store.read("games", filters={"season": int(season)})
    fbs_gids = {
        int(g.game_id)
        for g in games.itertuples(index=False)
        if int(g.home_team_id) in fbs_ids and int(g.away_team_id) in fbs_ids
    }
    matched_gids = {int(x) for x in matched["game_id"].dropna()}
    missing = sorted(fbs_gids - matched_gids)
    residuals: list[str] = []
    for gid in missing:
        g = games[games["game_id"] == gid].iloc[0]
        home = id_to[int(g.home_team_id)]
        away = id_to[int(g.away_team_id)]
        kick = pd.Timestamp(g.start_date)
        kick_utc = kick if kick.tzinfo is not None else kick.tz_localize("UTC")
        swapped = ev[(ev["home_team"] == away) & (ev["away_team"] == home)].copy()
        reason = "no_odds_event"
        if not swapped.empty:
            swapped["kickoff"] = pd.to_datetime(swapped["kickoff"], utc=True)
            swapped["dh"] = (swapped["kickoff"] - kick_utc).abs().dt.total_seconds() / 3600.0
            close = swapped[swapped["dh"] <= 36.0]
            if len(close):
                reason = f"home_away_swap(dh={float(close.iloc[0]['dh']):.2f})"
        else:
            exact = ev[(ev["home_team"] == home) & (ev["away_team"] == away)]
            if not exact.empty:
                st = str(exact.iloc[0]["match_status"])
                delta = exact.iloc[0]["kickoff_delta_hours"]
                reason = f"status={st} delta={delta}"
        residuals.append(f"{gid} {away}@{home} week={int(g.week)} reason={reason}")
    return {
        "events": n,
        "matched": n_matched,
        "match_pct": 100.0 * n_matched / n if n else 0.0,
        "fbs_fbs_unmatched": len(missing),
        "residuals": residuals,
    }


def _hist_row_count(store: ParquetStore, season: int) -> int:
    odds = store.read("odds_snapshots", filters={"season": int(season)})
    if odds.empty:
        return 0
    return int((odds["snapshot_source"] == "historical").sum())


def step0_scope_audit(store: ParquetStore) -> None:
    section("STEP 0 — SCOPE AUDIT")
    print(
        "Verdict: RE-NORMALIZATION FROM ARCHIVE REQUIRED (not crosswalk-only).\n"
        "Evidence: unmatched crosswalk events already have staged odds_snapshots\n"
        "rows under wrong/unmatched game_keys (null game_id). Crosswalk-only\n"
        "repair would leave snapshot game_key/home_team/away_team/side mis-keyed.\n"
        "Replay path: wipe historical odds + crosswalk + quarantine for 2021–2025,\n"
        "then replay_historical_from_archives from data/raw/odds_api_historical/\n"
        "(zero API)."
    )
    for season in SEASONS_EVAL:
        cw = store.read("odds_cfbd_game_crosswalk", filters={"season": season})
        unmatched = cw[cw["match_status"] != "matched"]
        odds = store.read("odds_snapshots", filters={"season": season})
        um_keys = set(unmatched["game_key"].astype(str))
        odds_keys = set(odds["game_key"].astype(str)) if not odds.empty else set()
        um_in = um_keys & odds_keys
        print(
            f"  season {season}: unmatched_events={len(unmatched)} "
            f"unmatched_keys_in_snapshots={len(um_in)}/{len(um_keys)} "
            f"hist_rows={_hist_row_count(store, season)}"
        )


def main() -> int:
    configure_logging(level="INFO")
    cfg = load_config()
    team_map = load_team_name_map(Path(cfg.data.team_names_path))

    before_stats: dict[int, dict[str, Any]] = {}
    before_rows: dict[int, int] = {}
    prior_keys: dict[str, str] = {}

    with ParquetStore(STAGED) as store:
        step0_scope_audit(store)
        section("BEFORE — crosswalk + row counts")
        prior_keys = _snapshot_matched_keys(store, SEASONS_ALL)
        print(f"prior matched events (2021-2025): {len(prior_keys)}")
        for season in SEASONS_EVAL:
            before_stats[season] = _crosswalk_stats(store, season)
            before_rows[season] = _hist_row_count(store, season)
            s = before_stats[season]
            print(
                f"  {season}: events={s['events']} matched={s['matched']} "
                f"({s['match_pct']:.1f}%) FBS-FBS unmatched={s['fbs_fbs_unmatched']} "
                f"hist_rows={before_rows[season]}"
            )
        before_rows[LOCKBOX] = _hist_row_count(store, LOCKBOX)
        print(f"  {LOCKBOX}: hist_rows={before_rows[LOCKBOX]} (hygiene only)")

        section("PREFLIGHT — game_key regression gate")
        regressions = preview_crosswalk_game_key_regression(store, SEASONS_ALL, team_map)
        if regressions:
            print(f"STOP: {len(regressions)} previously-matched events would re-key:")
            for r in regressions[:50]:
                print(
                    f"  {r.odds_event_id} season={r.season} "
                    f"{r.old_game_key!r} -> {r.new_game_key!r}"
                )
            return 1
        print("preflight OK: zero previously-matched game_key changes under new map")

    section("STEP 2 — ARCHIVE REPLAY (zero API)")
    try:
        result = replay_historical_from_archives(
            SEASONS_ALL,
            config=cfg,
            raw_root=RAW,
            staged_root=STAGED,
            team_map=team_map,
        )
    except RuntimeError as exc:
        print(f"STOP: {exc}")
        return 1

    print(
        f"archives_replayed={result.archives_replayed} "
        f"rows_written={result.rows_written} "
        f"rows_quarantined={result.rows_quarantined}"
    )

    after_stats: dict[int, dict[str, Any]] = {}
    after_rows: dict[int, int] = {}
    with ParquetStore(STAGED) as store:
        section("STEP 3 — REGRESSION DIFF")
        after_keys = _snapshot_matched_keys(store, SEASONS_ALL)
        rekeyed: list[str] = []
        lost: list[str] = []
        for eid, old_key in prior_keys.items():
            if eid not in after_keys:
                lost.append(f"{eid} missing_after old={old_key}")
                continue
            new_key = after_keys[eid]
            if new_key != old_key:
                rekeyed.append(f"{eid} {old_key!r} -> {new_key!r}")
        if rekeyed or lost:
            print(
                f"STOP: regression failures rekeyed={len(rekeyed)} lost_matched={len(lost)}"
            )
            for line in (rekeyed + lost)[:80]:
                print(f"  {line}")
            return 1
        print(
            f"regression OK: {len(prior_keys)} previously-matched events retained "
            "same game_key"
        )

        section("AFTER — crosswalk table (2021-2024)")
        print(
            f"{'season':>6} {'events':>7} {'matched':>8} {'match%':>7} "
            f"{'FBS-FBS um':>10} | before matched% / FBS-FBS um"
        )
        for season in SEASONS_EVAL:
            after_stats[season] = _crosswalk_stats(store, season)
            after_rows[season] = _hist_row_count(store, season)
            a = after_stats[season]
            b = before_stats[season]
            print(
                f"{season:6d} {a['events']:7d} {a['matched']:8d} {a['match_pct']:6.1f}% "
                f"{a['fbs_fbs_unmatched']:10d} | "
                f"{b['matched']}/{b['events']} ({b['match_pct']:.1f}%) / {b['fbs_fbs_unmatched']}"
            )
            for residual in a["residuals"]:
                print(f"         residual: {residual}")

        after_rows[LOCKBOX] = _hist_row_count(store, LOCKBOX)
        section("ROW-COUNT DELTA (historical odds_snapshots)")
        for season in SEASONS_ALL:
            delta = after_rows[season] - before_rows[season]
            note = "lockbox hygiene" if season == LOCKBOX else ""
            print(
                f"  season {season}: before={before_rows[season]} "
                f"after={after_rows[season]} delta={delta:+d} {note}"
            )

        section("SAM HOUSTON FCS GATE CHECK")
        for season in (2021, 2022):
            teams = store.read("teams", filters={"season": season})
            sh = teams[teams["school"] == "Sam Houston"]
            cls = str(sh.iloc[0]["classification"]).lower() if not sh.empty else "absent"
            stats = after_stats.get(season) or _crosswalk_stats(store, season)
            sh_residuals = [r for r in stats["residuals"] if "Sam Houston" in r]
            print(
                f"  season {season}: Sam Houston classification={cls}; "
                f"FBS-FBS residuals involving Sam Houston: {len(sh_residuals)}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
