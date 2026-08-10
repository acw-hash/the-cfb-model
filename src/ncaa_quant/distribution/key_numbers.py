"""Empirical key-number discretization kernel (DESIGN §2.3, audit A-7).

Learns a reallocation of continuous predictive mass onto exact integer margins
from historical residuals. Key-number bumps are **not** hand-tuned — they
emerge from the empirical residual offset distribution.

A-7: the kernel is conditional on the predicted margin. Games near pick'em land
on ±3 far more often than 20-point spreads; a pooled unconditional kernel
misallocates mass exactly where ATS pricing is most sensitive (spreads of
2.5–3.5 and 6.5–7.5). At minimum we condition on ``|μ|`` buckets.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import stats  # type: ignore[import-untyped]

#: Default ``|μ|`` bucket edges (inclusive lower, exclusive upper except last).
#: Chosen so 3 and 7 — the ATS-sensitive key numbers — sit in distinct buckets
#: from blowouts, where exact-margin mass concentrates differently.
DEFAULT_MU_ABS_EDGES: tuple[float, ...] = (0.0, 3.5, 7.5, 14.5, 21.5, float("inf"))


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


@dataclass
class ConditionalKeyNumberKernel:
    """Per-``|μ|``-bucket kernels with a pooled fallback.

    ``resolve(mu)`` returns the bucket whose ``[lo, hi)`` contains ``|μ|``, or
    the pooled kernel when that bucket was too thin to fit. Prediction paths
    that previously took a plain :class:`KeyNumberKernel` accept this type too.
    """

    buckets: tuple[tuple[float, float, KeyNumberKernel | None], ...]
    pooled: KeyNumberKernel
    edges: tuple[float, ...]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.pooled.n)

    def resolve(self, mu: float) -> KeyNumberKernel:
        """Select the kernel for ``|μ|``, falling back to the pooled fit."""
        abs_mu = abs(float(mu))
        for lo, hi, kernel in self.buckets:
            if lo <= abs_mu < hi and kernel is not None and kernel.n > 0:
                return kernel
        return self.pooled

    def weight(self, offset: int, *, mu: float = 0.0) -> float:
        return self.resolve(mu).weight(offset)


KernelLike = KeyNumberKernel | ConditionalKeyNumberKernel


def _fit_pooled_offsets(
    yt: np.ndarray,
    m: np.ndarray,
    *,
    min_count: int,
) -> KeyNumberKernel:
    """Fit a single (unconditional) offset-weight kernel on the given rows."""
    n = int(yt.size)
    if n == 0:
        return KeyNumberKernel(offset_weights={}, n=0)

    offsets = np.round(yt).astype(int) - np.round(m).astype(int)
    unique, counts = np.unique(offsets, return_counts=True)
    mean_count = float(np.mean(counts)) if counts.size else 1.0
    weights: dict[int, float] = {}
    for off, cnt in zip(unique.tolist(), counts.tolist(), strict=True):
        if int(cnt) < min_count:
            continue
        weights[int(off)] = float(cnt) / max(mean_count, 1e-9)

    if weights:
        mean_w = float(np.mean(list(weights.values())))
        if mean_w > 0:
            weights = {k: v / mean_w for k, v in weights.items()}

    return KeyNumberKernel(
        offset_weights=weights,
        n=n,
        meta={"min_count": min_count, "n_offsets": len(weights)},
    )


def fit_key_number_kernel(
    y: np.ndarray | Sequence[float],
    mu: np.ndarray | Sequence[float],
    *,
    min_count: int = 3,
    mu_abs_edges: Sequence[float] | None = DEFAULT_MU_ABS_EDGES,
    min_bucket_n: int = 40,
) -> ConditionalKeyNumberKernel | KeyNumberKernel:
    """Fit offset weights from historical ``(y, μ)`` pairs.

    Parameters
    ----------
    y, mu:
        Realized margins and (OOF) predicted means.
    min_count:
        Offsets with fewer than this many observations keep weight 1.0
        (avoids noisy spikes on rare offsets).
    mu_abs_edges:
        Edges of ``|μ|`` buckets. Pass ``None`` for a pooled (pre-A-7) kernel.
        Default is :data:`DEFAULT_MU_ABS_EDGES`.
    min_bucket_n:
        Buckets with fewer rows than this fall back to the pooled kernel at
        predict time (their per-bucket fit is stored as ``None``).
    """
    yt = np.asarray(y, dtype=float).reshape(-1)
    m = np.asarray(mu, dtype=float).reshape(-1)
    if yt.shape[0] != m.shape[0]:
        msg = "y/mu length mismatch"
        raise KeyNumberError(msg)
    mask = np.isfinite(yt) & np.isfinite(m)
    yt, m = yt[mask], m[mask]

    pooled = _fit_pooled_offsets(yt, m, min_count=min_count)
    if mu_abs_edges is None:
        return pooled

    edges = tuple(float(e) for e in mu_abs_edges)
    if len(edges) < 2:
        msg = "mu_abs_edges needs at least two values"
        raise KeyNumberError(msg)
    if any(edges[i] >= edges[i + 1] for i in range(len(edges) - 1)):
        msg = f"mu_abs_edges must be strictly increasing; got {edges}"
        raise KeyNumberError(msg)

    abs_mu = np.abs(m)
    bucket_list: list[tuple[float, float, KeyNumberKernel | None]] = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        in_bucket = (abs_mu >= lo) & (abs_mu < hi)
        n_bucket = int(in_bucket.sum())
        if n_bucket < int(min_bucket_n):
            bucket_list.append((lo, hi, None))
            continue
        bucket_list.append(
            (lo, hi, _fit_pooled_offsets(yt[in_bucket], m[in_bucket], min_count=min_count))
        )

    return ConditionalKeyNumberKernel(
        buckets=tuple(bucket_list),
        pooled=pooled,
        edges=edges,
        meta={
            "min_count": min_count,
            "min_bucket_n": min_bucket_n,
            "n_buckets": len(bucket_list),
            "n_buckets_fit": sum(1 for _, _, k in bucket_list if k is not None),
            "bucket_ns": {f"[{lo},{hi})": (0 if k is None else k.n) for lo, hi, k in bucket_list},
        },
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


def _resolve_kernel(kernel: KernelLike, mu: float) -> KeyNumberKernel:
    if isinstance(kernel, ConditionalKeyNumberKernel):
        return kernel.resolve(mu)
    return kernel


def discrete_margin_pmf(
    mu: float,
    sigma: float,
    kernel: KernelLike,
    *,
    margin_min: int = -80,
    margin_max: int = 80,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(integers, pmf)`` after key-number reallocation.

    Continuous Normal mass at each integer is multiplied by
    ``kernel.weight(k - round(μ))`` — resolved for this ``μ`` when the kernel
    is conditional — and renormalized.
    """
    resolved = _resolve_kernel(kernel, mu)
    integers = np.arange(margin_min, margin_max + 1, dtype=int)
    mass = continuous_integer_mass(mu, sigma, integers.astype(float))
    center = int(np.round(mu))
    weights = np.asarray(
        [resolved.weight(int(k) - center) for k in integers],
        dtype=float,
    )
    adj = mass * weights
    total = float(np.sum(adj))
    if total <= 0.0:
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
    kernel: KernelLike,
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


