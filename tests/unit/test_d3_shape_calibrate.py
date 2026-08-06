"""Tests for distributional shape helpers and calibration gates (D3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.distribution.key_numbers import fit_key_number_kernel
from ncaa_quant.distribution.shape import (
    coverage_from_z,
    crps_empirical,
    crps_student_t,
    fit_student_t_df,
    gaussian_log_score,
    ks_uniform,
    residual_shape_report,
    standardized_residuals,
    student_t_log_score,
    win_equals_cover_at_zero,
)
from ncaa_quant.evaluation.d3_eval import apply_sigma_correction, part1_sigma_before_after
from ncaa_quant.models.calibrate import (
    CalibrationError,
    apply_gated_calibration,
    fit_market_calibrator,
    gate_calibrator_vs_none,
    stamp_calibration_decisions,
)
from ncaa_quant.models.heads.sigma import HALF_NORMAL_MAD_TO_SIGMA


def test_standardized_residuals_and_shape() -> None:
    rng = np.random.default_rng(0)
    z = rng.normal(size=500)
    y = z * 10.0
    mu = np.zeros(500)
    sig = np.full(500, 10.0)
    zz = standardized_residuals(y, mu, sig)
    assert zz.std() == pytest.approx(1.0, abs=0.1)
    report = residual_shape_report(zz)
    assert report.n == 500
    assert abs(report.sd - 1.0) < 0.15


def test_student_t_fit_and_scores() -> None:
    rng = np.random.default_rng(1)
    z = rng.standard_t(8, size=800)
    fit = fit_student_t_df(z)
    assert fit.nu > 4
    y = z * 5.0
    mu = np.zeros_like(y)
    sig = np.full_like(y, 5.0)
    assert np.isfinite(gaussian_log_score(y, mu, sig))
    assert np.isfinite(student_t_log_score(y, mu, sig, nu=fit.nu))
    assert np.isfinite(crps_student_t(y[:50], mu[:50], sig[:50], nu=8.0, n_draws=500))
    cov = coverage_from_z(z, dist="student_t", nu=8.0)
    assert 0.4 < cov["0.5"] < 0.7


def test_empirical_crps_and_ks() -> None:
    rng = np.random.default_rng(2)
    y = rng.normal(size=40)
    samples = y[:, None] + rng.normal(size=(40, 200))
    assert np.isfinite(crps_empirical(y, samples))
    pit = np.mean(samples <= y[:, None], axis=1)
    ks = ks_uniform(pit)
    assert 0.0 <= ks["statistic"] <= 1.0


def test_win_equals_cover_continuous_and_kernel() -> None:
    mu = np.array([3.0, -2.0, 0.0])
    sig = np.array([14.0, 14.0, 14.0])
    cont = win_equals_cover_at_zero(mu, sig, kernel=None)
    assert cont["within_tolerance"]
    kernel = fit_key_number_kernel(
        np.array([3.0, 7.0, -3.0, 14.0, 0.0] * 20),
        np.array([2.0, 6.0, -2.0, 12.0, 1.0] * 20),
    )
    kn = win_equals_cover_at_zero(mu, sig, kernel=kernel, atol=0.15)
    assert kn["kernel"] is True
    assert "max_abs_diff" in kn


def test_beta_and_none_calibrators_bounded() -> None:
    rng = np.random.default_rng(3)
    p = rng.uniform(0.1, 0.9, size=300)
    y = (rng.random(300) < p).astype(float)
    beta = fit_market_calibrator(p, y, market="ml", force_kind="beta")
    out = beta.transform(p)
    assert out.min() > 0.0 and out.max() < 1.0
    none = fit_market_calibrator(p, y, market="ml", force_kind="none")
    assert none.kind == "none"
    with pytest.raises(CalibrationError, match="0 or 1"):
        # Force a broken isotonic by monkeypatching transform output path:
        bad = fit_market_calibrator(p, y, market="ml", force_kind="platt")
        bad.kind = "isotonic"
        from sklearn.isotonic import IsotonicRegression

        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(np.array([0.0, 1.0]), np.array([0.0, 1.0]))
        bad._isotonic = iso
        bad.transform(np.array([0.0, 1.0]))


def test_calibration_gate_default_off() -> None:
    rng = np.random.default_rng(4)
    p = rng.uniform(0.2, 0.8, 200)
    y = (rng.random(200) < p).astype(float)
    weeks = np.arange(200) % 10
    cal = fit_market_calibrator(p[:150], y[:150], market="ml", force_kind="platt")
    assert cal.applied is False
    gated = gate_calibrator_vs_none(cal, p[150:], y[150:], weeks[150:], n_boot=50, seed=0)
    # Well-calibrated raw probs: gate usually fails → stays OFF
    applied = apply_gated_calibration(p[150:], gated)
    assert applied.shape == p[150:].shape
    stamped = stamp_calibration_decisions(
        pd.DataFrame({"game_id": [1, 2]}),
        {"ml": {"kind": gated.kind, "applied": gated.applied}},
    )
    assert "calibrator_ml_applied" in stamped.columns


def test_apply_sigma_correction_and_part1_synthetic() -> None:
    n = 200
    rng = np.random.default_rng(5)
    mu = rng.normal(3, 8, n)
    y = mu + rng.normal(0, 17.5, n)
    frame = pd.DataFrame(
        {
            "game_id": np.arange(n),
            "season": np.full(n, 2023),
            "week": (np.arange(n) % 12) + 1,
            "pred_margin": mu,
            "realized_margin": y,
            "sigma_m": np.full(n, 14.0),
            "exclude_from_headline": False,
        }
    )
    corrected = apply_sigma_correction(frame)
    assert corrected["sigma_m"].mean() == pytest.approx(14.0 * HALF_NORMAL_MAD_TO_SIGMA)
    # part1 lives in the omitted harness module; exercise via import path still.
    report = part1_sigma_before_after(frame)
    assert report["hypothesis_confirmed"] is True
    assert report["after"]["resid_over_pred_ratio"] == pytest.approx(1.0, abs=0.15)


def test_attach_stage1_and_ensemble_attach() -> None:
    from ncaa_quant.models.ensemble import attach_stage1_mixture_variance, ensemble_sigma

    mus = np.array([[1.0, 3.0], [2.0, 4.0]])
    ens = ensemble_sigma(mus, np.array([5.0, 6.0]), weights=[0.7, 0.3])
    out = attach_stage1_mixture_variance(ens, np.array([1.0, 4.0]))
    assert out.stage1_var is not None
    decomp = out.variance_decomposition()
    assert decomp["stage1_mixture_mean_var"] == pytest.approx(2.5)
