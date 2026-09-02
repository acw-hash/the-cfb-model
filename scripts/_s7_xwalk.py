"""S7-XWALK — replay tonight's live archive and report crosswalk + gate deltas."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.odds_api import (
    extract_odds_events,
    load_cfbd_schedule,
    match_odds_events_to_cfbd,
    replay_live_from_archive,
)
from ncaa_quant.ingestion.teams import load_team_name_map

ROOT = Path(__file__).resolve().parents[1]
RAW_ARCHIVE = ROOT / "data/raw/odds_api/2026-09-01/20260901T203456940488Z.json"
CAPTURED_AT = datetime(2026, 9, 1, 20, 34, 56, 940488, tzinfo=UTC)
ANALYSIS_AS_OF = datetime(2026, 9, 1, 20, 38, 58, tzinfo=UTC)


def _match_summary(body: bytes, team_map: dict[str, str], store: ParquetStore) -> dict[str, object]:
    events = extract_odds_events(body, team_map)
    schedule = load_cfbd_schedule(store, [2026], team_map)
    existing = store.read("odds_cfbd_game_crosswalk", filters={"season": 2026})
    crosswalk = match_odds_events_to_cfbd(
        events,
        schedule,
        existing=existing if not existing.empty else None,
        ingested_at=CAPTURED_AT,
    )
    total = len(crosswalk)
    matched = int((crosswalk["match_status"] == "matched").sum())
    unmatched = crosswalk[crosswalk["match_status"] != "matched"]
    return {
        "total": total,
        "matched": matched,
        "unmatched": int(len(unmatched)),
        "match_rate": round(matched / total, 4) if total else None,
        "unmatched_events": [
            {
                "away_team": str(r.away_team),
                "home_team": str(r.home_team),
                "match_status": str(r.match_status),
            }
            for r in unmatched.itertuples(index=False)
        ],
    }


def main() -> int:
    if not RAW_ARCHIVE.is_file():
        print(f"STOP: raw archive missing: {RAW_ARCHIVE}")
        return 1

    cfg = load_config()
    team_map = load_team_name_map(cfg.data.team_names_path)
    body = RAW_ARCHIVE.read_bytes()

    with ParquetStore(cfg.paths.staged_dir) as store:
        before = _match_summary(body, team_map, store)

    print("=== BEFORE (current team map on archived pull) ===")
    print(json.dumps(before, indent=2))

    result = replay_live_from_archive(
        RAW_ARCHIVE,
        captured_at=CAPTURED_AT,
        ingested_at=datetime.now(tz=UTC),
    )
    print(
        f"replay rows_written={result.rows_written} rows_fetched={result.rows_fetched} "
        f"raw={result.raw_path}"
    )

    with ParquetStore(cfg.paths.staged_dir) as store:
        after = _match_summary(body, team_map, store)
    print("=== AFTER (post-replay crosswalk state) ===")
    print(json.dumps(after, indent=2))

    # Gate re-run via S6 card probe (Phase B only — no reply bank regen here).
    sys.path.insert(0, str(ROOT / "scripts"))
    from _s6_w1_card import run_analysis  # noqa: PLC0415

    payload = run_analysis(as_of=ANALYSIS_AS_OF)
    out = ROOT / "docs/notes/_artifacts/social-s7-xwalk/post_replay_analysis.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    slim = {
        "analysis_as_of": payload["analysis_as_of"],
        "crosswalk_summary": payload["crosswalk_summary"],
        "odds_summary": {
            "games_with_spread_odds": payload["odds_summary"]["games_with_spread_odds"],
            "games_without_spread_odds": payload["odds_summary"]["games_without_spread_odds"],
        },
        "gate_step_counts": payload["gate"]["step_counts"],
        "qb_worklist": payload["gate"].get("qb_worklist", []),
    }
    out.write_text(json.dumps(slim, indent=2), encoding="utf-8")
    print("=== PHASE B GATE SURVIVORS (post-replay) ===")
    print(json.dumps(slim, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
