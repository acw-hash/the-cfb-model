"""Bivariate (margin, total) predictive distribution (DESIGN §2.3).

Assembles a heteroskedastic bivariate normal on ``(M, T)`` with correlation
``ρ`` estimated from residuals — never assumed zero. Empirically small and
positive in CFB (~0.05–0.15).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]


class BivariateError(ValueError):
    """Raised for bivariate assembly contract violations."""


@dataclass(frozen=True)
class RhoEstimate:
    """Estimated Corr(residual_M, residual_T) with sample size."""

    rho: float
    n: int
    method: str = "pearson_oof_residuals"


@dataclass
class BivariateParams:
    """Per-game (or batched) bivariate Normal parameters on (M, T)."""

    mu_m: np.ndarray
    sigma_m: np.ndarray
    mu_t: np.ndarray
    sigma_t: np.ndarray
    rho: float
    rho_detail: RhoEstimate | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.mu_m = np.asarray(self.mu_m, dtype=float).reshape(-1)
        self.sigma_m = np.maximum(np.asarray(self.sigma_m, dtype=float).reshape(-1), 1e-8)
        self.mu_t = np.asarray(self.mu_t, dtype=float).reshape(-1)
        self.sigma_t = np.maximum(np.asarray(self.sigma_t, dtype=float).reshape(-1), 1e-8)
        n = self.mu_m.shape[0]
        if not (self.sigma_m.shape[0] == self.mu_t.shape[0] == self.sigma_t.shape[0] == n):
            msg = "mu/sigma arrays must share length"
            raise BivariateError(msg)
        if not (-0.999 <= float(self.rho) <= 0.999):
            msg = f"rho out of (-1, 1): {self.rho}"
            raise BivariateError(msg)

    @property
    def n(self) -> int:
        return int(self.mu_m.shape[0])

    def covariance_matrices(self) -> np.ndarray:
        """Return shape ``(n, 2, 2)`` covariance matrices."""
        n = self.n
        cov = np.zeros((n, 2, 2), dtype=float)
        cov[:, 0, 0] = self.sigma_m**2
        cov[:, 1, 1] = self.sigma_t**2
        cov[:, 0, 1] = self.rho * self.sigma_m * self.sigma_t
        cov[:, 1, 0] = cov[:, 0, 1]
        return cov


def estimate_rho(
    residual_m: np.ndarray | Sequence[float],
    residual_t: np.ndarray | Sequence[float],
    *,
    clip: float = 0.95,
) -> RhoEstimate:
    """Pearson correlation of margin/total residuals.

    Returns 0.0 when fewer than 3 finite pairs or zero variance.
    """
    rm = np.asarray(residual_m, dtype=float).reshape(-1)
    rt = np.asarray(residual_t, dtype=float).reshape(-1)
    if rm.shape[0] != rt.shape[0]:
        msg = "residual_m/residual_t length mismatch"
        raise BivariateError(msg)
    mask = np.isfinite(rm) & np.isfinite(rt)
    rm, rt = rm[mask], rt[mask]
    n = int(rm.size)
    if n < 3:
        return RhoEstimate(rho=0.0, n=n)
    if float(np.std(rm)) < 1e-12 or float(np.std(rt)) < 1e-12:
        return RhoEstimate(rho=0.0, n=n)
    rho = float(np.corrcoef(rm, rt)[0, 1])
    if not np.isfinite(rho):
        rho = 0.0
    rho = float(np.clip(rho, -clip, clip))
    return RhoEstimate(rho=rho, n=n)


def estimate_rho_from_frame(
    frame: pd.DataFrame,
    *,
    mu_m_col: str = "pred_margin",
    mu_t_col: str = "pred_total",
    y_m_col: str = "realized_margin",
    y_t_col: str = "realized_total",
) -> RhoEstimate:
    """Estimate ρ from OOF residuals ``y - μ`` on both targets."""
    for c in (mu_m_col, mu_t_col, y_m_col, y_t_col):
        if c not in frame.columns:
            msg = f"missing column '{c}' for rho estimation"
            raise BivariateError(msg)
    rm = np.asarray(frame[y_m_col], dtype=float) - np.asarray(frame[mu_m_col], dtype=float)
    rt = np.asarray(frame[y_t_col], dtype=float) - np.asarray(frame[mu_t_col], dtype=float)
    return estimate_rho(rm, rt)


def assemble_bivariate(
    mu_m: np.ndarray | Sequence[float],
    sigma_m: np.ndarray | Sequence[float],
    mu_t: np.ndarray | Sequence[float],
    sigma_t: np.ndarray | Sequence[float],
    *,
    rho: float | RhoEstimate,
) -> BivariateParams:
    """Build :class:`BivariateParams` from stacked μ/σ and estimated ρ."""
    detail: RhoEstimate | None
    if isinstance(rho, RhoEstimate):
        detail = rho
        rho_v = float(rho.rho)
    else:
        detail = None
        rho_v = float(rho)
    return BivariateParams(
        mu_m=np.asarray(mu_m, dtype=float),
        sigma_m=np.asarray(sigma_m, dtype=float),
        mu_t=np.asarray(mu_t, dtype=float),
        sigma_t=np.asarray(sigma_t, dtype=float),
        rho=rho_v,
        rho_detail=detail,
    )


def residuals_from_predictions(
    y_m: np.ndarray,
    mu_m: np.ndarray,
    y_t: np.ndarray,
    mu_t: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Convenience residual pair."""
    return (
        np.asarray(y_m, dtype=float) - np.asarray(mu_m, dtype=float),
        np.asarray(y_t, dtype=float) - np.asarray(mu_t, dtype=float),
    )
