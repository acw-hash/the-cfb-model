"""Conformal / CQR layer on quantile heads (DESIGN §2.6, audit A-9).

Conformalized Quantile Regression (Romano et al.) on the LightGBM quantile set,
initialized from the trailing 2 seasons as a split-conformal calibration set.

Coverage language (A-9): split conformal's finite-sample guarantee requires
exchangeability between calibration and test points. Season-over-season drift
(rule changes, portal-era shift, scoring-environment movement) violates that,
so the layer provides **approximate** coverage under mild drift — it does not
deliver an exchangeability-based guarantee in production. Production uses
Adaptive Conformal Inference (Gibbs & Candès): online α adjustment that tracks
realized coverage, with the trailing-2-season split fit as the initializer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.models.heads.quantile import QUANTILES, quantile_column

TargetKind = Literal["margin", "total"]

# Nominal → (low quantile, high quantile) from the Task-17 QUANTILES set.
NOMINAL_TO_QUANTILES: Mapping[float, tuple[float, float]] = {
    0.5: (0.25, 0.75),
    0.8: (0.10, 0.90),
    0.95: (0.05, 0.95),
}

DEFAULT_CALIBRATION_SEASONS: int = 2


class ConformalError(ValueError):
    """Raised for conformal contract violations."""


@dataclass(frozen=True)
class CoverageReport:
    """Empirical vs nominal interval coverage for one level."""

    nominal: float
    empirical: float
    n: int
    mean_width: float


@dataclass
class CQRResult:
    """Fitted CQR conformity scores and coverage diagnostics."""

    target: TargetKind
    calibration_seasons: tuple[int, ...]
    # conformity quantile (score threshold) per nominal level
    score_thresholds: dict[float, float]
    coverage: dict[float, CoverageReport]
    meta: dict[str, Any] = field(default_factory=dict)


def _q_cols(target: TargetKind, q_lo: float, q_hi: float) -> tuple[str, str]:
    t: Literal["margin", "total"] = "margin" if target == "margin" else "total"
    return quantile_column(t, q_lo), quantile_column(t, q_hi)


def conformity_scores(
    y: np.ndarray,
    q_lo: np.ndarray,
    q_hi: np.ndarray,
) -> np.ndarray:
    """CQR conformity: ``max(q_lo - y, y - q_hi)`` (Romano et al.)."""
    yt = np.asarray(y, dtype=float)
    lo = np.asarray(q_lo, dtype=float)
    hi = np.asarray(q_hi, dtype=float)
    scores: np.ndarray = np.maximum(lo - yt, yt - hi)
    return scores


def _finite_mask(*arrays: np.ndarray) -> np.ndarray:
    mask = np.ones(arrays[0].shape[0], dtype=bool)
    for a in arrays:
        mask &= np.isfinite(a)
    return mask


def select_trailing_seasons(
    seasons: Sequence[int],
    *,
    n_trailing: int = DEFAULT_CALIBRATION_SEASONS,
) -> tuple[int, ...]:
    """Return the last ``n_trailing`` distinct seasons (sorted ascending)."""
    uniq = sorted({int(s) for s in seasons})
    if len(uniq) < n_trailing:
        msg = f"need ≥{n_trailing} seasons for conformal calibration, got {uniq}"
        raise ConformalError(msg)
    return tuple(uniq[-n_trailing:])


def fit_cqr(
    frame: pd.DataFrame,
    *,
    target: TargetKind,
    label_column: str | None = None,
    season_column: str = "season",
    calibration_seasons: Sequence[int] | None = None,
    n_trailing: int = DEFAULT_CALIBRATION_SEASONS,
    nominal_levels: Sequence[float] = (0.5, 0.8, 0.95),
    quantiles: Sequence[float] = QUANTILES,
) -> CQRResult:
    """Fit CQR score thresholds on the trailing calibration seasons.

    Interval at nominal level ``1 - α`` uses the matching quantile pair from
    :data:`NOMINAL_TO_QUANTILES`, inflated by the ``(1-α)(1+1/n)`` conformal
    quantile of calibration conformity scores.
    """
    y_col = label_column or ("realized_margin" if target == "margin" else "realized_total")
    if y_col not in frame.columns or season_column not in frame.columns:
        msg = f"frame missing '{y_col}' or '{season_column}'"
        raise ConformalError(msg)

    if calibration_seasons is None:
        calib_seasons = select_trailing_seasons(
            frame[season_column].tolist(),
            n_trailing=n_trailing,
        )
    else:
        calib_seasons = tuple(int(s) for s in calibration_seasons)

    calib = frame.loc[frame[season_column].isin(calib_seasons)].copy()
    if calib.empty:
        msg = f"no rows in calibration seasons {calib_seasons}"
        raise ConformalError(msg)

    thresholds: dict[float, float] = {}
    coverage: dict[float, CoverageReport] = {}

    for level in nominal_levels:
        if float(level) not in NOMINAL_TO_QUANTILES:
            msg = f"unsupported nominal level {level}; expected one of {list(NOMINAL_TO_QUANTILES)}"
            raise ConformalError(msg)
        q_lo_v, q_hi_v = NOMINAL_TO_QUANTILES[float(level)]
        if q_lo_v not in quantiles or q_hi_v not in quantiles:
            msg = f"quantile pair ({q_lo_v}, {q_hi_v}) not in head quantiles {tuple(quantiles)}"
            raise ConformalError(msg)
        lo_col, hi_col = _q_cols(target, q_lo_v, q_hi_v)
        if lo_col not in calib.columns or hi_col not in calib.columns:
            msg = f"missing quantile columns {lo_col}, {hi_col}"
            raise ConformalError(msg)

        y = np.asarray(calib[y_col], dtype=float)
        lo = np.asarray(calib[lo_col], dtype=float)
        hi = np.asarray(calib[hi_col], dtype=float)
        mask = _finite_mask(y, lo, hi)
        y, lo, hi = y[mask], lo[mask], hi[mask]
        n = int(y.size)
        if n < 2:
            msg = f"need ≥2 finite calib rows for level={level}, got {n}"
            raise ConformalError(msg)

        scores = conformity_scores(y, lo, hi)
        # Split-conformal quantile index (Vovk / Romano): ceil((n+1)(1-α))/n
        alpha = 1.0 - float(level)
        q_level = min(1.0, np.ceil((n + 1) * (1.0 - alpha)) / n)
        threshold = float(np.quantile(scores, q_level, method="higher"))
        thresholds[float(level)] = threshold

        lo_c = lo - threshold
        hi_c = hi + threshold
        covered = (y >= lo_c) & (y <= hi_c)
        coverage[float(level)] = CoverageReport(
            nominal=float(level),
            empirical=float(np.mean(covered)),
            n=n,
            mean_width=float(np.mean(hi_c - lo_c)),
        )

    return CQRResult(
        target=target,
        calibration_seasons=calib_seasons,
        score_thresholds=thresholds,
        coverage=coverage,
        meta={"n_trailing": n_trailing},
    )


def conformalize_intervals(
    frame: pd.DataFrame,
    cqr: CQRResult,
    *,
    nominal: float,
) -> pd.DataFrame:
    """Return a copy with ``cqr_lo`` / ``cqr_hi`` columns for ``nominal``."""
    if float(nominal) not in cqr.score_thresholds:
        msg = f"nominal {nominal} not in fitted thresholds {list(cqr.score_thresholds)}"
        raise ConformalError(msg)
    q_lo_v, q_hi_v = NOMINAL_TO_QUANTILES[float(nominal)]
    lo_col, hi_col = _q_cols(cqr.target, q_lo_v, q_hi_v)
    thr = cqr.score_thresholds[float(nominal)]
    out = frame.copy()
    out["cqr_lo"] = np.asarray(out[lo_col], dtype=float) - thr
    out["cqr_hi"] = np.asarray(out[hi_col], dtype=float) + thr
    out["cqr_nominal"] = float(nominal)
    return out


def evaluate_coverage(
    y_true: np.ndarray | Sequence[float],
    lo: np.ndarray | Sequence[float],
    hi: np.ndarray | Sequence[float],
    *,
    nominal: float,
) -> CoverageReport:
    """Empirical coverage of ``[lo, hi]`` vs ``y_true``."""
    yt = np.asarray(y_true, dtype=float)
    a = np.asarray(lo, dtype=float)
    b = np.asarray(hi, dtype=float)
    mask = _finite_mask(yt, a, b)
    yt, a, b = yt[mask], a[mask], b[mask]
    n = int(yt.size)
    if n == 0:
        return CoverageReport(
            nominal=float(nominal),
            empirical=float("nan"),
            n=0,
            mean_width=float("nan"),
        )
    covered = (yt >= a) & (yt <= b)
    return CoverageReport(
        nominal=float(nominal),
        empirical=float(np.mean(covered)),
        n=n,
        mean_width=float(np.mean(b - a)),
    )


def coverage_table(cqr: CQRResult) -> pd.DataFrame:
    """Tabular coverage report."""
    rows = [
        {
            "nominal": r.nominal,
            "empirical": r.empirical,
            "n": r.n,
            "mean_width": r.mean_width,
            "target": cqr.target,
            "calibration_seasons": list(cqr.calibration_seasons),
        }
        for r in cqr.coverage.values()
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Adaptive Conformal Inference (production variant)
# ---------------------------------------------------------------------------


DEFAULT_ACI_GAMMA: float = 0.005


@dataclass
class AdaptiveCQR:
    """Online-α CQR: split-conformal initializer + ACI updates (A-9).

    Holds the calibration conformity scores from :func:`fit_cqr` and an online
    miscoverage level ``alpha_t``. Each observation updates

        α_{t+1} = clip(α_t + γ · (α_target − err_t), 0, 1)

    where ``err_t`` is 1 if the interval missed and 0 otherwise. The interval
    threshold is the ``(1 − α_t)``-quantile of the frozen calibration scores
    (with the usual ``ceil((n+1)(1-α))/n`` finite-sample index).
    """

    target: TargetKind
    nominal: float
    target_alpha: float
    gamma: float
    alpha_t: float
    calibration_scores: np.ndarray
    calibration_seasons: tuple[int, ...]
    n_updates: int = 0
    n_misses: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def empirical_coverage(self) -> float:
        if self.n_updates == 0:
            return float("nan")
        return 1.0 - float(self.n_misses) / float(self.n_updates)

    def threshold(self) -> float:
        """Current CQR score threshold from ``alpha_t`` and calibration scores."""
        scores = np.asarray(self.calibration_scores, dtype=float)
        n = int(scores.size)
        if n < 1:
            raise ConformalError("AdaptiveCQR has no calibration scores")
        alpha = float(np.clip(self.alpha_t, 0.0, 1.0))
        # Same finite-sample index as fit_cqr; α→0 → use the max score.
        q_level = min(1.0, np.ceil((n + 1) * (1.0 - alpha)) / n)
        return float(np.quantile(scores, q_level, method="higher"))

    def interval(self, q_lo: float, q_hi: float) -> tuple[float, float]:
        """Widen raw quantile bounds by the current threshold."""
        thr = self.threshold()
        return float(q_lo) - thr, float(q_hi) + thr

    def update(self, y: float, q_lo: float, q_hi: float) -> dict[str, float]:
        """Observe one outcome, return coverage diagnostics, advance ``alpha_t``."""
        lo, hi = self.interval(q_lo, q_hi)
        covered = bool(lo <= float(y) <= hi)
        err = 0.0 if covered else 1.0
        self.n_updates += 1
        self.n_misses += int(err)
        self.alpha_t = float(
            np.clip(
                self.alpha_t + float(self.gamma) * (self.target_alpha - err),
                0.0,
                1.0,
            )
        )
        return {
            "covered": float(covered),
            "err": err,
            "alpha_t": self.alpha_t,
            "lo": lo,
            "hi": hi,
            "threshold": self.threshold(),
        }


def fit_adaptive_cqr(
    frame: pd.DataFrame,
    *,
    target: TargetKind,
    nominal: float = 0.8,
    label_column: str | None = None,
    season_column: str = "season",
    calibration_seasons: Sequence[int] | None = None,
    n_trailing: int = DEFAULT_CALIBRATION_SEASONS,
    gamma: float = DEFAULT_ACI_GAMMA,
    quantiles: Sequence[float] = QUANTILES,
) -> AdaptiveCQR:
    """Initialize ACI from a trailing-season split-conformal fit.

    ``alpha_t`` starts at the nominal miscoverage ``1 - nominal``. Subsequent
    calls to :meth:`AdaptiveCQR.update` track realized coverage online.
    """
    if float(nominal) not in NOMINAL_TO_QUANTILES:
        msg = f"unsupported nominal level {nominal}; expected one of {list(NOMINAL_TO_QUANTILES)}"
        raise ConformalError(msg)

    cqr = fit_cqr(
        frame,
        target=target,
        label_column=label_column,
        season_column=season_column,
        calibration_seasons=calibration_seasons,
        n_trailing=n_trailing,
        nominal_levels=(float(nominal),),
        quantiles=quantiles,
    )

    y_col = label_column or ("realized_margin" if target == "margin" else "realized_total")
    q_lo_v, q_hi_v = NOMINAL_TO_QUANTILES[float(nominal)]
    lo_col, hi_col = _q_cols(target, q_lo_v, q_hi_v)
    calib = frame.loc[frame[season_column].isin(cqr.calibration_seasons)]
    y = np.asarray(calib[y_col], dtype=float)
    lo = np.asarray(calib[lo_col], dtype=float)
    hi = np.asarray(calib[hi_col], dtype=float)
    mask = _finite_mask(y, lo, hi)
    scores = conformity_scores(y[mask], lo[mask], hi[mask])
    target_alpha = 1.0 - float(nominal)

    return AdaptiveCQR(
        target=target,
        nominal=float(nominal),
        target_alpha=target_alpha,
        gamma=float(gamma),
        alpha_t=target_alpha,
        calibration_scores=scores,
        calibration_seasons=cqr.calibration_seasons,
        meta={
            "initializer": "split_cqr",
            "split_threshold": cqr.score_thresholds[float(nominal)],
            "n_calib": int(scores.size),
            "gamma": float(gamma),
        },
    )


def run_aci_stream(
    aci: AdaptiveCQR,
    y: np.ndarray | Sequence[float],
    q_lo: np.ndarray | Sequence[float],
    q_hi: np.ndarray | Sequence[float],
) -> pd.DataFrame:
    """Replay a stream of observations through ACI; return per-row diagnostics."""
    yt = np.asarray(y, dtype=float).ravel()
    lo = np.asarray(q_lo, dtype=float).ravel()
    hi = np.asarray(q_hi, dtype=float).ravel()
    if yt.shape != lo.shape or yt.shape != hi.shape:
        msg = "y/q_lo/q_hi length mismatch"
        raise ConformalError(msg)
    rows: list[dict[str, float]] = []
    for i in range(yt.size):
        if not (np.isfinite(yt[i]) and np.isfinite(lo[i]) and np.isfinite(hi[i])):
            continue
        rows.append(aci.update(float(yt[i]), float(lo[i]), float(hi[i])))
    return pd.DataFrame(rows)
