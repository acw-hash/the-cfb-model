"""Elo baseline unit tests (Task 13)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.ratings.elo_baseline import (
    EloConfig,
    apply_season_regression,
    ats_accuracy_vs_closing,
    end_of_season_ratings,
    expected_score,
    mov_multiplier,
    one_step_log_loss,
    rating_history_asof,
    run_elo,
    spearman_rank_corr,
    tune_elo_params,
    update_elo_game,
)


def test_expected_score_symmetric_at_equal_ratings() -> None:
    assert expected_score(1500.0, 1500.0) == pytest.approx(0.5)


def test_single_update_math_against_hand_computation() -> None:
    """Hand-computed 538 MOV update, neutral site, K=20, mov=2.2."""
    elo_h, elo_a = 1600.0, 1500.0
    home_pts, away_pts = 31, 17  # PD = 14, home wins
    cfg = EloConfig(k_factor=20.0, hfa=55.0, mov_factor=2.2, mov_autocorr=0.001)

    # Neutral ⇒ no HFA in expectation.
    elo_diff = elo_h - elo_a  # 100
    p_home = 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))
    mov = math.log(14.0 + 1.0) * (2.2 / ((elo_h - elo_a) * 0.001 + 2.2))
    shift = 20.0 * mov * (1.0 - p_home)

    result = update_elo_game(elo_h, elo_a, home_pts, away_pts, neutral_site=True, config=cfg)
    assert result.p_home == pytest.approx(p_home)
    assert result.mov_mult == pytest.approx(mov)
    assert result.shift == pytest.approx(shift)
    assert result.elo_home_after == pytest.approx(elo_h + shift)
    assert result.elo_away_after == pytest.approx(elo_a - shift)


def test_symmetry_equal_and_opposite_before_hfa() -> None:
    cfg = EloConfig(k_factor=20.0, hfa=65.0)
    # Identical pregame Elos, neutral site ⇒ pure symmetry.
    r = update_elo_game(1500.0, 1500.0, 28, 14, neutral_site=True, config=cfg)
    assert r.shift == pytest.approx(-(r.elo_away_after - 1500.0))
    assert r.elo_home_after - 1500.0 == pytest.approx(1500.0 - r.elo_away_after)

    # With HFA the shift is still equal-and-opposite on the two ratings.
    r2 = update_elo_game(1500.0, 1500.0, 28, 14, neutral_site=False, config=cfg)
    assert r2.elo_home_after - 1500.0 == pytest.approx(1500.0 - r2.elo_away_after)


def test_season_regression_toward_mean() -> None:
    assert apply_season_regression(1800.0, mean_rating=1500.0, season_regression=1.0 / 3.0) == (
        pytest.approx(1700.0)
    )
    assert apply_season_regression(1200.0, mean_rating=1500.0, season_regression=0.0) == 1200.0
    assert apply_season_regression(1200.0, mean_rating=1500.0, season_regression=1.0) == 1500.0


def test_monotonicity_bigger_margin_never_lowers_rating() -> None:
    cfg = EloConfig(k_factor=20.0, hfa=0.0)
    base_h, base_a = 1550.0, 1500.0
    shifts: list[float] = []
    for margin in (1, 3, 7, 14, 28, 45):
        r = update_elo_game(base_h, base_a, 20 + margin, 20, neutral_site=True, config=cfg)
        shifts.append(r.shift)
    for prev, nxt in zip(shifts, shifts[1:], strict=False):
        assert nxt >= prev - 1e-12


def test_mov_autocorr_discounts_favorite_blowout() -> None:
    # Same margin; favorite win gets smaller MOV than underdog win.
    fav = mov_multiplier(21.0, elo_winner=1700.0, elo_loser=1400.0)
    dog = mov_multiplier(21.0, elo_winner=1400.0, elo_loser=1700.0)
    assert dog > fav


def _toy_games() -> pd.DataFrame:
    rows = [
        # season 2022
        {
            "game_id": 1,
            "season": 2022,
            "week": 1,
            "start_date": datetime(2022, 9, 3, 19, 0, tzinfo=UTC),
            "event_time": datetime(2022, 9, 3, 19, 0, tzinfo=UTC),
            "home_team_id": 1,
            "away_team_id": 2,
            "home_points": 35,
            "away_points": 14,
            "neutral_site": False,
            "completed": True,
        },
        {
            "game_id": 2,
            "season": 2022,
            "week": 2,
            "start_date": datetime(2022, 9, 10, 19, 0, tzinfo=UTC),
            "event_time": datetime(2022, 9, 10, 19, 0, tzinfo=UTC),
            "home_team_id": 2,
            "away_team_id": 3,
            "home_points": 10,
            "away_points": 24,
            "neutral_site": False,
            "completed": True,
        },
        # season 2023 — triggers between-season regression
        {
            "game_id": 3,
            "season": 2023,
            "week": 1,
            "start_date": datetime(2023, 9, 2, 19, 0, tzinfo=UTC),
            "event_time": datetime(2023, 9, 2, 19, 0, tzinfo=UTC),
            "home_team_id": 1,
            "away_team_id": 3,
            "home_points": 27,
            "away_points": 24,
            "neutral_site": True,
            "completed": True,
        },
    ]
    return pd.DataFrame(rows)


def test_run_elo_produces_weekly_history_and_regression() -> None:
    cfg = EloConfig(k_factor=20.0, season_regression=1.0 / 3.0)
    game_log, history, final = run_elo(_toy_games(), config=cfg, fbs_only=False)
    assert len(game_log) == 3
    assert set(history["kind"]) >= {"postgame", "weekly", "preseason"}
    # Team 1 won big in 2022 then regressed before 2023.
    pre = history.loc[
        (history["kind"] == "preseason") & (history["season"] == 2023) & (history["team_id"] == 1)
    ]
    assert len(pre) == 1
    post_2022 = history.loc[
        (history["kind"] == "weekly") & (history["season"] == 2022) & (history["team_id"] == 1)
    ]["elo"].iloc[-1]
    assert pre["elo"].iloc[0] == pytest.approx(
        apply_season_regression(float(post_2022), season_regression=cfg.season_regression)
    )
    assert 1 in final


def test_end_of_season_and_asof() -> None:
    _, history, _ = run_elo(_toy_games(), fbs_only=False)
    eos = end_of_season_ratings(history, season=2022)
    assert list(eos.columns) == ["team_id", "season", "week", "elo"]
    assert set(eos["team_id"]) == {1, 2, 3}

    snap = rating_history_asof(
        history,
        datetime(2022, 9, 5, tzinfo=UTC),
        kind="postgame",
    )
    # Only game 1 has event_time < Sept 5.
    assert set(snap["team_id"]) == {1, 2}


def test_one_step_log_loss_and_tune_scan() -> None:
    game_log, _, _ = run_elo(_toy_games(), fbs_only=False)
    ll = one_step_log_loss(game_log)
    assert math.isfinite(ll) and ll > 0.0

    # Hand log-loss on the three decisive games.
    y = (game_log["home_points"] > game_log["away_points"]).astype(float).to_numpy()
    p = game_log["p_home"].clip(1e-15, 1.0 - 1e-15).to_numpy()
    hand = float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))
    assert ll == pytest.approx(hand)

    best, scan = tune_elo_params(
        _toy_games(),
        k_grid=(10.0, 20.0),
        mov_factor_grid=(2.2,),
        fbs_only=False,
    )
    assert best.k_factor in (10.0, 20.0)
    assert len(scan) == 2
    assert scan.iloc[0]["log_loss"] <= scan.iloc[1]["log_loss"]


def test_ats_accuracy_and_spearman() -> None:
    game_log, _, _ = run_elo(_toy_games(), fbs_only=False)
    lines = pd.DataFrame(
        [
            {"game_id": 1, "line_type": "close", "spread": -10.0, "book": "x"},
            {"game_id": 2, "line_type": "close", "spread": 3.0, "book": "x"},
            {"game_id": 3, "line_type": "close", "spread": -1.0, "book": "x"},
            {"game_id": 3, "line_type": "open", "spread": -2.0, "book": "x"},
        ]
    )
    ats = ats_accuracy_vs_closing(game_log, lines)
    assert ats["n_ats"] == 3.0
    assert 0.0 <= ats["ats_accuracy"] <= 1.0

    corr = spearman_rank_corr({1: 10.0, 2: 5.0, 3: 1.0}, {1: 100.0, 2: 50.0, 3: 10.0})
    assert corr == pytest.approx(1.0)
