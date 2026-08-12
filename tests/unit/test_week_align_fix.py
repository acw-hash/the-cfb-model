"""WEEK-ALIGN-FIX — CFBD-week decision calendar from kickoffs + DST."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    WeekDecisionCalendar,
    decision_points_from_kickoffs,
    labor_day_week_decision_as_of,
    week_decision_as_of,
)
from ncaa_quant.features.market_lines import feature_as_of_for_game, slot_close_instant
from ncaa_quant.utils.timeutils import resolve_decision_point


def test_decision_points_from_kickoffs_before_saturday_slate() -> None:
    """Modal Monday of a Sat slate → Tuesday 06:00 ET strictly before kickoffs."""
    kicks = [
        datetime(2021, 9, 11, 16, 0, tzinfo=UTC),
        datetime(2021, 9, 11, 19, 30, tzinfo=UTC),
        datetime(2021, 9, 12, 0, 0, tzinfo=UTC),
    ]
    pts = decision_points_from_kickoffs(kicks)
    # Monday 2021-09-06 → Tue 2021-09-07 06:00 EDT = 10:00Z
    assert pts.tuesday_0600_et == datetime(2021, 9, 7, 10, 0, tzinfo=UTC)
    assert pts.saturday_0600_et == datetime(2021, 9, 11, 10, 0, tzinfo=UTC)
    for k in kicks:
        assert pts.tuesday_0600_et < k


def test_week_calendar_shifts_cfbd_week_ahead_of_labor_day() -> None:
    """CFBD week 2 slate (Labor Day week 1 games) maps to Labor-Day week-1 Tuesday."""
    games = pd.DataFrame(
        [
            {
                "season": 2021,
                "week": 2,
                "event_time": datetime(2021, 9, 11, 19, 30, tzinfo=UTC),
                "game_id": 1,
            },
            {
                "season": 2021,
                "week": 2,
                "event_time": datetime(2021, 9, 11, 23, 0, tzinfo=UTC),
                "game_id": 2,
            },
        ]
    )
    cal = WeekDecisionCalendar.from_games(games)
    cfg = WalkForwardConfig()
    aligned = week_decision_as_of(2021, 2, cfg, calendar=cal)
    labor = labor_day_week_decision_as_of(2021, 2, cfg)
    # Labor-Day week 2 Tuesday is after these kickoffs; aligned is not.
    assert labor > datetime(2021, 9, 11, 19, 30, tzinfo=UTC)
    assert aligned == datetime(2021, 9, 7, 10, 0, tzinfo=UTC)
    assert aligned < datetime(2021, 9, 11, 19, 30, tzinfo=UTC)


def test_feature_as_of_uses_aligned_tuesday() -> None:
    cfg = WalkForwardConfig()
    games = pd.DataFrame(
        [
            {
                "season": 2021,
                "week": 2,
                "event_time": datetime(2021, 9, 11, 19, 30, tzinfo=UTC),
                "game_id": 401282809,
            }
        ]
    )
    cal = WeekDecisionCalendar.from_games(games)
    week_ao = week_decision_as_of(2021, 2, cfg, calendar=cal)
    kick = datetime(2021, 9, 11, 19, 30, tzinfo=UTC)
    got = feature_as_of_for_game(kick, week_ao, saturday_0600_et=cal.saturday(2021, 2))
    assert got == week_ao
    assert got < kick


def test_week0_exception_falls_back_to_slot_close() -> None:
    """Week-0 Friday kick before the week's modal Tuesday → slot_close."""
    kick = datetime(2021, 8, 28, 20, 50, tzinfo=UTC)
    # Modal slate is the following Saturday (Sep 4 week) → Tue Aug 31.
    tue = resolve_decision_point("tuesday_0600_et", datetime(2021, 8, 31).date())
    assert kick < tue
    got = feature_as_of_for_game(kick, tue)
    assert got == slot_close_instant(kick)


def test_dst_saturday_across_fall_back_via_kickoff_calendar() -> None:
    """AUDIT-6: Saturday 06:00 ET UTC offset shifts +1h across Nov fall-back."""
    # Week containing Sat Nov 2 2024 (EDT) vs Sat Nov 9 2024 (EST).
    before_kicks = [datetime(2024, 11, 2, 19, 0, tzinfo=UTC)]
    after_kicks = [datetime(2024, 11, 9, 19, 0, tzinfo=UTC)]
    before = decision_points_from_kickoffs(before_kicks).saturday_0600_et
    after = decision_points_from_kickoffs(after_kicks).saturday_0600_et
    assert before == datetime(2024, 11, 2, 10, 0, tzinfo=UTC)  # EDT UTC-4
    assert after == datetime(2024, 11, 9, 11, 0, tzinfo=UTC)  # EST UTC-5
    assert (after - before).total_seconds() == 7 * 24 * 3600 + 3600


def test_dst_tuesday_across_fall_back_via_kickoff_calendar() -> None:
    before_kicks = [datetime(2024, 11, 2, 19, 0, tzinfo=UTC)]
    after_kicks = [datetime(2024, 11, 9, 19, 0, tzinfo=UTC)]
    before = decision_points_from_kickoffs(before_kicks).tuesday_0600_et
    after = decision_points_from_kickoffs(after_kicks).tuesday_0600_et
    assert before == datetime(2024, 10, 29, 10, 0, tzinfo=UTC)
    assert after == datetime(2024, 11, 5, 11, 0, tzinfo=UTC)
    assert (after - before).total_seconds() == 7 * 24 * 3600 + 3600


def test_labor_day_fallback_without_calendar() -> None:
    cfg = WalkForwardConfig()
    as_of = week_decision_as_of(2023, 1, cfg)
    assert as_of == datetime(2023, 9, 5, 10, 0, tzinfo=UTC)