@dataclass(frozen=True)
class KeyNumberBucketValidation:
    """Empirical vs kernel P(exact margin) by predicted-``|μ|`` bucket."""

    key_margins: tuple[int, ...]
    rows: tuple[dict[str, Any], ...]
    meta: dict[str, Any] = field(default_factory=dict)

    def within_tolerance(self, *, atol: float = 0.02, rtol: float = 0.25) -> bool:
        """True when every reported cell is within absolute or relative tol."""
        for row in self.rows:
            for key in self.key_margins:
                emp = float(row["empirical"].get(key, 0.0))
                ker = float(row["kernel"].get(key, 0.0))
                if abs(emp - ker) <= atol:
                    continue
                if emp > 0 and abs(emp - ker) / emp <= rtol:
                    continue
                return False
        return True


def validate_key_number_kernel(
    y: np.ndarray | Sequence[float],
    mu: np.ndarray | Sequence[float],
    kernel: KernelLike,
    *,
    sigma: float | np.ndarray | Sequence[float] = 14.0,
    key_margins: Sequence[int] = (3, -3, 7, -7),
    mu_abs_edges: Sequence[float] = DEFAULT_MU_ABS_EDGES,
) -> KeyNumberBucketValidation:
    """Compare empirical exact-margin frequencies to kernel output by ``|μ|`` bucket.

    For each bucket, reports the fraction of games that landed on each key
    margin versus the mean kernel PMF at that margin evaluated at each row's
    ``(μ, σ)``. This is the A-7 acceptance check: a pooled kernel systematically
    misprices pick'em vs blowout buckets; a conditional one should track both.
    """
    yt = np.asarray(y, dtype=float).reshape(-1)
    m = np.asarray(mu, dtype=float).reshape(-1)
    if isinstance(sigma, (int, float, np.floating)):
        sig = np.full(m.shape, float(sigma))
    else:
        sig = np.asarray(sigma, dtype=float).reshape(-1)
    if yt.shape != m.shape or sig.shape != m.shape:
        msg = "y/mu/sigma length mismatch"
        raise KeyNumberError(msg)
    mask = np.isfinite(yt) & np.isfinite(m) & np.isfinite(sig)
    yt, m, sig = yt[mask], m[mask], sig[mask]

    keys = tuple(int(k) for k in key_margins)
    edges = tuple(float(e) for e in mu_abs_edges)
    abs_mu = np.abs(m)
    y_round = np.round(yt).astype(int)
    rows: list[dict[str, Any]] = []

    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        in_bucket = (abs_mu >= lo) & (abs_mu < hi)
        n_b = int(in_bucket.sum())
        if n_b == 0:
            continue
        emp: dict[int, float] = {}
        ker: dict[int, float] = {}
        for key in keys:
            emp[key] = float(np.mean(y_round[in_bucket] == key))
            # Mean kernel mass on this exact margin across rows in the bucket.
            masses = []
            for muj, sj in zip(m[in_bucket], sig[in_bucket], strict=True):
                integers, pmf = discrete_margin_pmf(float(muj), float(sj), kernel)
                idx = np.where(integers == key)[0]
                masses.append(float(pmf[idx[0]]) if idx.size else 0.0)
            ker[key] = float(np.mean(masses)) if masses else 0.0
        rows.append(
            {
                "lo": lo,
                "hi": hi,
                "n": n_b,
                "empirical": emp,
                "kernel": ker,
            }
        )

    return KeyNumberBucketValidation(
        key_margins=keys,
        rows=tuple(rows),
        meta={"n": int(yt.size), "edges": edges},
    )


__all__ = [
    "DEFAULT_MU_ABS_EDGES",
    "ConditionalKeyNumberKernel",
    "KeyNumberBucketValidation",
    "KeyNumberError",
    "KeyNumberKernel",
    "KernelLike",
    "continuous_integer_mass",
    "discrete_margin_pmf",
    "fit_key_number_kernel",
    "sample_discrete_margins",
    "validate_key_number_kernel",
]
