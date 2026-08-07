"""Distributional shape helpers for margin residuals (D3 / DESIGN §2.3).

Gaussian vs Student-t vs empirical-residual (conformal) predictive comparisons,
plus the §19 internal-consistency check that P(win) = P(cover at spread 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from scipy import stats  # type: ignore[import-untyped]
from scipy.optimize import minimize_scalar  # type: ignore[import-untyped]

ShapeKind = Literal["gaussian", "student_t", "empirical"]


@dataclass(frozen=True)
class ResidualShapeReport:
    """Standardized residual moments + Jarque-Bera."""

    n: int
    sd: float
    skew: float
    excess_kurtosis: float
    jarque_bera_stat: float
    jarque_bera_pvalue: float


@dataclass(frozen=True)
class StudentTFit:
    """MLE degrees of freedom on standardized residuals."""

    nu: float
    se: float
    n: int
    loglik: float


def standardized_residuals(
    y: np.ndarray,
    mu: np.ndarray,
    sigma: np.ndarray,
) -> np.ndarray:
    """z = (y − μ) / σ′ with positive σ′."""
    yt = np.asarray(y, dtype=float)
    m = np.asarray(mu, dtype=float)
    s = np.maximum(np.asarray(sigma, dtype=float), 1e-8)
    out: np.ndarray = (yt - m) / s
    return out


def residual_shape_report(z: np.ndarray) -> ResidualShapeReport:
    """SD, skew, excess kurtosis, Jarque-Bera on finite standardized residuals."""
    zz = np.asarray(z, dtype=float)
    zz = zz[np.isfinite(zz)]
    n = int(zz.size)
    if n < 8:
        return ResidualShapeReport(
            n=n,
            sd=float("nan"),
            skew=float("nan"),
            excess_kurtosis=float("nan"),
            jarque_bera_stat=float("nan"),
            jarque_bera_pvalue=float("nan"),
        )
    jb_stat, jb_p = stats.jarque_bera(zz)
    return ResidualShapeReport(
        n=n,
        sd=float(np.std(zz, ddof=0)),
        skew=float(stats.skew(zz, bias=False)),
        excess_kurtosis=float(stats.kurtosis(zz, fisher=True, bias=False)),
        jarque_bera_stat=float(jb_stat),
        jarque_bera_pvalue=float(jb_p),
    )


def fit_student_t_df(z: np.ndarray, *, nu_bounds: tuple[float, float] = (2.1, 80.0)) -> StudentTFit:
    """MLE for Student-t degrees of freedom on already-standardized residuals.

    Location 0 and scale 1 are held fixed (z should already be (y−μ)/σ′).
    Standard error from the observed Hessian (1-D finite difference).
    """
    zz = np.asarray(z, dtype=float)
    zz = zz[np.isfinite(zz)]
    n = int(zz.size)
    if n < 20:
        return StudentTFit(nu=float("nan"), se=float("nan"), n=n, loglik=float("nan"))

    def nll(nu: float) -> float:
        return float(-np.sum(stats.t.logpdf(zz, df=nu)))

    res = minimize_scalar(nll, bounds=nu_bounds, method="bounded")
    nu_hat = float(res.x)
    ll = float(-nll(nu_hat))
    # Observed information via central difference on NLL.
    eps = 1e-3
    nu_lo = max(nu_bounds[0], nu_hat - eps)
    nu_hi = min(nu_bounds[1], nu_hat + eps)
    d2 = (nll(nu_hi) - 2.0 * nll(nu_hat) + nll(nu_lo)) / max((nu_hi - nu_lo) / 2.0, 1e-8) ** 2
    se = float(1.0 / np.sqrt(d2)) if d2 > 0 and np.isfinite(d2) else float("nan")
    return StudentTFit(nu=nu_hat, se=se, n=n, loglik=ll)


def gaussian_log_score(y: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> float:
    """Mean −log N(y; μ, σ)."""
    yt = np.asarray(y, dtype=float)
    m = np.asarray(mu, dtype=float)
    s = np.maximum(np.asarray(sigma, dtype=float), 1e-8)
    return float(-np.mean(stats.norm.logpdf(yt, loc=m, scale=s)))


def student_t_log_score(
    y: np.ndarray,
    mu: np.ndarray,
    sigma: np.ndarray,
    *,
    nu: float,
) -> float:
    """Mean −log of location-scale Student-t."""
    yt = np.asarray(y, dtype=float)
    m = np.asarray(mu, dtype=float)
    s = np.maximum(np.asarray(sigma, dtype=float), 1e-8)
    z = (yt - m) / s
    # density = t_pdf(z; ν) / σ
    return float(-np.mean(stats.t.logpdf(z, df=nu) - np.log(s)))


def crps_student_t(
    y: np.ndarray,
    mu: np.ndarray,
    sigma: np.ndarray,
    *,
    nu: float,
    n_draws: int = 4000,
    seed: int = 0,
) -> float:
    """Monte Carlo CRPS under location-scale Student-t (Gneiting form)."""
    yt = np.asarray(y, dtype=float).ravel()
    m = np.asarray(mu, dtype=float).ravel()
    s = np.maximum(np.asarray(sigma, dtype=float).ravel(), 1e-8)
    rng = np.random.default_rng(seed)
    # Sample once shared latent t, scale per row.
    z_draws = rng.standard_t(nu, size=n_draws)
    scores = np.empty(yt.size, dtype=float)
    for i in range(yt.size):
        draws = m[i] + s[i] * z_draws
        term1 = float(np.mean(np.abs(draws - yt[i])))
        # E|X − X'| via sample pairwise (U-statistic on half the pairs).
        perm = rng.permutation(n_draws)
        term2 = float(np.mean(np.abs(draws - draws[perm])))
        scores[i] = term1 - 0.5 * term2
    return float(np.mean(scores))


def empirical_residual_predictive(
    y: np.ndarray,
    mu: np.ndarray,
    train_residuals: np.ndarray,
) -> dict[str, np.ndarray]:
    """Conformal / empirical-residual draws: y_hat = μ + residual_bootstrap.

    Returns dict with ``samples`` shape ``(n, n_train_resid)`` for PIT/CRPS.
    """
    resid = np.asarray(train_residuals, dtype=float)
    resid = resid[np.isfinite(resid)]
    m = np.asarray(mu, dtype=float).ravel()
    samples = m[:, None] + resid[None, :]
    return {"samples": samples, "residuals": resid}


def crps_empirical(y: np.ndarray, samples: np.ndarray) -> float:
    """CRPS from empirical predictive samples (Gneiting sample form)."""
    yt = np.asarray(y, dtype=float).ravel()
    samp = np.asarray(samples, dtype=float)
    if samp.ndim != 2 or samp.shape[0] != yt.size:
        msg = f"samples must be (n, m), got {samp.shape}"
        raise ValueError(msg)
    scores = np.empty(yt.size, dtype=float)
    rng = np.random.default_rng(0)
    for i in range(yt.size):
        draws = samp[i]
        term1 = float(np.mean(np.abs(draws - yt[i])))
        perm = rng.permutation(draws.size)
        term2 = float(np.mean(np.abs(draws - draws[perm])))
        scores[i] = term1 - 0.5 * term2
    return float(np.mean(scores))


def pit_from_samples(y: np.ndarray, samples: np.ndarray) -> np.ndarray:
    """Empirical PIT: fraction of samples ≤ y."""
    yt = np.asarray(y, dtype=float).ravel()
    samp = np.asarray(samples, dtype=float)
    return np.asarray(np.mean(samp <= yt[:, None], axis=1), dtype=float)


def pit_student_t(
    y: np.ndarray,
    mu: np.ndarray,
    sigma: np.ndarray,
    *,
    nu: float,
) -> np.ndarray:
    z = standardized_residuals(y, mu, sigma)
    return np.asarray(stats.t.cdf(z, df=nu), dtype=float)


def coverage_from_z(
    z: np.ndarray,
    *,
    levels: tuple[float, ...] = (0.5, 0.8, 0.95),
    dist: Literal["gaussian", "student_t"] = "gaussian",
    nu: float = 8.0,
) -> dict[str, float]:
    """Central interval coverage from standardized residuals."""
    zz = np.asarray(z, dtype=float)
    zz = zz[np.isfinite(zz)]
    out: dict[str, float] = {}
    for lev in levels:
        alpha = 1.0 - lev
        if dist == "gaussian":
            hi = float(stats.norm.ppf(1.0 - alpha / 2.0))
        else:
            hi = float(stats.t.ppf(1.0 - alpha / 2.0, df=nu))
        out[str(lev)] = float(np.mean(np.abs(zz) <= hi))
    return out


def ks_uniform(pit: np.ndarray) -> dict[str, float]:
    """KS statistic of PIT vs Uniform(0,1)."""
    u = np.asarray(pit, dtype=float)
    u = u[np.isfinite(u)]
    if u.size < 5:
        return {"statistic": float("nan"), "pvalue": float("nan")}
    stat, p = stats.kstest(u, "uniform")
    return {"statistic": float(stat), "pvalue": float(p)}


def win_equals_cover_at_zero(
    mu: np.ndarray,
    sigma: np.ndarray,
    *,
    kernel: Any | None = None,
    atol: float = 1e-3,
    n_draws: int = 5_000,
    seed: int = 0,
    spreads: np.ndarray | None = None,
) -> dict[str, Any]:
    """§19 internal consistency: P(win) == P(cover at spread 0) via the MC path.

    The continuous closed form Φ(μ/σ) is tautological for both quantities, so it
    cannot detect a broken ATS path. This check instead:

    1. Draws from ``sample_joint`` (optional key-number kernel).
    2. Compares ``moneyline_probs`` to ``spread_cover_probs(spread=0)``
       (raw three-way ``p_side`` — both are P(M > 0)).
    3. When ``spreads`` is provided, also checks that MC cover probs match the
       home-relative Gaussian ``Φ((μ + S) / σ)`` within ``atol`` (continuous
       kernel=None) or a looser MC tolerance with a kernel. An inverted-sign
       implementation ``Φ((μ − S) / σ)`` must *not* match — that is the H1
       guard the old Φ-only test could never fire.
    """
    from ncaa_quant.distribution.bivariate import assemble_bivariate
    from ncaa_quant.distribution.key_numbers import (
        ConditionalKeyNumberKernel,
        KeyNumberKernel,
    )
    from ncaa_quant.distribution.simulate import (
        moneyline_probs,
        sample_joint,
        spread_cover_probs,
        two_way_side_prob,
    )

    m = np.asarray(mu, dtype=float).reshape(-1)
    s = np.maximum(np.asarray(sigma, dtype=float).reshape(-1), 1e-8)
    if m.size != s.size:
        msg = "mu/sigma length mismatch"
        raise ValueError(msg)
    if m.size == 0:
        return {
            "max_abs_diff": 0.0,
            "within_tolerance": True,
            "atol": atol,
            "n": 0,
            "kernel": kernel is not None,
            "path": "mc",
        }

    if kernel is not None and not isinstance(
        kernel, (KeyNumberKernel, ConditionalKeyNumberKernel)
    ):
        msg = "kernel must be a KeyNumberKernel, ConditionalKeyNumberKernel, or None"
        raise TypeError(msg)

    # Cap MC batch size — full walk-forward tables are thousands of rows.
    max_games = 64
    if m.size > max_games:
        rng = np.random.default_rng(int(seed))
        pick = np.sort(rng.choice(m.size, size=max_games, replace=False))
        m = m[pick]
        s = s[pick]
        if spreads is not None:
            spreads = np.asarray(spreads, dtype=float).reshape(-1)[pick]

    # Dummy totals — ATS/ML only use margin draws.
    mt = np.full(m.size, 55.0)
    st = np.full(m.size, 13.0)
    params = assemble_bivariate(m, s, mt, st, rho=0.0)
    draws = sample_joint(params, kernel=kernel, n_draws=int(n_draws), seed=int(seed))

    p_win = np.empty(m.size, dtype=float)
    p_cover0 = np.empty(m.size, dtype=float)
    p_cover0_two_way = np.empty(m.size, dtype=float)
    for i in range(m.size):
        p_win[i] = moneyline_probs(draws, game_index=i).p_side
        ats0 = spread_cover_probs(draws, 0.0, game_index=i)
        p_cover0[i] = ats0.p_side
        p_cover0_two_way[i] = two_way_side_prob(ats0)

    max_abs = float(np.max(np.abs(p_win - p_cover0))) if m.size else 0.0
    # Two-way at spread 0 differs from P(M>0) by push mass; that is expected and
    # is *not* the consistency claim. Record it separately.
    max_abs_two_way = float(np.max(np.abs(p_win - p_cover0_two_way))) if m.size else 0.0

    out: dict[str, Any] = {
        "max_abs_diff": max_abs,
        "within_tolerance": bool(max_abs <= atol),
        "atol": atol,
        "n": int(m.size),
        "kernel": kernel is not None,
        "path": "mc",
        "mean_p_win": float(np.mean(p_win)),
        "mean_p_cover0": float(np.mean(p_cover0)),
        "max_abs_diff_two_way": max_abs_two_way,
        "n_draws": int(n_draws),
    }

    if spreads is not None:
        sp = np.asarray(spreads, dtype=float).reshape(-1)
        if sp.size != m.size:
            msg = "spreads length must match mu"
            raise ValueError(msg)
        p_mc = np.empty(m.size, dtype=float)
        p_gauss = stats.norm.cdf((m + sp) / s)
        p_gauss_inv = stats.norm.cdf((m - sp) / s)
        for i in range(m.size):
            if not np.isfinite(sp[i]):
                p_mc[i] = float("nan")
                continue
            p_mc[i] = two_way_side_prob(spread_cover_probs(draws, float(sp[i]), game_index=i))
        mask = np.isfinite(p_mc) & np.isfinite(p_gauss)
        if int(mask.sum()) >= 1:
            # Continuous: MC ≈ Gaussian. With kernel, allow a wider band.
            spread_atol = atol if kernel is None else max(atol, 0.05)
            max_vs_gauss = float(np.max(np.abs(p_mc[mask] - p_gauss[mask])))
            max_vs_inv = float(np.max(np.abs(p_mc[mask] - p_gauss_inv[mask])))
            out["nonzero_spread"] = {
                "max_abs_diff_vs_gaussian": max_vs_gauss,
                "max_abs_diff_vs_inverted_sign": max_vs_inv,
                "within_tolerance": bool(max_vs_gauss <= spread_atol),
                "inverted_sign_closer": bool(max_vs_inv < max_vs_gauss),
                "atol": spread_atol,
                "n": int(mask.sum()),
            }
            # Fail the overall check if nonzero-spread path mismatches Gaussian
            # (continuous) or prefers the inverted-sign formula.
            if kernel is None and max_vs_gauss > spread_atol:
                out["within_tolerance"] = False
            if max_vs_inv < max_vs_gauss and max_vs_inv <= spread_atol:
                # MC matches inverted sign better than correct — H1 symptom.
                out["within_tolerance"] = False
                out["h1_sign_inversion_suspected"] = True

    return out
