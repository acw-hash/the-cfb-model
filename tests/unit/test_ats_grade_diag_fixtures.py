"""ATS-GRADE-DIAG fixtures — hand-graded rows + all-sides median bug witness.

No production code under test (diagnosis session; fix deferred). These tests
lock the fixture arithmetic and the side-relative median failure mode so a
later fix cannot silently rewrite the evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ats_grade_diag_24.json"


def _home_covers(margin: float, home_spread: float) -> bool | None:
    edge = float(margin) + float(home_spread)
    if not np.isfinite(edge) or abs(edge) < 1e-12:
        return None
    return bool(edge > 0)


def test_fixture_hand_grades_match_cover_arithmetic() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = payload["rows"]
    assert len(rows) == 24
    assert payload["n_agree"] == 19

    for row in rows:
        margin = float(row["home_pts"]) - float(row["away_pts"])
        assert margin == pytest.approx(float(row["realized_margin"]))
        hand = _home_covers(margin, float(row["book_close_home_spread"]))
        assert hand == row["hand_home_covers"]
        grader = _home_covers(margin, float(row["grader_spread_close"]))
        assert grader == row["grader_home_covers"]


def test_fixture_marks_snapshot_disagreements() -> None:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]
    disagrees = [r for r in rows if not r["agree"]]
    assert len(disagrees) == 5
    # Every disagreement is a snapshot-regime row with grader spread ≈ 0 while
    # the book CFBD-home close is a real non-pick'em number.
    for r in disagrees:
        assert int(r["season"]) >= 2021
        assert abs(float(r["grader_spread_close"])) < 0.5
        assert abs(float(r["book_close_home_spread"])) >= 0.5


def test_all_sides_median_collapses_paired_spreads_to_zero() -> None:
    """Witness: median of side-relative ±S lines is 0 — the resolve bug.

    Odds snapshots store one row per (book, side) with outcome.point side-
    relative. Taking median(line) without filtering to CFBD home side yields
    ~0 whenever both sides are present.
    """
    lines = np.array([-7.0, 7.0, -7.0, 7.0, -7.5, 7.5], dtype=float)
    assert float(np.median(lines)) == pytest.approx(0.0)
    home_only = lines[lines < 0]  # home favorite example
    assert float(np.median(home_only)) == pytest.approx(-7.0)
