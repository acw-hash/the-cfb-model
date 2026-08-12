"""ATS-GRADE-FIX — home-side ladder, fixture reproduction, plausibility guard."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.metrics import (
    AtsPlausibilityError,
    assert_ats_vs_close_plausible,
    assert_prediction_ats_plausible,
    ats_home_outcomes,
    ats_plausibility_band,
)
from ncaa_quant.evaluation.walkforward import WalkForwardConfig, resolve_lines_for_games
from ncaa_quant.features.market_lines import filter_home_side_spreads, median_home_spread

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ats_grade_diag_24.json"


def test_both_sides_synthetic_resolves_to_home_spread_not_zero() -> None:
    """Paired ±S lines must NOT median to ~0 (the ATS-GRADE bug)."""
    # Tuesday 06:00 ET decision (kickoff-aligned calendar for this Sat slate).
    as_of = datetime(2021, 9, 7, 10, 0, tzinfo=UTC)
    kick = datetime(2021, 9, 11, 19, 0, tzinfo=UTC)
    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "game_key": "2021:Home U:Away U:2021-09-11",
                "season": 2021,
                "week": 2,
                "event_time": kick,
                "home_team": "Home U",
                "away_team": "Away U",
            }
        ]
    )
    ts = as_of  # eligible under inclusive feature bound at week Tuesday
    snapshots = pd.DataFrame(
        [
            {
                "game_id": 1,
                "book": "dk",
                "market": "spread",
                "side": "Home U",
                "line": -7.0,
                "event_time": ts,
                "snapshot_id": "s1",
                "n_books_available": 2,
            },
            {
                "game_id": 1,
                "book": "dk",
                "market": "spread",
                "side": "Away U",
                "line": 7.0,
                "event_time": ts,
                "snapshot_id": "s2",
                "n_books_available": 2,
            },
            {
                "game_id": 1,
                "book": "mgm",
                "market": "spread",
                "side": "Home U",
                "line": -7.5,
                "event_time": ts,
                "snapshot_id": "s3",
                "n_books_available": 2,
            },
            {
                "game_id": 1,
                "book": "mgm",
                "market": "spread",
                "side": "Away U",
                "line": 7.5,
                "event_time": ts,
                "snapshot_id": "s4",
                "n_books_available": 2,
            },
            {
                "game_id": 1,
                "book": "dk",
                "market": "total",
                "side": "over",
                "line": 55.5,
                "event_time": ts,
                "snapshot_id": "t1",
                "n_books_available": 2,
            },
            {
                "game_id": 1,
                "book": "dk",
                "market": "total",
                "side": "under",
                "line": 55.5,
                "event_time": ts,
                "snapshot_id": "t2",
                "n_books_available": 2,
            },
        ]
    )
    # Bug witness: all-sides median is 0.
    assert float(snapshots.loc[snapshots["market"] == "spread", "line"].median()) == pytest.approx(
        0.0
    )

    resolved = resolve_lines_for_games(
        games,
        as_of,
        snapshots=snapshots,
        cfbd_lines=None,
        config=WalkForwardConfig(),
        closing=False,
    )
    row = resolved.iloc[0]
    assert row["spread"] == pytest.approx(-7.25)  # median of -7, -7.5
    assert abs(float(row["spread"])) > 0.5
    assert row["side"] == "Home U"
    assert row["book"] in {"dk", "mgm"}
    assert row["total"] == pytest.approx(55.5)

    # Dog home: positive home spread.
    games2 = games.copy()
    games2["home_team"] = "Away U"
    games2["away_team"] = "Home U"
    resolved2 = resolve_lines_for_games(
        games2,
        as_of,
        snapshots=snapshots,
        cfbd_lines=None,
        config=WalkForwardConfig(),
        closing=False,
    )
    assert resolved2.iloc[0]["spread"] == pytest.approx(7.25)


def test_median_home_spread_feature_orientation() -> None:
    """Feature helper: CFBD-home name match, not Odds listing home."""
    rows = pd.DataFrame(
        [
            {"side": "Nevada", "line": 7.0, "book": "dk", "home_team": "Western Michigan"},
            {
                "side": "Western Michigan",
                "line": -7.0,
                "book": "dk",
                "home_team": "Western Michigan",
            },
            {"side": "Nevada", "line": 7.0, "book": "mgm", "home_team": "Western Michigan"},
            {
                "side": "Western Michigan",
                "line": -7.0,
                "book": "mgm",
                "home_team": "Western Michigan",
            },
        ]
    )
    # CFBD home is Nevada (Odds listing swapped) → +7.
    spread, meta = median_home_spread(rows, "Nevada")
    assert spread == pytest.approx(7.0)
    assert meta["side"] == "Nevada"
    home_only = filter_home_side_spreads(rows, "Nevada")
    assert set(home_only["side"].unique()) == {"Nevada"}


def test_fixture_24_hand_grades_agree_under_fixed_closes() -> None:
    """Diag 24-row set: hand cover from book close; five prior NOs become YES."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = payload["rows"]
    assert len(rows) == 24
    prior_no = [r for r in rows if not r["agree"]]
    assert len(prior_no) == 5

    for r in rows:
        margin = float(r["home_pts"]) - float(r["away_pts"])
        book_sp = float(r["book_close_home_spread"])
        hand = ats_home_outcomes(np.array([margin]), np.array([book_sp]))[0]
        expected = r["hand_home_covers"]
        if expected is None:
            assert not np.isfinite(hand)
        else:
            assert bool(hand == 1.0) == bool(expected)

        # Fixed close vs stored pick → hand hit/miss.
        pick_home = bool(r["model_picked_home"])
        if expected is None:
            continue
        hit = pick_home == bool(expected)
        assert hit == bool(r["hand_model_ats_hit"])

    # The five prior grader disagreements: fixed book close ≠ grader ~0.
    for r in prior_no:
        assert abs(float(r["grader_spread_close"])) < 0.5
        assert abs(float(r["book_close_home_spread"])) >= 0.5
        margin = float(r["realized_margin"])
        fixed_cover = ats_home_outcomes(
            np.array([margin]), np.array([float(r["book_close_home_spread"])])
        )[0]
        buggy_cover = ats_home_outcomes(
            np.array([margin]), np.array([float(r["grader_spread_close"])])
        )[0]
        assert np.isfinite(fixed_cover) and np.isfinite(buggy_cover)
        assert bool(fixed_cover == 1.0) != bool(buggy_cover == 1.0)


