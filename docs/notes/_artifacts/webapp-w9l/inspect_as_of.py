"""W9-L Amendment 1: D1 2024-w5 as_of confirm + D2 v3 week-1 straddle evidence.

Read-only. No R2, no lake write, no fit.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.evaluation.backtest_runner import (
    load_backtest_config,
    load_staged_games,
    walkforward_config_from_mapping,
)
from ncaa_quant.evaluation.walkforward import WeekDecisionCalendar, week_decision_as_of
from ncaa_quant.ingestion.cfbd import GAME_DURATION

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent
V3_PRED = ROOT / "data" / "backtests" / "task23_fundamental_reduced_v3" / "full" / "predictions.parquet"
EXPECTED_2024_W5 = datetime(2024, 9, 24, 10, 0, tzinfo=UTC)


def _iso(ts: Any) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t.isoformat()


def main() -> None:
    cfg_app = load_config()
    staged = Path(cfg_app.paths.staged_dir)
    payload = load_backtest_config("task23_fundamental_full_reduced_v3")
    wf = walkforward_config_from_mapping(payload)

    games_hist = load_staged_games(staged, (2021, 2022, 2023, 2024))
    games_2026 = load_staged_games(staged, (2026,))

    # --- D1: 2024 week 5 from staged event_time (calendar unchanged) ---
    w5 = games_hist.loc[
        (games_hist["season"].astype(int) == 2024) & (games_hist["week"].astype(int) == 5)
    ].copy()
    cal_w5 = WeekDecisionCalendar.from_games(w5)
    as_of_w5 = week_decision_as_of(2024, 5, wf, calendar=cal_w5)
    pts_w5 = cal_w5.get(2024, 5)
    d1 = {
        "n_games": int(len(w5)),
        "completed_true": int(w5["completed"].astype(bool).sum()) if "completed" in w5.columns else None,
        "event_time_min": _iso(w5["event_time"].min()),
        "event_time_max": _iso(w5["event_time"].max()),
        "start_date_min": _iso(w5["start_date"].min()),
        "start_date_max": _iso(w5["start_date"].max()),
        "as_of": as_of_w5.isoformat(),
        "expected": EXPECTED_2024_W5.isoformat(),
        "matches_expected": as_of_w5 == EXPECTED_2024_W5,
        "modal_et_monday": pts_w5.modal_et_monday.isoformat() if pts_w5 is not None else None,
        "tuesday": pts_w5.tuesday_0600_et.isoformat() if pts_w5 is not None else None,
    }
    print("D1_2024_WEEK5=" + json.dumps(d1, sort_keys=True))
    if as_of_w5 != EXPECTED_2024_W5:
        raise SystemExit(f"historical as_of moved: {as_of_w5.isoformat()} != {EXPECTED_2024_W5.isoformat()}")

    # --- D2: v3 walk-forward stamped as_of for week-1 2021-2024 ---
    pred = pd.read_parquet(V3_PRED)
    pred["as_of"] = pd.to_datetime(pred["as_of"], utc=True)
    w1 = pred.loc[(pred["week"].astype(int) == 1) & (pred["season"].astype(int).isin([2021, 2022, 2023, 2024]))].copy()

    games_hist["game_id"] = games_hist["game_id"].astype("int64")
    w1["game_id"] = w1["game_id"].astype("int64")
    meta = games_hist[["game_id", "season", "week", "start_date", "event_time", "completed"]].drop_duplicates(
        "game_id"
    )
    joined = w1.merge(meta, on="game_id", how="left", suffixes=("", "_g"))
    joined["kickoff"] = pd.to_datetime(joined["start_date"], utc=True)

    seasons: list[dict[str, Any]] = []
    early_rows: list[dict[str, Any]] = []
    for season, sg in joined.groupby(joined["season"].astype(int), sort=True):
        as_ofs = sorted({_iso(x) for x in sg["as_of"]})
        kick = sg["kickoff"]
        ao = sg["as_of"]
        n_early = int((kick < ao).sum())
        n_as_of_after_kick = n_early
        seasons.append(
            {
                "season": int(season),
                "n_pred_rows": int(len(sg)),
                "n_unique_as_of": len(as_ofs),
                "as_of_values": as_ofs,
                "n_kickoff_before_as_of": n_early,
                "n_kickoff_on_or_after_as_of": int((kick >= ao).sum()),
                "kickoff_min": _iso(kick.min()) if kick.notna().any() else None,
                "kickoff_max": _iso(kick.max()) if kick.notna().any() else None,
            }
        )
        if n_as_of_after_kick:
            sub = sg.loc[kick < ao].sort_values("kickoff")
            for r in sub.itertuples(index=False):
                early_rows.append(
                    {
                        "season": int(r.season),
                        "game_id": int(r.game_id),
                        "kickoff": _iso(r.kickoff),
                        "as_of": _iso(r.as_of),
                        "home_team_id": int(getattr(r, "home_team_id", 0) or 0)
                        if hasattr(r, "home_team_id")
                        else None,
                    }
                )

    n_unique_global = int(joined.groupby(joined["season"].astype(int))["as_of"].nunique().max())
    policy = "single_week_as_of" if n_unique_global == 1 else "per_game_or_mixed"
    d2 = {
        "policy": policy,
        "source": str(V3_PRED.as_posix()),
        "n_week1_rows_2021_2024": int(len(joined)),
        "max_unique_as_of_within_season": n_unique_global,
        "seasons": seasons,
        "n_early_kickoff_rows": len(early_rows),
        "early_kickoff_sample": early_rows[:30],
        "note": (
            "WalkForwardHarness stamps one week_decision_as_of per (season, week) "
            "onto every prediction row (walkforward.py). Per-game slot_close lives "
            "only in resolve_lines_for_games / feature_as_of_for_game (market path). "
            "Champion v3 is fundamental (market_features=False)."
        ),
    }
    print("D2_V3_WEEK1=" + json.dumps({k: d2[k] for k in d2 if k != "early_kickoff_sample"}))
    print("D2_EARLY_SAMPLE=" + json.dumps(early_rows[:12], indent=2))

    # --- 2026 week 1 calendar under D1 semantics (in-memory; lake still clamped) ---
    live = games_2026.loc[games_2026["week"].astype(int) == 1].copy()
    live["start_date"] = pd.to_datetime(live["start_date"], utc=True)
    live["event_time"] = pd.to_datetime(live["event_time"], utc=True)
    unclamped = live.copy()
    unclamped["event_time"] = unclamped["start_date"] + GAME_DURATION
    cal_clamped = WeekDecisionCalendar.from_games(live)
    cal_unclamped = WeekDecisionCalendar.from_games(unclamped)
    cal_start = live.copy()
    cal_start["event_time"] = cal_start["start_date"]
    cal_kick = WeekDecisionCalendar.from_games(cal_start)
    as_clamped = week_decision_as_of(2026, 1, wf, calendar=cal_clamped)
    as_unclamped = week_decision_as_of(2026, 1, wf, calendar=cal_unclamped)
    as_kick = week_decision_as_of(2026, 1, wf, calendar=cal_kick)
    kicks = live["start_date"]
    d2_2026 = {
        "n": int(len(live)),
        "staged_event_time_nunique": int(live["event_time"].nunique()),
        "staged_event_eq_ingested": int((live["event_time"] == pd.to_datetime(live["ingested_at"], utc=True)).sum())
        if "ingested_at" in live.columns
        else None,
        "as_of_staged_clamped_event_time": as_clamped.isoformat(),
        "as_of_unclamped_kickoff_plus_duration": as_unclamped.isoformat(),
        "as_of_from_start_date": as_kick.isoformat(),
        "n_kickoff_before_unclamped_as_of": int((kicks < pd.Timestamp(as_unclamped)).sum()),
        "n_kickoff_before_start_date_as_of": int((kicks < pd.Timestamp(as_kick)).sum()),
        "kickoff_min": _iso(kicks.min()),
        "kickoff_max": _iso(kicks.max()),
        "early_kickoffs": [
            {"game_id": int(r.game_id), "kickoff": _iso(r.start_date)}
            for r in live.loc[kicks < pd.Timestamp(as_unclamped)]
            .sort_values("start_date")
            .itertuples(index=False)
        ],
    }
    print("D2_2026_WEEK1_CAL=" + json.dumps(d2_2026, indent=2, default=str))

    out = {"d1_2024_week5": d1, "d2_v3_week1": d2, "d2_2026_week1_calendar": d2_2026}
    (ART / "as_of_inspect.json").write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wrote {ART / 'as_of_inspect.json'}")


if __name__ == "__main__":
    main()
