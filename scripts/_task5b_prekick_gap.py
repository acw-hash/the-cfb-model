"""Identify historical games with rows but none before kickoff."""

from __future__ import annotations

import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.odds_api import _game_keys_from_games

store = ParquetStore(load_config().paths.staged_dir)
rows: list[dict] = []
for season in (2021, 2022, 2023, 2024):
    games = store.read("games", filters={"season": season})
    teams = store.read("teams", filters={"season": season})
    id_to_school = dict(zip(teams.team_id.astype(int), teams.school.astype(str), strict=False))
    odds = store.read("odds_snapshots", filters={"season": season})
    hist = odds[odds.snapshot_source == "historical"].copy()
    hist["event_time"] = pd.to_datetime(hist["event_time"], utc=True)
    gk = _game_keys_from_games(store, [season])
    gmap = gk.merge(
        games[["game_id", "start_date", "home_team_id", "away_team_id"]],
        on="game_id",
    )
    kicks = dict(zip(gmap.game_key.astype(str), pd.to_datetime(gmap.start_date, utc=True), strict=False))
    meta = {str(r.game_key): r for _, r in gmap.iterrows()}
    for game_key, grp in hist.groupby("game_key"):
        kick = kicks.get(str(game_key))
        if kick is None or pd.isna(kick):
            continue
        pre = grp[grp.event_time < kick]
        if pre.empty:
            r = meta[str(game_key)]
            away = id_to_school.get(int(r.away_team_id))
            home = id_to_school.get(int(r.home_team_id))
            rows.append(
                {
                    "season": season,
                    "game_id": int(r.game_id),
                    "week": int(r.week),
                    "label": f"{away} @ {home}",
                    "n_hist": len(grp),
                    "min_et": str(grp.event_time.min()),
                    "max_et": str(grp.event_time.max()),
                    "kick": str(kick),
                    "dps": ",".join(sorted(grp.decision_point.dropna().unique().tolist())),
                }
            )

print(pd.DataFrame(rows).to_string(index=False))
print("count", len(rows))