def test_ats_plausibility_band_derived_from_n() -> None:
    lo, hi = ats_plausibility_band(3577, z=3.0)
    se = np.sqrt(0.25 / 3577)
    assert lo == pytest.approx(0.5 - 3.0 * se)
    assert hi == pytest.approx(0.5 + 3.0 * se)
    # Not a round number like 45–55.
    assert abs(lo - 0.45) > 0.01


def test_ats_guard_fails_40pct_at_n_3577() -> None:
    with pytest.raises(AtsPlausibilityError, match="PIPELINE ERROR"):
        assert_ats_vs_close_plausible(
            0.40,
            3577,
            regime="snapshots_2021_plus",
            line_source_mix={"odds_api_snapshot": 3000},
            pct_abs_spread_lt_0_5=0.86,
        )


def test_ats_guard_passes_51pct_at_n_3577() -> None:
    assert_ats_vs_close_plausible(0.51, 3577, regime="snapshots_2021_plus")


def test_ats_guard_fails_implausibly_good() -> None:
    """Two-sided: 60% at n=3577 is also a pipeline error."""
    with pytest.raises(AtsPlausibilityError, match="PIPELINE ERROR"):
        assert_ats_vs_close_plausible(0.60, 3577, regime="snapshots_2021_plus")


def test_assert_prediction_ats_plausible_on_frame() -> None:
    rng = np.random.default_rng(0)
    n = 400
    margin = rng.normal(0, 14, size=n)
    spread = rng.choice([-7.0, -3.5, 3.5, 7.0], size=n)
    y = ats_home_outcomes(margin, spread)
    # Near-coin-flip predictions (~50% hard-pick accuracy).
    p = np.where(np.isfinite(y), rng.uniform(0.35, 0.65, size=n), np.nan)
    frame = pd.DataFrame(
        {
            "season": np.full(n, 2022),
            "realized_margin": margin,
            "spread_close": spread,
            "p_ats_home": p,
            "exclude_from_headline": False,
            "line_source_close": "odds_api_snapshot",
        }
    )
    assert_prediction_ats_plausible(frame)

    # Force ~0% accuracy (always wrong side of 0.5 vs outcome).
    frame2 = frame.copy()
    frame2["p_ats_home"] = np.where(y >= 0.5, 0.1, 0.9)
    with pytest.raises(AtsPlausibilityError):
        assert_prediction_ats_plausible(frame2)
