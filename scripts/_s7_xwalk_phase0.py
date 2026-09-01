"""S7-XWALK Phase 0 analysis — read-only crosswalk diagnostic."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.odds_api import (
    extract_odds_events,
    load_cfbd_schedule,
    match_odds_events_to_cfbd,
)
from ncaa_quant.ingestion.teams import load_team_name_map, normalize_team_name

RAW_PATH = Path("data/raw/odds_api/2026-09-01/20260901T203456940488Z.json")
INGESTED_AT = datetime(2026, 9, 1, 20, 34, 56, 940488, tzinfo=UTC)


def _raw_team_strings(body: bytes) -> dict[str, dict[str, str]]:
    data = json.loads(body)
    out: dict[str, dict[str, str]] = {}
    for event in data:
        eid = str(event.get("id", ""))
        out[eid] = {
            "home_raw": str(event.get("home_team", "")),
            "away_raw": str(event.get("away_team", "")),
            "commence": str(event.get("commence_time", "")),
        }
    return out


def _infer_game_id(
    schedule: pd.DataFrame,
    *,
    home_norm: str,
    away_norm: str,
    kickoff: datetime,
    season: int = 2026,
) -> tuple[int | None, str]:
    """Infer intended game_id from kickoff + one resolved opponent."""
    tol_h = 36.0
    cands = schedule[schedule["season"] == season].copy()
    if cands.empty:
        return None, "no_schedule"

    def within_tol(row: pd.Series) -> bool:
        cfbd_kick = pd.Timestamp(row["start_date"]).to_pydatetime()
        delta_h = abs((kickoff - cfbd_kick).total_seconds()) / 3600.0
        return delta_h <= tol_h

    for gid, g in cands.groupby("game_id"):
        row = g.iloc[0]
        if not within_tol(row):
            continue
        h, a = str(row["home_team"]), str(row["away_team"])
        if h == home_norm and a == away_norm:
            return int(gid), "exact_pair"
        if h == away_norm and a == home_norm:
            return int(gid), "swapped_pair"

    # One-team match: find game at kickoff where one team matches
    hits: list[tuple[int, str, str, str]] = []
    for _, row in cands.iterrows():
        if not within_tol(row):
            continue
        h, a = str(row["home_team"]), str(row["away_team"])
        if home_norm in (h, a) or away_norm in (h, a):
            hits.append((int(row["game_id"]), h, a, "one_team"))
    if len(hits) == 1:
        return hits[0][0], "one_team_kickoff"
    if len(hits) > 1:
        return None, f"ambiguous_one_team({len(hits)})"
    return None, "no_kickoff_match"


def _classify_failure(
    home_raw: str,
    away_raw: str,
    home_norm: str,
    away_norm: str,
    team_map: dict[str, str],
) -> tuple[str, str]:
    """Return (classification, detail)."""
    from ncaa_quant.ingestion.teams import _norm_key

    def alias_hit(raw: str) -> bool:
        return _norm_key(raw) in team_map

    def norm_changed(raw: str, norm: str) -> bool:
        return " ".join(raw.split()) != norm

    issues: list[str] = []
    for side, raw, norm in [("home", home_raw, home_norm), ("away", away_raw, away_norm)]:
        if alias_hit(raw):
            continue
        if raw != norm and norm_changed(raw, norm):
            # mascot strip or partial normalization
            if " Sycamores" in raw or " Bison" in raw or " Hornets" in raw:
                issues.append(f"{side}:mascot_appended")
            elif raw.startswith("The ") and not norm.startswith("The "):
                issues.append(f"{side}:article_present")
            elif raw in ("Albany", "Citadel", "Houston Baptist"):
                issues.append(f"{side}:stale_school_name")
            else:
                issues.append(f"{side}:normalization_gap")
        else:
            issues.append(f"{side}:unmapped_no_alias")

    if not issues:
        return "unknown", ""
    primary = issues[0].split(":", 1)[1]
    return primary, "; ".join(issues)


def main() -> None:
    cfg = load_config()
    team_map = load_team_name_map(cfg.data.team_names_path)
    body = RAW_PATH.read_bytes()
    raw_lookup = _raw_team_strings(body)

    events = extract_odds_events(body, team_map)
    with ParquetStore(cfg.paths.staged_dir) as store:
        schedule = load_cfbd_schedule(store, [2026], team_map)
        existing = store.read("odds_cfbd_game_crosswalk", filters={"season": 2026})
        crosswalk = match_odds_events_to_cfbd(
            events, schedule, existing=existing, ingested_at=INGESTED_AT
        )

    unmatched = crosswalk[crosswalk["match_status"] != "matched"].copy()
    print(f"Total events: {len(events)}")
    print(f"Matched: {(crosswalk['match_status'] == 'matched').sum()}")
    print(f"Unmatched+quarantined: {len(unmatched)}")
    print()

    rows: list[dict[str, object]] = []
    for _, row in unmatched.sort_values("kickoff").iterrows():
        eid = str(row["odds_event_id"])
        raw = raw_lookup.get(eid, {})
        home_raw = raw.get("home_raw", "")
        away_raw = raw.get("away_raw", "")
        home_norm = str(row["home_team"])
        away_norm = str(row["away_team"])
        kickoff = pd.Timestamp(row["kickoff"]).to_pydatetime()
        gid, infer_method = _infer_game_id(
            schedule,
            home_norm=home_norm,
            away_norm=away_norm,
            kickoff=kickoff,
        )
        cls, detail = _classify_failure(home_raw, away_raw, home_norm, away_norm, team_map)
        sched_row = schedule[schedule["game_id"] == gid].iloc[0] if gid else None
        matchup = (
            f"{sched_row['away_team']} @ {sched_row['home_team']}" if sched_row is not None else ""
        )
        rows.append(
            {
                "odds_event_id": eid,
                "home_raw": home_raw,
                "away_raw": away_raw,
                "home_norm": home_norm,
                "away_norm": away_norm,
                "commence": raw.get("commence", ""),
                "intended_game_id": gid,
                "intended_matchup": matchup,
                "infer_method": infer_method,
                "classification": cls,
                "detail": detail,
                "match_status": row["match_status"],
            }
        )

    df = pd.DataFrame(rows)
    for i, r in df.iterrows():
        print(f"--- {i + 1}/34 ---")
        print(f"  away_raw: {r['away_raw']!r}")
        print(f"  home_raw: {r['home_raw']!r}")
        print(f"  commence: {r['commence']}")
        print(f"  normalized: {r['away_norm']} @ {r['home_norm']}")
        print(f"  intended_game_id: {r['intended_game_id']} ({r['infer_method']})")
        print(f"  intended_matchup: {r['intended_matchup']}")
        print(f"  classification: {r['classification']} — {r['detail']}")
        print()

    print("=== CLASSIFICATION COUNTS ===")
    print(df["classification"].value_counts().to_string())
    print()
    print("=== UNIDENTIFIED ===")
    unidentified = df[df["intended_game_id"].isna()]
    print(f"count={len(unidentified)}")
    for _, r in unidentified.iterrows():
        print(f"  {r['away_raw']} @ {r['home_raw']} | {r['commence']}")

    out = Path("docs/notes/_artifacts/social-s7-xwalk/phase0_unmatched.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(df.to_json(orient="records", indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
