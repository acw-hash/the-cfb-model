"""State-space identifiability and covariance consistency (§9.2-9.4, audit A-3/A-6).

Two distinct defects are covered here:

* **A-3 identifiability.** ``off_h − def_a`` identifies only differences, so the
  filter has a null direction it can drift along. Fixed by an explicit
  league-mean-zero projection after every update.
* **A-6 covariance consistency.** The Joseph update shrank ``P`` as if a clipped
  blowout had been fully observed, even though only 2.5 sigma of the residual was
  acted on. Fixed by inflating ``R`` on clipped rows.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from ncaa_quant.ratings.state_space import (
    GaussianState,
    effective_obs_noise,
    kalman_update,
    mean_centering_operator,
    project_league_mean_zero,
    winsorize_innovation,
)

# ---------------------------------------------------------------------------
# A-6: clipped observations must not shrink the covariance as if fully observed
# ---------------------------------------------------------------------------


def _posterior_var_for_outlier(z_target: float, *, sigma: float = 2.5) -> float:
    """One scalar update whose standardized residual is ``z_target``."""
    prior_var, obs_var = 1.0, 1.0
    pred_sd = np.sqrt(prior_var + obs_var)
    state = GaussianState(mean=np.zeros(1), cov=np.array([[prior_var]]))
    y = np.array([z_target * pred_sd])
    post, _innov, _used, _z, _ll = kalman_update(
        state, np.array([[1.0]]), y, np.array([obs_var]), winsor_sigma=sigma
    )
    return float(post.cov[0, 0])


def test_unclipped_update_is_unaffected_by_the_inflation() -> None:
    """At the threshold nothing is clipped, so the textbook update must hold."""
    # P = 1, R = 1 -> K = 0.5, posterior var = 0.5.
    assert _posterior_var_for_outlier(2.4) == pytest.approx(0.5, abs=1e-9)


def test_clipped_blowout_leaves_more_posterior_variance() -> None:
    at_threshold = _posterior_var_for_outlier(2.4)
    blowout = _posterior_var_for_outlier(10.0)

    assert blowout > at_threshold
    # |z|/sigma = 4, so R_eff = 16: S = 17, K = 1/17, and the Joseph update gives
    # (16/17)^2 + 16/17^2 = 272/289.
    assert blowout == pytest.approx(272.0 / 289.0, rel=1e-9)


def test_posterior_variance_rises_monotonically_with_the_outlier() -> None:
    """The further into the tail, the less the clipped residual should teach us."""
    variances = [_posterior_var_for_outlier(z) for z in (3.0, 5.0, 10.0, 25.0)]

    assert all(b > a for a, b in itertools.pairwise(variances))
    # In the limit the update is uninformative and P returns toward the prior.
    assert variances[-1] < 1.0
    assert variances[-1] > 0.99


def test_extreme_outliers_do_not_move_the_mean_more_than_the_clip() -> None:
    """Winsorizing bounds the mean shift; inflation must not undo that."""
    prior_var, obs_var = 1.0, 1.0
    pred_sd = np.sqrt(prior_var + obs_var)
    state = GaussianState(mean=np.zeros(1), cov=np.array([[prior_var]]))
    post, _i, used, z, _ll = kalman_update(
        state,
        np.array([[1.0]]),
        np.array([50.0 * pred_sd]),
        np.array([obs_var]),
        winsor_sigma=2.5,
    )

    assert abs(float(z[0])) > 2.5
    assert float(used[0]) == pytest.approx(2.5 * pred_sd)
    # Gain is now tiny because R was inflated, so the mean barely moves.
    assert abs(float(post.mean[0])) < 0.3


def test_effective_obs_noise_scales_only_clipped_rows() -> None:
    r = np.diag([1.0, 4.0, 9.0])
    z = np.array([1.0, 7.5, -5.0])
    clipped = np.array([False, True, True])

    out = effective_obs_noise(r, z, clipped, sigma=2.5)

    assert out[0, 0] == pytest.approx(1.0)
    assert out[1, 1] == pytest.approx(4.0 * 9.0)  # (7.5 / 2.5)^2 = 9
    assert out[2, 2] == pytest.approx(9.0 * 4.0)  # (5.0 / 2.5)^2 = 4


def test_effective_obs_noise_is_identity_when_nothing_clips() -> None:
    r = np.diag([1.0, 2.0])
    out = effective_obs_noise(r, np.array([0.5, -1.0]), np.array([False, False]), sigma=2.5)

    assert out == pytest.approx(r)


def test_effective_obs_noise_keeps_a_correlated_r_positive_semidefinite() -> None:
    r = np.array([[1.0, 0.6], [0.6, 1.0]])
    out = effective_obs_noise(r, np.array([10.0, 1.0]), np.array([True, False]), sigma=2.5)

    assert np.all(np.linalg.eigvalsh(out) > 0.0)
    assert out[0, 1] == pytest.approx(out[1, 0])


def test_winsorize_reports_which_components_clipped() -> None:
    clipped, z, mask = winsorize_innovation(np.array([1.0, 10.0]), np.array([1.0, 1.0]), sigma=2.5)

    assert mask.tolist() == [False, True]
    assert clipped[0] == pytest.approx(1.0)
    assert clipped[1] == pytest.approx(2.5)
    assert z[1] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# A-3: identifiability of the off/def contrast
# ---------------------------------------------------------------------------


def test_projection_zeroes_the_group_mean() -> None:
    state = GaussianState(mean=np.array([1.0, 2.0, 6.0, 10.0]), cov=np.eye(4))
    out = project_league_mean_zero(state, [[0, 1, 2]])

    assert out.mean[:3].sum() == pytest.approx(0.0)
    assert out.mean[3] == pytest.approx(10.0)  # outside the group, untouched


def test_projection_centres_offense_and_defense_independently() -> None:
    # positions 0-2 = offense, 3-5 = defense
    state = GaussianState(mean=np.array([1.0, 2.0, 3.0, 10.0, 20.0, 30.0]), cov=np.eye(6))
    out = project_league_mean_zero(state, [[0, 1, 2], [3, 4, 5]])

    assert out.mean[:3].sum() == pytest.approx(0.0)
    assert out.mean[3:].sum() == pytest.approx(0.0)
    # Spreads within each block survive; only the level is removed.
    assert np.diff(out.mean[:3]) == pytest.approx([1.0, 1.0])
    assert np.diff(out.mean[3:]) == pytest.approx([10.0, 10.0])


def test_projection_is_idempotent() -> None:
    """A constraint that is exact after every update must not drift on reapply."""
    state = GaussianState(mean=np.array([1.0, -4.0, 9.0]), cov=np.diag([1.0, 2.0, 3.0]))
    once = project_league_mean_zero(state, [[0, 1, 2]])
    twice = project_league_mean_zero(once, [[0, 1, 2]])

    assert twice.mean == pytest.approx(once.mean)
    assert twice.cov == pytest.approx(once.cov)


def test_projection_removes_variance_along_the_null_direction() -> None:
    """Uncertainty about the unidentified league level must not survive.

    Before projection the filter holds variance along "shift everyone equally",
    which is exactly the direction the data cannot speak to.
    """
    state = GaussianState(mean=np.zeros(3), cov=np.eye(3))
    out = project_league_mean_zero(state, [[0, 1, 2]])

    ones = np.ones(3) / np.sqrt(3.0)
    assert float(ones @ out.cov @ ones) == pytest.approx(0.0, abs=1e-12)
    # Contrasts keep their uncertainty.
    contrast = np.array([1.0, -1.0, 0.0]) / np.sqrt(2.0)
    assert float(contrast @ out.cov @ contrast) > 0.5


def test_projection_keeps_the_covariance_symmetric_and_psd() -> None:
    rng = np.random.default_rng(9)
    a = rng.normal(size=(6, 6))
    cov = a @ a.T + np.eye(6)
    out = project_league_mean_zero(GaussianState(mean=rng.normal(size=6), cov=cov), [[0, 1, 2]])

    assert out.cov == pytest.approx(out.cov.T)
    assert np.all(np.linalg.eigvalsh(out.cov) > -1e-9)


def test_shift_invariance_of_the_projected_state() -> None:
    """The A-3 acceptance test, at the primitive level.

    Adding a constant to every team's offense and every team's defense leaves all
    measurements unchanged, so the projected (identified) state must be identical.
    Without the projection these two runs give different ratings forever.
    """
    off, dfn = [0, 1, 2], [3, 4, 5]
    base = np.array([0.4, -0.1, 0.2, 0.05, -0.3, 0.1])
    shifted = base + np.array([1.7, 1.7, 1.7, 1.7, 1.7, 1.7])

    a = project_league_mean_zero(GaussianState(mean=base, cov=np.eye(6)), [off, dfn])
    b = project_league_mean_zero(GaussianState(mean=shifted, cov=np.eye(6)), [off, dfn])

    assert a.mean == pytest.approx(b.mean)
    assert a.cov == pytest.approx(b.cov)


def test_overlapping_index_sets_are_refused() -> None:
    with pytest.raises(ValueError, match="overlap at positions"):
        mean_centering_operator(4, [[0, 1], [1, 2]])


def test_empty_index_set_is_a_no_op() -> None:
    assert mean_centering_operator(3, [[]]) == pytest.approx(np.eye(3))
