"""W11-1 week-11 slate + week-10 finals + rows-per-week 1-11."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.webapp.export import load_teams_frame

ET = ZoneInfo("America/New_York")


def _fmt_et(ts: pd.Timestamp) -> str:
    local = ts.tz_convert(ET)
    return local.strftime("%a %b %d, %I:%M %p ET").replace(" 0", " ")


def main() -> None:
    now = datetime.now(tz=UTC)
    cfg = load_config()
    store = ParquetStore(Path(cfg.paths.staged_dir))
    teams = load_teams_frame(season=2026, config=cfg)
    names = {int(r.team_id): str(r.school) for r in teams.itertuples()}

    games = store.read("games", filters={"season": 2026, "week": 11}).copy()
    games["start_date"] = pd.to_datetime(games["start_date"], utc=True)

    fri_7pm = datetime(2026, 11, 13, 19, 0, tzinfo=ET).astimezone(UTC)
    sat_start = datetime(2026, 11, 14, 0, 0, tzinfo=ET).astimezone(UTC)

    print("=== WEEK 11 SLATE ===")
    print("now_utc", now.isoformat())
    print("now_et", now.astimezone(ET).isoformat())
    print("total", len(games))
    kicked = games.loc[games["start_date"] <= pd.Timestamp(now)]
    print("kickoff_utc<=now", len(kicked))
    earliest = games["start_date"].min()
    print("earliest_et", _fmt_et(earliest))
    early = games.loc[games["start_date"] < pd.Timestamp(fri_7pm)]
    print("before_fri_7pm_et", len(early))
    if len(early):
        print("ALERT: games kick off before Fri Nov 13 7:00 PM ET")

    before_sat = games.loc[games["start_date"] < pd.Timestamp(sat_start)].sort_values(
        "start_date"
    )
    print("--- every game before Saturday ---")
    for _, r in before_sat.iterrows():
        home = names.get(int(r.home_team_id), str(r.home_team_id))
        away = names.get(int(r.away_team_id), str(r.away_team_id))
        completed = bool(r.completed) if pd.notna(r.completed) else False
        final = "final" if completed else "not-final"
        hp = r.home_points if pd.notna(r.home_points) else None
        ap = r.away_points if pd.notna(r.away_points) else None
        flag = " **BEFORE_FRI_7PM**" if r.start_date < pd.Timestamp(fri_7pm) else ""
        print(
            f"{_fmt_et(r.start_date)} | {away} at {home} | {final} | "
            f"pts {ap}-{hp} | id={r.game_id}{flag}"
        )

    print("=== ROWS PER WEEK 1-11 ===")
    for week in range(1, 12):
        counts = {}
        for table in ("games", "plays", "drives", "advanced_box"):
            path = (
                Path(cfg.paths.staged_dir)
                / table
                / "season=2026"
                / f"week={week}"
                / "part.parquet"
            )
            if path.is_file():
                counts[table] = len(pd.read_parquet(path))
            else:
                counts[table] = 0
        print(f"w{week}: {counts}")

    print("=== WEEK 10 FINALS CHECK ===")
    w10 = store.read("games", filters={"season": 2026, "week": 10}).copy()
    bad = []
    for _, r in w10.iterrows():
        completed = bool(r.completed) if pd.notna(r.completed) else False
        hp_ok = pd.notna(r.home_points)
        ap_ok = pd.notna(r.away_points)
        if not (completed and hp_ok and ap_ok):
            home = names.get(int(r.home_team_id), str(r.home_team_id))
            away = names.get(int(r.away_team_id), str(r.away_team_id))
            bad.append(
                {
                    "game_id": r.game_id,
                    "matchup": f"{away} at {home}",
                    "completed": completed,
                    "home_points": r.home_points,
                    "away_points": r.away_points,
                }
            )
    print("week10_total", len(w10))
    print("week10_incomplete_or_null_points", len(bad))
    for b in bad:
        print("BAD", b)


if __name__ == "__main__":
    main()
