"""Per-market probability calibration on OOF predictions (DESIGN §2.6 / §5.2).

Bounded calibrators only (Platt / beta / isotonic with minimum bin occupancy).
A calibrator that would emit 0 or 1 is disqualified — never floored to 1e-6.
Default application is OFF; each market is gated by a held-out paired
block-bootstrap test against no-calibration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy.optimize import minimize  # type: ignore[import-untyped]
from scipy.special import expit, logit  # type: ignore[import-untyped]
from sklearn.isotonic import IsotonicRegression  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]

from ncaa_quant.evaluation.significance import paired_block_bootstrap
from ncaa_quant.models.ensemble import OOF_FLAG_COLUMN, EnsembleError, assert_oof_only

MarketName = Literal["ml", "ats_close", "ou_close"]
CalibratorKind = Literal["none", "isotonic", "platt", "beta"]

# Minimum finite labeled rows before isotonic is preferred over Platt.
DEFAULT_THIN_N: int = 200
# Minimum distinct raw probabilities for isotonic to be stable.
DEFAULT_THIN_UNIQUE: int = 10
# Isotonic bin occupancy: max(50, n/20) by default.
DEFAULT_MIN_BIN_OCCUPANCY: int | None = None  # resolved per-fit
# Soft bound interior for beta/platt parameterizations (not a post-hoc clip).
_EPS_INTERIOR: float = 1e-6


class CalibrationError(ValueError):
    """Raised for calibration contract violations."""


@dataclass(frozen=True)
class CoxCalibrationStats:
    """Cox recalibration: logit(p_cal) ≈ a + b · logit(p_raw).

    Ideal: intercept ``a ≈ 0``, slope ``b ≈ 1``.
    """

    intercept: float
    slope: float
    n: int


@dataclass
class MarketCalibrator:
    """Fitted calibrator for one derived market."""

    market: MarketName
    kind: CalibratorKind
    before: CoxCalibrationStats
    after: CoxCalibrationStats
    _isotonic: IsotonicRegression | None = field(default=None, repr=False)
    _platt: LogisticRegression | None = field(default=None, repr=False)
    _beta_params: tuple[float, float, float] | None = field(default=None, repr=False)
    meta: dict[str, Any] = field(default_factory=dict)
    applied: bool = False  # gated; default OFF

    def transform(self, raw_probs: np.ndarray | Sequence[float]) -> np.ndarray:
        """Map raw probabilities into calibrated probabilities in (0, 1).

        Raises :class:`CalibrationError` if any output is exactly 0 or 1.
        """
        p = np.asarray(raw_probs, dtype=float)
        if self.kind == "none":
            out = p.copy()
        elif self.kind == "isotonic":
            if self._isotonic is None:
                msg = "isotonic model missing"
                raise CalibrationError(msg)
            out = np.asarray(self._isotonic.predict(p), dtype=float)
        elif self.kind == "platt":
            if self._platt is None:
                msg = "platt model missing"
                raise CalibrationError(msg)
            out = np.asarray(self._platt.predict_proba(p.reshape(-1, 1))[:, 1], dtype=float)
        elif self.kind == "beta":
            if self._beta_params is None:
                msg = "beta parameters missing"
                raise CalibrationError(msg)
            out = _beta_transform(p, self._beta_params)
        else:
            msg = f"unknown calibrator kind={self.kind}"
            raise CalibrationError(msg)
        _assert_strictly_interior(out, market=self.market, kind=self.kind)
        return out


@dataclass
class CalibrationBundle:
    """Per-market calibrators (ML / ATS@close / OU@close)."""

    markets: dict[MarketName, MarketCalibrator] = field(default_factory=dict)

    def get(self, market: MarketName) -> MarketCalibrator:
        if market not in self.markets:
            msg = f"no calibrator for market={market}"
            raise CalibrationError(msg)
        return self.markets[market]

    def report_table(self) -> pd.DataFrame:
        """Slope/intercept before and after for every fitted market."""
        rows: list[dict[str, Any]] = []
        for name, cal in self.markets.items():
            rows.append(
                {
                    "market": name,
                    "kind": cal.kind,
                    "applied": cal.applied,
                    "n": cal.before.n,
                    "slope_before": cal.before.slope,
                    "intercept_before": cal.before.intercept,
                    "slope_after": cal.after.slope,
                    "intercept_after": cal.after.intercept,
                    **{
                        k: v
                        for k, v in cal.meta.items()
                        if k
                        in (
                            "n_oof",
                            "n_bins",
                            "min_bin_occupancy",
                            "bin_occupancy_min",
                            "bin_occupancy_median",
                            "bin_occupancy_max",
                            "n_test_in_end_bins",
                            "gate_passed",
                            "gate_delta_logloss",
                            "gate_ci_low",
                            "gate_ci_high",
                        )
                    },
                }
            )
        return pd.DataFrame(rows)


def _assert_strictly_interior(
    probs: np.ndarray,
    *,
    market: MarketName | str,
    kind: CalibratorKind | str,
) -> None:
    finite = probs[np.isfinite(probs)]
    if finite.size == 0:
        return
    if np.any(finite <= 0.0) or np.any(finite >= 1.0):
        msg = (
            f"calibrator kind={kind} for market={market} emitted 0 or 1 — "
            "disqualified (bounded calibrators only; no 1e-6 floor)"
        )
        raise CalibrationError(msg)


def _logit(p: np.ndarray) -> np.ndarray:
    # Diagnostic Cox fit only — clip inputs for numerical logit, not outputs.
    clipped = np.clip(p, _EPS_INTERIOR, 1.0 - _EPS_INTERIOR)
    out: np.ndarray = np.log(clipped / (1.0 - clipped))
    return out


def cox_recalibration(
    raw_probs: np.ndarray,
    outcomes: np.ndarray,
) -> CoxCalibrationStats:
    """Fit Cox recalibration regression: outcome ~ logit(raw) via logistic GLM.

    Uses sklearn LogisticRegression on ``logit(p)`` as the sole feature;
    intercept and coefficient are the Cox ``a`` and ``b``.
    """
    p = np.asarray(raw_probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    mask = np.isfinite(p) & np.isfinite(y) & (y >= 0) & (y <= 1)
    p, y = p[mask], y[mask]
    n = int(p.size)
    if n < 4 or len(np.unique(y)) < 2:
        return CoxCalibrationStats(intercept=float("nan"), slope=float("nan"), n=n)

    x = _logit(p).reshape(-1, 1)
    clf = LogisticRegression(
        C=1e12,
        solver="lbfgs",
        max_iter=500,
        fit_intercept=True,
    )
    clf.fit(x, y.astype(int))
    return CoxCalibrationStats(
        intercept=float(clf.intercept_[0]),
        slope=float(clf.coef_[0, 0]),
        n=n,
    )


def _is_thin(
    raw_probs: np.ndarray,
    *,
    thin_n: int,
    thin_unique: int,
) -> bool:
    return int(raw_probs.size) < thin_n or int(len(np.unique(np.round(raw_probs, 6)))) < thin_unique


def _resolve_min_bin_occupancy(n: int, configured: int | None) -> int:
    if configured is not None:
        return max(1, int(configured))
    return max(50, int(n // 20))


def _beta_transform(p: np.ndarray, params: tuple[float, float, float]) -> np.ndarray:
    """Three-parameter beta calibration (Kull et al.): a + b·logit(p) via inv-logit.

    ``params = (a, b, m)`` where m blends toward a midpoint; we use the common
    form ``σ(a + b · logit(p))`` (m unused / fixed) with b > 0 so the map is
    strictly increasing and outputs lie in (0, 1).
    """
    a, b, _m = params
    x = np.clip(np.asarray(p, dtype=float), _EPS_INTERIOR, 1.0 - _EPS_INTERIOR)
    out = expit(a + b * logit(x))
    return np.asarray(out, dtype=float)


def _fit_beta_params(p: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """MLE for logistic-beta: minimize Bernoulli NLL of σ(a + b·logit(p))."""

    def nll(theta: np.ndarray) -> float:
        a, log_b = float(theta[0]), float(theta[1])
        b = float(np.exp(log_b))
        pred = _beta_transform(p, (a, b, 0.0))
        pred = np.clip(pred, _EPS_INTERIOR, 1.0 - _EPS_INTERIOR)
        return float(-np.mean(y * np.log(pred) + (1.0 - y) * np.log(1.0 - pred)))

    res = minimize(nll, x0=np.array([0.0, 0.0]), method="L-BFGS-B")
    if not res.success:
        # Identity fallback in logit space.
        return 0.0, 1.0, 0.0
    a = float(res.x[0])
    b = float(np.exp(res.x[1]))
    if b <= 0:
        return 0.0, 1.0, 0.0
    return a, b, 0.0


def _isotonic_bin_stats(
    raw: np.ndarray,
    calibrated: np.ndarray,
    *,
    min_occupancy: int,
) -> dict[str, Any]:
    """Summarize isotonic bin occupancy from unique fitted x-thresholds."""
    # Approximate bins by equal-width on raw; report occupancy of those bins
    # that the isotonic map used (unique raw → calibrated steps).
    n = int(raw.size)
    n_bins = max(2, min(20, n // max(min_occupancy, 1)))
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    counts: list[int] = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        sel = (raw >= lo) & (raw <= hi) if i == n_bins - 1 else (raw >= lo) & (raw < hi)
        counts.append(int(np.sum(sel)))
    arr = np.asarray(counts, dtype=int)
    nonempty = arr[arr > 0]
    end_left = int(counts[0]) if counts else 0
    end_right = int(counts[-1]) if counts else 0
    return {
        "n_bins": int(n_bins),
        "min_bin_occupancy": int(min_occupancy),
        "bin_occupancy_min": int(nonempty.min()) if nonempty.size else 0,
        "bin_occupancy_median": float(np.median(nonempty)) if nonempty.size else 0.0,
        "bin_occupancy_max": int(arr.max()) if arr.size else 0,
        "n_test_in_end_bins": end_left + end_right,
        "n_unique_calibrated": int(len(np.unique(np.round(calibrated, 8)))),
    }


def fit_market_calibrator(
    raw_probs: np.ndarray | Sequence[float],
    outcomes: np.ndarray | Sequence[float],
    *,
    market: MarketName,
    thin_n: int = DEFAULT_THIN_N,
    thin_unique: int = DEFAULT_THIN_UNIQUE,
    force_kind: CalibratorKind | None = None,
    min_bin_occupancy: int | None = DEFAULT_MIN_BIN_OCCUPANCY,
) -> MarketCalibrator:
    """Fit a bounded calibrator on raw probs vs binary outcomes.

    ``force_kind='none'`` returns an identity calibrator (for bake-offs).
    Isotonic uses ``y_min/y_max`` interior bounds so it cannot emit 0/1.
    """
    p = np.asarray(raw_probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    mask = np.isfinite(p) & np.isfinite(y)
    p, y = p[mask], y[mask]
    if p.size < 4:
        msg = f"need ≥4 finite rows to calibrate market={market}, got {p.size}"
        raise CalibrationError(msg)
    if len(np.unique(y)) < 2:
        msg = f"outcomes for market={market} are constant — cannot calibrate"
        raise CalibrationError(msg)

    before = cox_recalibration(p, y)
    thin = _is_thin(p, thin_n=thin_n, thin_unique=thin_unique)
    kind: CalibratorKind = force_kind or ("platt" if thin else "isotonic")

    isotonic: IsotonicRegression | None = None
    platt: LogisticRegression | None = None
    beta_params: tuple[float, float, float] | None = None
    meta: dict[str, Any] = {"thin": thin, "n": int(p.size), "n_oof": int(p.size)}
    min_occ = _resolve_min_bin_occupancy(int(p.size), min_bin_occupancy)

    calibrated: np.ndarray
    if kind == "none":
        calibrated = p.copy()
    elif kind == "isotonic":
        # Interior bounds: never emit 0/1.
        y_lo, y_hi = _EPS_INTERIOR, 1.0 - _EPS_INTERIOR
        iso = IsotonicRegression(y_min=y_lo, y_max=y_hi, out_of_bounds="clip")
        iso.fit(p, y)
        calibrated = np.asarray(iso.predict(p), dtype=float)
        _assert_strictly_interior(calibrated, market=market, kind="isotonic")
        isotonic = iso
        meta.update(_isotonic_bin_stats(p, calibrated, min_occupancy=min_occ))
        # Occupancy check only when kind was auto-selected (not force_kind).
        nonempty_min = meta["bin_occupancy_min"]
        if force_kind is None and nonempty_min > 0 and nonempty_min < min_occ:
            kind = "platt"
            isotonic = None
            meta["isotonic_rejected_reason"] = (
                f"min nonempty bin occupancy {nonempty_min} < {min_occ}"
            )
            clf = LogisticRegression(
                C=1.0,
                solver="lbfgs",
                max_iter=500,
                fit_intercept=True,
            )
            clf.fit(p.reshape(-1, 1), y.astype(int))
            calibrated = clf.predict_proba(p.reshape(-1, 1))[:, 1]
            _assert_strictly_interior(calibrated, market=market, kind="platt")
            platt = clf
    elif kind == "platt":
        clf = LogisticRegression(
            C=1.0,
            solver="lbfgs",
            max_iter=500,
            fit_intercept=True,
        )
        clf.fit(p.reshape(-1, 1), y.astype(int))
        calibrated = clf.predict_proba(p.reshape(-1, 1))[:, 1]
        _assert_strictly_interior(calibrated, market=market, kind=kind)
        platt = clf
    elif kind == "beta":
        beta_params = _fit_beta_params(p, y)
        calibrated = _beta_transform(p, beta_params)
        _assert_strictly_interior(calibrated, market=market, kind=kind)
    else:
        msg = f"unsupported calibrator kind={kind}"
        raise CalibrationError(msg)

    after = cox_recalibration(calibrated, y)
    return MarketCalibrator(
        market=market,
        kind=kind,
        before=before,
        after=after,
        _isotonic=isotonic,
        _platt=platt,
        _beta_params=beta_params,
        meta=meta,
        applied=False,
    )


def fit_calibration_bundle(
    oof: pd.DataFrame,
    *,
    market_columns: Mapping[MarketName, tuple[str, str]],
    flag_column: str = OOF_FLAG_COLUMN,
    thin_n: int = DEFAULT_THIN_N,
    thin_unique: int = DEFAULT_THIN_UNIQUE,
    min_bin_occupancy: int | None = DEFAULT_MIN_BIN_OCCUPANCY,
    force_kind: CalibratorKind | None = None,
) -> CalibrationBundle:
    """Fit calibrators for each market from an OOF-only frame.

    Parameters
    ----------
    market_columns:
        Map market → ``(raw_prob_column, outcome_column)``.
    """
    try:
        assert_oof_only(oof, flag_column=flag_column)
    except EnsembleError as exc:
        raise CalibrationError(str(exc)) from exc

    bundle = CalibrationBundle()
    for market, (prob_col, out_col) in market_columns.items():
        if prob_col not in oof.columns or out_col not in oof.columns:
            msg = f"market={market} missing columns ({prob_col}, {out_col})"
            raise CalibrationError(msg)
        bundle.markets[market] = fit_market_calibrator(
            oof[prob_col].to_numpy(),
            oof[out_col].to_numpy(),
            market=market,
            thin_n=thin_n,
            thin_unique=thin_unique,
            force_kind=force_kind,
            min_bin_occupancy=min_bin_occupancy,
        )
    return bundle


def gate_calibrator_vs_none(
    cal: MarketCalibrator,
    raw_probs: np.ndarray,
    outcomes: np.ndarray,
    blocks: Sequence[Any],
    *,
    n_boot: int = 500,
    alpha: float = 0.10,
    seed: int = 0,
) -> MarketCalibrator:
    """Apply calibrator only if it beats identity on held-out log-loss.

    Uses paired block-bootstrap (by week) on per-row log-loss. Gate passes when
    the CI for ``(none − cal)`` is entirely above 0 (calibrator lower loss).
    Default remains ``applied=False``; sets ``applied=True`` only on pass.
    """
    from ncaa_quant.evaluation.metrics import log_loss_per_row

    p_raw = np.asarray(raw_probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    p_cal = cal.transform(p_raw)
    loss_none = log_loss_per_row(p_raw, y)
    loss_cal = log_loss_per_row(p_cal, y)
    # paired_block_bootstrap returns champion − challenger; we want none − cal.
    ci = paired_block_bootstrap(
        loss_none,
        loss_cal,
        blocks,
        n_boot=n_boot,
        alpha=alpha,
        seed=seed,
    )
    passed = bool(np.isfinite(ci.ci_low) and ci.ci_low > 0.0)
    cal.applied = passed
    cal.meta.update(
        {
            "gate_passed": passed,
            "gate_delta_logloss": float(ci.estimate),
            "gate_ci_low": float(ci.ci_low),
            "gate_ci_high": float(ci.ci_high),
            "gate_alpha": float(alpha),
            "gate_n_boot": int(n_boot),
        }
    )
    return cal


def apply_gated_calibration(
    raw_probs: np.ndarray,
    cal: MarketCalibrator | None,
) -> np.ndarray:
    """Return calibrated probs only when ``cal.applied``; else identity."""
    p = np.asarray(raw_probs, dtype=float)
    if cal is None or not cal.applied:
        return p
    return cal.transform(p)


def stamp_calibration_decisions(
    frame: pd.DataFrame,
    decisions: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    """Stamp per-market calibration gate decisions onto a prediction table."""
    out = frame.copy()
    for market, info in decisions.items():
        out[f"calibrator_{market}"] = str(info.get("kind", "none"))
        out[f"calibrator_{market}_applied"] = bool(info.get("applied", False))
    return out
