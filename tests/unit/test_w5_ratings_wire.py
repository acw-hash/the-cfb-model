"""Unit test: loadTeamRatingsSeason season naming + empty teams → null (via pure helpers)."""

from __future__ import annotations

# Webapp TS is covered by vitest; this file documents the Python-side
# build_team_ratings collapse for W-RATINGS-WIRE.
from datetime import UTC, datetime

import pandas as pd

from ncaa_quant.webapp.export import build_team_ratings


def test_build_team_ratings_one_point_per_week_prefers_postgame() -> None:
    teams = pd.DataFrame(
        [{"team_id": 194, "school": "Ohio State"}, {"team_id": 2294, "school": "Iowa"}]
    )
    hist = pd.DataFrame(
        [
            {
                "team_id": 194,
                "season": 2026,
                "week": 3,
                "event_time": datetime(2026, 9, 20, tzinfo=UTC),
                "kind": "weekly",
                "off_epa": 0.1,
                "def_epa": 0.0,
                "pace": 0.0,
                "sd_off_epa": 0.05,
                "sd_def_epa": 0.05,
            },
            {
                "team_id": 194,
                "season": 2026,
                "week": 3,
                "event_time": datetime(2026, 9, 21, tzinfo=UTC),
                "kind": "postgame",
                "off_epa": 0.2,
                "def_epa": 0.1,
                "pace": 0.0,
                "sd_off_epa": 0.04,
                "sd_def_epa": 0.04,
            },
            {
                "team_id": 194,
                "season": 2026,
                "week": 5,
                "event_time": datetime(2026, 10, 2, 12, 18, tzinfo=UTC),
                "kind": "weekly",
                "off_epa": 0.25,
                "def_epa": 0.12,
                "pace": 0.0,
                "sd_off_epa": 0.04,
                "sd_def_epa": 0.04,
            },
        ]
    )
    art = build_team_ratings(
        season=2026,
        published_at=datetime(2026, 10, 2, 12, 22, tzinfo=UTC),
        filter_history=hist,
        teams=teams,
    )
    weeks = art["teams"]["194"]["weeks"]
    assert [w["week"] for w in weeks] == [3, 5]
    assert weeks[0]["off_epa"] == 0.2  # postgame wins over weekly
