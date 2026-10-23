"""Statistics for the situational-angle harness.

Everything here is pure numpy/scipy on arrays the caller has already filtered.
Standard errors are cluster-robust by ``season-week`` (CR1 small-sample
factor, t reference with ``G − 1`` degrees of freedom); cover-rate intervals
are season-week block bootstraps; multiple testing is Benjamini–Hochberg.
Units: points unless stated. No randomness outside the seeded bootstrap RNG.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy import stats  # type: ignore[import-untyped]


@dataclass(frozen=True)
class ClusterMean:
    """Mean with season-week cluster-robust SE."""

    n: int
    n_clusters: int
    mean: float
    se: float
    t: float
    p_two_sided: float
    ci_lo: float
    ci_hi: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite_pairs(y: np.ndarray, clusters: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(y, dtype=float)
    c = np.asarray(clusters)
    keep = np.isfinite(y)
    return y[keep], c[keep]


def _nan_cluster_mean(n: int = 0, n_clusters: int = 0) -> ClusterMean:
    nan = float("nan")
    return ClusterMean(n, n_clusters, nan, nan, nan, nan, nan, nan)


def cluster_mean(
    y: Sequence[float] | np.ndarray,
    clusters: Sequence[Any] | np.ndarray,
    *,
    ci: float = 0.95,
) -> ClusterMean:
    """Sample mean with CR1 cluster-robust SE (clusters = season-week blocks).

    ``Var(mean) = G/(G−1) · Σ_g (Σ_{i∈g} (y_i − ȳ))² / n²``; p-value from a t
    distribution with ``G − 1`` df (two-sided, H0: mean = 0).
    """
    yy, cc = _finite_pairs(np.asarray(y, dtype=float), np.asarray(clusters))
    n = int(yy.size)
    if n == 0:
        return _nan_cluster_mean()
    mean = float(yy.mean())
    _, inv = np.unique(cc, return_inverse=True)
    g = int(inv.max()) + 1
    if g < 2:
        return ClusterMean(
            n, g, mean, float("nan"), float("nan"), float("nan"), float("nan"), float("nan")
        )
    resid = yy - mean
    sums = np.bincount(inv, weights=resid, minlength=g)
    var = (g / (g - 1.0)) * float(np.sum(sums**2)) / float(n) ** 2
    se = math.sqrt(var) if var > 0 else 0.0
    if se == 0.0:
        t = float("inf") if mean != 0 else 0.0
        p = 0.0 if mean != 0 else 1.0
        crit = 0.0
    else:
        t = mean / se
        p = float(2.0 * stats.t.sf(abs(t), df=g - 1))
        crit = float(stats.t.ppf(0.5 + ci / 2.0, df=g - 1))
    return ClusterMean(n, g, mean, se, float(t), float(p), mean - crit * se, mean + crit * se)


@dataclass(frozen=True)
class BootstrapRate:
    """Cover rate with a season-week block-bootstrap interval."""

    n: int
    n_clusters: int
    rate: float
    ci_lo: float
    ci_hi: float
    reps: int
    seed: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def block_bootstrap_rate(
    indicator: Sequence[float] | np.ndarray,
    clusters: Sequence[Any] | np.ndarray,
    *,
    reps: int,
    seed: int,
    ci: float = 0.95,
) -> BootstrapRate:
    """Rate of a 0/1 indicator (nulls dropped) with cluster block-bootstrap CI.

    Clusters are resampled with replacement ``reps`` times; the rate is
    recomputed on the concatenated resample. Percentile interval.
    """
    yy, cc = _finite_pairs(np.asarray(indicator, dtype=float), np.asarray(clusters))
    n = int(yy.size)
    if n == 0:
        nan = float("nan")
        return BootstrapRate(0, 0, nan, nan, nan, int(reps), int(seed))
    rate = float(yy.mean())
    labels, inv = np.unique(cc, return_inverse=True)
    g = int(labels.size)
    if g < 2 or reps <= 0:
        return BootstrapRate(n, g, rate, float("nan"), float("nan"), int(reps), int(seed))
    sums = np.bincount(inv, weights=yy, minlength=g)
    counts = np.bincount(inv, minlength=g).astype(float)
    rng = np.random.default_rng(int(seed))
    draws = rng.integers(0, g, size=(int(reps), g))
    boot_rate = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    alpha = (1.0 - ci) / 2.0
    lo, hi = np.quantile(boot_rate, [alpha, 1.0 - alpha])
    return BootstrapRate(n, g, rate, float(lo), float(hi), int(reps), int(seed))


@dataclass(frozen=True)
class OlsResult:
    """OLS with CR1 cluster-robust covariance."""

    n: int
    n_clusters: int
    names: tuple[str, ...]
    coef: tuple[float, ...]
    se: tuple[float, ...]
    t: tuple[float, ...]
    p: tuple[float, ...]
    r_squared: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "n_clusters": self.n_clusters,
            "terms": [
                {"name": nm, "coef": c, "se": s, "t": tt, "p": pp}
                for nm, c, s, tt, pp in zip(
                    self.names, self.coef, self.se, self.t, self.p, strict=True
                )
            ],
            "r_squared": self.r_squared,
        }

    def term(self, name: str) -> dict[str, float]:
        if name not in self.names:
            return {
                "coef": float("nan"),
                "se": float("nan"),
                "t": float("nan"),
                "p": float("nan"),
            }
        i = self.names.index(name)
        return {"coef": self.coef[i], "se": self.se[i], "t": self.t[i], "p": self.p[i]}


def ols_cluster(
    y: np.ndarray,
    x: np.ndarray,
    names: Sequence[str],
    clusters: np.ndarray,
) -> OlsResult:
    """OLS of ``y`` on design ``x`` (intercept included by caller) with CR1 SEs.

    Rows with any non-finite value are dropped. Rank-deficient designs drop
    all-zero columns (reported with NaN coefficients).
    """
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    c = np.asarray(clusters)
    keep = np.isfinite(y) & np.all(np.isfinite(x), axis=1)
    y, x, c = y[keep], x[keep], c[keep]
    n, k = x.shape
    names_t = tuple(str(s) for s in names)
    nan_k = tuple(float("nan") for _ in range(k))
    if n == 0:
        return OlsResult(0, 0, names_t, nan_k, nan_k, nan_k, nan_k, float("nan"))
    active = np.array([np.any(x[:, j] != 0) for j in range(k)])
    xa = x[:, active]
    labels, inv = np.unique(c, return_inverse=True)
    g = int(labels.size)
    if n <= xa.shape[1] or g < 2:
        return OlsResult(n, g, names_t, nan_k, nan_k, nan_k, nan_k, float("nan"))
    xtx = xa.T @ xa
    try:
        xtx_inv = np.linalg.pinv(xtx)
    except np.linalg.LinAlgError:  # pragma: no cover
        return OlsResult(n, g, names_t, nan_k, nan_k, nan_k, nan_k, float("nan"))
    beta = xtx_inv @ xa.T @ y
    resid = y - xa @ beta
    meat = np.zeros((xa.shape[1], xa.shape[1]))
    for gi in range(g):
        m = inv == gi
        s = xa[m].T @ resid[m]
        meat += np.outer(s, s)
    kk = xa.shape[1]
    factor = (g / (g - 1.0)) * ((n - 1.0) / max(n - kk, 1.0))
    cov = factor * xtx_inv @ meat @ xtx_inv
    se_a = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        t_a = np.where(se_a > 0, beta / se_a, np.nan)
    p_a = 2.0 * stats.t.sf(np.abs(t_a), df=g - 1)
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    coef = np.full(k, np.nan)
    se = np.full(k, np.nan)
    t = np.full(k, np.nan)
    p = np.full(k, np.nan)
    coef[active] = beta
    se[active] = se_a
    t[active] = t_a
    p[active] = p_a
    return OlsResult(
        n,
        g,
        names_t,
        tuple(float(v) for v in coef),
        tuple(float(v) for v in se),
        tuple(float(v) for v in t),
        tuple(float(v) for v in p),
        float(r2),
    )


@dataclass(frozen=True)
class BhDecision:
    """One hypothesis under Benjamini–Hochberg."""

    hypothesis_id: str
    p: float
    rank: int
    threshold: float
    rejected: bool
    p_adjusted: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def benjamini_hochberg(pvalues: Mapping[str, float], *, q: float) -> dict[str, BhDecision]:
    """BH step-up at FDR ``q`` across the supplied p-values (NaN p → not tested).

    Returns rank, threshold ``q·rank/m``, rejection flag, and BH-adjusted p.
    """
    tested = {k: float(v) for k, v in pvalues.items() if v is not None and math.isfinite(float(v))}
    out: dict[str, BhDecision] = {}
    for k in pvalues:
        if k not in tested:
            out[k] = BhDecision(k, float("nan"), 0, float("nan"), False, float("nan"))
    items = list(tested.items())
    m = len(items)
    if m == 0:
        return out
    items.sort(key=lambda kv: kv[1])
    ps = np.array([p for _, p in items])
    ranks = np.arange(1, m + 1)
    thresholds = q * ranks / m
    passing = np.nonzero(ps <= thresholds)[0]
    cutoff = int(passing.max()) + 1 if passing.size else 0
    adj = np.minimum.accumulate((ps * m / ranks)[::-1])[::-1]
    adj = np.clip(adj, 0.0, 1.0)
    for i, (k, p) in enumerate(items):
        out[k] = BhDecision(k, p, int(ranks[i]), float(thresholds[i]), i < cutoff, float(adj[i]))
    return out


def sign_table(
    seasons: np.ndarray,
    values: np.ndarray,
    cover: np.ndarray,
) -> list[dict[str, Any]]:
    """Per-season n, mean, sign and cover rate (nulls dropped)."""
    seasons = np.asarray(seasons)
    values = np.asarray(values, dtype=float)
    cover = np.asarray(cover, dtype=float)
    rows: list[dict[str, Any]] = []
    for s in sorted({int(v) for v in seasons}):
        m = seasons == s
        v = values[m]
        v = v[np.isfinite(v)]
        c = cover[m]
        c = c[np.isfinite(c)]
        mean = float(v.mean()) if v.size else float("nan")
        rows.append(
            {
                "season": int(s),
                "n": int(v.size),
                "mean": mean,
                "sign": "+" if mean > 0 else "-" if mean < 0 else "0" if v.size else "",
                "cover_rate": float(c.mean()) if c.size else float("nan"),
                "n_cover": int(c.size),
            }
        )
    return rows


__all__ = [
    "BhDecision",
    "BootstrapRate",
    "ClusterMean",
    "OlsResult",
    "benjamini_hochberg",
    "block_bootstrap_rate",
    "cluster_mean",
    "ols_cluster",
    "sign_table",
]
