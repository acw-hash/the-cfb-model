"""S7-XWALK-B — Phase B gate re-run with populated qb_status (read-only probe)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ncaa_quant.betting.provider import qb_status_known_for_game
from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.utils.timeutils import to_utc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _s6_w1_card import GATE_STEPS, run_analysis  # noqa: E402

# After QB row writes (~22:25Z); snapshot still fresh until 02:34:56Z.
ANALYSIS_AS_OF = datetime(2026, 9, 1, 22, 30, 0, tzinfo=UTC)
SNAPSHOT_EVENT_TIME = datetime(2026, 9, 1, 20, 34, 56, 940488, tzinfo=UTC)
PRIOR_STEP_COUNTS = {
    "start": 91,
    "step1_snapshot_stale_kickoff_quarantine": 90,
    "step2_edge_ev_sigma": 84,
    "step3_model_market_disagree": 28,
    "step4_exposure_caps": 8,
    "step5_qb_status_unknown": 0,
}


def _qb_detail_for_game(
    game: dict,
    qb_frame: pd.DataFrame,
    as_of: datetime,
) -> dict:
    gid = int(game["game_id"])
    teams_out = []
    for tid, name in (
        (int(game["home_team_id"]), game["home_team"]),
        (int(game["away_team_id"]), game["away_team"]),
    ):
        sub = qb_frame.loc[
            (qb_frame["game_id"].astype("Int64") == gid)
            & (qb_frame["team_id"].astype(int) == tid)
        ].copy()
        status = None
        source = None
        event_time = None
        if not sub.empty:
            sub["event_time"] = pd.to_datetime(sub["event_time"], utc=True)
            sub = sub.loc[sub["event_time"] <= pd.Timestamp(as_of)]
            if not sub.empty:
                latest = sub.sort_values("event_time").iloc[-1]
                status = str(latest["status"])
                source = str(latest.get("source_version", ""))
                event_time = latest["event_time"].isoformat()
        teams_out.append(
            {
                "team": name,
                "team_id": tid,
                "status": status,
                "source_version": source,
                "event_time": event_time,
            }
        )
    known, qb_source = qb_status_known_for_game(
        qb_frame,
        game_id=gid,
        home_team_id=int(game["home_team_id"]),
        away_team_id=int(game["away_team_id"]),
        as_of=as_of,
    )
    passes_qb_gate = not any(
        "qb_status_unknown" in r.get("filter_reasons_firing", [])
        for r in []  # filled by caller
    )
    return {
        "game_id": str(gid),
        "matchup": f"{game['away_team']} @ {game['home_team']}",
        "teams": teams_out,
        "qb_status_known": known,
        "qb_status_source": qb_source,
    }


def main() -> None:
    payload = run_analysis(as_of=ANALYSIS_AS_OF)
    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        qb_frame = store.read("qb_status", filters={"season": 2026})

    # Load slate games for step-4 QB detail
    from _s6_w1_card import _load_slate  # noqa: PLC0415

    wp, _ = _load_slate()
    games_by_id = {int(g["game_id"]): g for g in wp["games"]}

    gate = payload["gate"]
    step4_ids = gate.get("qb_worklist", [])
    step4_gids = [int(x["game_id"]) for x in step4_ids]

    # Re-derive step-4 survivor rows for QB gate pass/fail on filter_reasons
    rows_by_gid = {r["game_id"]: r for r in payload["betting_rows"] if "edge" in r}
    qb_game_details = []
    for item in step4_ids:
        gid = int(item["game_id"])
        game = games_by_id[gid]
        detail = _qb_detail_for_game(game, qb_frame, ANALYSIS_AS_OF)
        row = rows_by_gid.get(str(gid), {})
        detail["passes_qb_gate"] = "qb_status_unknown" not in row.get("filter_reasons_firing", [])
        detail["filter_reasons_firing"] = row.get("filter_reasons_firing", [])
        qb_game_details.append(detail)

    final_ids = gate.get("final_survivors", [])
    final_rows = [rows_by_gid[gid] for gid in final_ids if gid in rows_by_gid]

    age_h = round((ANALYSIS_AS_OF - SNAPSHOT_EVENT_TIME).total_seconds() / 3600.0, 3)
    stale = payload["odds_summary"]["stale_at_as_of"]

    out = {
        "analysis_as_of": ANALYSIS_AS_OF.isoformat(),
        "snapshot_event_time": SNAPSHOT_EVENT_TIME.isoformat(),
        "as_of_minus_snapshot_hours": age_h,
        "stale_at_as_of": stale,
        "odds_max_age_hours": payload["odds_summary"]["odds_max_age_hours"],
        "prior_step_counts": PRIOR_STEP_COUNTS,
        "new_step_counts": gate["step_counts"],
        "step4_qb_detail": qb_game_details,
        "final_survivors": final_rows,
        "qb_gate_pass_count": sum(1 for d in qb_game_details if d["passes_qb_gate"]),
        "qb_gate_fail_count": sum(1 for d in qb_game_details if not d["passes_qb_gate"]),
    }

    art = ROOT / "docs/notes/_artifacts/social-s7-xwalk-b"
    art.mkdir(parents=True, exist_ok=True)
    (art / "gate_rerun.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
