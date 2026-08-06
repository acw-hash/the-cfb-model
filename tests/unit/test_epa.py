"""Tests for EPA normalization, garbage-time filter, and aggregations (Task 8)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from ncaa_quant.features.epa import (
    CONNELLY_MARGIN_BY_PERIOD,
    SUCCESS_FRAC_BY_DOWN,
    UniformWeighting,
    aggregate_efficiency,
    apply_garbage_time,
    classify_play_type,
    connelly_garbage_time,
    filter_garbage_time,
    garbage_time_summary,
    is_havoc_play,
    is_successful_play,
    load_season_plays_from_cfbd_raw,
    normalize_epa_plays,
    plays_from_cfbd_raw_json,
    wp_garbage_time,
)

# ---------------------------------------------------------------------------
# Hand-labeled garbage-time fixture (~30 plays, 3 games)
# ---------------------------------------------------------------------------
# Labels follow §4.2: WP outside (0.02, 0.98) when present; else Connelly
# margin-by-quarter (Q1>28, Q2>24, Q3>21, Q4>16).


def _gt_fixture() -> pd.DataFrame:
    """30 hand-labeled plays across 3 games with expected ``garbage_time``."""
    rows: list[dict[str, object]] = [
        # Game A — WP primary rule
        {
            "game_id": 1,
            "play_id": 101,
            "offense": "A",
            "defense": "B",
            "play_type": "Rush",
            "down": 1,
            "distance": 10,
            "yards_gained": 4,
            "yards_to_goal": 60,
            "period": 1,
            "clock": {"minutes": 12, "seconds": 0},
            "epa": 0.1,
            "wp_before": 0.55,
            "offense_score": 0,
            "defense_score": 0,
            "expected_gt": False,
            "expected_rule": "wp",
        },
        {
            "game_id": 1,
            "play_id": 102,
            "offense": "A",
            "defense": "B",
            "play_type": "Pass Reception",
            "down": 2,
            "distance": 6,
            "yards_gained": 5,
            "yards_to_goal": 56,
            "period": 2,
            "clock": 400,
            "epa": 0.2,
            "wp_before": 0.99,
            "offense_score": 28,
            "defense_score": 0,
            "expected_gt": True,
            "expected_rule": "wp",
        },
        {
            "game_id": 1,
            "play_id": 103,
            "offense": "B",
            "defense": "A",
            "play_type": "Rush",
            "down": 1,
            "distance": 10,
            "yards_gained": 2,
            "yards_to_goal": 75,
            "period": 3,
            "clock": 500,
            "epa": -0.3,
            "wp_before": 0.01,
            "offense_score": 0,
            "defense_score": 35,
            "expected_gt": True,
            "expected_rule": "wp",
        },
        {
            "game_id": 1,
            "play_id": 104,
            "offense": "A",
            "defense": "B",
            "play_type": "Pass Incompletion",
            "down": 3,
            "distance": 8,
            "yards_gained": 0,
            "yards_to_goal": 40,
            "period": 4,
            "clock": 120,
            "epa": -0.5,
            "wp_before": 0.02,
            "offense_score": 21,
            "defense_score": 17,
            "expected_gt": False,  # boundary: < 0.02 is GT; == 0.02 is not
            "expected_rule": "wp",
        },
        {
            "game_id": 1,
            "play_id": 105,
            "offense": "A",
            "defense": "B",
            "play_type": "Rushing Touchdown",
            "down": 2,
            "distance": 5,
            "yards_gained": 5,
            "yards_to_goal": 5,
            "period": 4,
            "clock": 60,
            "epa": 2.0,
            "wp_before": 0.98,
            "offense_score": 24,
            "defense_score": 17,
            "expected_gt": False,  # boundary: > 0.98 is GT; == 0.98 is not
            "expected_rule": "wp",
        },
        {
            "game_id": 1,
            "play_id": 106,
            "offense": "B",
            "defense": "A",
            "play_type": "Sack",
            "down": 3,
            "distance": 7,
            "yards_gained": -6,
            "yards_to_goal": 50,
            "period": 4,
            "clock": 30,
            "epa": -1.2,
            "wp_before": 0.005,
            "offense_score": 17,
            "defense_score": 31,
            "expected_gt": True,
            "expected_rule": "wp",
        },
        {
            "game_id": 1,
            "play_id": 107,
            "offense": "A",
            "defense": "B",
            "play_type": "Penalty",
            "down": 1,
            "distance": 15,
            "yards_gained": -5,
            "yards_to_goal": 55,
            "period": 1,
            "clock": 600,
            "epa": None,
            "wp_before": 0.50,
            "offense_score": 0,
            "defense_score": 0,
            "expected_gt": False,
            "expected_rule": "wp",
        },
        {
            "game_id": 1,
            "play_id": 108,
            "offense": "A",
            "defense": "B",
            "play_type": "Kickoff",
            "down": 0,
            "distance": 0,
            "yards_gained": 25,
            "yards_to_goal": 65,
            "period": 1,
            "clock": 900,
            "epa": 0.0,
            "wp_before": 0.50,
            "offense_score": 0,
            "defense_score": 0,
            "expected_gt": False,
            "expected_rule": "wp",
        },
        {
            "game_id": 1,
            "play_id": 109,
            "offense": "B",
            "defense": "A",
            "play_type": "Pass Reception",
            "down": 1,
            "distance": 10,
            "yards_gained": 12,
            "yards_to_goal": 40,
            "period": 2,
            "clock": 300,
            "epa": 0.8,
            "wp_before": 0.40,
            "offense_score": 7,
            "defense_score": 14,
            "expected_gt": False,
            "expected_rule": "wp",
        },
        {
            "game_id": 1,
            "play_id": 110,
            "offense": "A",
            "defense": "B",
            "play_type": "Interception",
            "down": 2,
            "distance": 8,
            "yards_gained": 0,
            "yards_to_goal": 45,
            "period": 3,
            "clock": 200,
            "epa": -4.0,
            "wp_before": 0.981,
            "offense_score": 28,
            "defense_score": 7,
            "expected_gt": True,
            "expected_rule": "wp",
        },
        # Game B — Connelly fallback (WP null)
        {
            "game_id": 2,
            "play_id": 201,
            "offense": "C",
            "defense": "D",
            "play_type": "Rush",
            "down": 1,
            "distance": 10,
            "yards_gained": 3,
            "yards_to_goal": 70,
            "period": 1,
            "clock": 800,
            "epa": -0.1,
            "wp_before": None,
            "offense_score": 0,
            "defense_score": 0,
            "expected_gt": False,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 2,
            "play_id": 202,
            "offense": "C",
            "defense": "D",
            "play_type": "Rush",
            "down": 1,
            "distance": 10,
            "yards_gained": 5,
            "yards_to_goal": 50,
            "period": 1,
            "clock": 400,
            "epa": 0.2,
            "wp_before": None,
            "offense_score": 35,
            "defense_score": 0,  # margin 35 > 28
            "expected_gt": True,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 2,
            "play_id": 203,
            "offense": "D",
            "defense": "C",
            "play_type": "Pass Reception",
            "down": 2,
            "distance": 7,
            "yards_gained": 6,
            "yards_to_goal": 60,
            "period": 1,
            "clock": 200,
            "epa": 0.3,
            "wp_before": None,
            "offense_score": 0,
            "defense_score": 28,  # margin 28 is NOT > 28
            "expected_gt": False,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 2,
            "play_id": 204,
            "offense": "C",
            "defense": "D",
            "play_type": "Passing Touchdown",
            "down": 3,
            "distance": 5,
            "yards_gained": 20,
            "yards_to_goal": 20,
            "period": 2,
            "clock": 100,
            "epa": 3.0,
            "wp_before": None,
            "offense_score": 31,
            "defense_score": 0,  # 31 > 24
            "expected_gt": True,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 2,
            "play_id": 205,
            "offense": "D",
            "defense": "C",
            "play_type": "Rush",
            "down": 1,
            "distance": 10,
            "yards_gained": 4,
            "yards_to_goal": 80,
            "period": 2,
            "clock": 500,
            "epa": 0.0,
            "wp_before": None,
            "offense_score": 0,
            "defense_score": 24,  # == 24 not GT
            "expected_gt": False,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 2,
            "play_id": 206,
            "offense": "C",
            "defense": "D",
            "play_type": "Punt",
            "down": 4,
            "distance": 8,
            "yards_gained": 40,
            "yards_to_goal": 60,
            "period": 3,
            "clock": 600,
            "epa": 0.1,
            "wp_before": None,
            "offense_score": 28,
            "defense_score": 6,  # 22 > 21
            "expected_gt": True,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 2,
            "play_id": 207,
            "offense": "D",
            "defense": "C",
            "play_type": "Pass Incompletion",
            "down": 3,
            "distance": 10,
            "yards_gained": 0,
            "yards_to_goal": 55,
            "period": 3,
            "clock": 300,
            "epa": -0.4,
            "wp_before": None,
            "offense_score": 6,
            "defense_score": 27,  # 21 == 21 not GT
            "expected_gt": False,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 2,
            "play_id": 208,
            "offense": "C",
            "defense": "D",
            "play_type": "Rush",
            "down": 2,
            "distance": 5,
            "yards_gained": 2,
            "yards_to_goal": 30,
            "period": 4,
            "clock": 180,
            "epa": -0.2,
            "wp_before": None,
            "offense_score": 35,
            "defense_score": 14,  # 21 > 16
            "expected_gt": True,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 2,
            "play_id": 209,
            "offense": "D",
            "defense": "C",
            "play_type": "Field Goal Good",
            "down": 4,
            "distance": 3,
            "yards_gained": 0,
            "yards_to_goal": 20,
            "period": 4,
            "clock": 10,
            "epa": 1.5,
            "wp_before": None,
            "offense_score": 17,
            "defense_score": 31,  # 14 <= 16 not GT
            "expected_gt": False,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 2,
            "play_id": 210,
            "offense": "C",
            "defense": "D",
            "play_type": "Rush",
            "down": 1,
            "distance": 10,
            "yards_gained": 1,
            "yards_to_goal": 40,
            "period": 5,  # OT → Q4 threshold
            "clock": 0,
            "epa": -0.1,
            "wp_before": None,
            "offense_score": 38,
            "defense_score": 21,  # 17 > 16
            "expected_gt": True,
            "expected_rule": "connelly_fallback",
        },
        # Game C — mixed WP + fallback
        {
            "game_id": 3,
            "play_id": 301,
            "offense": "E",
            "defense": "F",
            "play_type": "Pass Reception",
            "down": 1,
            "distance": 10,
            "yards_gained": 15,
            "yards_to_goal": 50,
            "period": 1,
            "clock": 700,
            "epa": 1.0,
            "wp_before": 0.60,
            "offense_score": 0,
            "defense_score": 0,
            "expected_gt": False,
            "expected_rule": "wp",
        },
        {
            "game_id": 3,
            "play_id": 302,
            "offense": "F",
            "defense": "E",
            "play_type": "Rush",
            "down": 2,
            "distance": 8,
            "yards_gained": 3,
            "yards_to_goal": 65,
            "period": 2,
            "clock": 450,
            "epa": -0.2,
            "wp_before": None,
            "offense_score": 3,
            "defense_score": 14,
            "expected_gt": False,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 3,
            "play_id": 303,
            "offense": "E",
            "defense": "F",
            "play_type": "Fumble Recovery (Opponent)",
            "down": 2,
            "distance": 6,
            "yards_gained": -2,
            "yards_to_goal": 40,
            "period": 3,
            "clock": 250,
            "epa": -3.0,
            "wp_before": 0.70,
            "offense_score": 21,
            "defense_score": 10,
            "expected_gt": False,
            "expected_rule": "wp",
        },
        {
            "game_id": 3,
            "play_id": 304,
            "offense": "F",
            "defense": "E",
            "play_type": "Passing Touchdown",
            "down": 1,
            "distance": 10,
            "yards_gained": 45,
            "yards_to_goal": 45,
            "period": 4,
            "clock": 90,
            "epa": 4.0,
            "wp_before": None,
            "offense_score": 10,
            "defense_score": 35,  # 25 > 16
            "expected_gt": True,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 3,
            "play_id": 305,
            "offense": "E",
            "defense": "F",
            "play_type": "Timeout",
            "down": 0,
            "distance": 0,
            "yards_gained": 0,
            "yards_to_goal": 50,
            "period": 4,
            "clock": 80,
            "epa": None,
            "wp_before": 0.995,
            "offense_score": 35,
            "defense_score": 10,
            "expected_gt": True,
            "expected_rule": "wp",
        },
        {
            "game_id": 3,
            "play_id": 306,
            "offense": "F",
            "defense": "E",
            "play_type": "Pass Incompletion",
            "down": 4,
            "distance": 1,
            "yards_gained": 0,
            "yards_to_goal": 30,
            "period": 4,
            "clock": 40,
            "epa": -0.8,
            "wp_before": 0.15,
            "offense_score": 17,
            "defense_score": 28,
            "expected_gt": False,
            "expected_rule": "wp",
        },
        {
            "game_id": 3,
            "play_id": 307,
            "offense": "E",
            "defense": "F",
            "play_type": "Rush",
            "down": 1,
            "distance": 10,
            "yards_gained": 8,
            "yards_to_goal": 70,
            "period": 1,
            "clock": 850,
            "epa": 0.4,
            "wp_before": None,
            "offense_score": 0,
            "defense_score": 0,
            "expected_gt": False,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 3,
            "play_id": 308,
            "offense": "F",
            "defense": "E",
            "play_type": "Sack",
            "down": 3,
            "distance": 12,
            "yards_gained": -8,
            "yards_to_goal": 55,
            "period": 2,
            "clock": 200,
            "epa": -1.5,
            "wp_before": 0.019,
            "offense_score": 7,
            "defense_score": 21,
            "expected_gt": True,
            "expected_rule": "wp",
        },
        {
            "game_id": 3,
            "play_id": 309,
            "offense": "E",
            "defense": "F",
            "play_type": "Rushing Touchdown",
            "down": 2,
            "distance": 4,
            "yards_gained": 4,
            "yards_to_goal": 4,
            "period": 3,
            "clock": 100,
            "epa": 2.5,
            "wp_before": None,
            "offense_score": 42,
            "defense_score": 14,  # 28 > 21
            "expected_gt": True,
            "expected_rule": "connelly_fallback",
        },
        {
            "game_id": 3,
            "play_id": 310,
            "offense": "F",
            "defense": "E",
            "play_type": "Pass Reception",
            "down": 1,
            "distance": 10,
            "yards_gained": 7,
            "yards_to_goal": 60,
            "period": 4,
            "clock": 200,
            "epa": 0.3,
            "wp_before": 0.45,
            "offense_score": 21,
            "defense_score": 28,
            "expected_gt": False,
            "expected_rule": "wp",
        },
    ]
    assert len(rows) == 30
    assert {r["game_id"] for r in rows} == {1, 2, 3}
    return pd.DataFrame(rows)


def test_garbage_time_hand_labeled_fixture() -> None:
    raw = _gt_fixture()
    expected_gt = raw["expected_gt"].tolist()
    expected_rule = raw["expected_rule"].tolist()
    normalized = normalize_epa_plays(raw.drop(columns=["expected_gt", "expected_rule"]))
    assert len(normalized) == 30
    assert normalized["garbage_time"].tolist() == expected_gt
    assert normalized["gt_rule"].tolist() == expected_rule
    # Fallback fires exactly when WP was null
    assert normalized["gt_fallback_used"].tolist() == [bool(pd.isna(r)) for r in raw["wp_before"]]


def test_filter_drops_garbage_rows() -> None:
    normalized = normalize_epa_plays(_gt_fixture().drop(columns=["expected_gt", "expected_rule"]))
    kept = filter_garbage_time(normalized)
    assert len(kept) == int((~normalized["garbage_time"]).sum())
    assert not kept["garbage_time"].any()


def test_fallback_fires_when_wp_null() -> None:
    frame = pd.DataFrame(
        [
            {
                "game_id": 9,
                "play_id": 1,
                "offense": "X",
                "defense": "Y",
                "play_type": "Rush",
                "down": 1,
                "distance": 10,
                "yards_gained": 3,
                "yards_to_goal": 50,
                "period": 4,
                "offense_score": 35,
                "defense_score": 10,
                "epa": 0.0,
                "wp": None,
            }
        ]
    )
    out = normalize_epa_plays(frame)
    assert bool(out.loc[0, "gt_fallback_used"])
    assert out.loc[0, "gt_rule"] == "connelly_fallback"
    assert bool(out.loc[0, "garbage_time"]) is True


# ---------------------------------------------------------------------------
# Success-rate definition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("down", "distance", "yards", "expected"),
    [
        (1, 10, 5, True),  # exactly 50%
        (1, 10, 4, False),
        (2, 10, 7, True),  # exactly 70%
        (2, 10, 6, False),
        (3, 5, 5, True),  # 100%
        (3, 5, 4, False),
        (4, 2, 2, True),
        (4, 2, 1, False),
        (0, 10, 10, None),  # non-scrimmage down
        (1, None, 5, None),
        (None, 10, 5, None),
        (2, 10, None, None),
    ],
)
def test_success_rate_by_down(
    down: int | None,
    distance: int | None,
    yards: int | None,
    expected: bool | None,
) -> None:
    assert is_successful_play(down, distance, yards) is expected
    if down in SUCCESS_FRAC_BY_DOWN and distance is not None and yards is not None:
        needed = distance * SUCCESS_FRAC_BY_DOWN[down]
        assert (yards >= needed) is expected


# ---------------------------------------------------------------------------
# Aggregation helpers (hand-computed)
# ---------------------------------------------------------------------------


def test_aggregate_efficiency_hand_computed() -> None:
    # Two non-GT plays for team T, one rush success EPA=1.0, one pass fail EPA=-0.5
    plays = pd.DataFrame(
        [
            {
                "game_key": "g1",
                "play_id": 1,
                "game_id": 1,
                "offense_team": "T",
                "defense_team": "O",
                "play_type": "Rush",
                "down": 1,
                "distance": 10,
                "yardline": 50,
                "period": 1,
                "clock": 500,
                "yards_gained": 6,  # success (need 5)
                "epa": 1.0,
                "wp_before": 0.5,
                "wp_after": None,
                "offense_score": 0,
                "defense_score": 0,
            },
            {
                "game_key": "g1",
                "play_id": 2,
                "game_id": 1,
                "offense_team": "T",
                "defense_team": "O",
                "play_type": "Pass Incompletion",
                "down": 3,
                "distance": 4,
                "yardline": 40,
                "period": 1,
                "clock": 400,
                "yards_gained": 0,  # fail
                "epa": -0.5,
                "wp_before": 0.5,
                "wp_after": None,
                "offense_score": 0,
                "defense_score": 0,
            },
            {
                # Garbage-time pass (WP) — must be excluded from aggregates
                "game_key": "g1",
                "play_id": 3,
                "game_id": 1,
                "offense_team": "T",
                "defense_team": "O",
                "play_type": "Pass Reception",
                "down": 1,
                "distance": 10,
                "yardline": 30,
                "period": 4,
                "clock": 50,
                "yards_gained": 20,
                "epa": 2.0,
                "wp_before": 0.99,
                "wp_after": None,
                "offense_score": 40,
                "defense_score": 7,
            },
            {
                # Havoc sack on pass
                "game_key": "g1",
                "play_id": 4,
                "game_id": 1,
                "offense_team": "T",
                "defense_team": "O",
                "play_type": "Sack",
                "down": 2,
                "distance": 8,
                "yardline": 55,
                "period": 2,
                "clock": 300,
                "yards_gained": -7,  # fail (need 5.6)
                "epa": -1.0,
                "wp_before": 0.5,
                "wp_after": None,
                "offense_score": 7,
                "defense_score": 7,
            },
        ]
    )
    normalized = normalize_epa_plays(plays)
    agg = aggregate_efficiency(normalized, ["offense_team"])
    assert len(agg) == 1
    row = agg.iloc[0]
    # Kept plays: 1 (rush), 2 (pass fail), 4 (sack). EPA = (1.0 - 0.5 - 1.0) / 3 = -0.1667
    assert row["offense_team"] == "T"
    assert row["n_plays"] == 3.0
    assert row["epa_per_play"] == pytest.approx((1.0 - 0.5 - 1.0) / 3.0)
    # success: rush yes, pass no, sack no → 1/3
    assert row["success_rate"] == pytest.approx(1.0 / 3.0)
    # explosiveness: mean EPA on successful only → 1.0
    assert row["explosiveness"] == pytest.approx(1.0)
    # havoc: only sack → 1/3
    assert row["havoc_rate"] == pytest.approx(1.0 / 3.0)
    assert row["rush_n_plays"] == 1.0
    assert row["rush_epa_per_play"] == pytest.approx(1.0)
    assert row["rush_success_rate"] == pytest.approx(1.0)
    assert row["pass_n_plays"] == 2.0
    assert row["pass_epa_per_play"] == pytest.approx((-0.5 + -1.0) / 2.0)
    assert row["pass_havoc_rate"] == pytest.approx(0.5)


def test_uniform_weighting_interface() -> None:
    plays = normalize_epa_plays(
        pd.DataFrame(
            [
                {
                    "game_id": 1,
                    "play_id": 1,
                    "offense": "A",
                    "defense": "B",
                    "play_type": "Rush",
                    "down": 1,
                    "distance": 10,
                    "yards_gained": 5,
                    "yards_to_goal": 50,
                    "period": 1,
                    "epa": 0.5,
                    "wp_before": 0.5,
                    "offense_score": 0,
                    "defense_score": 0,
                }
            ]
        )
    )
    w = UniformWeighting().weights(plays)
    assert list(w) == [1.0]
    assert UniformWeighting().name == "uniform"


def test_normalize_maps_staged_aliases() -> None:
    staged = pd.DataFrame(
        [
            {
                "play_id": 99,
                "game_id": 401,
                "offense_id": 10,
                "defense_id": 20,
                "play_type": "Rush",
                "down": 1,
                "distance": 10,
                "yards_to_goal": 45,
                "period": 2,
                "yards_gained": 4,
                "epa": 0.1,
                "wp": 0.55,
            }
        ]
    )
    out = normalize_epa_plays(staged)
    assert out.loc[0, "game_key"] == "401"
    assert out.loc[0, "offense_team"] == "10"
    assert out.loc[0, "yardline"] == 45
    assert out.loc[0, "wp_before"] == pytest.approx(0.55)
    assert pd.isna(out.loc[0, "clock"])
    # no scores → Connelly cannot fire; WP present so rule=wp
    assert out.loc[0, "gt_rule"] == "wp"
    assert bool(out.loc[0, "garbage_time"]) is False


def test_wp_boundary_helpers() -> None:
    assert wp_garbage_time(0.98) is False
    assert wp_garbage_time(0.981) is True
    assert wp_garbage_time(0.02) is False
    assert wp_garbage_time(0.019) is True
    assert wp_garbage_time(None) is False


def test_connelly_thresholds_match_published() -> None:
    assert dict(CONNELLY_MARGIN_BY_PERIOD) == {1: 28, 2: 24, 3: 21, 4: 16}


def test_garbage_time_summary() -> None:
    normalized = normalize_epa_plays(_gt_fixture().drop(columns=["expected_gt", "expected_rule"]))
    summary = garbage_time_summary(normalized)
    assert summary["n_plays"] == 30.0
    assert summary["n_garbage"] == float(normalized["garbage_time"].sum())
    assert summary["garbage_frac"] == pytest.approx(summary["n_garbage"] / 30.0)
    assert summary["n_fallback"] == float(normalized["gt_fallback_used"].sum())
    assert not math.isnan(summary["fallback_frac"])


def test_apply_garbage_time_idempotent_columns() -> None:
    base = normalize_epa_plays(_gt_fixture().drop(columns=["expected_gt", "expected_rule"]))
    again = apply_garbage_time(base)
    assert again["garbage_time"].tolist() == base["garbage_time"].tolist()


def test_normalize_empty_frame() -> None:
    out = normalize_epa_plays(pd.DataFrame())
    assert out.empty
    assert "garbage_time" in out.columns


def test_aggregate_empty_and_no_drop() -> None:
    empty = normalize_epa_plays(pd.DataFrame())
    agg = aggregate_efficiency(empty, ["offense_team"])
    assert agg.empty
    plays = normalize_epa_plays(
        pd.DataFrame(
            [
                {
                    "game_id": 1,
                    "play_id": 1,
                    "offense": "A",
                    "defense": "B",
                    "play_type": "Rush",
                    "down": 1,
                    "distance": 10,
                    "yards_gained": 5,
                    "yards_to_goal": 50,
                    "period": 4,
                    "epa": 1.0,
                    "wp_before": 0.99,
                    "offense_score": 40,
                    "defense_score": 0,
                }
            ]
        )
    )
    kept = aggregate_efficiency(plays, ["offense_team"], drop_garbage=False)
    assert kept.iloc[0]["n_plays"] == 1.0
    dropped = aggregate_efficiency(plays, ["offense_team"], drop_garbage=True)
    assert dropped.empty or dropped.iloc[0]["n_plays"] == 0.0 or len(dropped) == 0


def test_classify_and_havoc_nulls() -> None:
    assert classify_play_type(None) == (False, False, False, False)
    assert classify_play_type(float("nan")) == (False, False, False, False)
    assert is_havoc_play(None) is False
    assert is_havoc_play("Sack") is True
    assert connelly_garbage_time(None, 30) is False
    assert connelly_garbage_time(1, None) is False
    assert connelly_garbage_time(float("nan"), 30) is False  # type: ignore[arg-type]


def test_dirty_down_and_yardline_coerced() -> None:
    out = normalize_epa_plays(
        pd.DataFrame(
            [
                {
                    "game_id": 1,
                    "play_id": 1,
                    "offense": "A",
                    "defense": "B",
                    "play_type": "Rush",
                    "down": 5,
                    "distance": 10,
                    "yards_gained": 3,
                    "yards_to_goal": 150,
                    "period": 0,
                    "epa": 0.0,
                    "wp_before": 0.5,
                    "offense_score": 0,
                    "defense_score": 0,
                }
            ]
        )
    )
    assert pd.isna(out.loc[0, "down"])
    assert pd.isna(out.loc[0, "yardline"])
    assert pd.isna(out.loc[0, "period"])


def test_plays_from_cfbd_raw_json(tmp_path: Path) -> None:
    payload = [
        {
            "id": 1,
            "gameId": 10,
            "offense": "Alabama",
            "defense": "Texas",
            "playType": "Rush",
            "down": 1,
            "distance": 10,
            "yardsGained": 4,
            "yardsToGoal": 60,
            "period": 1,
            "clock": {"minutes": 10, "seconds": 15},
            "ppa": 0.25,
            "offenseScore": 0,
            "defenseScore": 0,
        }
    ]
    path = tmp_path / "plays_s2023_w1_regular.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    out = plays_from_cfbd_raw_json(path)
    assert len(out) == 1
    assert out.loc[0, "offense_team"] == "Alabama"
    assert out.loc[0, "clock"] == 10 * 60 + 15
    assert out.loc[0, "epa"] == pytest.approx(0.25)
    assert out.loc[0, "gt_fallback_used"]


def test_load_season_plays_from_cfbd_raw(tmp_path: Path) -> None:
    raw_root = tmp_path / "cfbd" / "2026-01-01"
    raw_root.mkdir(parents=True)
    (raw_root / "plays_s2023_w1_regular.json").write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "gameId": 10,
                    "offense": "A",
                    "defense": "B",
                    "playType": "Pass Reception",
                    "down": 2,
                    "distance": 8,
                    "yardsGained": 6,
                    "yardsToGoal": 40,
                    "period": 2,
                    "ppa": 0.1,
                    "offenseScore": 7,
                    "defenseScore": 0,
                }
            ]
        ),
        encoding="utf-8",
    )
    out = load_season_plays_from_cfbd_raw(tmp_path / "cfbd", 2023)
    assert len(out) == 1
    assert out.loc[0, "is_pass"]
    with pytest.raises(FileNotFoundError):
        load_season_plays_from_cfbd_raw(tmp_path / "cfbd", 1999)


def test_garbage_time_summary_empty() -> None:
    summary = garbage_time_summary(pd.DataFrame())
    assert summary["n_plays"] == 0.0
    assert math.isnan(summary["garbage_frac"])
