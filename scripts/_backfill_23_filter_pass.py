"""Run Task 14 filter on staged games+advanced (no CFBD)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ratings.diagnostics import filter_health_stats
from ncaa_quant.ratings.state_space import (
    StateSpaceConfig,
    build_game_observations_from_advanced,
    run_filter,
)

SEASONS = list(range(2014, 2026))
OUT = Path("data/tmp/backfill_23_filter")


def main() -> None:
    store = ParquetStore("data/staged")
    games = pd.concat(
        [store.read("games", filters={"season": s}) for s in SEASONS],
        ignore_index=True,
    )
    advanced = pd.concat(
        [store.read("advanced_box", filters={"season": s}) for s in SEASONS],
        ignore_index=True,
    )
    games = games.rename(columns={"home_id": "home_team_id", "away_id": "away_team_id"})
    obs = build_game_observations_from_advanced(advanced, games)
    t0 = time.monotonic()
    result = run_filter(obs, config=StateSpaceConfig(), record_weekly=True)
    elapsed = time.monotonic() - t0
    health = filter_health_stats(result.innovations)
    summary = {
        "seasons_included": SEASONS,
        "n_games": int(len(games)),
        "n_obs": int(len(obs)),
        "elapsed_s": round(elapsed, 2),
        "health": {
            "mean_z": health.mean_z,
            "var_z": health.var_z,
            "n": health.n,
            "misspecified": health.misspecified,
        },
        "history_rows": int(len(result.history)) if result.history is not None else 0,
        "note": (
            "Full 2014-2025 from staged games+advanced; "
            "roster-family season-grain still incomplete"
        ),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
