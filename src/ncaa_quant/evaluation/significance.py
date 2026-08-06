"""Block / paired bootstrap CIs and the anti-metric rate formatter (DESIGN §7.3).

Every rate the reporting layer prints must carry a confidence interval. The
formatter refuses bare rates so win% on thin samples cannot silently appear
without uncertainty.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ncaa_quant.utils.seeding import set_global_seed

DEFAULT_N_BOOT: int = 2_000
DEFAULT_ALPHA: float = 0.05


class SignificanceError(ValueError):
    """Invalid bootstrap / rate-formatting inputs."""


class BareRateError(ValueError):
    """Raised when a rate is requested without a confidence interval.

    DESIGN §7.3 anti-metric rule: raw win% without a CI is culturally forbidden.
    """


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    """Point estimate with a percentile bootstrap (or analytic) CI."""

    estimate: float
    ci_low: float
    ci_high: float
    n: int
    alpha: float = DEFAULT_ALPHA
    method: str = "block_bootstrap"

    @property
    def level(self) -> float:
        return 1.0 - self.alpha


@dataclass(frozen=True, slots=True)
class RateWithCI:
    """A rate that cannot exist without its confidence interval.

    Construction requires ``ci_low`` / ``ci_high``. There is no constructor that
    accepts a bare rate alone — the anti-metric safeguard lives in the type.
    """

    rate: float
    ci_low: float
    ci_high: float
    n: int
    label: str = ""
    alpha: float = DEFAULT_ALPHA

    def __post_init__(self) -> None:
        if not np.isfinite(self.ci_low) or not np.isfinite(self.ci_high):
            raise BareRateError(
                "RateWithCI requires finite ci_low and ci_high "
                f"(got ci_low={self.ci_low!r}, ci_high={self.ci_high!r})"
            )
        if self.n < 0:
            raise SignificanceError(f"n must be non-negative, got {self.n}")

    def to_interval(self) -> ConfidenceInterval:
        return ConfidenceInterval(
            estimate=self.rate,
            ci_low=self.ci_low,
            ci_high=self.ci_high,
            n=self.n,
            alpha=self.alpha,
            method="rate",
        )


def format_rate_with_ci(rate: RateWithCI, *, digits: int = 1, as_percent: bool = True) -> str:
    """Format a rate **only** when a CI is attached.

    Parameters
    ----------
    rate:
        :class:`RateWithCI` — the only accepted input type. Passing a bare
        float is a TypeError at the call site; constructing without finite
        bounds raises :class:`BareRateError`.
    digits:
        Decimal places for percent (or proportion) display.
    as_percent:
        If True, multiply by 100 and append ``%``.
    """
    if not isinstance(rate, RateWithCI):
        raise BareRateError(
            f"format_rate_with_ci refuses bare rates; pass a RateWithCI (got {type(rate).__name__})"
        )
    # Re-validate in case someone bypassed __post_init__ via object.__new__.
    if not np.isfinite(rate.ci_low) or not np.isfinite(rate.ci_high):
        raise BareRateError("cannot format a rate without a finite confidence interval")

    if as_percent:
        est = 100.0 * rate.rate
        lo = 100.0 * rate.ci_low
        hi = 100.0 * rate.ci_high
        pct = f"{{:.{digits}f}}%"
        body = f"{pct.format(est)} [{pct.format(lo)}, {pct.format(hi)}]"
    else:
        fmt = f"{{:.{digits}f}}"
        body = f"{fmt.format(rate.rate)} [{fmt.format(rate.ci_low)}, {fmt.format(rate.ci_high)}]"

    level = int(round(100.0 * (1.0 - rate.alpha)))
    prefix = f"{rate.label}: " if rate.label else ""
    return f"{prefix}{body} ({level}% CI, n={rate.n})"


def format_interval(ci: ConfidenceInterval, *, digits: int = 4, label: str = "") -> str:
    """Format a general (non-rate) estimate with its CI."""
    if not np.isfinite(ci.ci_low) or not np.isfinite(ci.ci_high):
        raise BareRateError("cannot format an estimate without a finite confidence interval")
    fmt = f"{{:.{digits}f}}"
    level = int(round(100.0 * ci.level))
    prefix = f"{label}: " if label else ""
    return (
        f"{prefix}{fmt.format(ci.estimate)} "
        f"[{fmt.format(ci.ci_low)}, {fmt.format(ci.ci_high)}] "
        f"({level}% CI, n={ci.n}, {ci.method})"
    )


# ---------------------------------------------------------------------------
# Bootstrap engines
# ---------------------------------------------------------------------------


def _group_indices(blocks: Sequence[Any]) -> list[np.ndarray]:
    """Map block labels → row index arrays (order of first appearance)."""
    order: list[Any] = []
    buckets: dict[Any, list[int]] = {}
    for i, b in enumerate(blocks):
        key = b if not isinstance(b, (np.floating, float)) or np.isfinite(b) else None
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(i)
    return [np.asarray(buckets[k], dtype=int) for k in order]


def block_bootstrap(
    values: Sequence[float] | np.ndarray,
    blocks: Sequence[Any],
    *,
    statistic: Callable[[np.ndarray], float] | None = None,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
) -> ConfidenceInterval:
    """Block bootstrap CI, resampling whole blocks (e.g. weeks) with replacement.

    Respects intra-week correlation by never splitting a block across draws.
    """
    x = np.asarray(values, dtype=float).ravel()
    if x.size == 0:
        return ConfidenceInterval(
            estimate=float("nan"),
            ci_low=float("nan"),
            ci_high=float("nan"),
            n=0,
            alpha=alpha,
            method="block_bootstrap",
        )
    if len(blocks) != x.size:
        raise SignificanceError(f"blocks length {len(blocks)} != values length {x.size}")
    if not 0.0 < alpha < 1.0:
        raise SignificanceError(f"alpha must be in (0, 1), got {alpha}")
    if n_boot < 1:
        raise SignificanceError(f"n_boot must be ≥1, got {n_boot}")

    stat = statistic if statistic is not None else (lambda arr: float(np.mean(arr)))
    groups = _group_indices(blocks)
    n_groups = len(groups)
    set_global_seed(seed)
    rng = np.random.default_rng(seed)

    point = float(stat(x))
    boot = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        chosen = rng.integers(0, n_groups, size=n_groups)
        idx = np.concatenate([groups[i] for i in chosen])
        boot[b] = float(stat(x[idx]))

    lo = float(np.quantile(boot, alpha / 2.0))
    hi = float(np.quantile(boot, 1.0 - alpha / 2.0))
    return ConfidenceInterval(
        estimate=point,
        ci_low=lo,
        ci_high=hi,
        n=int(x.size),
        alpha=alpha,
        method="block_bootstrap",
    )


def iid_bootstrap(
    values: Sequence[float] | np.ndarray,
    *,
    statistic: Callable[[np.ndarray], float] | None = None,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
) -> ConfidenceInterval:
    """Naive i.i.d. bootstrap (for comparison — understates correlated variance)."""
    x = np.asarray(values, dtype=float).ravel()
    if x.size == 0:
        return ConfidenceInterval(
            estimate=float("nan"),
            ci_low=float("nan"),
            ci_high=float("nan"),
            n=0,
            alpha=alpha,
            method="iid_bootstrap",
        )
    if not 0.0 < alpha < 1.0:
        raise SignificanceError(f"alpha must be in (0, 1), got {alpha}")
    if n_boot < 1:
        raise SignificanceError(f"n_boot must be ≥1, got {n_boot}")

    stat = statistic if statistic is not None else (lambda arr: float(np.mean(arr)))
    set_global_seed(seed)
    rng = np.random.default_rng(seed)
    point = float(stat(x))
    n = int(x.size)
    boot = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        boot[b] = float(stat(x[idx]))

    lo = float(np.quantile(boot, alpha / 2.0))
    hi = float(np.quantile(boot, 1.0 - alpha / 2.0))
    return ConfidenceInterval(
        estimate=point,
        ci_low=lo,
        ci_high=hi,
        n=n,
        alpha=alpha,
        method="iid_bootstrap",
    )


def paired_block_bootstrap(
    champion: Sequence[float] | np.ndarray,
    challenger: Sequence[float] | np.ndarray,
    blocks: Sequence[Any],
    *,
    statistic: Callable[[np.ndarray], float] | None = None,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
) -> ConfidenceInterval:
    """Paired block bootstrap on ``champion − challenger`` (promotion test).

    Both series must share the same row order and block labels. The returned
    CI is for the paired difference of the statistic (champion − challenger).
    """
    a = np.asarray(champion, dtype=float).ravel()
    b = np.asarray(challenger, dtype=float).ravel()
    if a.size != b.size:
        raise SignificanceError(f"champion length {a.size} != challenger length {b.size}")
    if a.size == 0:
        return ConfidenceInterval(
            estimate=float("nan"),
            ci_low=float("nan"),
            ci_high=float("nan"),
            n=0,
            alpha=alpha,
            method="paired_block_bootstrap",
        )
    if len(blocks) != a.size:
        raise SignificanceError(f"blocks length {len(blocks)} != series length {a.size}")
    if not 0.0 < alpha < 1.0:
        raise SignificanceError(f"alpha must be in (0, 1), got {alpha}")
    if n_boot < 1:
        raise SignificanceError(f"n_boot must be ≥1, got {n_boot}")

    stat = statistic if statistic is not None else (lambda arr: float(np.mean(arr)))
    groups = _group_indices(blocks)
    n_groups = len(groups)
    set_global_seed(seed)
    rng = np.random.default_rng(seed)

    point = float(stat(a) - stat(b))
    boot = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        chosen = rng.integers(0, n_groups, size=n_groups)
        idx = np.concatenate([groups[j] for j in chosen])
        boot[i] = float(stat(a[idx]) - stat(b[idx]))

    lo = float(np.quantile(boot, alpha / 2.0))
    hi = float(np.quantile(boot, 1.0 - alpha / 2.0))
    return ConfidenceInterval(
        estimate=point,
        ci_low=lo,
        ci_high=hi,
        n=int(a.size),
        alpha=alpha,
        method="paired_block_bootstrap",
    )


def rate_ci_block(
    outcomes: Sequence[float] | np.ndarray,
    blocks: Sequence[Any],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
    label: str = "",
) -> RateWithCI:
    """Block-bootstrap CI for a binary/Bernoulli rate, returned as RateWithCI."""
    y = np.asarray(outcomes, dtype=float).ravel()
    ci = block_bootstrap(
        y,
        blocks,
        statistic=lambda arr: float(np.mean(arr)),
        n_boot=n_boot,
        alpha=alpha,
        seed=seed,
    )
    return RateWithCI(
        rate=ci.estimate,
        ci_low=ci.ci_low,
        ci_high=ci.ci_high,
        n=ci.n,
        label=label,
        alpha=alpha,
    )


def ci_width(ci: ConfidenceInterval) -> float:
    """Width of a confidence interval (``high − low``)."""
    if not np.isfinite(ci.ci_low) or not np.isfinite(ci.ci_high):
        return float("nan")
    return float(ci.ci_high - ci.ci_low)


def bootstrap_distribution(
    values: Sequence[float] | np.ndarray,
    blocks: Sequence[Any] | None,
    *,
    statistic: Callable[[np.ndarray], float],
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
    block: bool = True,
) -> np.ndarray:
    """Return the raw bootstrap replicates (for drawdown / path distributions)."""
    x = np.asarray(values, dtype=float).ravel()
    if x.size == 0:
        return np.asarray([], dtype=float)
    set_global_seed(seed)
    rng = np.random.default_rng(seed)
    boot = np.empty(n_boot, dtype=float)

    if block:
        if blocks is None:
            raise SignificanceError("blocks required when block=True")
        if len(blocks) != x.size:
            raise SignificanceError(f"blocks length {len(blocks)} != values length {x.size}")
        groups = _group_indices(blocks)
        n_groups = len(groups)
        for i in range(n_boot):
            chosen = rng.integers(0, n_groups, size=n_groups)
            idx = np.concatenate([groups[j] for j in chosen])
            boot[i] = float(statistic(x[idx]))
    else:
        n = int(x.size)
        for i in range(n_boot):
            idx = rng.integers(0, n, size=n)
            boot[i] = float(statistic(x[idx]))
    return boot


def summarize_bootstrap(
    replicates: np.ndarray,
    *,
    point: float,
    n: int,
    alpha: float = DEFAULT_ALPHA,
    method: str = "block_bootstrap",
) -> ConfidenceInterval:
    """Percentile CI from precomputed bootstrap replicates."""
    r = np.asarray(replicates, dtype=float).ravel()
    if r.size == 0 or not np.any(np.isfinite(r)):
        return ConfidenceInterval(
            estimate=point,
            ci_low=float("nan"),
            ci_high=float("nan"),
            n=n,
            alpha=alpha,
            method=method,
        )
    finite = r[np.isfinite(r)]
    return ConfidenceInterval(
        estimate=float(point),
        ci_low=float(np.quantile(finite, alpha / 2.0)),
        ci_high=float(np.quantile(finite, 1.0 - alpha / 2.0)),
        n=n,
        alpha=alpha,
        method=method,
    )


def metric_dict_with_cis(
    estimates: Mapping[str, float],
    cis: Mapping[str, ConfidenceInterval],
) -> dict[str, dict[str, float | int | str]]:
    """Zip point estimates with CIs into a report-ready nested dict."""
    out: dict[str, dict[str, float | int | str]] = {}
    for key, est in estimates.items():
        if key not in cis:
            raise SignificanceError(f"missing CI for metric {key!r}")
        ci = cis[key]
        out[key] = {
            "estimate": float(est),
            "ci_low": float(ci.ci_low),
            "ci_high": float(ci.ci_high),
            "n": int(ci.n),
            "alpha": float(ci.alpha),
            "method": ci.method,
        }
    return out
