"""W9-I week-1 verification. No predict, no R2, no 2025 metrics."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.utils.timeutils import season_of, week_of
from ncaa_quant.webapp.push import CFBD_GAME_ID_PATTERN

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent / "week1_verify.json"
ID_RE = re.compile(CFBD_GAME_ID_PATTERN.pattern)


def main() -> None:
    staged = ROOT / "data" / "staged"
    games_root = staged / "games" / "season=2026"
    teams_path = staged / "teams" / "season=2026" / "part.parquet"
    venues_path = staged / "venues" / "season=2026" / "part.parquet"

    week_dirs = sorted(p.name for p in games_root.glob("week=*")) if games_root.is_dir() else []
    by_week: dict[str, int] = {}
    frames: list[pd.DataFrame] = []
    if games_root.is_dir():
        for wdir in sorted(games_root.glob("week=*")):
            part = wdir / "part.parquet"
            if part.is_file():
                df = pd.read_parquet(part)
                frames.append(df)
                by_week[str(int(str(wdir.name).split("=")[1]))] = int(len(df))

    all_games = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    w0 = all_games.loc[all_games["week"].astype(int) == 0] if not all_games.empty else pd.DataFrame()
    w1 = all_games.loc[all_games["week"].astype(int) == 1] if not all_games.empty else pd.DataFrame()

    teams = pd.read_parquet(teams_path) if teams_path.is_file() else pd.DataFrame()
    team_ids = set(int(x) for x in teams["team_id"].dropna()) if not teams.empty else set()

    now = datetime.now(tz=UTC)
    ids = [str(int(x)) for x in w1["game_id"].tolist()] if not w1.empty else []
    id_ok = all(bool(ID_RE.fullmatch(g)) for g in ids)

    kick = None
    tz_notes: list[str] = []
    if not w1.empty:
        et = pd.to_datetime(w1["event_time"], utc=True, errors="coerce")
        sd = (
            pd.to_datetime(w1["start_date"], utc=True, errors="coerce")
            if "start_date" in w1.columns
            else et
        )
        kick = {
            "start_date_min": str(sd.min()) if sd.notna().any() else None,
            "start_date_max": str(sd.max()) if sd.notna().any() else None,
            "n_null_start_date": int(sd.isna().sum()),
            "n_kickoff_future": int((sd > now).sum()),
            "n_kickoff_past": int((sd <= now).sum()),
            "start_date_tz": "UTC",
            "event_time_is_ingest_clamp_for_unplayed": bool(
                (et.nunique(dropna=True) == 1) and bool((~w1["completed"].astype(bool)).all())
            ),
            "event_time_min": str(et.min()) if et.notna().any() else None,
            "event_time_max": str(et.max()) if et.notna().any() else None,
        }
        tz_notes.append(
            "kickoff = start_date (UTC). Unplayed event_time clamped to ingested_at (DESIGN §8)."
        )

    unresolved_home = []
    unresolved_away = []
    if not w1.empty and team_ids:
        for r in w1.itertuples(index=False):
            hid = int(r.home_team_id)
            aid = int(r.away_team_id)
            if hid not in team_ids:
                unresolved_home.append({"game_id": int(r.game_id), "home_team_id": hid})
            if aid not in team_ids:
                unresolved_away.append({"game_id": int(r.game_id), "away_team_id": aid})

    cfg = load_config()
    payload = {
        "inspected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_end_season": cfg.data.end_season,
        "season_of_now": season_of(now),
        "week_of_now": week_of(now, season_of(now)),
        "publish_flow_default_week": 1,
        "week_dirs": week_dirs,
        "n_by_week": by_week,
        "week0": {
            "exists": games_root.joinpath("week=0", "part.parquet").is_file(),
            "n": int(len(w0)),
        },
        "week1": {
            "n": int(len(w1)),
            "date_range": kick,
            "five_ids": ids[:5],
            "five_kickoffs_utc": (
                [str(x) for x in pd.to_datetime(w1["start_date"], utc=True).head(5).tolist()]
                if not w1.empty
                else []
            ),
            "all_ids_match_cfbd_shape": id_ok,
            "pattern": ID_RE.pattern,
            "n_ids_checked": len(ids),
            "n_ids_failing": int(sum(1 for g in ids if not ID_RE.fullmatch(g))),
            "unresolved_home": unresolved_home[:20],
            "unresolved_away": unresolved_away[:20],
            "n_unresolved_home": len(unresolved_home),
            "n_unresolved_away": len(unresolved_away),
            "season_type": (
                {str(k): int(v) for k, v in w1["season_type"].astype(str).value_counts().items()}
                if not w1.empty and "season_type" in w1.columns
                else {}
            ),
        },
        "teams_2026": {
            "exists": teams_path.is_file(),
            "n": int(len(teams)),
        },
        "venues_2026": {
            "exists": venues_path.is_file(),
            "n": int(len(pd.read_parquet(venues_path))) if venues_path.is_file() else 0,
        },
        "tz_notes": tz_notes,
    }
    OUT.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
