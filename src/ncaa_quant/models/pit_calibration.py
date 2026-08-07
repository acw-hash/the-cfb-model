"""Distributional (PIT) recalibration of the predictive distributions (§2.6, §5.2).

One monotone map is fit on the PIT values of the OOF **margin** predictive CDF,
and one on the **total**. Applying the map to the CDF recalibrates every derived
market probability at once — moneyline, ATS at any line, OU at any line — so the
§2.2 internal-consistency guarantee survives calibration.

Why this replaces per-market probability maps (audit A-4): three independently
fitted isotonic maps on ML, ATS@close and OU@close have no reason to agree with
each other. Nothing stops the ML map from moving `P(home wins)` up while the ATS
map moves `P(home covers 0)` down, and those are the same event. A single monotone
map on the CDF cannot do that, because all three probabilities are read off one
recalibrated distribution.

Method (Kuleshov et al. 2018, *Accurate Uncertainties for Deep Learning Using
Calibrated Regression*): with `u_i = F_i(y_i)` the PIT of each OOF observation, a
perfectly calibrated forecaster gives `u ~ Uniform(0, 1)`. Fitting `R` as the
empirical CDF of the observed PIT values and reporting `F̃ = R ∘ F` makes the PIT
uniform by construction. `R` is monotone, so `F̃` is a valid CDF and all
derived probabilities stay coherent and ordered in the line.

Per-market reliability diagrams and Cox slope/intercept remain **diagnostics**;
they are never the fitting target. For the fundamental stack the targets are
market-free by construction: the margin and total distributions know nothing
about the closing line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from scipy import stats  # type: ignore[import-untyped]
from sklearn.isotonic import IsotonicRegression  # type: ignore[import-untyped]

DistributionTarget = Literal["margin", "total"]
PitMapKind = Literal["isotonic", "beta", "none"]

MARKET_FREE_TARGETS: frozenset[str] = frozenset({"margin", "total"})
"""Calibration targets that carry no market information (fundamental stack)."""

#: Minimum OOF rows before isotonic-on-PIT is preferred over the parametric map.
DEFAULT_THIN_N: int = 200
#: Minimum distinct PIT values for isotonic to be stable.
DEFAULT_THIN_UNIQUE: int = 20
#: Interior bound; a calibrated CDF may not return exactly 0 or 1.
_EPS_INTERIOR: float = 1e-6
#: Grid resolution for numerically inverting the monotone map.
_INVERSE_GRID: int = 4096


class PitCalibrationError(ValueError):
    """Raised for PIT-recalibration contract violations."""


def assert_market_free_target(target: str) -> None:
    """Raise when a fundamental-stack calibration target embeds market data.

    §5.2: "Fundamental-stack calibration targets are market-free (distribution /
    moneyline PIT); ATS@close reliability is never a fitting target for the
    fundamental stack." Fitting on a market-derived target would let the closing
    line in through the calibration layer after §4 kept it out of the features.
    """
    if target not in MARKET_FREE_TARGETS:
        raise PitCalibrationError(
            f"calibration target {target!r} is not market-free; the fundamental "
            f"stack may only calibrate on {sorted(MARKET_FREE_TARGETS)}. Per-market "
            "reliability (ML / ATS@close / OU@close) is a diagnostic, never a fitting target."
        )


# ---------------------------------------------------------------------------
# PIT computation
# ---------------------------------------------------------------------------


def pit_values_normal(
    y: np.ndarray | Sequence[float],
    mu: np.ndarray | Sequence[float],
    sigma: np.ndarray | Sequence[float],
) -> np.ndarray:
    """PIT of a Normal predictive distribution: ``Φ((y − μ) / σ)``.

    Non-finite inputs and non-positive σ yield ``nan`` rather than a fabricated
    value; the caller drops them.
    """
    y_a = np.asarray(y, dtype=float)
    mu_a = np.asarray(mu, dtype=float)
    sd_a = np.asarray(sigma, dtype=float)
    if not (y_a.shape == mu_a.shape == sd_a.shape):
        raise PitCalibrationError("y, mu and sigma must share a shape")
    out = np.full(y_a.shape, np.nan, dtype=float)
    ok = np.isfinite(y_a) & np.isfinite(mu_a) & np.isfinite(sd_a) & (sd_a > 0.0)
    out[ok] = stats.norm.cdf((y_a[ok] - mu_a[ok]) / sd_a[ok])
    return out


def pit_values_from_draws(
    y: np.ndarray | Sequence[float],
    draws: np.ndarray,
) -> np.ndarray:
    """PIT from Monte Carlo draws: the mid-PIT ``P(X < y) + 0.5·P(X = y)``.

    ``draws`` is ``(n_games, n_draws)``. Mid-PIT is used because simulated margins
    are integer-valued once the key-number kernel is applied, and the plain
    ``P(X ≤ y)`` convention would make the PIT non-uniform purely from ties.
    """
    y_a = np.asarray(y, dtype=float)
    d = np.asarray(draws, dtype=float)
    if d.ndim != 2 or d.shape[0] != y_a.size:
        raise PitCalibrationError(
            f"draws must be (n_games, n_draws) matching y; got {d.shape} for n={y_a.size}"
        )
    below = np.mean(d < y_a[:, None], axis=1)
    equal = np.mean(d == y_a[:, None], axis=1)
    out = below + 0.5 * equal
    out[~np.isfinite(y_a)] = np.nan
    return np.asarray(out, dtype=float)


def ks_uniform(pit: np.ndarray | Sequence[float]) -> float:
    """Kolmogorov-Smirnov distance of PIT values from Uniform(0, 1).

    0 is perfect calibration. Reported before and after fitting so the map's
    effect is visible rather than assumed.
    """
    u = np.asarray(pit, dtype=float)
    u = u[np.isfinite(u)]
    if u.size < 2:
        return float("nan")
    return float(stats.kstest(u, "uniform").statistic)


# ---------------------------------------------------------------------------
# The recalibration map
# ---------------------------------------------------------------------------


@dataclass
class PitRecalibrator:
    """A fitted monotone map from raw CDF values to calibrated CDF values."""

    target: DistributionTarget
    kind: PitMapKind
    n_oof: int
    ks_before: float
    ks_after: float
    _iso: IsotonicRegression | None = field(default=None, repr=False)
    _beta: tuple[float, float] | None = field(default=None, repr=False)
    meta: dict[str, Any] = field(default_factory=dict)
    applied: bool = False
    """Gated OFF by default, like every other calibration layer in the system."""

    def transform_cdf(self, cdf_values: np.ndarray | Sequence[float]) -> np.ndarray:
        """Map raw CDF values ``F(x)`` to calibrated ``R(F(x))``.

        Monotone and strictly interior, so the output is a valid CDF and no
        derived probability can be exactly 0 or 1.
        """
        p = np.asarray(cdf_values, dtype=float)
        out = np.full(p.shape, np.nan, dtype=float)
        ok = np.isfinite(p)
        if not np.any(ok):
            return out

        clipped = np.clip(p[ok], 0.0, 1.0)
        if self.kind == "none":
            mapped = clipped
        elif self.kind == "isotonic":
            if self._iso is None:
                raise PitCalibrationError("isotonic PIT map missing")
            mapped = np.asarray(self._iso.predict(clipped), dtype=float)
        elif self.kind == "beta":
            if self._beta is None:
                raise PitCalibrationError("beta PIT parameters missing")
            a, b = self._beta
            mapped = np.asarray(stats.beta.cdf(clipped, a, b), dtype=float)
        else:
            raise PitCalibrationError(f"unknown PIT map kind={self.kind}")

        out[ok] = np.clip(mapped, _EPS_INTERIOR, 1.0 - _EPS_INTERIOR)
        return out

    def side_prob(self, raw_side_prob: np.ndarray | Sequence[float]) -> np.ndarray:
        """Recalibrate an upper-tail probability ``P(X > line)``.

        ``P(X > line) = 1 − F(line)``, so the calibrated value is
        ``1 − R(1 − p_raw)``. Every margin-derived market (moneyline, ATS at any
        line) goes through this one map, which is what keeps them consistent.
        """
        p = np.asarray(raw_side_prob, dtype=float)
        return 1.0 - self.transform_cdf(1.0 - p)

    def transform_quantile_level(self, q: np.ndarray | Sequence[float]) -> np.ndarray:
        """Invert the map: which raw quantile level yields calibrated level ``q``.

        Used to read calibrated quantiles off the raw distribution, since
        ``F̃⁻¹(q) = F⁻¹(R⁻¹(q))``. Inverted numerically on a grid because the
        isotonic map has no closed form.
        """
        target = np.asarray(q, dtype=float)
        grid = np.linspace(0.0, 1.0, _INVERSE_GRID)
        mapped = self.transform_cdf(grid)
        # np.interp needs an increasing x; the map is monotone non-decreasing, so
        # deduplicate flats to keep the inverse single-valued.
        keep = np.concatenate(([True], np.diff(mapped) > 0))
        out = np.interp(target, mapped[keep], grid[keep])
        return np.asarray(out, dtype=float)


def _fit_beta_on_pit(u: np.ndarray) -> tuple[float, float]:
    """MLE of Beta(a, b) on PIT values, with the support fixed to [0, 1]."""
    interior = np.clip(u, _EPS_INTERIOR, 1.0 - _EPS_INTERIOR)
    a, b, _loc, _scale = stats.beta.fit(interior, floc=0.0, fscale=1.0)
    if not (np.isfinite(a) and np.isfinite(b)) or a <= 0.0 or b <= 0.0:
        return 1.0, 1.0  # identity: Beta(1, 1) is Uniform
    return float(a), float(b)


def fit_pit_recalibrator(
    pit: np.ndarray | Sequence[float],
    *,
    target: DistributionTarget,
    thin_n: int = DEFAULT_THIN_N,
    thin_unique: int = DEFAULT_THIN_UNIQUE,
    force_kind: PitMapKind | None = None,
) -> PitRecalibrator:
    """Fit the monotone PIT map for one predictive distribution.

    Isotonic-on-PIT when there are enough distinct OOF values; otherwise the
    parametric Beta map, per §2.6's thin-data fallback. ``force_kind='none'``
    returns identity for bake-offs.
    """
    assert_market_free_target(target)

    u = np.asarray(pit, dtype=float)
    u = u[np.isfinite(u)]
    if u.size < 4:
        raise PitCalibrationError(f"need ≥4 finite PIT values to calibrate {target}, got {u.size}")
    if np.any(u < 0.0) or np.any(u > 1.0):
        raise PitCalibrationError(f"PIT values for {target} must lie in [0, 1]")

    n = int(u.size)
    n_unique = int(np.unique(np.round(u, 9)).size)
    thin = n < thin_n or n_unique < thin_unique
    kind: PitMapKind = force_kind or ("beta" if thin else "isotonic")

    iso: IsotonicRegression | None = None
    beta: tuple[float, float] | None = None
    meta: dict[str, Any] = {"thin": thin, "n_oof": n, "n_unique_pit": n_unique}

    if kind == "isotonic":
        # R = empirical CDF of the PIT values, fit as a monotone map.
        order = np.argsort(u, kind="mergesort")
        x = u[order]
        y = (np.arange(1, n + 1) - 0.5) / n
        iso = IsotonicRegression(
            y_min=_EPS_INTERIOR,
            y_max=1.0 - _EPS_INTERIOR,
            increasing=True,
            out_of_bounds="clip",
        )
        iso.fit(x, y)
    elif kind == "beta":
        beta = _fit_beta_on_pit(u)
        meta["beta_a"], meta["beta_b"] = beta
    elif kind != "none":
        raise PitCalibrationError(f"unsupported PIT map kind={kind}")

    cal = PitRecalibrator(
        target=target,
        kind=kind,
        n_oof=n,
        ks_before=ks_uniform(u),
        ks_after=float("nan"),
        _iso=iso,
        _beta=beta,
        meta=meta,
    )
    cal.ks_after = ks_uniform(cal.transform_cdf(u))
    return cal


@dataclass
class DistributionalCalibrationBundle:
    """The margin and total PIT maps for one fitted model version."""

    margin: PitRecalibrator | None = None
    total: PitRecalibrator | None = None

    def get(self, target: DistributionTarget) -> PitRecalibrator | None:
        return self.margin if target == "margin" else self.total

    def report(self) -> dict[str, Any]:
        """Flat diagnostic record for the manifest / calibration report."""
        out: dict[str, Any] = {}
        names: tuple[DistributionTarget, ...] = ("margin", "total")
        for name in names:
            cal = self.get(name)
            if cal is None:
                out[f"{name}_pit_kind"] = "absent"
                continue
            out[f"{name}_pit_kind"] = cal.kind
            out[f"{name}_pit_applied"] = cal.applied
            out[f"{name}_pit_n_oof"] = cal.n_oof
            out[f"{name}_pit_ks_before"] = cal.ks_before
            out[f"{name}_pit_ks_after"] = cal.ks_after
        return out


def gate_pit_recalibrator(
    cal: PitRecalibrator,
    holdout_pit: np.ndarray | Sequence[float],
    *,
    min_ks_improvement: float = 0.0,
) -> PitRecalibrator:
    """Apply the map only if it improves held-out PIT uniformity.

    Same posture as every other calibration layer: default OFF, switched on by
    evidence. The comparison is on held-out PIT values, not the ones fit on —
    isotonic-on-PIT is guaranteed to look perfect in sample.
    """
    u = np.asarray(holdout_pit, dtype=float)
    u = u[np.isfinite(u)]
    if u.size < 4:
        cal.applied = False
        cal.meta["gate_reason"] = f"holdout too small (n={u.size})"
        return cal

    ks_raw = ks_uniform(u)
    ks_cal = ks_uniform(cal.transform_cdf(u))
    improved = bool(np.isfinite(ks_raw) and np.isfinite(ks_cal))
    improved = improved and (ks_raw - ks_cal) > float(min_ks_improvement)
    cal.applied = improved
    cal.meta.update(
        {
            "gate_holdout_n": int(u.size),
            "gate_ks_raw": float(ks_raw),
            "gate_ks_calibrated": float(ks_cal),
            "gate_passed": improved,
        }
    )
    return cal


def apply_pit_calibration(
    raw_side_prob: np.ndarray | Sequence[float],
    cal: PitRecalibrator | None,
) -> np.ndarray:
    """Recalibrate an upper-tail probability when the map is gated on."""
    p = np.asarray(raw_side_prob, dtype=float)
    if cal is None or not cal.applied:
        return p
    return cal.side_prob(p)


__all__ = [
    "MARKET_FREE_TARGETS",
    "DistributionTarget",
    "DistributionalCalibrationBundle",
    "PitCalibrationError",
    "PitMapKind",
    "PitRecalibrator",
    "apply_pit_calibration",
    "assert_market_free_target",
    "fit_pit_recalibrator",
    "gate_pit_recalibrator",
    "ks_uniform",
    "pit_values_from_draws",
    "pit_values_normal",
]
