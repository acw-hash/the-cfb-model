"""Split conformal / CQR layer on quantile heads (DESIGN §2.6).

Conformalized Quantile Regression (Romano et al.) wrapped on the LightGBM
quantile set, using the trailing 2 seasons as the calibration set. Reports
empirical coverage vs nominal at 50% / 80% / 95%.
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
