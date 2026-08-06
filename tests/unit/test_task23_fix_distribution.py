"""Task 23-FIX — distributional wiring + degeneracy guards."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.production_stack import (
    DistributionDegeneracyError,
    assert_a1_priors_precondition,
    assert_a5_garbage_time_precondition,
    build_production_stack,
    validate_prediction_distribution,
)
from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    WalkForwardHarness,
    week_decision_as_of,
)


def _kickoff(season: int, week: int, slot: int = 0) -> datetime:
    tuesday = week_decision_as_of(season, week, WalkForwardConfig())
    return tuesday + timedelta(days=4, hours=slot)


def _games() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    gid = 9000
    rng = np.random.default_rng(23)
    for week in (1, 2, 5, 10):
        for slot in range(3):
            home, away = 10 + slot, 20 + slot
            start = _kickoff(2023, week, slot)
            hm = int(24 + rng.integers(0, 21))
            am = int(21 + rng.integers(0, 21))
            rows.append(
                {
                    "game_id": gid,
                    "game_key": f"2023:{home}:{away}:{start.date()}",
                    "season": 2023,
                    "week": week,
                    "event_time": start,
                    "home_team_id": home,
                    "away_team_id": away,
                    "home_points": hm,
                    "away_points": am,
                    "neutral_site": False,
                }
            )
            gid += 1
    return pd.DataFrame(rows)


def _lines(games: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for g in games.itertuples(index=False):
        rows.append(
            {
                "game_id": int(g.game_id),
                "book": "a",
                "line_type": "open",
                "spread": -3.0 - (int(g.game_id) % 5) * 0.5,
                "total": 52.0 + (int(g.game_id) % 7),
            }
        )
        rows.append(
            {
                "game_id": int(g.game_id),
                "book": "a",
                "line_type": "close",
                "spread": -3.5 - (int(g.game_id) % 5) * 0.5,
                "total": 53.0 + (int(g.game_id) % 7),
            }
        )
    return pd.DataFrame(rows)


def _obs(games: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for g in games.itertuples(index=False):
        rows.append(
            {
                "game_id": int(g.game_id),
                "season": int(g.season),
                "week": int(g.week),
                "event_time": g.event_time,
                "home_team_id": int(g.home_team_id),
                "away_team_id": int(g.away_team_id),
                "home_epa": 0.05,
                "away_epa": -0.02,
                "home_plays": 70.0,
                "away_plays": 68.0,
                "margin": float(g.home_points) - float(g.away_points),
                "neutral_site": False,
            }
        )
    return pd.DataFrame(rows)


def test_validate_rejects_constant_sigma() -> None:
    frame = pd.DataFrame(
        {
            "sigma_m": np.full(20, 14.0),
            "sigma_t": np.full(20, 13.5),
            "pred_margin": np.linspace(-10, 10, 20),
            "spread_close": np.full(20, -3.5),
            "p_ats_home": np.linspace(0.3, 0.7, 20),
            "exclude_from_headline": False,
        }
    )
    with pytest.raises(DistributionDegeneracyError, match="sigma_m"):
        validate_prediction_distribution(frame)


def test_validate_rejects_fixed_sigma_normal_cdf_probs() -> None:
    from scipy import stats

    mu = np.linspace(-12, 12, 30)
    spread = np.full(30, -3.5)
    sigma = np.full(30, 14.0)
    frame = pd.DataFrame(
        {
            "sigma_m": sigma,
            "pred_margin": mu,
            "spread_close": spread,
            "p_ats_home": stats.norm.cdf((mu + spread) / 14.0),
            "exclude_from_headline": False,
        }
    )
    with pytest.raises(DistributionDegeneracyError, match="sigma_m is constant|fixed sigma"):
        validate_prediction_distribution(frame)


def test_production_predict_emits_varying_sigma() -> None:
    games = _games()
    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(5,),
        market_features_available=False,
        seed=23,
        run_id="dist_guard",
        ablation_id="fundamental",
        nnls_equal_weight_fallback=True,
    )
    stack = build_production_stack(
        cfg,
        kind="fundamental",
        observations=_obs(games),
        cfbd_lines=_lines(games),
        n_mc_draws=300,
        n_epistemic_draws=2,
    )
    harness = WalkForwardHarness(
        config=stack.config,
        predictor=stack.predictor,
        feature_provider=stack.feature_provider,
        rating_engine=stack.rating_engine,
    )
    result = harness.run(games, cfbd_lines=_lines(games))
    preds = result.predictions
    assert "sigma_m" in preds.columns
    assert preds["sigma_m"].notna().any()
    validate_prediction_distribution(preds)
    # Calibration path present (raw and calibrated ML cols).
    assert "p_ml_home_raw" in preds.columns
    assert "p_ml_home" in preds.columns


def test_a5_precondition_errors_when_inert() -> None:
    with pytest.raises(Exception, match="garbage-time filter is inert"):
        assert_a5_garbage_time_precondition(n_plays_gt_on=100, n_plays_gt_off=100)


def test_a1_precondition_errors_when_priors_missing() -> None:
    with pytest.raises(Exception, match="fitted priors are missing"):
        assert_a1_priors_precondition(None)
    with pytest.raises(Exception, match="already degenerate"):
        assert_a1_priors_precondition(pd.DataFrame({"team_id": [1, 2], "prior_mean": [0.0, 0.0]}))
