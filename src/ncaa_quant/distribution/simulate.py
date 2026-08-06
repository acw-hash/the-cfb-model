"""Monte Carlo engine over the joint (margin, total) distribution (DESIGN §2.3 / §2.6).

100k-draw seeded simulation produces probabilities for any spread, total, or
moneyline. Epistemic uncertainty mixes 50 Stage-1 posterior draws pushed
through the mapping layer into a mixture predictive distribution.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy import stats  # type: ignore[import-untyped]

from ncaa_quant.distribution.bivariate import BivariateParams
from ncaa_quant.distribution.key_numbers import KeyNumberKernel, sample_discrete_margins
from ncaa_quant.utils.seeding import set_global_seed

DEFAULT_N_DRAWS: int = 100_000
DEFAULT_EPISTEMIC_DRAWS: int = 50
MarketSide = Literal["home", "away", "over", "under"]


class SimulateError(ValueError):
    """Raised for simulation contract violations."""


@dataclass
class JointDraws:
    """Monte Carlo draws for one batch of games.

    ``margins`` / ``totals`` have shape ``(n_games, n_draws)``.
    """

    margins: np.ndarray
    totals: np.ndarray
    seed: int
    n_draws: int
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def n_games(self) -> int:
        return int(self.margins.shape[0])


@dataclass(frozen=True)
class MarketProbabilities:
    """Probabilities for a single line query (sums to 1 for two-way + push)."""

    p_side: float
    p_other: float
    p_push: float
    line: float
    market: str

    def two_way_side(self) -> float:
        """Bernoulli side probability net of push (for graded non-push outcomes).

        Outcomes exclude pushes; scoring must use ``p_side / (p_side + p_other)``
        so ``P(cover) + P(not cover) == 1`` conditional on no push. Continuous
        Gaussian draws have ``p_push == 0`` and this is a no-op.
        """
        denom = float(self.p_side) + float(self.p_other)
        if denom <= 0.0:
            return 0.5
        return float(self.p_side) / denom


def two_way_side_prob(p: MarketProbabilities) -> float:
    """Return push-conditional side probability (see :meth:`MarketProbabilities.two_way_side`)."""
    return p.two_way_side()


# ---------------------------------------------------------------------------
# Core sampling
# ---------------------------------------------------------------------------


def sample_bivariate_normal(
    params: BivariateParams,
    *,
    n_draws: int = DEFAULT_N_DRAWS,
    seed: int = 0,
) -> JointDraws:
    """Draw ``(M, T)`` from the continuous bivariate Normal (no key-number)."""
    if n_draws < 1:
        msg = f"n_draws must be ≥1, got {n_draws}"
        raise SimulateError(msg)
    set_global_seed(seed)
    rng = np.random.default_rng(seed)
    n = params.n
    margins = np.empty((n, n_draws), dtype=float)
    totals = np.empty((n, n_draws), dtype=float)
    for i in range(n):
        mean = np.array([params.mu_m[i], params.mu_t[i]], dtype=float)
        cov = np.array(
            [
                [params.sigma_m[i] ** 2, params.rho * params.sigma_m[i] * params.sigma_t[i]],
                [params.rho * params.sigma_m[i] * params.sigma_t[i], params.sigma_t[i] ** 2],
            ],
            dtype=float,
        )
        # Numerical guard for near-singular corr.
        cov = cov + np.eye(2) * 1e-12
        draws = rng.multivariate_normal(mean, cov, size=n_draws)
        margins[i] = draws[:, 0]
        totals[i] = draws[:, 1]
    return JointDraws(margins=margins, totals=totals, seed=seed, n_draws=n_draws)


def sample_joint(
    params: BivariateParams,
    *,
    kernel: KeyNumberKernel | None = None,
    n_draws: int = DEFAULT_N_DRAWS,
    seed: int = 0,
) -> JointDraws:
    """Sample the production joint: key-number margin + conditional total.

    Margin is drawn from the empirical key-number PMF when ``kernel`` is
    provided; otherwise continuous. Total is drawn from the conditional
    Normal ``T | M`` under the bivariate parameterization (using continuous
    latent margin for the conditioning step when the kernel is active, via
    an auxiliary continuous draw).
    """
    if n_draws < 1:
        raise SimulateError(f"n_draws must be ≥1, got {n_draws}")
    set_global_seed(seed)
    rng = np.random.default_rng(seed)

    continuous = sample_bivariate_normal(params, n_draws=n_draws, seed=seed)
    if kernel is None:
        return continuous

    # Re-draw margins from discrete kernel; re-draw totals conditional on the
    # continuous latent margin so ρ is preserved in expectation.
    margins = sample_discrete_margins(
        params.mu_m,
        params.sigma_m,
        kernel,
        n_draws=n_draws,
        rng=rng,
    )
    # Conditional T | M_cont: μ_T + ρ σ_T/σ_M (M - μ_M), var = σ_T²(1-ρ²)
    rho = float(params.rho)
    totals = np.empty_like(margins)
    for i in range(params.n):
        m_lat = continuous.margins[i]
        cond_mu = params.mu_t[i] + rho * (params.sigma_t[i] / params.sigma_m[i]) * (
            m_lat - params.mu_m[i]
        )
        cond_sig = params.sigma_t[i] * np.sqrt(max(1.0 - rho**2, 1e-12))
        totals[i] = rng.normal(cond_mu, cond_sig)
    # Replace continuous margins with discrete key-number draws.
    return JointDraws(
        margins=margins,
        totals=totals,
        seed=seed,
        n_draws=n_draws,
        meta={"key_numbers": True, "rho": rho},
    )


# ---------------------------------------------------------------------------
# Probability queries
# ---------------------------------------------------------------------------


def _game_draws(draws: JointDraws, game_index: int) -> tuple[np.ndarray, np.ndarray]:
    if game_index < 0 or game_index >= draws.n_games:
        msg = f"game_index {game_index} out of range [0, {draws.n_games})"
        raise SimulateError(msg)
    return draws.margins[game_index], draws.totals[game_index]


def moneyline_probs(draws: JointDraws, *, game_index: int = 0) -> MarketProbabilities:
    """``P(home win)``, ``P(away win)``, ``P(tie)`` from margin draws."""
    m, _t = _game_draws(draws, game_index)
    p_home = float(np.mean(m > 0))
    p_away = float(np.mean(m < 0))
    p_push = float(np.mean(m == 0))
    return MarketProbabilities(
        p_side=p_home,
        p_other=p_away,
        p_push=p_push,
        line=0.0,
        market="ml",
    )


def spread_cover_probs(
    draws: JointDraws,
    spread: float,
    *,
    game_index: int = 0,
    side: Literal["home", "away"] = "home",
) -> MarketProbabilities:
    """ATS probabilities vs a home-centric spread (home gives ``spread`` points).

    Home covers when ``margin + spread > 0``; push when equal to 0.
    """
    m, _t = _game_draws(draws, game_index)
    # margin_home + spread: positive ⇒ home covers (spread is home line, e.g. -7).
    adj = m + float(spread)
    p_home_cover = float(np.mean(adj > 0))
    p_away_cover = float(np.mean(adj < 0))
    p_push = float(np.mean(adj == 0))
    if side == "home":
        return MarketProbabilities(
            p_side=p_home_cover,
            p_other=p_away_cover,
            p_push=p_push,
            line=float(spread),
            market="ats_home",
        )
    return MarketProbabilities(
        p_side=p_away_cover,
        p_other=p_home_cover,
        p_push=p_push,
        line=float(spread),
        market="ats_away",
    )


def total_probs(
    draws: JointDraws,
    total_line: float,
    *,
    game_index: int = 0,
    side: Literal["over", "under"] = "over",
) -> MarketProbabilities:
    """Over/under/push probabilities vs a total line."""
    _m, t = _game_draws(draws, game_index)
    line = float(total_line)
    p_over = float(np.mean(t > line))
    p_under = float(np.mean(t < line))
    p_push = float(np.mean(t == line))
    if side == "over":
        return MarketProbabilities(
            p_side=p_over,
            p_other=p_under,
            p_push=p_push,
            line=line,
            market="ou_over",
        )
    return MarketProbabilities(
        p_side=p_under,
        p_other=p_over,
        p_push=p_push,
        line=line,
        market="ou_under",
    )


def probs_sum_to_one(p: MarketProbabilities, *, atol: float = 1e-9) -> bool:
    """Property helper: three-way probabilities sum to 1."""
    s = p.p_side + p.p_other + p.p_push
    return bool(abs(s - 1.0) <= atol) and all(
        0.0 - atol <= x <= 1.0 + atol for x in (p.p_side, p.p_other, p.p_push)
    )


def two_way_probs_sum_to_one(p: MarketProbabilities, *, atol: float = 1e-9) -> bool:
    """Net-of-push side probabilities sum to 1."""
    a = p.two_way_side()
    other = (
        float(p.p_other) / (float(p.p_side) + float(p.p_other))
        if (float(p.p_side) + float(p.p_other)) > 0.0
        else 0.5
    )
    return bool(abs(a + other - 1.0) <= atol)


# ---------------------------------------------------------------------------
# Epistemic mixture (§2.6)
# ---------------------------------------------------------------------------

# Mapping: rating_sample_features (n_games, n_features) → dict with mu_m, sigma_m, mu_t, sigma_t
MappingFn = Callable[[np.ndarray], Mapping[str, np.ndarray]]


@dataclass
class EpistemicMixture:
    """Mixture predictive from Stage-1 posterior draws through the mapping."""

    params: BivariateParams
    n_posterior_draws: int
    seed: int
    meta: dict[str, Any] = field(default_factory=dict)


def mix_epistemic_predictions(
    posterior_feature_draws: np.ndarray,
    mapping_fn: MappingFn,
    *,
    rho: float,
    seed: int = 0,
) -> EpistemicMixture:
    """Push Stage-1 posterior feature draws through the mapping layer.

    Parameters
    ----------
    posterior_feature_draws:
        Shape ``(n_posterior, n_games, n_features)`` — typically 50 draws.
    mapping_fn:
        Maps a single draw's feature matrix ``(n_games, n_features)`` to a
        dict with keys ``mu_m``, ``sigma_m``, ``mu_t``, ``sigma_t``.
    rho:
        Residual correlation used for the mixture bivariate (held fixed).

    Returns
    -------
    EpistemicMixture
        Mixture mean μ and total variance (law of total variance across
        posterior draws) as :class:`BivariateParams`.
    """
    arr = np.asarray(posterior_feature_draws, dtype=float)
    if arr.ndim != 3:
        msg = f"posterior_feature_draws must be (S, n, f), got {arr.shape}"
        raise SimulateError(msg)
    n_post, n_games, _n_feat = arr.shape
    if n_post < 1:
        raise SimulateError("need ≥1 posterior draw")

    set_global_seed(seed)
    mu_m_s = np.empty((n_post, n_games), dtype=float)
    sig_m_s = np.empty((n_post, n_games), dtype=float)
    mu_t_s = np.empty((n_post, n_games), dtype=float)
    sig_t_s = np.empty((n_post, n_games), dtype=float)

    for s in range(n_post):
        out = mapping_fn(arr[s])
        mu_m_s[s] = np.asarray(out["mu_m"], dtype=float).reshape(-1)
        sig_m_s[s] = np.maximum(np.asarray(out["sigma_m"], dtype=float).reshape(-1), 1e-8)
        mu_t_s[s] = np.asarray(out["mu_t"], dtype=float).reshape(-1)
        sig_t_s[s] = np.maximum(np.asarray(out["sigma_t"], dtype=float).reshape(-1), 1e-8)

    # Law of total variance: Var = E[σ²] + Var(μ) across posterior draws.
    mu_m = np.mean(mu_m_s, axis=0)
    mu_t = np.mean(mu_t_s, axis=0)
    var_mu_m = np.var(mu_m_s, axis=0)
    var_mu_t = np.var(mu_t_s, axis=0)
    var_m = np.mean(sig_m_s**2, axis=0) + var_mu_m
    var_t = np.mean(sig_t_s**2, axis=0) + var_mu_t

    params = BivariateParams(
        mu_m=mu_m,
        sigma_m=np.sqrt(var_m),
        mu_t=mu_t,
        sigma_t=np.sqrt(var_t),
        rho=float(rho),
        meta={
            "epistemic_draws": n_post,
            "stage1_var_m": var_mu_m,
            "stage1_var_t": var_mu_t,
            "aleatoric_var_m": np.mean(sig_m_s**2, axis=0),
            "aleatoric_var_t": np.mean(sig_t_s**2, axis=0),
        },
    )
    return EpistemicMixture(
        params=params,
        n_posterior_draws=n_post,
        seed=seed,
        meta={
            "rho": float(rho),
            "mean_stage1_var_m": float(np.mean(var_mu_m)),
            "mean_aleatoric_var_m": float(np.mean(np.mean(sig_m_s**2, axis=0))),
        },
    )


def default_epistemic_draws() -> int:
    """DESIGN §2.6: 50 posterior draws."""
    return DEFAULT_EPISTEMIC_DRAWS


# ---------------------------------------------------------------------------
# Metrics (acceptance / §7.3 helpers — evaluation package is Task 21)
# ---------------------------------------------------------------------------


def crps_gaussian(y_true: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> float:
    """Mean Gaussian CRPS (same closed form as HPO)."""
    yt = np.asarray(y_true, dtype=float)
    m = np.asarray(mu, dtype=float)
    s = np.maximum(np.asarray(sigma, dtype=float), 1e-8)
    if yt.size == 0:
        return float("nan")
    z = (yt - m) / s
    phi = stats.norm.pdf(z)
    Phi = stats.norm.cdf(z)
    crps = s * (z * (2.0 * Phi - 1.0) + 2.0 * phi - 1.0 / np.sqrt(np.pi))
    return float(np.mean(crps))


def brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean Brier score for binary outcomes."""
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    mask = np.isfinite(p) & np.isfinite(y)
    if not np.any(mask):
        return float("nan")
    return float(np.mean((p[mask] - y[mask]) ** 2))


