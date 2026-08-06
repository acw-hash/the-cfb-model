"""Empirical key-number discretization kernel (DESIGN §2.3).

Learns a reallocation of continuous predictive mass onto exact integer margins
from historical residuals. Key-number bumps are **not** hand-tuned — they
emerge from the empirical residual offset distribution.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import stats  # type: ignore[import-untyped]


class KeyNumberError(ValueError):
    """Raised for key-number kernel contract violations."""


@dataclass
class KeyNumberKernel:
    """Empirical PMF over integer residual offsets ``round(y) - round(μ)``.

    At prediction time the continuous Normal mass on each integer margin is
    reweighted by the relative frequency of the corresponding offset in the
    training residuals, then renormalized. Offsets never seen in training keep
    weight 1.0 (no bump).
    """

    # offset → relative weight (mean 1 over observed offsets after fit)
    offset_weights: dict[int, float]
    n: int
    meta: dict[str, Any] = field(default_factory=dict)

    def weight(self, offset: int) -> float:
        return float(self.offset_weights.get(int(offset), 1.0))


def fit_key_number_kernel(
    y: np.ndarray | Sequence[float],
    mu: np.ndarray | Sequence[float],
    *,
    min_count: int = 3,
) -> KeyNumberKernel:
    """Fit offset weights from historical ``(y, μ)`` pairs.

    Parameters
    ----------
    y, mu:
        Realized margins and (OOF) predicted means.
    min_count:
        Offsets with fewer than this many observations keep weight 1.0
        (avoids noisy spikes on rare offsets).
    """
    yt = np.asarray(y, dtype=float).reshape(-1)
    m = np.asarray(mu, dtype=float).reshape(-1)
    if yt.shape[0] != m.shape[0]:
        msg = "y/mu length mismatch"
        raise KeyNumberError(msg)
    mask = np.isfinite(yt) & np.isfinite(m)
    yt, m = yt[mask], m[mask]
    n = int(yt.size)
    if n == 0:
        return KeyNumberKernel(offset_weights={}, n=0)

    offsets = np.round(yt).astype(int) - np.round(m).astype(int)
    unique, counts = np.unique(offsets, return_counts=True)
    # Expected under a flat offset model over observed support.
    mean_count = float(np.mean(counts)) if counts.size else 1.0
    weights: dict[int, float] = {}
    for off, cnt in zip(unique.tolist(), counts.tolist(), strict=True):
        if int(cnt) < min_count:
            continue
        # Relative to mean count → >1 boosts common key offsets (3, 7, …).
        weights[int(off)] = float(cnt) / max(mean_count, 1e-9)

    # Renormalize so mean weight over stored offsets is 1 (neutral scale).
    if weights:
        mean_w = float(np.mean(list(weights.values())))
        if mean_w > 0:
            weights = {k: v / mean_w for k, v in weights.items()}

    return KeyNumberKernel(
        offset_weights=weights,
        n=n,
        meta={"min_count": min_count, "n_offsets": len(weights)},
    )


def continuous_integer_mass(
    mu: float,
    sigma: float,
    integers: np.ndarray,
) -> np.ndarray:
    """``P(round(X)=k)`` under ``X ~ N(μ, σ)`` via ``Φ(k+0.5) - Φ(k-0.5)``."""
    s = max(float(sigma), 1e-8)
    lo = stats.norm.cdf((integers - 0.5 - mu) / s)
    hi = stats.norm.cdf((integers + 0.5 - mu) / s)
    mass: np.ndarray = np.maximum(hi - lo, 0.0)
    return mass


def discrete_margin_pmf(
    mu: float,
    sigma: float,
    kernel: KeyNumberKernel,
    *,
    margin_min: int = -80,
    margin_max: int = 80,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(integers, pmf)`` after key-number reallocation.

    Continuous Normal mass at each integer is multiplied by
    ``kernel.weight(k - round(μ))`` and renormalized.
    """
    integers = np.arange(margin_min, margin_max + 1, dtype=int)
    mass = continuous_integer_mass(mu, sigma, integers.astype(float))
    center = int(np.round(mu))
    weights = np.asarray(
        [kernel.weight(int(k) - center) for k in integers],
        dtype=float,
    )
    adj = mass * weights
    total = float(np.sum(adj))
    if total <= 0.0:
        # Degenerate → fall back to continuous mass.
        total = float(np.sum(mass))
        adj = mass
    if total <= 0.0:
        pmf = np.zeros_like(adj)
        mid = int(np.clip(center - margin_min, 0, len(pmf) - 1))
        pmf[mid] = 1.0
        return integers, pmf
    return integers, adj / total


def sample_discrete_margins(
    mu: np.ndarray,
    sigma: np.ndarray,
    kernel: KeyNumberKernel,
    *,
    n_draws: int,
    rng: np.random.Generator,
    margin_min: int = -80,
    margin_max: int = 80,
) -> np.ndarray:
    """Sample integer margins from the kernel-adjusted PMF.

    Returns shape ``(n_games, n_draws)``.
    """
    mu_a = np.asarray(mu, dtype=float).reshape(-1)
    sig_a = np.maximum(np.asarray(sigma, dtype=float).reshape(-1), 1e-8)
    n = mu_a.shape[0]
    out = np.empty((n, n_draws), dtype=float)
    for i in range(n):
        integers, pmf = discrete_margin_pmf(
            float(mu_a[i]),
            float(sig_a[i]),
            kernel,
            margin_min=margin_min,
            margin_max=margin_max,
        )
        out[i] = rng.choice(integers.astype(float), size=n_draws, p=pmf)
    return out
