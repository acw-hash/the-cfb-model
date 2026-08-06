"""State-space Kalman rating engine tests (Task 14)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.ratings.diagnostics import (
    filter_health_stats,
    flag_consecutive_innovations,
    standardized_innovations,
)
from ncaa_quant.ratings.state_space import (
    V11_STATE_DIMS,
    GaussianState,
    StateSpaceConfig,
    analytic_1d_kalman_update,
    inflate_q,
    initial_hfa_global,
    initial_hfa_team,
    initial_team_state,
    kalman_predict,
    kalman_update,
    parameter_recovery_coverage,
    posterior_asof,
    run_filter,
    update_game,
    winsorize_innovation,
)


def test_analytic_1d_kalman_against_hand_computation() -> None:
    """Closed-form 1-D predict+update matches hand algebra."""
    prior_mean, prior_var = 0.5, 0.04
    q, r = 0.01, 0.09
    y = 0.8

    pred_var = prior_var + q  # 0.05
    gain = pred_var / (pred_var + r)  # 0.05 / 0.14
    post_mean = prior_mean + gain * (y - prior_mean)
    post_var = (1.0 - gain) * pred_var

    m, v, k = analytic_1d_kalman_update(
        prior_mean, prior_var, observation=y, obs_var=r, process_var=q
    )
    assert k == pytest.approx(gain)
    assert m == pytest.approx(post_mean)
    assert v == pytest.approx(post_var)

    state = GaussianState(mean=np.array([prior_mean]), cov=np.array([[prior_var]]))
    pred = kalman_predict(state, np.array([[q]]))
    post, innov, _, _, _ = kalman_update(
        pred, np.array([[1.0]]), np.array([y]), np.array([r]), winsor_sigma=10.0
    )
    assert float(innov[0]) == pytest.approx(y - prior_mean)
    assert float(post.mean[0]) == pytest.approx(post_mean)
    assert float(post.cov[0, 0]) == pytest.approx(post_var, rel=1e-9)


def test_parameter_recovery_calibrated_coverage() -> None:
    """Filter recovers planted random-walk paths with ~95% band coverage."""
    stats = parameter_recovery_coverage(n_teams=16, n_weeks=10, seed=7)
    # Task acceptance: empirical coverage of the 95% band in 93–97%.
    assert 0.93 <= stats["coverage"] <= 0.97, stats
    assert stats["mean_abs_error_off"] < 0.20
    assert stats["mean_abs_error_def"] < 0.20


def test_winsorization_bounds_extreme_update() -> None:
    """Extreme planted result triggers winsorization and bounds the update."""
    cfg = StateSpaceConfig(residual_winsor_sigma=2.5, prior_var=0.01, r_epa_base=0.05)
    home = initial_team_state(cfg)
    away = initial_team_state(cfg)

    mild = update_game(
        home,
        away,
        initial_hfa_global(cfg),
        initial_hfa_team(cfg),
        home_epa=0.05,
        away_epa=0.0,
        home_plays=70.0,
        away_plays=70.0,
        margin=3.0,
        config=cfg,
        event_time=datetime(2020, 9, 5, tzinfo=UTC),
    )
    extreme = update_game(
        home,
        away,
        initial_hfa_global(cfg),
        initial_hfa_team(cfg),
        home_epa=2.5,
        away_epa=-2.0,
        home_plays=70.0,
        away_plays=70.0,
        margin=70.0,
        config=cfg,
        event_time=datetime(2020, 9, 5, tzinfo=UTC),
    )
    assert any(r.winsorized for r in extreme.innovations)
    i_off = cfg.dim_index("off_epa")
    mild_delta = abs(float(mild.home.mean[i_off] - home.mean[i_off]))
    ext_delta = abs(float(extreme.home.mean[i_off] - home.mean[i_off]))
    assert ext_delta > mild_delta
    assert ext_delta < 0.5

    innov = np.array([10.0])
    clipped, z, was = winsorize_innovation(innov, np.array([1.0]), sigma=2.5)
    assert was[0]
    assert clipped[0] == pytest.approx(2.5)
    assert z[0] == pytest.approx(10.0)


def test_q_inflation_widens_posterior_variance() -> None:
    cfg = StateSpaceConfig()
    q = cfg.q_matrix()
    q_inf = inflate_q(q, "qb_change", config=cfg)
    assert q_inf[0, 0] == pytest.approx(q[0, 0] * 5.0)

    state = initial_team_state(cfg)
    pred_base = kalman_predict(state, q)
    pred_inf = kalman_predict(state, q_inf)
    assert float(np.trace(pred_inf.cov)) > float(np.trace(pred_base.cov))

    q_man = inflate_q(q, "manual", manual_multiplier=10.0, config=cfg)
    assert q_man[0, 0] == pytest.approx(q[0, 0] * 10.0)


def test_asof_never_returns_future_posterior() -> None:
    """pit_audit: as-of queries exclude posteriors from future games."""
    cfg = StateSpaceConfig()
    t0 = datetime(2023, 9, 2, 12, 0, tzinfo=UTC)
    rows = []
    for week, tid, off in ((1, 1, 0.1), (2, 1, 0.2), (3, 1, 0.3)):
        et = t0 + timedelta(days=7 * (week - 1))
        rows.append(
            {
                "team_id": tid,
                "season": 2023,
                "week": week,
                "game_id": week,
                "event_time": et,
                "kind": "postgame",
                "off_epa": off,
                "def_epa": 0.0,
                "st_value": 0.0,
                "pace": 0.0,
                "sd_off_epa": 0.1,
                "sd_def_epa": 0.1,
                "sd_st_value": 0.1,
                "sd_pace": 0.1,
                "cov": np.eye(4).tolist(),
            }
        )
    history = pd.DataFrame(rows)

    as_of = t0 + timedelta(days=7)
    post = posterior_asof(history, 1, as_of, config=cfg)
    assert post is not None
    assert float(post.mean[0]) == pytest.approx(0.1)

    post2 = posterior_asof(history, 1, t0 + timedelta(days=10), config=cfg)
    assert post2 is not None
    assert float(post2.mean[0]) == pytest.approx(0.2)

    assert posterior_asof(history, 1, t0 - timedelta(hours=1), config=cfg) is None


def test_run_filter_smoke_and_sd_shrinks() -> None:
    """Synthetic season: posterior SD shrinks as games accumulate."""
    cfg = StateSpaceConfig(prior_var=0.05)
    t0 = datetime(2021, 9, 4, tzinfo=UTC)
    rows = []
    for week in range(1, 9):
        rows.append(
            {
                "game_id": week,
                "season": 2021,
                "week": week,
                "event_time": t0 + timedelta(days=7 * (week - 1)),
                "home_team_id": 0,
                "away_team_id": week,
                "home_epa": 0.12,
                "away_epa": -0.05,
                "home_plays": 70.0,
                "away_plays": 65.0,
                "home_st_epa": np.nan,
                "away_st_epa": np.nan,
                "pace_obs": 67.5,
                "margin": 14.0,
                "neutral_site": False,
                "home_is_fcs": False,
                "away_is_fcs": False,
            }
        )
    obs = pd.DataFrame(rows)
    result = run_filter(obs, config=cfg, record_weekly=True)
    assert not result.history.empty
    traj = result.history.loc[
        (result.history["team_id"].astype(str) == "0") & (result.history["kind"] == "postgame")
    ].sort_values("event_time")
    sds = traj["sd_off_epa"].to_numpy(dtype=float)
    assert sds[-1] < sds[0]


def test_diagnostics_health_and_flags() -> None:
    innov = pd.DataFrame(
        {
            "game_id": [1, 2, 3, 4],
            "team_id": [10, 10, 10, 10],
            "season": [2020] * 4,
            "week": [1, 2, 3, 4],
            "event_time": [datetime(2020, 9, w, tzinfo=UTC) for w in range(1, 5)],
            "obs_name": ["home_epa"] * 4,
            "innovation": [0.5, 0.6, 0.7, 0.1],
            "pred_sd": [0.2, 0.2, 0.2, 0.2],
            "z": [2.5, 3.0, 3.5, 0.5],
            "winsorized": [False, True, True, False],
        }
    )
    series = standardized_innovations(innov, team_id=10)
    assert len(series) == 4
    health = filter_health_stats(innov)
    assert health.n == 4
    assert not health.misspecified

    flags = flag_consecutive_innovations(innov, n_consecutive=3, threshold=2.0)
    assert len(flags) == 1
    assert flags[0].team_id == 10


def test_v11_dims_are_config_extension() -> None:
    """Extending to 7-dim state is a config change, not a rewrite."""
    cfg = StateSpaceConfig(state_dims=V11_STATE_DIMS)
    assert cfg.n_dims == 7
    state = initial_team_state(cfg)
    assert state.mean.shape == (7,)
    assert state.cov.shape == (7, 7)


def test_build_observations_from_advanced_and_tune() -> None:
    from ncaa_quant.ratings.state_space import (
        build_game_observations_from_advanced,
        tune_process_noise,
    )

    t0 = datetime(2019, 9, 7, tzinfo=UTC)
    games = pd.DataFrame(
        {
            "game_id": [1, 2],
            "season": [2019, 2019],
            "week": [1, 2],
            "home_team_id": [10, 10],
            "away_team_id": [20, 30],
            "home_points": [31, 28],
            "away_points": [17, 21],
            "neutral_site": [False, False],
            "completed": [True, True],
            "event_time": [t0, t0 + timedelta(days=7)],
        }
    )
    advanced = pd.DataFrame(
        {
            "game_id": [1, 1, 2, 2],
            "team_id": [10, 20, 10, 30],
            "offense_epa": [0.2, -0.1, 0.15, 0.0],
            "n_plays": [70, 65, 72, 68],
        }
    )
    obs = build_game_observations_from_advanced(advanced, games)
    assert len(obs) == 2
    assert "home_epa" in obs.columns

    cfg, q, ll = tune_process_noise(obs, q_scales=[1.0, 2.0], dims=())
    assert "off_epa" in q
    assert np.isfinite(ll)


def test_q_events_inflate_during_filter() -> None:
    cfg = StateSpaceConfig()
    t0 = datetime(2022, 9, 3, tzinfo=UTC)
    rows = [
        {
            "game_id": 1,
            "season": 2022,
            "week": 1,
            "event_time": t0,
            "home_team_id": 1,
            "away_team_id": 2,
            "home_epa": 0.1,
            "away_epa": 0.0,
            "home_plays": 70.0,
            "away_plays": 70.0,
            "margin": 7.0,
            "neutral_site": False,
            "home_is_fcs": False,
            "away_is_fcs": False,
        },
        {
            "game_id": 2,
            "season": 2022,
            "week": 2,
            "event_time": t0 + timedelta(days=7),
            "home_team_id": 1,
            "away_team_id": 3,
            "home_epa": 0.1,
            "away_epa": 0.0,
            "home_plays": 70.0,
            "away_plays": 70.0,
            "margin": 7.0,
            "neutral_site": False,
            "home_is_fcs": False,
            "away_is_fcs": False,
        },
    ]
    base = run_filter(pd.DataFrame(rows), config=cfg, record_weekly=False)
    inflated = run_filter(
        pd.DataFrame(rows),
        config=cfg,
        q_events={(1, 2022, 2): [("qb_change", None)]},
        record_weekly=False,
    )

    # After week-2 update, team 1 variance should be at least as wide with inflation.
    def _var(hist: pd.DataFrame) -> float:
        mask = (
            (hist["team_id"].astype(str) == "1")
            & (hist["week"] == 2)
            & (hist["kind"] == "postgame")
        )
        sub = hist.loc[mask]
        return float(sub.iloc[0]["sd_off_epa"])

    assert _var(inflated.history) >= _var(base.history) - 1e-12


def test_fcs_opponent_does_not_create_fcs_history_row() -> None:
    cfg = StateSpaceConfig()
    t0 = datetime(2022, 9, 3, tzinfo=UTC)
    obs = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2022,
                "week": 1,
                "event_time": t0,
                "home_team_id": 100,
                "away_team_id": 999,
                "home_epa": 0.3,
                "away_epa": -0.2,
                "home_plays": 60.0,
                "away_plays": 55.0,
                "margin": 35.0,
                "neutral_site": False,
                "home_is_fcs": False,
                "away_is_fcs": True,
            }
        ]
    )
    result = run_filter(obs, config=cfg, record_weekly=False)
    ids = set(result.history["team_id"].astype(str))
    assert "100" in ids
    assert "999" not in ids
