"""Distributional PIT recalibration (DESIGN §2.6 / §5.2, audit A-4).

The defect being fixed: three independently fitted per-market isotonic maps (ML,
ATS@close, OU@close) can move the same event in opposite directions, because
`P(home wins)` and `P(home covers 0)` are the same event read through two
different maps. One monotone map on the CDF cannot do that.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from ncaa_quant.models.pit_calibration import (
    DistributionalCalibrationBundle,
    PitCalibrationError,
    apply_pit_calibration,
    assert_market_free_target,
    fit_pit_recalibrator,
    gate_pit_recalibrator,
    ks_uniform,
    pit_values_from_draws,
    pit_values_normal,
)

RNG_SEED = 20260807


def _ks_critical(n: int) -> float:
    """Asymptotic KS critical value at alpha = 0.05, so thresholds mean something."""
    return 1.36 / np.sqrt(n)


def _overconfident_pit(n: int = 4000, *, understatement: float = 0.6) -> np.ndarray:
    """PIT values from a forecaster whose sigma is too small.

    Truth is N(0, 1); the forecaster claims N(0, understatement). The realized
    outcomes land in the tails far more often than the claimed distribution
    allows, so the PIT piles up at 0 and 1 instead of being uniform.
    """
    rng = np.random.default_rng(RNG_SEED)
    y = rng.normal(0.0, 1.0, size=n)
    mu = np.zeros(n)
    sigma = np.full(n, understatement)
    return pit_values_normal(y, mu, sigma)


# ---------------------------------------------------------------------------
# PIT computation
# ---------------------------------------------------------------------------


def test_pit_of_a_correct_forecaster_is_uniform() -> None:
    rng = np.random.default_rng(RNG_SEED)
    y = rng.normal(3.0, 12.0, size=5000)
    pit = pit_values_normal(y, np.full(5000, 3.0), np.full(5000, 12.0))

    assert ks_uniform(pit) < _ks_critical(5000)


def test_pit_of_an_overconfident_forecaster_is_not_uniform() -> None:
    n = 4000
    # Roughly 6x the alpha = 0.05 critical value: unmistakably miscalibrated.
    assert ks_uniform(_overconfident_pit(n=n)) > 5.0 * _ks_critical(n)


def test_pit_normal_returns_nan_rather_than_inventing_values() -> None:
    pit = pit_values_normal([1.0, 2.0, 3.0], [0.0, 0.0, np.nan], [1.0, 0.0, 1.0])

    assert np.isfinite(pit[0])
    assert np.isnan(pit[1])  # sigma == 0
    assert np.isnan(pit[2])  # mu missing


def test_pit_from_draws_uses_mid_pit_for_ties() -> None:
    """Integer margins tie constantly; P(X <= y) alone would skew the PIT."""
    draws = np.array([[-3.0, 0.0, 0.0, 7.0]])
    # y == 0: strictly below is 1/4, tied is 2/4 -> 0.25 + 0.25 = 0.5
    assert pit_values_from_draws([0.0], draws)[0] == pytest.approx(0.5)


def test_pit_from_draws_rejects_a_shape_mismatch() -> None:
    with pytest.raises(PitCalibrationError, match="n_games, n_draws"):
        pit_values_from_draws([0.0, 1.0], np.zeros((3, 10)))


# ---------------------------------------------------------------------------
# The map fixes calibration
# ---------------------------------------------------------------------------


def test_isotonic_map_restores_uniformity() -> None:
    n = 4000
    cal = fit_pit_recalibrator(_overconfident_pit(n=n), target="margin")

    assert cal.kind == "isotonic"
    assert cal.ks_before > 5.0 * _ks_critical(n)
    assert cal.ks_after < _ks_critical(n)
    assert cal.ks_after < cal.ks_before / 100.0


def test_map_generalizes_to_held_out_pit_values() -> None:
    """In-sample uniformity is guaranteed; the gate has to see held-out data."""
    fit_pit = _overconfident_pit(n=3000)
    cal = fit_pit_recalibrator(fit_pit, target="margin")

    rng = np.random.default_rng(RNG_SEED + 1)
    y = rng.normal(0.0, 1.0, size=3000)
    holdout = pit_values_normal(y, np.zeros(3000), np.full(3000, 0.6))

    ks_raw = ks_uniform(holdout)
    ks_cal = ks_uniform(cal.transform_cdf(holdout))
    assert ks_cal < ks_raw / 2.0


def test_thin_data_falls_back_to_the_parametric_beta_map() -> None:
    pit = _overconfident_pit(n=50)
    cal = fit_pit_recalibrator(pit, target="total")

    assert cal.kind == "beta"
    assert cal.meta["thin"] is True
    assert cal.ks_after < cal.ks_before


def test_forced_identity_map_changes_nothing() -> None:
    pit = _overconfident_pit(n=500)
    cal = fit_pit_recalibrator(pit, target="margin", force_kind="none")

    assert cal.transform_cdf([0.1, 0.5, 0.9]) == pytest.approx([0.1, 0.5, 0.9])
    assert cal.ks_after == pytest.approx(cal.ks_before)


# ---------------------------------------------------------------------------
# Coherence: the property per-market maps could not guarantee
# ---------------------------------------------------------------------------


def test_moneyline_equals_cover_at_zero_after_calibration() -> None:
    """The A-4 property test, now applied post-calibration.

    Winning and covering a zero-point spread are the same event, so the two
    probabilities must agree exactly *after* calibration. With one map on the
    margin CDF this holds by construction; with separate ML and ATS maps it holds
    only by luck.
    """
    cal = fit_pit_recalibrator(_overconfident_pit(), target="margin")
    cal.applied = True

    rng = np.random.default_rng(RNG_SEED + 2)
    mu = rng.normal(0.0, 10.0, size=200)
    sigma = np.full(200, 13.0)

    # Both markets are read off the same raw distribution at line 0.
    p_win_raw = 1.0 - stats.norm.cdf((0.0 - mu) / sigma)
    p_cover_zero_raw = 1.0 - stats.norm.cdf((0.0 - mu) / sigma)

    p_win = apply_pit_calibration(p_win_raw, cal)
    p_cover_zero = apply_pit_calibration(p_cover_zero_raw, cal)

    assert p_win == pytest.approx(p_cover_zero, abs=1e-12)


def test_calibrated_cover_probability_stays_monotone_in_the_line() -> None:
    """Laying more points can never raise the cover probability."""
    cal = fit_pit_recalibrator(_overconfident_pit(), target="margin")
    cal.applied = True

    lines = np.linspace(-21.0, 21.0, 200)
    raw = 1.0 - stats.norm.cdf((lines - 3.0) / 13.0)
    calibrated = apply_pit_calibration(raw, cal)

    assert np.all(np.diff(calibrated) <= 1e-12)


def test_calibrated_probabilities_never_reach_zero_or_one() -> None:
    cal = fit_pit_recalibrator(_overconfident_pit(), target="margin")

    out = cal.transform_cdf([0.0, 1e-12, 0.5, 1.0 - 1e-12, 1.0])
    assert np.all(out > 0.0)
    assert np.all(out < 1.0)


def test_two_way_side_probabilities_still_sum_to_one() -> None:
    """P(over) + P(under) must stay 1 through the map, or EV maths breaks."""
    cal = fit_pit_recalibrator(_overconfident_pit(), target="total")
    cal.applied = True

    raw_over = np.array([0.2, 0.5, 0.73, 0.91])
    over = cal.side_prob(raw_over)
    under = cal.transform_cdf(1.0 - raw_over)

    assert over + under == pytest.approx(np.ones_like(raw_over))


def test_quantile_level_inversion_round_trips() -> None:
    cal = fit_pit_recalibrator(_overconfident_pit(), target="margin")

    levels = np.array([0.05, 0.25, 0.5, 0.75, 0.95])
    raw_levels = cal.transform_quantile_level(levels)

    assert cal.transform_cdf(raw_levels) == pytest.approx(levels, abs=5e-3)
    assert np.all(np.diff(raw_levels) > 0)


# ---------------------------------------------------------------------------
# Market-free targets and gating
# ---------------------------------------------------------------------------


def test_market_derived_targets_are_refused() -> None:
    assert_market_free_target("margin")
    assert_market_free_target("total")

    for bad in ("ats_close", "ou_close", "ml"):
        with pytest.raises(PitCalibrationError, match="not market-free"):
            assert_market_free_target(bad)


def test_fitting_on_a_market_target_is_refused() -> None:
    with pytest.raises(PitCalibrationError, match="not market-free"):
        fit_pit_recalibrator([0.1, 0.4, 0.6, 0.9], target="ats_close")  # type: ignore[arg-type]


def test_gate_is_off_by_default_and_needs_holdout_evidence() -> None:
    cal = fit_pit_recalibrator(_overconfident_pit(n=3000), target="margin")
    assert cal.applied is False

    rng = np.random.default_rng(RNG_SEED + 3)
    y = rng.normal(0.0, 1.0, size=2000)
    holdout = pit_values_normal(y, np.zeros(2000), np.full(2000, 0.6))

    gate_pit_recalibrator(cal, holdout)
    assert cal.applied is True
    assert cal.meta["gate_ks_calibrated"] < cal.meta["gate_ks_raw"]


def test_gate_refuses_a_map_that_does_not_help() -> None:
    """A map fit on a broken forecaster must not be applied to a sound one."""
    cal = fit_pit_recalibrator(_overconfident_pit(n=3000), target="margin")

    rng = np.random.default_rng(RNG_SEED + 4)
    y = rng.normal(0.0, 1.0, size=2000)
    already_calibrated = pit_values_normal(y, np.zeros(2000), np.ones(2000))

    gate_pit_recalibrator(cal, already_calibrated)
    assert cal.applied is False


def test_gate_refuses_when_the_holdout_is_too_small() -> None:
    cal = fit_pit_recalibrator(_overconfident_pit(n=500), target="margin")
    gate_pit_recalibrator(cal, [0.4, 0.6])

    assert cal.applied is False
    assert "holdout too small" in cal.meta["gate_reason"]


def test_ungated_map_is_a_no_op() -> None:
    cal = fit_pit_recalibrator(_overconfident_pit(), target="margin")
    raw = np.array([0.3, 0.7])

    assert apply_pit_calibration(raw, cal) == pytest.approx(raw)
    assert apply_pit_calibration(raw, None) == pytest.approx(raw)


def test_bundle_reports_both_targets() -> None:
    bundle = DistributionalCalibrationBundle(
        margin=fit_pit_recalibrator(_overconfident_pit(n=1000), target="margin"),
    )
    report = bundle.report()

    assert report["margin_pit_kind"] == "isotonic"
    assert report["margin_pit_applied"] is False
    assert report["total_pit_kind"] == "absent"
    assert report["margin_pit_ks_after"] < report["margin_pit_ks_before"]


def test_fitting_needs_enough_rows_and_valid_pit() -> None:
    with pytest.raises(PitCalibrationError, match="≥4 finite"):
        fit_pit_recalibrator([0.5, 0.6], target="margin")
    with pytest.raises(PitCalibrationError, match=r"lie in \[0, 1\]"):
        fit_pit_recalibrator([0.5, 0.6, 1.4, 0.2], target="margin")
