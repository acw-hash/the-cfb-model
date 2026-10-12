"""ElasticNet μ on the top-K features (DESIGN §5.2 item 5).

Selects the ``top_k`` (default 30) features with largest absolute Pearson
correlation to the training label, then fits sklearn ``ElasticNet``. Features
with zero variance are dropped before selection.

NaN policy (ADR 0014): within each training window, drop columns whose null
share exceeds :data:`NULL_SHARE_DROP_THRESHOLD`, then impute remaining NaN with
training-window medians only (no zero-fill; no cross-window leakage). Persist
medians for predict-time transform.

Unpenalized features (MODEL-HFA-4): columns listed in
``unpenalized_features`` are force-kept (when present). Fit order is:
ElasticNet on the penalized columns first (``fit_intercept`` as configured),
then weighted OLS of the residual on ``[1 | unpenalized]`` so a near-constant
``is_home_site`` identifies the home−neutral contrast rather than the grand
mean. The penalty cannot shrink the unpenalized coefficients. Raw-scale coefs
live on ``unpenalized_coef_``; the residual intercept is ``unpenalized_intercept_``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from sklearn.linear_model import ElasticNet  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from ncaa_quant.models.heads.base import (
    BasePredictor,
    NotFittedError,
    PredictorError,
    TargetName,
)

DEFAULT_TOP_K: int = 30

# ADR 0014: drop columns whose training-window null share exceeds this.
NULL_SHARE_DROP_THRESHOLD: float = 0.50


def select_top_k_features(
    x: pd.DataFrame,
    y: np.ndarray,
    *,
    top_k: int = DEFAULT_TOP_K,
) -> list[str]:
    """Rank columns by |corr(x_j, y)|; return up to ``top_k`` names."""
    if top_k <= 0:
        msg = f"top_k must be > 0, got {top_k}"
        raise ValueError(msg)
    scores: list[tuple[float, str]] = []
    y_arr = np.asarray(y, dtype=float)
    for col in x.columns:
        series = pd.to_numeric(x[col], errors="coerce").to_numpy(dtype=float)
        if np.nanstd(series) < 1e-12:
            continue
        mask = np.isfinite(series) & np.isfinite(y_arr)
        if mask.sum() < 3:
            continue
        corr = np.corrcoef(series[mask], y_arr[mask])[0, 1]
        if not np.isfinite(corr):
            continue
        scores.append((abs(float(corr)), str(col)))
    scores.sort(key=lambda t: (-t[0], t[1]))
    chosen = [name for _, name in scores[:top_k]]
    if not chosen:
        msg = "ElasticNet: no finite-correlation features available"
        raise PredictorError(msg)
    return chosen


def drop_high_null_columns(
    x: pd.DataFrame,
    *,
    threshold: float = NULL_SHARE_DROP_THRESHOLD,
) -> tuple[pd.DataFrame, list[str]]:
    """Drop columns whose null share exceeds ``threshold``; return kept frame + names."""
    if not 0.0 <= threshold <= 1.0:
        msg = f"null-share threshold must be in [0, 1], got {threshold}"
        raise ValueError(msg)
    kept: list[str] = []
    for col in x.columns:
        series = pd.to_numeric(x[col], errors="coerce")
        n = int(len(series))
        if n == 0:
            continue
        null_share = float(series.isna().mean())
        if null_share > threshold:
            continue
        kept.append(str(col))
    if not kept:
        msg = (
            f"ElasticNet: all columns exceed null-share threshold={threshold}; nothing left to fit"
        )
        raise PredictorError(msg)
    return x[kept].copy(), kept


def training_window_medians(x: pd.DataFrame) -> dict[str, float]:
    """Column medians from ``x`` only (PIT: no rows outside the training window)."""
    out: dict[str, float] = {}
    for col in x.columns:
        series = pd.to_numeric(x[col], errors="coerce")
        med = float(series.median(skipna=True))
        if not np.isfinite(med):
            # Column survived null-share drop but is all-NaN after coerce — refuse
            # zero-fill; caller must have dropped it.
            msg = f"ElasticNet: no finite training median for column {col}"
            raise PredictorError(msg)
        out[str(col)] = med
    return out


def impute_with_medians(x: pd.DataFrame, medians: dict[str, float]) -> pd.DataFrame:
    """Fill NaN with provided training medians; never invents zeros."""
    out = x.copy()
    for col, med in medians.items():
        if col not in out.columns:
            continue
        series = pd.to_numeric(out[col], errors="coerce")
        out[col] = series.fillna(float(med))
    return out


def _merge_force_include(
    selected: list[str], forced: tuple[str, ...], available: set[str]
) -> list[str]:
    """Append force-include names present in ``available`` and not already selected."""
    out = list(selected)
    for name in forced:
        if name in available and name not in out:
            out.append(name)
    return out


def _weighted_ols(
    x: np.ndarray,
    y: np.ndarray,
    sample_weight: np.ndarray,
    *,
    fit_intercept: bool = False,
) -> tuple[np.ndarray, float]:
    """Weighted least squares; returns ``(coef, intercept)``."""
    if x.ndim != 2:
        msg = "X must be 2-d"
        raise ValueError(msg)
    w = np.asarray(sample_weight, dtype=float).reshape(-1)
    y_arr = np.asarray(y, dtype=float).reshape(-1)
    if x.shape[0] == 0:
        return np.zeros(x.shape[1], dtype=float), 0.0
    if fit_intercept:
        design = np.column_stack([np.ones(x.shape[0], dtype=float), x])
    else:
        design = x
    if design.shape[1] == 0:
        return np.zeros(0, dtype=float), 0.0
    sw = np.sqrt(np.clip(w, 0.0, None))
    xw = design * sw[:, None]
    yw = y_arr * sw
    beta, *_ = np.linalg.lstsq(xw, yw, rcond=None)
    beta = np.asarray(beta, dtype=float)
    if fit_intercept:
        return beta[1:], float(beta[0])
    return beta, 0.0


@dataclass
class ElasticNetMuHead(BasePredictor):
    """ElasticNet μ head restricted to the strongest ``top_k`` features."""

    target: TargetName = "margin"
    model_version: str = "enet-mu-v0"
    top_k: int = DEFAULT_TOP_K
    alpha: float = 0.1
    l1_ratio: float = 0.5
    max_iter: int = 10_000
    null_share_drop_threshold: float = NULL_SHARE_DROP_THRESHOLD
    fit_intercept: bool = True
    force_include_features: tuple[str, ...] = ()
    unpenalized_features: tuple[str, ...] = ()
    _model: ElasticNet | None = field(default=None, init=False, repr=False)
    _scaler: StandardScaler | None = field(default=None, init=False, repr=False)
    _selected_features: list[str] = field(default_factory=list, init=False, repr=False)
    _impute_medians: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _kept_columns: list[str] = field(default_factory=list, init=False, repr=False)
    _penalized_features: list[str] = field(default_factory=list, init=False, repr=False)
    _unpenalized_fitted: list[str] = field(default_factory=list, init=False, repr=False)
    _unpenalized_coef: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _unpenalized_intercept: float = field(default=0.0, init=False, repr=False)

    @property
    def unpenalized_coef_(self) -> dict[str, float]:
        """Raw-scale coefficients for unpenalized features (empty if none)."""
        return dict(self._unpenalized_coef)

    @property
    def unpenalized_intercept_(self) -> float:
        """Intercept from the unpenalized residual OLS (0 when unused)."""
        return float(self._unpenalized_intercept)

    def _clear_estimator_state(self) -> None:
        """Forget selection + estimator together (ADR 0014 consistency clause)."""
        self._model = None
        self._scaler = None
        self._selected_features = []
        self._impute_medians = {}
        self._kept_columns = []
        self._penalized_features = []
        self._unpenalized_fitted = []
        self._unpenalized_coef = {}
        self._unpenalized_intercept = 0.0

    def _serializable_state(self) -> dict[str, Any]:
        return {
            "top_k": self.top_k,
            "alpha": self.alpha,
            "l1_ratio": self.l1_ratio,
            "max_iter": self.max_iter,
            "null_share_drop_threshold": self.null_share_drop_threshold,
            "fit_intercept": self.fit_intercept,
            "force_include_features": list(self.force_include_features),
            "unpenalized_features": list(self.unpenalized_features),
        }

    def _fit_estimator(
        self,
        x: pd.DataFrame,
        y: np.ndarray,
        *,
        sample_weight: np.ndarray,
    ) -> None:
        self._monotone_constraints = None
        self._clear_estimator_state()
        try:
            x_kept, kept = drop_high_null_columns(
                x, threshold=float(self.null_share_drop_threshold)
            )
            medians = training_window_medians(x_kept)
            x_imp = impute_with_medians(x_kept, medians)
            selected = select_top_k_features(x_imp, y, top_k=self.top_k)
            forced = tuple(
                dict.fromkeys([*self.force_include_features, *self.unpenalized_features])
            )
            selected = _merge_force_include(
                selected, forced, set(x_imp.columns.astype(str))
            )
            unpen = [c for c in self.unpenalized_features if c in selected]
            pen = [c for c in selected if c not in unpen]
            if not pen and not unpen:
                msg = "ElasticNet: no features left after selection"
                raise PredictorError(msg)

            y_arr = np.asarray(y, dtype=float).reshape(-1)
            w = np.asarray(sample_weight, dtype=float).reshape(-1)
            if w.shape[0] != y_arr.shape[0]:
                msg = "ElasticNet: sample_weight length mismatch"
                raise PredictorError(msg)

            unpen_coef: dict[str, float] = {}
            model: ElasticNet | None = None
            scaler: StandardScaler | None = None
            pen_pred = np.zeros_like(y_arr, dtype=float)
            if pen:
                x_pen = x_imp[pen].to_numpy(dtype=float)
                if not np.isfinite(x_pen).all():
                    msg = "ElasticNet: NaN remain after training-window median impute"
                    raise PredictorError(msg)
                scaler = StandardScaler()
                x_scaled = scaler.fit_transform(x_pen)
                # Unpenalized site term carries the home mean; do not also fit an
                # intercept on the penalized block (would double-count).
                use_intercept = bool(self.fit_intercept) and not unpen
                model = ElasticNet(
                    alpha=self.alpha,
                    l1_ratio=self.l1_ratio,
                    max_iter=self.max_iter,
                    random_state=self.seed,
                    fit_intercept=use_intercept,
                )
                model.fit(x_scaled, y_arr, sample_weight=w)
                pen_pred = np.asarray(model.predict(x_scaled), dtype=float)
            elif self.fit_intercept and not unpen:
                msg = "ElasticNet: intercept-only model is not supported"
                raise PredictorError(msg)

            unpen_intercept = 0.0
            if unpen:
                x_unpen = x_imp[unpen].to_numpy(dtype=float)
                if not np.isfinite(x_unpen).all():
                    msg = "ElasticNet: NaN remain in unpenalized features after impute"
                    raise PredictorError(msg)
                # Sequential: OLS of residual on [1 | unpen] so is_home_site is the
                # home−neutral contrast (penalty cannot shrink these coefs).
                resid = y_arr - pen_pred
                beta_u, unpen_intercept = _weighted_ols(
                    x_unpen, resid, w, fit_intercept=True
                )
                unpen_coef = {name: float(beta_u[i]) for i, name in enumerate(unpen)}
        except Exception:
            # Selection must not outlive a failed estimator (SDMU-DIAG ambiguity).
            self._clear_estimator_state()
            raise
        self._kept_columns = list(kept)
        self._impute_medians = dict(medians)
        self._selected_features = list(selected)
        self._penalized_features = list(pen)
        self._unpenalized_fitted = list(unpen)
        self._unpenalized_coef = dict(unpen_coef)
        self._unpenalized_intercept = float(unpen_intercept)
        self._scaler = scaler
        self._model = model

    def _predict_estimator(self, x: pd.DataFrame) -> np.ndarray:
        if not self._selected_features or not self._impute_medians:
            raise NotFittedError("ElasticNet model missing")
        if self._model is None and not self._unpenalized_fitted:
            raise NotFittedError("ElasticNet model missing")
        missing = [c for c in self._selected_features if c not in x.columns]
        if missing:
            from ncaa_quant.models.heads.base import FeatureSignatureError

            msg = f"ElasticNet selected features missing at predict: {missing}"
            raise FeatureSignatureError(msg)
        # Predict-time: same kept columns + training medians only (PIT).
        cols = [c for c in self._kept_columns if c in x.columns]
        if not cols:
            msg = "ElasticNet: no training-kept columns present at predict"
            raise PredictorError(msg)
        x_kept = x[cols].copy()
        for col in self._selected_features:
            if col not in x_kept.columns:
                x_kept[col] = np.nan
        x_imp = impute_with_medians(x_kept, self._impute_medians)
        n = int(len(x_imp))
        pred = np.zeros(n, dtype=float)
        if self._penalized_features:
            if self._model is None or self._scaler is None:
                raise NotFittedError("ElasticNet penalized model missing")
            x_pen = x_imp[self._penalized_features].to_numpy(dtype=float)
            if not np.isfinite(x_pen).all():
                msg = "ElasticNet: NaN remain after predict-time median impute"
                raise PredictorError(msg)
            x_scaled = self._scaler.transform(x_pen)
            pred = pred + np.asarray(self._model.predict(x_scaled), dtype=float)
        if self._unpenalized_fitted:
            x_u = x_imp[self._unpenalized_fitted].to_numpy(dtype=float)
            if not np.isfinite(x_u).all():
                msg = "ElasticNet: NaN remain in unpenalized features at predict"
                raise PredictorError(msg)
            beta = np.asarray(
                [self._unpenalized_coef[c] for c in self._unpenalized_fitted], dtype=float
            )
            pred = pred + float(self._unpenalized_intercept) + x_u @ beta
        return pred

    def _get_estimator(self) -> Any:
        return {
            "model": self._model,
            "scaler": self._scaler,
            "selected_features": self._selected_features,
            "impute_medians": self._impute_medians,
            "kept_columns": self._kept_columns,
            "penalized_features": self._penalized_features,
            "unpenalized_fitted": self._unpenalized_fitted,
            "unpenalized_coef": self._unpenalized_coef,
            "unpenalized_intercept": self._unpenalized_intercept,
        }

    def _set_estimator(self, estimator: Any) -> None:
        payload = dict(estimator)
        self._model = payload["model"]
        self._scaler = payload["scaler"]
        self._selected_features = list(payload["selected_features"])
        self._impute_medians = {
            str(k): float(v) for k, v in dict(payload.get("impute_medians") or {}).items()
        }
        self._kept_columns = list(payload.get("kept_columns") or [])
        self._penalized_features = list(payload.get("penalized_features") or [])
        self._unpenalized_fitted = list(payload.get("unpenalized_fitted") or [])
        self._unpenalized_coef = {
            str(k): float(v) for k, v in dict(payload.get("unpenalized_coef") or {}).items()
        }
        self._unpenalized_intercept = float(payload.get("unpenalized_intercept") or 0.0)
