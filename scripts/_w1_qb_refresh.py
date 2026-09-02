"""W1-QB — refresh QB worklist against current step-4 survivor set (read-only)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ncaa_quant.betting.filters import FilterReason
from ncaa_quant.betting.provider import qb_status_known_for_game
from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _s6_w1_card import GATE_STEPS, _load_slate, run_analysis  # noqa: E402

ANALYSIS_AS_OF = datetime(2026, 9, 1, 22, 30, 0, tzinfo=UTC)
ARTIFACT_DIR = ROOT / "docs" / "notes" / "_artifacts" / "w1-qb-refresh"
PRIOR_STEP5_MATCHUPS = ("Baylor @ Auburn", "Tulane @ Duke")


def _passes_step(reasons: set[str], step_reasons: tuple[str, ...]) -> bool:
    return not any(r in reasons for r in step_reasons)


def _step3_pool(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    survivors = list(rows)
    for idx, step_reasons in enumerate(GATE_STEPS):
        if idx >= 3:
            break
        survivors = [
            r
            for r in survivors
            if _passes_step(set(r.get("filter_reasons_firing", [])), step_reasons)
        ]
    return sorted(survivors, key=lambda r: float(r["edge"]), reverse=True)


def _team_qb_row(
    *,
    game_id: int,
    team_id: int,
    team_name: str,
    qb_frame: pd.DataFrame,
    as_of: datetime,
) -> dict[str, Any]:
    sub = qb_frame.loc[
        (qb_frame["game_id"].astype("Int64") == game_id)
        & (qb_frame["team_id"].astype(int) == team_id)
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
    resolves = status is not None and status != "unknown"
    return {
        "team": team_name,
        "team_id": team_id,
        "status": status,
        "source_version": source,
        "event_time": event_time,
        "resolves": resolves,
    }


def _qb_detail_for_game(
    game: dict[str, Any],
    qb_frame: pd.DataFrame,
    as_of: datetime,
) -> dict[str, Any]:
    gid = int(game["game_id"])
    teams_out = [
        _team_qb_row(
            game_id=gid,
            team_id=int(tid),
            team_name=str(name),
            qb_frame=qb_frame,
            as_of=as_of,
        )
        for tid, name in (
            (int(game["home_team_id"]), game["home_team"]),
            (int(game["away_team_id"]), game["away_team"]),
        )
    ]
    known, qb_source = qb_status_known_for_game(
        qb_frame,
        game_id=gid,
        home_team_id=int(game["home_team_id"]),
        away_team_id=int(game["away_team_id"]),
        as_of=as_of,
    )
    return {
        "game_id": str(gid),
        "matchup": f"{game['away_team']} @ {game['home_team']}",
        "teams": teams_out,
        "qb_status_known": known,
        "qb_status_source": qb_source,
    }


def _future_stamped_rows(qb_frame: pd.DataFrame, as_of: datetime) -> list[dict[str, Any]]:
    as_of_ts = pd.Timestamp(as_of)
    qb = qb_frame.copy()
    qb["event_time"] = pd.to_datetime(qb["event_time"], utc=True)
    bad = qb.loc[qb["event_time"] > as_of_ts]
    out: list[dict[str, Any]] = []
    for _, row in bad.iterrows():
        out.append(
            {
                "game_id": str(int(row["game_id"])),
                "team_id": int(row["team_id"]),
                "status": str(row["status"]),
                "source_version": str(row.get("source_version", "")),
                "event_time": row["event_time"].isoformat(),
            }
        )
    return out


S6_DISPLACED_GIDS = {401864496, 401864499}  # dropped from step-4 after S7 crosswalk


def _orphan_rows(
    qb_frame: pd.DataFrame,
    step4_gids: set[int],
    as_of: datetime,
    *,
    source_version: str = "manual_v1",
) -> list[dict[str, Any]]:
    qb = qb_frame.copy()
    qb["event_time"] = pd.to_datetime(qb["event_time"], utc=True)
    qb = qb.loc[qb["event_time"] <= pd.Timestamp(as_of)]
    qb = qb.loc[qb["source_version"].astype(str) == source_version]
    if qb.empty:
        return []
    latest = (
        qb.sort_values("event_time")
        .groupby(["game_id", "team_id"], sort=False)
        .tail(1)
    )
    orphans: list[dict[str, Any]] = []
    for _, row in latest.iterrows():
        gid = int(row["game_id"])
        if gid not in step4_gids:
            category = "s6_displaced" if gid in S6_DISPLACED_GIDS else "other"
            orphans.append(
                {
                    "game_id": str(gid),
                    "team_id": int(row["team_id"]),
                    "status": str(row["status"]),
                    "source_version": str(row["source_version"]),
                    "event_time": row["event_time"].isoformat(),
                    "orphan_reason": "game not in current step-4 set",
                    "orphan_category": category,
                }
            )
    return sorted(orphans, key=lambda r: (r["game_id"], r["team_id"]))


def _operator_worklist(step4_details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for detail in step4_details:
        gid = detail["game_id"]
        matchup = detail["matchup"]
        for team in detail["teams"]:
            if team["resolves"]:
                continue
            reason = "no row" if team["status"] is None else "status=unknown"
            rows.append(
                {
                    "game_id": gid,
                    "matchup": matchup,
                    "team": team["team"],
                    "team_id": team["team_id"],
                    "lookup_reason": reason,
                    "current_status": team["status"] or "—",
                    "current_source": team["source_version"] or "—",
                    "current_event_time": team["event_time"] or "—",
                }
            )
    return rows


def _step4_with_ranks(
    payload: dict[str, Any],
    games_by_id: dict[int, dict[str, Any]],
    qb_frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    rows = [r for r in payload["betting_rows"] if "edge" in r]
    step3 = _step3_pool(rows)
    rank_by_gid = {int(r["game_id"]): idx + 1 for idx, r in enumerate(step3)}

    gate = payload["gate"]
    step4_items = gate.get("qb_worklist", [])
    rows_by_gid = {r["game_id"]: r for r in rows}
    out: list[dict[str, Any]] = []
    for item in step4_items:
        gid = int(item["game_id"])
        row = rows_by_gid[str(gid)]
        game = games_by_id[gid]
        detail = _qb_detail_for_game(game, qb_frame, ANALYSIS_AS_OF)
        detail["edge"] = row["edge"]
        detail["step3_rank"] = rank_by_gid.get(gid)
        detail["passes_qb_gate"] = FilterReason.QB_STATUS_UNKNOWN.value not in row.get(
            "filter_reasons_firing", []
        )
        out.append(detail)
    return out


def _render_worklist_md(report: dict[str, Any]) -> str:
    lines = [
        "# W1-QB — QB worklist refresh",
        "",
        f"**Task:** W1-QB  ",
        f"**Branch:** `social-s1-s2`  ",
        f"**`as_of`:** `{report['analysis_as_of']}`  ",
        "**Odds API / publish / R2 / merge:** OFF  ",
        "**2025 lockbox / threshold writes / Best Bets:** untouched  ",
        "",
        "---",
        "",
        "## 1 — Step-4 survivors (current staged state)",
        "",
        f"Gate step counts: `{json.dumps(report['step_counts'])}`",
        "",
        "| rank (step-3 pool) | game_id | matchup | edge |",
        "|-------------------:|--------:|---------|-----:|",
    ]
    for item in report["step4_survivors"]:
        lines.append(
            f"| {item['step3_rank']} | {item['game_id']} | {item['matchup']} | {item['edge']} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 2 — QB coverage per step-4 survivor",
            "",
        ]
    )
    for item in report["step4_survivors"]:
        lines.append(f"### {item['matchup']} (`{item['game_id']}`)")
        lines.append("")
        lines.append(
            f"**Gate:** `qb_status_known={item['qb_status_known']}` "
            f"(`{item['qb_status_source']}`) — "
            f"{'passes' if item['passes_qb_gate'] else 'fails'} step 5"
        )
        lines.append("")
        lines.append(
            "| team | team_id | status | source | event_time | resolves |"
        )
        lines.append("|------|--------:|--------|--------|------------|:--------:|")
        for team in item["teams"]:
            lines.append(
                f"| {team['team']} | {team['team_id']} | "
                f"{team['status'] or '—'} | {team['source_version'] or '—'} | "
                f"{team['event_time'] or '—'} | {'yes' if team['resolves'] else 'no'} |"
            )
        lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## 3 — ORPHAN rows (`manual_v1`, game ∉ current step-4 set)",
            "",
            "Do **not** delete — report only.",
            "",
        ]
    )
    displaced = [r for r in report["orphan_rows"] if r.get("orphan_category") == "s6_displaced"]
    other = [r for r in report["orphan_rows"] if r.get("orphan_category") != "s6_displaced"]
    if displaced:
        lines.append(
            "### S6-displaced (written for old step-4 set — Duquesne @ Air Force, Fordham @ NDSU)"
        )
        lines.append("")
        lines.append("| game_id | team_id | status | source | event_time |")
        lines.append("|--------:|--------:|--------|--------|------------|")
        for row in displaced:
            lines.append(
                f"| {row['game_id']} | {row['team_id']} | {row['status']} | "
                f"{row['source_version']} | {row['event_time']} |"
            )
        lines.append("")
    if other:
        lines.append("### Other (`manual_v1` on games outside current W1 step-4 — week-0 historical)")
        lines.append("")
        lines.append("| game_id | team_id | status | source | event_time |")
        lines.append("|--------:|--------:|--------|--------|------------|")
        for row in other:
            lines.append(
                f"| {row['game_id']} | {row['team_id']} | {row['status']} | "
                f"{row['source_version']} | {row['event_time']} |"
            )
        lines.append("")
    if not report["orphan_rows"]:
        lines.append("_None._")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 4 — Operator lookup worklist",
            "",
            "Teams in the current step-4 set with **no row** or **`status=unknown`** "
            "(does not resolve). Operator writes these by hand — do not infer.",
            "",
            "| game_id | matchup | team | team_id | reason | current status | current source | current event_time |",
            "|--------:|---------|------|--------:|--------|----------------|----------------|-------------------|",
        ]
    )
    for row in report["operator_worklist"]:
        lines.append(
            f"| {row['game_id']} | {row['matchup']} | {row['team']} | {row['team_id']} | "
            f"{row['lookup_reason']} | {row['current_status']} | {row['current_source']} | "
            f"{row['current_event_time']} |"
        )

    lines.extend(["", "---", "", "## STOP AND REPORT", ""])
    if report["future_stamped_rows"]:
        lines.append("### Future-stamped rows (`event_time > as_of`) — LEAK RISK")
        lines.append("")
        lines.append("| game_id | team_id | status | source | event_time |")
        lines.append("|--------:|--------:|--------|--------|------------|")
        for row in report["future_stamped_rows"]:
            lines.append(
                f"| {row['game_id']} | {row['team_id']} | {row['status']} | "
                f"{row['source_version']} | {row['event_time']} |"
            )
        lines.append("")
    else:
        lines.append(
            "**Future-stamped rows:** none — all `qb_status.event_time ≤ as_of`."
        )
        lines.append("")

    step5_matchups = tuple(d["matchup"] for d in report["step5_survivors"])
    if step5_matchups != PRIOR_STEP5_MATCHUPS:
        lines.append("### Step-5 survivor set differs from prior `(Baylor @ Auburn, Tulane @ Duke)`")
        lines.append("")
        lines.append(f"**Current step-5 set:** {', '.join(step5_matchups) or '_(empty)_'}")
        lines.append("")
        lines.append(
            "_No claim on which set is correct — report only._"
        )
        lines.append("")
    else:
        lines.append(
            "**Step-5 survivor set:** unchanged — `(Baylor @ Auburn, Tulane @ Duke)`."
        )
        lines.append("")

    lines.extend(
        [
            "### Step-5 detail (current `manual_v1` rows, no operator additions)",
            "",
        ]
    )
    if report["step5_survivors"]:
        for item in report["step5_survivors"]:
            lines.append(f"- **{item['matchup']}** (`{item['game_id']}`, edge {item['edge']})")
            for team in item["teams"]:
                lines.append(
                    f"  - {team['team']}: `{team['status']}` "
                    f"({team['source_version']}, {team['event_time']})"
                )
    else:
        lines.append("_Empty — no game clears step 5 with current rows._")

    lines.append("")
    lines.extend(
        [
            "---",
            "",
            "## 6 — Post-operator re-run (pending)",
            "",
            "After the operator writes `qb_status` rows for §4 worklist teams, re-run:",
            "",
            "```bash",
            "uv run python scripts/_w1_qb_refresh.py",
            "```",
            "",
            "Then append the new §1–§5 output (step-5 survivor set with full per-game QB detail).",
            "",
        ]
    )
    return "\n".join(lines)


def build_report() -> dict[str, Any]:
    payload = run_analysis(as_of=ANALYSIS_AS_OF)
    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        qb_frame = store.read("qb_status", filters={"season": 2026})

    wp, _ = _load_slate()
    games_by_id = {int(g["game_id"]): g for g in wp["games"]}

    step4_survivors = _step4_with_ranks(payload, games_by_id, qb_frame)
    step4_gids = {int(x["game_id"]) for x in step4_survivors}

    rows = [r for r in payload["betting_rows"] if "edge" in r]
    rows_by_gid = {r["game_id"]: r for r in rows}

    orphan_rows = _orphan_rows(qb_frame, step4_gids, ANALYSIS_AS_OF)
    future_stamped = _future_stamped_rows(qb_frame, ANALYSIS_AS_OF)
    operator_worklist = _operator_worklist(step4_survivors)

    rows_by_gid = {r["game_id"]: r for r in rows}
    final_ids = payload["gate"].get("final_survivors", [])
    step5_survivors = []
    for gid in final_ids:
        game = games_by_id[int(gid)]
        detail = _qb_detail_for_game(game, qb_frame, ANALYSIS_AS_OF)
        detail["edge"] = rows_by_gid[gid]["edge"]
        detail["passes_qb_gate"] = True
        step5_survivors.append(detail)

    return {
        "analysis_as_of": ANALYSIS_AS_OF.isoformat(),
        "step_counts": payload["gate"]["step_counts"],
        "step4_survivors": step4_survivors,
        "step5_survivors": step5_survivors,
        "orphan_rows": orphan_rows,
        "future_stamped_rows": future_stamped,
        "operator_worklist": operator_worklist,
        "manual_v1_row_count_at_as_of": int(
            (
                qb_frame.copy()
                .assign(event_time=lambda df: pd.to_datetime(df["event_time"], utc=True))
                .loc[
                    lambda df: (df["event_time"] <= pd.Timestamp(ANALYSIS_AS_OF))
                    & (df["source_version"].astype(str) == "manual_v1")
                ]
                .shape[0]
            )
        ),
    }


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    report = build_report()
    md = _render_worklist_md(report)
    worklist_path = ARTIFACT_DIR / "worklist.md"
    existing = worklist_path.read_text(encoding="utf-8") if worklist_path.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    worklist_path.write_text(existing + md + "\n", encoding="utf-8")
    (ARTIFACT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(f"wrote {worklist_path}")
    print("step_counts:", report["step_counts"])
    print("operator_worklist rows:", len(report["operator_worklist"]))
    print("orphan rows:", len(report["orphan_rows"]))
    print("future_stamped:", len(report["future_stamped_rows"]))


if __name__ == "__main__":
    main()
