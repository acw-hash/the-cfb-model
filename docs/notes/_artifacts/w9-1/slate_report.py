"""W9-1 week-9 slate + week-8 finals + rows-per-week."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.webapp.export import load_teams_frame

ET = ZoneInfo("America/New_York")


def main() -> None:
    now = datetime.now(tz=UTC)
    cfg = load_config()
    store = ParquetStore(Path(cfg.paths.staged_dir))
    teams = load_teams_frame(season=2026, config=cfg)
    names = {int(r.team_id): str(r.school) for r in teams.itertuples()}

    games = store.read("games", filters={"season": 2026, "week": 9}).copy()
    games["start_date"] = pd.to_datetime(games["start_date"], utc=True)

    print("=== WEEK 9 SLATE ===")
    print("now_utc", now.isoformat())
    print("total", len(games))
    kicked = games.loc[games["start_date"] <= pd.Timestamp(now)]
    print("kickoff_utc<=now", len(kicked))
    earliest = games["start_date"].min()
    print(
        "earliest_kickoff_ET",
        earliest.tz_convert(ET).strftime("%a %b %d, %I:%M %p ET"),
    )

    wed6 = pd.Timestamp("2026-10-28 18:00:00", tz=ET)
    sat = pd.Timestamp("2026-10-31 00:00:00", tz=ET)
    before_sat = games.loc[games["start_date"].dt.tz_convert(ET) < sat]
    before_wed6 = games.loc[games["start_date"].dt.tz_convert(ET) < wed6]
    print("before_saturday_count", len(before_sat))
    print("before_wednesday_6pm_ET_count", len(before_wed6))
    if len(before_wed6):
        print("ALERT: games kick off before Wednesday 6 PM ET")
    print("--- every game before Saturday ---")
    for _, r in before_sat.sort_values("start_date").iterrows():
        et = r["start_date"].tz_convert(ET)
        home = names.get(int(r["home_team_id"]), str(r["home_team_id"]))
        away = names.get(int(r["away_team_id"]), str(r["away_team_id"]))
        flag = " BEFORE_WED_6PM" if et < wed6 else ""
        print(
            f"{et.strftime('%a %b %d %I:%M %p ET')} | {away} at {home} | "
            f"completed={r['completed']}{flag}"
        )

    print("\n=== WEEK 8 FINALS ===")
    w8 = store.read("games", filters={"season": 2026, "week": 8}).copy()
    print("week8_rows", len(w8))
    bad = w8.loc[
        (w8["completed"] != True)  # noqa: E712
        | w8["home_points"].isna()
        | w8["away_points"].isna()
    ]
    print("not_completed_or_null_points", len(bad))
    for _, r in bad.iterrows():
        home = names.get(int(r["home_team_id"]), str(r["home_team_id"]))
        away = names.get(int(r["away_team_id"]), str(r["away_team_id"]))
        print(
            r["game_id"],
            f"{away} at {home}",
            "completed=",
            r["completed"],
            "hp=",
            r["home_points"],
            "ap=",
            r["away_points"],
        )

    print("\n=== ROWS PER WEEK 1-9 ===")
    allg = store.read("games", filters={"season": 2026})
    for w in range(1, 10):
        print(f"games w{w}", int((allg["week"] == w).sum()))
    for table in ("plays", "drives", "advanced_box"):
        try:
            df = store.read(table, filters={"season": 2026})
        except Exception as exc:  # noqa: BLE001
            print(table, "ERR", exc)
            continue
        print(f"--- {table} ---")
        for w in range(1, 10):
            print(f"  w{w}", int((df["week"] == w).sum()))


if __name__ == "__main__":
    main()
