"""Tests for recruiting schema allowing CFBD negative points (Phase 2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from ncaa_quant.data.schemas import validate_table


def test_recruiting_schema_allows_small_negative_points() -> None:
    """CFBD 2014/15/16/18 emitted points=-0.04; schema must not drop the season."""
    now = datetime(2026, 8, 7, tzinfo=UTC)
    frame = pd.DataFrame(
        [
            {
                "season": 2014,
                "team_id": 1,
                "rank": 50,
                "points": -0.04,
                "average_rating": 0.7,
                "blue_chip_ratio": 0.1,
                "source_version": "test",
                "event_time": now,
                "ingested_at": now,
            }
        ]
    )
    out = validate_table("recruiting", frame)
    assert float(out.iloc[0]["points"]) == pytest.approx(-0.04)
