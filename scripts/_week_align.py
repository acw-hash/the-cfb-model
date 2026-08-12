"""WEEK-ALIGN-FIX — CFBD-week ↔ decision-point calendar from kickoffs.

Phases:
  step1   — build mapping by construction; count tuesday-before-kickoff violations
  step2   — ladder fallback distribution (tuesday / saturday / slot_close / null)
  all     — step1 → step2

Does not widen the ATS guard band. Does not touch the lockbox.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    WeekDecisionCalendar,
    week_decision_as_of,
)
from ncaa_quant.features.market_lines import feature_as_of_for_game, slot_close_instant

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "data" / "staged"
ART = ROOT / "docs" / "notes" / "_artifacts" / "week_align_fix"
SEASONS = (2021, 2022, 2023, 2024)


def _ensure_art() -> None:
    ART.mkdir(parents=True, exist_ok=True)


def step1_acceptance(games: pd.DataFrame | None = None) -> dict[str, Any]:
    """Count games whose mapped tuesday is not strictly before kickoff."""
    _ensure_art()
    if games is None:
        games = load_staged_games(STAGED, list(SEASONS))
        games = games.loc[games["season"].astype(int).between(2021, 2024)].copy()
    calendar = WeekDecisionCalendar.from_games(games)
    work = games.copy()
    work["kick"] = pd.to_datetime(work["event_time"], utc=True)
    violations: list[dict[str, Any]] = []
    n_ok = 0
    for r in work.itertuples(index=False):
        season = int(r.season)
        week = int(r.week)
        kick = pd.Timestamp(r.kick).to_pydatetime()
        pts = calendar.get(season, week)
        assert pts is not None
        tue = pts.tuesday_0600_et
        if tue < kick:
            n_ok += 1
            continue
        violations.append(
            {
                "season": season,
                "week": week,
                "game_id": int(r.game_id),
                "home_team": str(getattr(r, "home_team", "") or ""),
                "away_team": str(getattr(r, "away_team", "") or ""),
                "kickoff": kick.isoformat(),
                "tuesday_0600_et": tue.isoformat(),
                "saturday_0600_et": pts.saturday_0600_et.isoformat(),
                "resolved_decision_point": _resolve_name_for_exception(
                    kick, tue, pts.saturday_0600_et
                ),
            }
        )
    cal_rows = []
    for (season, week), pts in calendar.items():
        cal_rows.append(
            {
                "season": season,
                "week": week,
                "modal_et_monday": pts.modal_et_monday.date().isoformat(),
                "tuesday_0600_et": pts.tuesday_0600_et.isoformat(),
                "saturday_0600_et": pts.saturday_0600_et.isoformat(),
            }
        )
    out = {
        "seasons": list(SEASONS),
        "n_games": int(len(work)),
        "n_ok_tuesday_before_kickoff": n_ok,
        "n_violations": len(violations),
        "violations": violations,
        "calendar": cal_rows,
        "construction": (
            "Per (season, CFBD week): modal America/New_York Monday among that "
            "week's kickoffs → tuesday_0600_et / saturday_0600_et via zoneinfo"
        ),
    }
    (ART / "step1_mapping.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"STEP1 n_games={out['n_games']} ok={n_ok} violations={len(violations)}")
    for v in violations:
        print(
            f"  EXCEPTION season={v['season']} week={v['week']} "
            f"game_id={v['game_id']} {v['away_team']}@{v['home_team']} "
            f"kick={v['kickoff']} tue={v['tuesday_0600_et']} "
            f"resolved={v['resolved_decision_point']}"
        )
    return out


def _resolve_name_for_exception(
    kick: datetime,
    tuesday: datetime,
    saturday: datetime,
) -> str:
    candidates: list[tuple[str, datetime]] = [
        ("tuesday_0600_et", tuesday),
        ("saturday_0600_et", saturday),
        ("slot_close", slot_close_instant(kick)),
    ]
    qualified = [(n, t) for n, t in candidates if t < kick]
    if not qualified:
        return "null"
    return max(qualified, key=lambda x: x[1])[0]


def step2_fallback_distribution(games: pd.DataFrame | None = None) -> dict[str, Any]:
    """Per-season counts of feature as-of slot: tuesday / saturday / slot_close / null."""
    _ensure_art()
    if games is None:
        games = load_staged_games(STAGED, list(SEASONS))
        games = games.loc[games["season"].astype(int).between(2021, 2024)].copy()
    calendar = WeekDecisionCalendar.from_games(games)
    work = games.copy()
    work["kick"] = pd.to_datetime(work["event_time"], utc=True)
    cfg = WalkForwardConfig()
    rows: list[dict[str, Any]] = []
    for r in work.itertuples(index=False):
        season = int(r.season)
        week = int(r.week)
        kick = pd.Timestamp(r.kick).to_pydatetime()
        week_ao = week_decision_as_of(season, week, cfg, calendar=calendar)
        pts = calendar.get(season, week)
        assert pts is not None
        feat = feature_as_of_for_game(
            kick,
            week_ao,
            saturday_0600_et=pts.saturday_0600_et,
        )
        slot = "null"
        if feat is not None:
            tue = pts.tuesday_0600_et
            sat = pts.saturday_0600_et
            sc = slot_close_instant(kick)
            if abs((feat - tue).total_seconds()) < 1:
                slot = "tuesday"
            elif abs((feat - sat).total_seconds()) < 1:
                slot = "saturday"
            elif abs((feat - sc).total_seconds()) < 1:
                slot = "slot_close"
            elif abs((feat - week_ao).total_seconds()) < 1:
                slot = "tuesday"
            else:
                slot = "other"
        rows.append(
            {
                "season": season,
                "week": week,
                "game_id": int(r.game_id),
                "slot": slot,
                "week_as_of": week_ao.isoformat(),
                "feature_as_of": feat.isoformat() if feat is not None else None,
                "kickoff": kick.isoformat(),
            }
        )
    frame = pd.DataFrame(rows)
    by_season: dict[str, Any] = {}
    for season, sg in frame.groupby("season"):
        counts = sg["slot"].value_counts().to_dict()
        n = int(len(sg))
        by_season[str(int(season))] = {
            "n_games": n,
            "tuesday": int(counts.get("tuesday", 0)),
            "saturday": int(counts.get("saturday", 0)),
            "slot_close": int(counts.get("slot_close", 0)),
            "null": int(counts.get("null", 0)),
            "other": int(counts.get("other", 0)),
            "pct_slot_close": round(100.0 * int(counts.get("slot_close", 0)) / n, 3) if n else 0.0,
        }
    total_counts = frame["slot"].value_counts().to_dict()
    n_total = int(len(frame))
    out = {
        "seasons": list(SEASONS),
        "n_games": n_total,
        "totals": {
            "tuesday": int(total_counts.get("tuesday", 0)),
            "saturday": int(total_counts.get("saturday", 0)),
            "slot_close": int(total_counts.get("slot_close", 0)),
            "null": int(total_counts.get("null", 0)),
            "other": int(total_counts.get("other", 0)),
            "pct_slot_close": (
                round(100.0 * int(total_counts.get("slot_close", 0)) / n_total, 3)
                if n_total
                else 0.0
            ),
        },
        "by_season": by_season,
    }
    (ART / "step2_fallback_distribution.json").write_text(
        json.dumps(out, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"STEP2 totals tuesday={out['totals']['tuesday']} "
        f"saturday={out['totals']['saturday']} "
        f"slot_close={out['totals']['slot_close']} "
        f"null={out['totals']['null']} "
        f"pct_slot_close={out['totals']['pct_slot_close']}"
    )
    for season, s in by_season.items():
        print(
            f"  {season}: tue={s['tuesday']} sat={s['saturday']} "
            f"slot_close={s['slot_close']} null={s['null']} "
            f"pct_sc={s['pct_slot_close']}"
        )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=("step1", "step2", "all"),
        help="Which phase to run",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    games = load_staged_games(STAGED, list(SEASONS))
    games = games.loc[games["season"].astype(int).between(2021, 2024)].copy()
    if args.phase in {"step1", "all"}:
        step1_acceptance(games)
    if args.phase in {"step2", "all"}:
        step2_fallback_distribution(games)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
