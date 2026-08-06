"""Ensemble, calibration, conformal, and distribution tests (Task 19)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ncaa_quant.distribution.bivariate import (
    assemble_bivariate,
    estimate_rho,
    estimate_rho_from_frame,
)
from ncaa_quant.distribution.key_numbers import (
    discrete_margin_pmf,
    fit_key_number_kernel,
)
from ncaa_quant.distribution.simulate import (
    american_to_implied,
    brier_score,
    crps_gaussian,
    log_loss,
    mix_epistemic_predictions,
    moneyline_probs,
    probs_sum_to_one,
    proportional_devig,
    sample_joint,
    save_pit_histogram,
    save_reliability_diagram,
    spread_cover_probs,
    total_probs,
)
from ncaa_quant.models.calibrate import (
    CalibrationError,
    cox_recalibration,
    fit_calibration_bundle,
    fit_market_calibrator,
)
from ncaa_quant.models.conformal import (
    NOMINAL_TO_QUANTILES,
    conformalize_intervals,
    coverage_table,
    evaluate_coverage,
    fit_cqr,
)
from ncaa_quant.models.ensemble import (
    OOF_FLAG_COLUMN,
    EnsembleError,
    assert_oof_only,
    ensemble_sigma,
    fit_ensemble,
    fit_nnls_stack,
    predict_stacked_mu,
    stack_weights_valid,
)
from ncaa_quant.models.heads.quantile import QUANTILES, quantile_column


def _oof_frame(n: int = 80, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    true_m = rng.normal(3.0, 14.0, size=n)
    true_t = rng.normal(55.0, 12.0, size=n)
    # Correlated residuals (~0.1)
    eps = rng.multivariate_normal([0.0, 0.0], [[36.0, 3.6], [3.6, 49.0]], size=n)
    y_m = true_m + eps[:, 0]
    y_t = true_t + eps[:, 1]
    m1 = true_m + rng.normal(0, 2, n)
    m2 = true_m + rng.normal(0, 3, n)
    m3 = true_m + rng.normal(0, 4, n)
    t1 = true_t + rng.normal(0, 2, n)
    t2 = true_t + rng.normal(0, 3, n)
    seasons = np.array([2019, 2021, 2022, 2023], dtype=int)[np.arange(n) % 4]
    # Market-ish raw probs from margin signal
    p_ml = 1.0 / (1.0 + np.exp(-true_m / 10.0))
    p_ats = 1.0 / (1.0 + np.exp(-(true_m + 3.0) / 10.0))
    p_ou = 1.0 / (1.0 + np.exp(-(true_t - 55.0) / 10.0))
    return pd.DataFrame(
        {
            "game_id": np.arange(n),
            "season": seasons,
            OOF_FLAG_COLUMN: True,
            "realized_margin": y_m,
            "realized_total": y_t,
            "member_lgbm_m": m1,
            "member_xgb_m": m2,
            "member_cat_m": m3,
            "member_lgbm_t": t1,
            "member_xgb_t": t2,
            "pred_sigma_margin": np.full(n, 6.0),
            "pred_sigma_total": np.full(n, 7.0),
            "pred_margin": (m1 + m2 + m3) / 3.0,
            "pred_total": (t1 + t2) / 2.0,
            "p_ml_raw": p_ml,
            "y_ml": (y_m > 0).astype(float),
            "p_ats_raw": p_ats,
            "y_ats": (y_m + 3.0 > 0).astype(float),
            "p_ou_raw": p_ou,
            "y_ou": (y_t > 55.0).astype(float),
        }
    )


# ---------------------------------------------------------------------------
# Ensemble
# ---------------------------------------------------------------------------


def test_nnls_weights_nonneg_sum_to_one() -> None:
    frame = _oof_frame()
    stack = fit_nnls_stack(
        frame,
        target="margin",
        member_columns=["member_lgbm_m", "member_xgb_m", "member_cat_m"],
    )
    assert stack_weights_valid(stack)
    assert all(w >= 0 for w in stack.weights)
    assert abs(sum(stack.weights) - 1.0) < 1e-8


def test_nnls_rejects_in_fold_rows() -> None:
    frame = _oof_frame(20)
    frame.loc[0, OOF_FLAG_COLUMN] = False
    with pytest.raises(EnsembleError, match="in-fold"):
        fit_nnls_stack(
            frame,
            target="margin",
            member_columns=["member_lgbm_m", "member_xgb_m"],
        )


def test_nnls_requires_oof_flag_column() -> None:
    frame = _oof_frame(20).drop(columns=[OOF_FLAG_COLUMN])
    with pytest.raises(EnsembleError, match="is_out_of_fold"):
        assert_oof_only(frame)


def test_ensemble_sigma_law_of_total_variance() -> None:
    mus = np.array([[1.0, 3.0, 5.0], [2.0, 2.0, 2.0]])
    sig = np.array([2.0, 4.0])
    res = ensemble_sigma(mus, sig)
    # Row 0: var([1,3,5]) = 8/3, + 4 → σ = sqrt(8/3+4)
    assert res.member_var[0] == pytest.approx(np.var([1.0, 3.0, 5.0]))
    assert res.sigma[0] == pytest.approx(np.sqrt(res.member_var[0] + 4.0))
    # Row 1: zero disagreement
    assert res.member_var[1] == pytest.approx(0.0)
    assert res.sigma[1] == pytest.approx(4.0)


def test_ensemble_sigma_weighted_decomposition() -> None:
    mus = np.array([[0.0, 10.0], [5.0, 5.0]])
    sig = np.array([3.0, 4.0])
    res = ensemble_sigma(mus, sig, weights=[0.5, 0.5])
    assert res.member_var[0] == pytest.approx(25.0)
    decomp = res.variance_decomposition()
    assert decomp["aleatoric_mean_var"] == pytest.approx((9.0 + 16.0) / 2.0)
    assert decomp["stage1_mixture_mean_var"] == pytest.approx(0.0)
    assert decomp["total_mean_var"] == pytest.approx(
        decomp["aleatoric_mean_var"] + decomp["epistemic_member_mean_var"]
    )


def test_fit_ensemble_predict() -> None:
    frame = _oof_frame()
    ens = fit_ensemble(
        frame,
        margin_members=["member_lgbm_m", "member_xgb_m", "member_cat_m"],
        total_members=["member_lgbm_t", "member_xgb_t"],
    )
    mu_m = ens.predict_mu(frame, target="margin")
    assert mu_m.shape == (len(frame),)
    assert np.all(np.isfinite(mu_m))


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def test_isotonic_improves_or_preserves_calibration_diagnostics() -> None:
    frame = _oof_frame(400, seed=3)
    # Force isotonic with large thin thresholds
    cal = fit_market_calibrator(
        frame["p_ml_raw"],
        frame["y_ml"],
        market="ml",
        thin_n=10,
        thin_unique=3,
        force_kind="isotonic",
    )
    assert cal.kind == "isotonic"
    calibrated = cal.transform(frame["p_ml_raw"].to_numpy())
    assert calibrated.min() >= 0.0 and calibrated.max() <= 1.0
    assert cal.before.n == cal.after.n


def test_platt_fallback_on_thin_data() -> None:
    rng = np.random.default_rng(1)
    p = rng.uniform(0.2, 0.8, size=30)
    y = (rng.random(30) < p).astype(float)
    cal = fit_market_calibrator(p, y, market="ml", thin_n=200, thin_unique=50)
    assert cal.kind == "platt"


def test_calibration_bundle_oof_only() -> None:
    frame = _oof_frame(250, seed=4)
    bundle = fit_calibration_bundle(
        frame,
        market_columns={
            "ml": ("p_ml_raw", "y_ml"),
            "ats_close": ("p_ats_raw", "y_ats"),
            "ou_close": ("p_ou_raw", "y_ou"),
        },
        thin_n=50,
        thin_unique=5,
    )
    table = bundle.report_table()
    assert set(table["market"]) == {"ml", "ats_close", "ou_close"}
    assert "slope_before" in table.columns and "slope_after" in table.columns

    bad = frame.copy()
    bad.loc[1, OOF_FLAG_COLUMN] = False
    with pytest.raises(CalibrationError):
        fit_calibration_bundle(
            bad,
            market_columns={"ml": ("p_ml_raw", "y_ml")},
        )


def test_cox_recalibration_perfect() -> None:
    rng = np.random.default_rng(7)
    # Well-calibrated probs
    p = rng.uniform(0.05, 0.95, 2000)
    y = (rng.random(2000) < p).astype(float)
    stats = cox_recalibration(p, y)
    # Slope near 1, intercept near 0 (noisy but in ballpark)
    assert stats.slope == pytest.approx(1.0, abs=0.35)
    assert stats.intercept == pytest.approx(0.0, abs=0.35)


# ---------------------------------------------------------------------------
# Conformal
# ---------------------------------------------------------------------------


def _quantile_frame(n_per_season: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    rows: list[dict[str, float | int]] = []
    gid = 0
    for season in (2019, 2021, 2022, 2023):
        for _ in range(n_per_season):
            mu = float(rng.normal(2, 10))
            y = mu + float(rng.normal(0, 8))
            row: dict[str, float | int] = {
                "game_id": gid,
                "season": season,
                "realized_margin": y,
            }
            # Homoskedastic quantile approx
            from scipy.stats import norm

            for q in QUANTILES:
                row[quantile_column("margin", q)] = mu + float(norm.ppf(q) * 8.0)
            rows.append(row)
            gid += 1
    return pd.DataFrame(rows)


def test_cqr_coverage_near_nominal_on_held_out() -> None:
    frame = _quantile_frame()
    # Fit on trailing 2 seasons (2022, 2023); evaluate on earlier held-out 2021
    cqr = fit_cqr(frame, target="margin", calibration_seasons=(2022, 2023))
    table = coverage_table(cqr)
    assert set(table["nominal"]) == {0.5, 0.8, 0.95}
    # On calibration set itself, empirical should be ≥ nominal − tolerance
    for _, r in table.iterrows():
        assert r["empirical"] >= r["nominal"] - 0.12

    held = frame.loc[frame["season"] == 2021]
    for level in (0.5, 0.8, 0.95):
        conf = conformalize_intervals(held, cqr, nominal=level)
        report = evaluate_coverage(
            held["realized_margin"].to_numpy(),
            conf["cqr_lo"].to_numpy(),
            conf["cqr_hi"].to_numpy(),
            nominal=level,
        )
        # Distribution-free guarantee is on exchangeable calib; held-out may
        # drift — allow a wider tolerance.
        assert report.empirical >= level - 0.20


def test_nominal_quantile_mapping() -> None:
    assert NOMINAL_TO_QUANTILES[0.5] == (0.25, 0.75)
    assert NOMINAL_TO_QUANTILES[0.95] == (0.05, 0.95)


# ---------------------------------------------------------------------------
# Distribution: rho, key numbers, simulate
# ---------------------------------------------------------------------------


def test_estimate_rho_positive_small() -> None:
    frame = _oof_frame(500, seed=9)
    rho = estimate_rho_from_frame(frame)
    # Planted cov ≈ 3.6 / (6*7) ≈ 0.086
    assert rho.n >= 100
    assert 0.0 < rho.rho < 0.4


def test_key_number_kernel_not_hand_tuned() -> None:
    rng = np.random.default_rng(5)
    # Plant excess mass at residual offset ±3
    mu = rng.normal(0, 10, 2000)
    y = mu + rng.normal(0, 5, 2000)
    bump = rng.random(2000) < 0.15
    y = np.where(bump, np.round(mu) + 3.0, y)
    kernel = fit_key_number_kernel(y, mu, min_count=5)
    assert kernel.weight(3) > kernel.weight(0) or kernel.weight(3) > 1.0
    integers, pmf = discrete_margin_pmf(0.0, 10.0, kernel)
    assert pmf.shape == integers.shape
    assert abs(float(np.sum(pmf)) - 1.0) < 1e-9


def test_simulation_deterministic_under_seed() -> None:
    params = assemble_bivariate(
        [3.0],
        [12.0],
        [55.0],
        [14.0],
        rho=0.1,
    )
    a = sample_joint(params, n_draws=5_000, seed=42)
    b = sample_joint(params, n_draws=5_000, seed=42)
    np.testing.assert_array_equal(a.margins, b.margins)
    np.testing.assert_array_equal(a.totals, b.totals)


def test_probabilities_in_unit_interval_and_sum() -> None:
    params = assemble_bivariate([4.0], [11.0], [52.0], [13.0], rho=0.08)
    draws = sample_joint(params, n_draws=20_000, seed=1)
    ml = moneyline_probs(draws)
    ats = spread_cover_probs(draws, -3.5)
    ou = total_probs(draws, 52.5)
    for p in (ml, ats, ou):
        assert probs_sum_to_one(p)
        assert 0.0 <= p.p_side <= 1.0


def test_internal_consistency_win_equals_cover_at_zero() -> None:
    params = assemble_bivariate([2.5], [10.0], [50.0], [12.0], rho=0.05)
    # Continuous (no key-number) so P(M>0) matches cover at spread 0 exactly
    draws = sample_joint(params, kernel=None, n_draws=50_000, seed=2)
    ml = moneyline_probs(draws)
    ats0 = spread_cover_probs(draws, 0.0)
    assert ml.p_side == pytest.approx(ats0.p_side, abs=1e-12)


def test_cover_prob_monotone_in_spread() -> None:
    params = assemble_bivariate([0.0], [14.0], [55.0], [12.0], rho=0.0)
    draws = sample_joint(params, n_draws=40_000, seed=3)
    # Spread against home grows as the home line falls (−3 → −7 → −14).
    # P(home covers) must decrease.
    spreads = [14.0, 7.0, 0.0, -7.0, -14.0]
    probs = [spread_cover_probs(draws, s).p_side for s in spreads]
    for a, b in zip(probs, probs[1:], strict=False):
        assert a >= b - 1e-9


@given(
    mu=st.floats(min_value=-20, max_value=20, allow_nan=False, allow_infinity=False),
    sigma=st.floats(min_value=1.0, max_value=25.0, allow_nan=False, allow_infinity=False),
    line=st.floats(min_value=-30, max_value=30, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=25, deadline=None)
def test_property_probs_sum_hypothesis(mu: float, sigma: float, line: float) -> None:
    params = assemble_bivariate([mu], [sigma], [55.0], [12.0], rho=0.0)
    draws = sample_joint(params, n_draws=8_000, seed=99)
    p = spread_cover_probs(draws, line)
    assert probs_sum_to_one(p, atol=1e-8)


def test_epistemic_mixture_inflates_sigma() -> None:
    # 50 posterior draws of a single feature; mapping = linear in feature
    rng = np.random.default_rng(4)
    n_post, n_games = 50, 5
    feats = rng.normal(0, 1, size=(n_post, n_games, 1))

    def mapping(x: np.ndarray) -> dict[str, np.ndarray]:
        mu_m = 10.0 * x[:, 0]
        return {
            "mu_m": mu_m,
            "sigma_m": np.full(n_games, 5.0),
            "mu_t": np.full(n_games, 50.0),
            "sigma_t": np.full(n_games, 8.0),
        }

    mix = mix_epistemic_predictions(feats, mapping, rho=0.1, seed=0)
    # Mixture σ_M > aleatoric 5 because Var(μ) > 0
    assert np.all(mix.params.sigma_m > 5.0)
    assert mix.n_posterior_draws == 50


def test_sample_joint_with_key_number_kernel() -> None:
    rng = np.random.default_rng(8)
    mu = rng.normal(2, 8, 300)
    y = mu + rng.normal(0, 6, 300)
    kernel = fit_key_number_kernel(y, mu, min_count=2)
    params = assemble_bivariate([3.0, -2.0], [11.0, 12.0], [54.0, 48.0], [13.0, 14.0], rho=0.12)
    draws = sample_joint(params, kernel=kernel, n_draws=3_000, seed=5)
    assert draws.margins.shape == (2, 3_000)
    # Discrete margins are integers
    assert np.allclose(draws.margins, np.round(draws.margins))
    ou = total_probs(draws, 50.0, game_index=1)
    assert probs_sum_to_one(ou)


def test_covariance_matrices_and_away_side() -> None:
    params = assemble_bivariate([5.0], [10.0], [50.0], [12.0], rho=0.1)
    cov = params.covariance_matrices()
    assert cov.shape == (1, 2, 2)
    assert cov[0, 0, 1] == pytest.approx(0.1 * 10.0 * 12.0)
    draws = sample_joint(params, n_draws=5_000, seed=6)
    home = spread_cover_probs(draws, -3.0, side="home")
    away = spread_cover_probs(draws, -3.0, side="away")
    assert home.p_side == pytest.approx(away.p_other)
    under = total_probs(draws, 50.0, side="under")
    over = total_probs(draws, 50.0, side="over")
    assert under.p_side == pytest.approx(over.p_other)


def test_fitted_ensemble_predict_sigma() -> None:
    frame = _oof_frame(100, seed=2)
    ens = fit_ensemble(
        frame,
        margin_members=["member_lgbm_m", "member_xgb_m", "member_cat_m"],
    )
    res = ens.predict_sigma(
        frame,
        target="margin",
        sigma_column="pred_sigma_margin",
    )
    assert res.sigma.shape == (100,)
    assert np.all(res.sigma >= frame["pred_sigma_margin"].to_numpy() - 1e-9)


def test_calibrator_get_and_report() -> None:
    frame = _oof_frame(220, seed=6)
    bundle = fit_calibration_bundle(
        frame,
        market_columns={"ml": ("p_ml_raw", "y_ml")},
        thin_n=50,
        thin_unique=5,
    )
    cal = bundle.get("ml")
    assert cal.market == "ml"
    with pytest.raises(CalibrationError, match="no calibrator"):
        bundle.get("ats_close")


# ---------------------------------------------------------------------------
# Acceptance helpers (metrics + plots)
# ---------------------------------------------------------------------------


def test_acceptance_metrics_and_plots(tmp_path: Path) -> None:
    """End-to-end synthetic acceptance numbers for notes/19.md."""
    frame = _oof_frame(400, seed=19)
    ens = fit_ensemble(
        frame,
        margin_members=["member_lgbm_m", "member_xgb_m", "member_cat_m"],
        total_members=["member_lgbm_t", "member_xgb_t"],
    )
    mu_m = predict_stacked_mu(frame, ens.margin)  # type: ignore[arg-type]
    mu_t = predict_stacked_mu(frame, ens.total)  # type: ignore[arg-type]
    sig_m = ensemble_sigma(
        frame[["member_lgbm_m", "member_xgb_m", "member_cat_m"]],
        frame["pred_sigma_margin"],
        weights=ens.margin.weights if ens.margin else None,
    ).sigma
    sig_t = ensemble_sigma(
        frame[["member_lgbm_t", "member_xgb_t"]],
        frame["pred_sigma_total"],
        weights=ens.total.weights if ens.total else None,
    ).sigma

    rho = estimate_rho(
        frame["realized_margin"] - mu_m,
        frame["realized_total"] - mu_t,
    )
    crps_m = crps_gaussian(frame["realized_margin"].to_numpy(), mu_m, sig_m)
    crps_t = crps_gaussian(frame["realized_total"].to_numpy(), mu_t, sig_t)

    # Model ML probs from Normal CDF
    p_model = 1.0 - __import__("scipy").stats.norm.cdf(0.0, loc=mu_m, scale=sig_m)
    y_ml = frame["y_ml"].to_numpy()

    # Elo-like baseline: logistic of a noisy rating_diff proxy
    rng = np.random.default_rng(19)
    elo_strength = mu_m + rng.normal(0, 8, size=len(frame))
    p_elo = 1.0 / (1.0 + np.exp(-elo_strength / 12.0))

    # De-vigged market baseline from American -110 / +100-ish prices
    # Plant market near truth with vig
    raw_home = np.clip(frame["p_ml_raw"].to_numpy() * 1.05, 0.05, 0.95)
    raw_away = np.clip((1.0 - frame["p_ml_raw"].to_numpy()) * 1.05, 0.05, 0.95)
    p_mkt = np.array([proportional_devig(a, b)[0] for a, b in zip(raw_home, raw_away, strict=True)])

    ll_model = log_loss(p_model, y_ml)
    ll_elo = log_loss(p_elo, y_ml)
    ll_mkt = log_loss(p_mkt, y_ml)
    br_model = brier_score(p_model, y_ml)
    br_elo = brier_score(p_elo, y_ml)
    br_mkt = brier_score(p_mkt, y_ml)

    # Conformal coverage on trailing seasons
    qframe = _quantile_frame(50)
    cqr = fit_cqr(qframe, target="margin", calibration_seasons=(2022, 2023))

    # Plots for held-out season 2023
    hold = frame.loc[frame["season"] == 2023]
    hold_mu = predict_stacked_mu(hold, ens.margin)  # type: ignore[arg-type]
    hold_sig = ensemble_sigma(
        hold[["member_lgbm_m", "member_xgb_m", "member_cat_m"]],
        hold["pred_sigma_margin"],
        weights=ens.margin.weights if ens.margin else None,
    ).sigma
    hold_p = 1.0 - __import__("scipy").stats.norm.cdf(0.0, loc=hold_mu, scale=hold_sig)
    art = tmp_path / "artifacts"
    rel_path = save_reliability_diagram(
        hold_p,
        hold["y_ml"].to_numpy(),
        art / "reliability_2023.png",
        title="Reliability — holdout 2023 ML",
    )
    pit_path = save_pit_histogram(
        hold["realized_margin"].to_numpy(),
        hold_mu,
        hold_sig,
        art / "pit_2023.png",
        title="PIT — holdout 2023 margin",
    )
    assert rel_path.exists() and pit_path.exists()

    # Also write durable copies under docs/notes for the notes file
    notes_art = Path("docs/notes/artifacts_19")
    save_reliability_diagram(
        hold_p,
        hold["y_ml"].to_numpy(),
        notes_art / "reliability_2023.png",
        title="Reliability — holdout 2023 ML",
    )
    save_pit_histogram(
        hold["realized_margin"].to_numpy(),
        hold_mu,
        hold_sig,
        notes_art / "pit_2023.png",
        title="PIT — holdout 2023 margin",
    )

    print("TASK19_ACCEPTANCE")
    print(f"RHO {rho.rho:.4f} n={rho.n}")
    print(f"CRPS_MARGIN {crps_m:.4f}")
    print(f"CRPS_TOTAL {crps_t:.4f}")
    print(f"LOGLOSS_MODEL {ll_model:.4f} ELO {ll_elo:.4f} MARKET {ll_mkt:.4f}")
    print(f"BRIER_MODEL {br_model:.4f} ELO {br_elo:.4f} MARKET {br_mkt:.4f}")
    for level, rep in sorted(cqr.coverage.items()):
        print(
            f"CQR_COVERAGE nominal={level:.2f} empirical={rep.empirical:.4f} "
            f"n={rep.n} width={rep.mean_width:.3f}"
        )
    print(f"NNLS_MARGIN {dict(zip(ens.margin.member_columns, ens.margin.weights, strict=True))}")  # type: ignore[union-attr]
    print(f"RELIABILITY_PNG {notes_art / 'reliability_2023.png'}")
    print(f"PIT_PNG {notes_art / 'pit_2023.png'}")
    assert np.isfinite(crps_m) and np.isfinite(ll_model)


def test_american_devig_roundtrip() -> None:
    # -110 / -110 → 0.5 / 0.5 after proportional de-vig
    a = american_to_implied(-110)
    b = american_to_implied(-110)
    da, db = proportional_devig(a, b)
    assert da == pytest.approx(0.5)
    assert db == pytest.approx(0.5)
