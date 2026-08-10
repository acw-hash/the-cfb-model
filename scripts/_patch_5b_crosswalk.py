"""TASK 5B-PATCH-2 — zero-API home/away swap matcher + archive replay.

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

# 5B-PATCH FBS–FBS home_away_swap residuals (Odds away@home listing vs CFBD).
PATCH1_SWAP_RESIDUALS: dict[int, tuple[str, ...]] = {
    2021: (
        "Northern Illinois@Coastal Carolina",
        "Western Kentucky@App State",
        "Utah State@Oregon State",
        "Louisiana@Marshall",
        "Old Dominion@Tulsa",
        "Kent State@Wyoming",
        "UTSA@San Diego State",
        "Georgia State@Ball State",
        "Air Force@Louisville",
        "Mississippi State@Texas Tech",
        "Clemson@Iowa State",
        "North Carolina@South Carolina",
        "Tennessee@Purdue",
        "Wisconsin@Arizona State",
        "Wake Forest@Rutgers",
        "Penn State@Arkansas",
        "Iowa@Kentucky",
        "North Texas@Miami (OH)",
    ),
    2022: ("LSU@Florida State", "Clemson@Georgia Tech"),
    2023: ("Navy@Army", "North Carolina@West Virginia"),
    2024: ("USC@LSU",),
}


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
            "swap_matched": 0,
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

    # Reconstruct swap_detected (matcher-only; not persisted under strict schema).
    swap_n = 0
    for row in matched.itertuples(index=False):
        gid = int(row.game_id)
        ghit = games[games["game_id"] == gid]
        if ghit.empty:
            continue
        g = ghit.iloc[0]
        cfbd_home = id_to[int(g.home_team_id)]
        cfbd_away = id_to[int(g.away_team_id)]
        if str(row.home_team) == cfbd_away and str(row.away_team) == cfbd_home:
            swap_n += 1

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
                reason = f"swap_outside_tolerance(min_dh={float(swapped['dh'].min()):.2f})"
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
        "swap_matched": swap_n,
    }


def _hist_row_count(store: ParquetStore, season: int) -> int:
    odds = store.read("odds_snapshots", filters={"season": int(season)})
    if odds.empty:
        return 0
    return int((odds["snapshot_source"] == "historical").sum())


def _resolve_patch1_residual_games(
    store: ParquetStore,
) -> list[dict[str, Any]]:
    """Map 5B-PATCH residual labels to CFBD games (neutral_site audit)."""
    rows: list[dict[str, Any]] = []
    for season, labels in PATCH1_SWAP_RESIDUALS.items():
        teams = store.read("teams", filters={"season": season})
        id_to = {int(r.team_id): str(r.school) for r in teams.itertuples(index=False)}
        games = store.read("games", filters={"season": season})
        for label in labels:
            away_l, home_l = label.split("@", 1)
            hit = None
            for g in games.itertuples(index=False):
                home = id_to.get(int(g.home_team_id), "")
                away = id_to.get(int(g.away_team_id), "")
                # CFBD lists home as designated home; residual label is Odds away@home
                # which equals CFBD home as second token when Odds flipped.
                if home == home_l and away == away_l:
                    hit = g
                    break
                if home == away_l and away == home_l:
                    hit = g
                    break
            if hit is None:
                rows.append(
                    {
                        "season": season,
                        "label": label,
                        "game_id": None,
                        "neutral_site": None,
                        "cfbd_home": None,
                        "cfbd_away": None,
                        "found": False,
                    }
                )
                continue
            rows.append(
                {
                    "season": season,
                    "label": label,
                    "game_id": int(hit.game_id),
                    "neutral_site": bool(hit.neutral_site),
                    "cfbd_home": id_to[int(hit.home_team_id)],
                    "cfbd_away": id_to[int(hit.away_team_id)],
                    "found": True,
                }
            )
    return rows


def step0_side_semantics() -> None:
    section("STEP 0 — SIDE-SEMANTICS AUDIT")
    print(
        "Verdict: NAME-BASED (proceed with swap-tolerant match).\n"
        "\n"
        "Code path in normalize_odds_payload:\n"
        "  for outcome in outcomes:\n"
        "      name = str(outcome.get('name', ''))\n"
        "      if schema_market == 'total':\n"
        "          side = name.strip().casefold()   # over/under\n"
        "      else:\n"
        "          side = normalize_team_name(name, team_map)  # team NAME\n"
        "\n"
        "Spread/h2h sides come from the outcome team name, never from whether\n"
        "the team is event home_team vs away_team. home_team/away_team columns\n"
        "are stored for context/game_key only. A home/away listing swap therefore\n"
        "does NOT flip spread signs — side remains the named team.\n"
        "\n"
        "FORBIDDEN failure mode (position-derived side + swap match) does not apply."
    )


def _spot_check_spread_signs(
    store: ParquetStore,
    residual_games: list[dict[str, Any]],
    *,
    sample_n: int = 5,
) -> None:
    section("SPREAD SIGN SPOT-CHECK (5 swap-matched games)")
    # Prefer neutral_site True first, then fill; deterministic order by season/label.
    found = [r for r in residual_games if r["found"] and r["game_id"] is not None]
    neutrals = [r for r in found if r["neutral_site"]]
    non_neutrals = [r for r in found if not r["neutral_site"]]
    sample = (neutrals + non_neutrals)[:sample_n]
    print(
        f"{'season':>6} {'game_id':>10} {'cfbd_home':<22} "
        f"{'odds_home_line':>14} {'cfbd_home_spread':>16} {'agree':>5} neutral"
    )
    for r in sample:
        season = int(r["season"])
        gid = int(r["game_id"])
        cfbd_home = str(r["cfbd_home"])
        odds = store.read("odds_snapshots", filters={"season": season})
        lines = store.read("lines_historical", filters={"season": season})
        snap = odds[
            (odds["game_id"] == gid)
            & (odds["market"] == "spread")
            & (odds["side"] == cfbd_home)
            & (odds["snapshot_source"] == "historical")
        ]
        cfbd = lines[(lines["game_id"] == gid) & (lines["line_type"] == "close")]
        odds_med = float(snap["line"].median()) if not snap.empty else float("nan")
        cfbd_med = float(cfbd["spread"].median()) if not cfbd.empty else float("nan")
        agree = "Y" if abs(odds_med - cfbd_med) <= 1.5 else "N"
        if snap.empty or cfbd.empty:
            agree = "?"
        print(
            f"{season:6d} {gid:10d} {cfbd_home:<22} "
            f"{odds_med:14.2f} {cfbd_med:16.2f} {agree:>5} "
            f"{r['neutral_site']}"
        )
        print(
            f"         label={r['label']} odds_rows={len(snap)} "
            f"cfbd_close_rows={len(cfbd)}"
        )


def main() -> int:
    configure_logging(level="INFO")
    cfg = load_config()
    team_map = load_team_name_map(Path(cfg.data.team_names_path))

    step0_side_semantics()

    before_stats: dict[int, dict[str, Any]] = {}
    before_rows: dict[int, int] = {}
    prior_keys: dict[str, str] = {}
    residual_games: list[dict[str, Any]] = []

    with ParquetStore(STAGED) as store:
        section("NEUTRAL-SITE BREAKDOWN (23 patch-1 residuals)")
        residual_games = _resolve_patch1_residual_games(store)
        n_found = sum(1 for r in residual_games if r["found"])
        n_neutral = sum(1 for r in residual_games if r["found"] and r["neutral_site"])
        n_non = sum(1 for r in residual_games if r["found"] and not r["neutral_site"])
        print(f"resolved={n_found}/23 neutral_site=True={n_neutral} non-neutral={n_non}")
        for r in residual_games:
            if not r["found"]:
                print(f"  MISSING {r['season']} {r['label']}")
                continue
            tag = "neutral" if r["neutral_site"] else "NON-NEUTRAL (suspicious)"
            print(
                f"  {r['season']} gid={r['game_id']} {r['label']} "
                f"cfbd={r['cfbd_away']}@{r['cfbd_home']} {tag}"
            )

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
                f"swap_matched={s['swap_matched']} hist_rows={before_rows[season]}"
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
        print("preflight OK: zero previously-matched game_key changes under current map")

    section("ARCHIVE REPLAY (zero API)")
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
        section("REGRESSION DIFF")
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
            "same game_key (0 re-keyed)"
        )

        section("AFTER — crosswalk table (2021-2024)")
        print(
            f"{'season':>6} {'events':>7} {'matched':>8} {'match%':>7} "
            f"{'FBS-FBS um':>10} {'swaps':>6} | before matched% / FBS-FBS um"
        )
        for season in SEASONS_EVAL:
            after_stats[season] = _crosswalk_stats(store, season)
            after_rows[season] = _hist_row_count(store, season)
            a = after_stats[season]
            b = before_stats[season]
            print(
                f"{season:6d} {a['events']:7d} {a['matched']:8d} {a['match_pct']:6.1f}% "
                f"{a['fbs_fbs_unmatched']:10d} {a['swap_matched']:6d} | "
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

        # Refresh residual game list post-replay for spot-check (same CFBD ids).
        residual_games = _resolve_patch1_residual_games(store)
        _spot_check_spread_signs(store, residual_games)

        section("POST — residual game match status")
        for r in residual_games:
            if not r["found"]:
                print(f"  {r['season']} {r['label']}: CFBD game not found")
                continue
            cw = store.read(
                "odds_cfbd_game_crosswalk",
                filters={"season": int(r["season"])},
            )
            hit = cw[cw["game_id"] == int(r["game_id"])]
            if hit.empty:
                print(f"  {r['season']} gid={r['game_id']} {r['label']}: still unmatched")
            else:
                st = str(hit.iloc[0]["match_status"])
                print(
                    f"  {r['season']} gid={r['game_id']} {r['label']}: "
                    f"status={st} odds={hit.iloc[0]['away_team']}@"
                    f"{hit.iloc[0]['home_team']}"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