def log_loss(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean Bernoulli log-loss."""
    p = np.clip(np.asarray(probs, dtype=float), 1e-15, 1.0 - 1e-15)
    y = np.asarray(outcomes, dtype=float)
    mask = np.isfinite(p) & np.isfinite(y)
    if not np.any(mask):
        return float("nan")
    p, y = p[mask], y[mask]
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def proportional_devig(prob_a: float, prob_b: float) -> tuple[float, float]:
    """Proportional de-vig of a two-way implied-probability pair."""
    a, b = float(prob_a), float(prob_b)
    s = a + b
    if s <= 0:
        return 0.5, 0.5
    return a / s, b / s


def american_to_implied(american: float) -> float:
    """American odds → raw implied probability (with vig)."""
    a = float(american)
    if a == 0:
        raise SimulateError("american odds cannot be 0")
    if a > 0:
        return 100.0 / (a + 100.0)
    return (-a) / ((-a) + 100.0)


def pit_values(y_true: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """Probability integral transform under Normal(μ, σ)."""
    yt = np.asarray(y_true, dtype=float)
    m = np.asarray(mu, dtype=float)
    s = np.maximum(np.asarray(sigma, dtype=float), 1e-8)
    pit: np.ndarray = np.asarray(stats.norm.cdf((yt - m) / s), dtype=float)
    return pit


def reliability_curve(
    probs: np.ndarray,
    outcomes: np.ndarray,
    *,
    n_bins: int = 10,
) -> pd.DataFrame:
    """Binned reliability diagram data (mean predicted vs mean outcome)."""
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    mask = np.isfinite(p) & np.isfinite(y)
    p, y = p[mask], y[mask]
    if p.size == 0:
        return pd.DataFrame(columns=["bin_left", "bin_right", "mean_pred", "mean_outcome", "count"])
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows: list[dict[str, Any]] = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        sel = (p >= lo) & (p <= hi) if i == n_bins - 1 else (p >= lo) & (p < hi)
        if not np.any(sel):
            continue
        rows.append(
            {
                "bin_left": float(lo),
                "bin_right": float(hi),
                "mean_pred": float(np.mean(p[sel])),
                "mean_outcome": float(np.mean(y[sel])),
                "count": int(np.sum(sel)),
            }
        )
    return pd.DataFrame(rows)


def save_reliability_diagram(
    probs: np.ndarray,
    outcomes: np.ndarray,
    path: Path | str,
    *,
    title: str = "Reliability diagram",
    n_bins: int = 10,
) -> Path:
    """Write a reliability diagram PNG."""
    curve = reliability_curve(probs, outcomes, n_bins=n_bins)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], ls="--", color="gray", label="ideal")
    if not curve.empty:
        ax.plot(curve["mean_pred"], curve["mean_outcome"], marker="o", label="model")
    ax.set_xlabel("Predicted probability")
    ax.set_ylabel("Observed frequency")
    ax.set_title(title)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def save_pit_histogram(
    y_true: np.ndarray,
    mu: np.ndarray,
    sigma: np.ndarray,
    path: Path | str,
    *,
    title: str = "PIT histogram",
    n_bins: int = 10,
) -> Path:
    """Write a PIT histogram PNG."""
    pit = pit_values(y_true, mu, sigma)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.hist(pit, bins=n_bins, range=(0, 1), density=True, color="steelblue", edgecolor="white")
    ax.axhline(1.0, color="gray", ls="--", label="uniform")
    ax.set_xlabel("PIT")
    ax.set_ylabel("Density")
    ax.set_title(title)
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out
