"""End-to-end joint league filter tests (DESIGN §9.2–§9.3, audit A-3/B-1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from ncaa_quant.ratings.league_state import LeagueState
from ncaa_quant.ratings.state_space import (
    StateSpaceConfig,
    initial_team_state,
    run_filter,
)


def _toy_schedule(*, n_teams: int = 8, n_weeks: int = 6, seed: int = 3) -> pd.DataFrame:
    """Round-robin-ish schedule with planted off/def levels."""
    rng = np.random.default_rng(seed)
    # Plant relative strengths; absolute level is arbitrary (identifiability).
    off = rng.normal(0.0, 0.08, size=n_teams)
    off = off - off.mean()
    defense = rng.normal(0.0, 0.08, size=n_teams)
    defense = defense - defense.mean()
    true_env = 0.04

    rows: list[dict[str, object]] = []
    gid = 1
    t0 = datetime(2023, 9, 2, tzinfo=UTC)
    for week in range(1, n_weeks + 1):
        # Pair teams (0,1), (2,3), … rotating.
        order = list(range(n_teams))
        rng.shuffle(order)
        for i in range(0, n_teams, 2):
            h, a = order[i], order[i + 1]
            home_epa = off[h] - defense[a] + 0.02 + true_env + rng.normal(0, 0.04)
            away_epa = off[a] - defense[h] + true_env + rng.normal(0, 0.04)
            margin = 80.0 * ((off[h] - defense[a]) - (off[a] - defense[h]) + 0.02) + rng.normal(
                0, 10
            )
            rows.append(
                {
                    "game_id": gid,
                    "season": 2023,
                    "week": week,
                    "event_time": t0 + timedelta(days=7 * (week - 1), hours=i),
                    "home_team_id": h,
                    "away_team_id": a,
                    "home_epa": home_epa,
                    "away_epa": away_epa,
                    "home_plays": 70.0,
                    "away_plays": 70.0,
                    "margin": margin,
                    "neutral_site": False,
                    "home_is_fcs": False,
                    "away_is_fcs": False,
                }
            )
            gid += 1
    return pd.DataFrame(rows)


def test_league_state_admits_and_indexes() -> None:
    cfg = StateSpaceConfig()
    league = LeagueState.empty(cfg)
    assert league.dim == 2
    league.admit("10")
    league.admit("20")
    assert league.n_teams == 2
    assert league.dim == 2 * cfg.n_dims + 2 + 2
    assert league.team_slice("10").stop == cfg.n_dims
    assert league.hfa_global_index() == 2 * cfg.n_dims + 2
    assert league.scoring_env_index() == league.hfa_global_index() + 1


def test_cross_team_covariance_is_retained_after_a_game() -> None:
    """Beating a common opponent must induce nonzero cross-covariance."""
    cfg = StateSpaceConfig(prior_var=0.04)
    league = LeagueState.empty(cfg)
    for tid in ("A", "B", "C"):
        league.admit(tid)

    # A vs B
    league.update_game(
        "A",
        "B",
        home_epa=0.15,
        away_epa=-0.05,
        home_plays=70.0,
        away_plays=70.0,
        margin=14.0,
        event_time=datetime(2023, 9, 2, tzinfo=UTC),
    )
    # Off blocks of A and B should now be correlated through the joint update.
    a_sl = league.team_slice("A")
    b_sl = league.team_slice("B")
    cross = league.cov[a_sl, b_sl]
    assert float(np.max(np.abs(cross))) > 1e-8


def test_filter_shift_invariance_end_to_end() -> None:
    """Constant shift of all initial off/def leaves constrained ratings unchanged.

    The measurement contrast identifies only differences; after the §9.3
    projection, adding ``c`` to every offense and defense at initialization
    must not change any posterior (or any measurement prediction).
    """
    obs = _toy_schedule(n_teams=8, n_weeks=5, seed=11)
    cfg = StateSpaceConfig(prior_var=0.04, prior_mean=0.0)

    def _priors(shift: float) -> dict[int, dict[object, object]]:
        out: dict[int, dict[object, object]] = {2023: {}}
        for tid in range(8):
            state = initial_team_state(cfg)
            state.mean[cfg.dim_index("off_epa")] += shift
            state.mean[cfg.dim_index("def_epa")] += shift
            out[2023][tid] = state
        return out

    baseline = run_filter(
        obs,
        config=cfg,
        record_weekly=False,
        preseason_states=_priors(0.0),  # type: ignore[arg-type]
    )
    shifted = run_filter(
        obs,
        config=cfg,
        record_weekly=False,
        preseason_states=_priors(0.2),  # type: ignore[arg-type]
    )

    for tid in range(8):
        b = baseline.history.loc[
            (baseline.history["team_id"] == tid) & (baseline.history["kind"] == "postgame")
        ].sort_values("event_time")
        s = shifted.history.loc[
            (shifted.history["team_id"] == tid) & (shifted.history["kind"] == "postgame")
        ].sort_values("event_time")
        assert len(b) == len(s)
        for col in ("off_epa", "def_epa"):
            np.testing.assert_allclose(
                b[col].to_numpy(dtype=float),
                s[col].to_numpy(dtype=float),
                atol=1e-5,
                err_msg=f"team {tid} {col} not shift-invariant",
            )

    assert "scoring_env" in baseline.hfa_history.columns
    assert np.isfinite(baseline.hfa_history["scoring_env"].to_numpy()).all()
    # A joint +c shift of every off and every def is in the measurement null
    # space (off_h − def_a is unchanged), so scoring_env is correctly identical.


def test_parameter_recovery_still_calibrated_under_joint_filter() -> None:
    from ncaa_quant.ratings.state_space import parameter_recovery_coverage

    stats = parameter_recovery_coverage(n_teams=12, n_weeks=8, seed=5)
    assert 0.90 <= stats["coverage"] <= 0.99, stats
    assert stats["mean_abs_error_off"] < 0.25
    assert stats["mean_abs_error_def"] < 0.25


def test_run_filter_writes_scoring_env() -> None:
    obs = _toy_schedule(n_teams=6, n_weeks=3, seed=1)
    result = run_filter(obs, record_weekly=False)
    assert "scoring_env" in result.hfa_history.columns
    assert len(result.history) > 0
    # League-mean-zero: within a week, mean off_epa across teams near 0.
    last_week = int(result.history["week"].max())
    week_rows = result.history.loc[
        (result.history["week"] == last_week) & (result.history["kind"] == "postgame")
    ]
    # One row per team-game; take last per team.
    finals = week_rows.sort_values("event_time").groupby("team_id").tail(1)
    assert abs(float(finals["off_epa"].mean())) < 0.05
    assert abs(float(finals["def_epa"].mean())) < 0.05
