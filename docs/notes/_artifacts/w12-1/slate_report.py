"""W12-1 week-12 slate + week-11 finals."""

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

    print("=== WEEK 11 FINALS CHECK ===")
    w11 = store.read("games", filters={"season": 2026, "week": 11}).copy()
    completed = 0
    with_pts = 0
    bad = []
    for _, r in w11.iterrows():
        is_completed = bool(r.completed) if pd.notna(r.completed) else False
        hp_ok = pd.notna(r.home_points)
        ap_ok = pd.notna(r.away_points)
        if is_completed:
            completed += 1
        if is_completed and hp_ok and ap_ok:
            with_pts += 1
        else:
            home = names.get(int(r.home_team_id), str(r.home_team_id))
            away = names.get(int(r.away_team_id), str(r.away_team_id))
            bad.append(
                f"{away} at {home} completed={is_completed} pts={r.away_points}-{r.home_points}"
            )
    print(f"w11 total={len(w11)} completed_with_points={with_pts}/{len(w11)}")
    if bad:
        print("w11 not fully complete:")
        for line in bad:
            print(" ", line)

    print("=== WEEK 12 SLATE ===")
    games = store.read("games", filters={"season": 2026, "week": 12}).copy()
    games["start_date"] = pd.to_datetime(games["start_date"], utc=True)
    sat_start = datetime(2026, 11, 21, 0, 0, tzinfo=ET).astimezone(UTC)
    tonight_start = datetime(2026, 11, 19, 0, 0, tzinfo=ET).astimezone(UTC)
    tonight_end = datetime(2026, 11, 20, 0, 0, tzinfo=ET).astimezone(UTC)

    print("now_utc", now.isoformat())
    print("now_et", now.astimezone(ET).isoformat())
    print("total", len(games))
    kicked = games.loc[games["start_date"] <= pd.Timestamp(now)]
    print("kickoff_utc<=now", len(kicked))
    earliest = games["start_date"].min()
    print("earliest_et", _fmt_et(earliest))

    before_sat = games.loc[games["start_date"] < pd.Timestamp(sat_start)].sort_values(
        "start_date"
    )
    print("--- every game before Saturday ---")
    for _, r in before_sat.iterrows():
        home = names.get(int(r.home_team_id), str(r.home_team_id))
        away = names.get(int(r.away_team_id), str(r.away_team_id))
        is_completed = bool(r.completed) if pd.notna(r.completed) else False
        final = "final" if is_completed else "not-final"
        hp = r.home_points if pd.notna(r.home_points) else None
        ap = r.away_points if pd.notna(r.away_points) else None
        tonight = (
            pd.Timestamp(tonight_start) <= r.start_date < pd.Timestamp(tonight_end)
        )
        flag = " **TONIGHT**" if tonight else ""
        print(
            f"{_fmt_et(r.start_date)} | {away} at {home} | {final} | "
            f"pts {ap}-{hp} | id={r.game_id}{flag}"
        )


if __name__ == "__main__":
    main()
