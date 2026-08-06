"""ElasticNet μ on the top-K features (DESIGN §5.2 item 5).

Selects the ``top_k`` (default 30) features with largest absolute Pearson
correlation to the training label, then fits sklearn ``ElasticNet``. Features
with zero variance are dropped before selection.
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


@dataclass
class ElasticNetMuHead(BasePredictor):
    """ElasticNet μ head restricted to the strongest ``top_k`` features."""

    target: TargetName = "margin"
    model_version: str = "enet-mu-v0"
    top_k: int = DEFAULT_TOP_K
    alpha: float = 0.1
    l1_ratio: float = 0.5
    max_iter: int = 10_000
    _model: ElasticNet | None = field(default=None, init=False, repr=False)
    _scaler: StandardScaler | None = field(default=None, init=False, repr=False)
    _selected_features: list[str] = field(default_factory=list, init=False, repr=False)

    def _serializable_state(self) -> dict[str, Any]:
        return {
            "top_k": self.top_k,
            "alpha": self.alpha,
            "l1_ratio": self.l1_ratio,
            "max_iter": self.max_iter,
        }

    def _fit_estimator(
        self,
        x: pd.DataFrame,
        y: np.ndarray,
        *,
        sample_weight: np.ndarray,
    ) -> None:
        self._monotone_constraints = None
        selected = select_top_k_features(x, y, top_k=self.top_k)
        self._selected_features = selected
        x_sel = x[selected].to_numpy(dtype=float)
        self._scaler = StandardScaler()
        x_scaled = self._scaler.fit_transform(x_sel)
        self._model = ElasticNet(
            alpha=self.alpha,
            l1_ratio=self.l1_ratio,
            max_iter=self.max_iter,
            random_state=self.seed,
        )
        self._model.fit(x_scaled, y, sample_weight=sample_weight)

    def _predict_estimator(self, x: pd.DataFrame) -> np.ndarray:
        if self._model is None or self._scaler is None or not self._selected_features:
            raise NotFittedError("ElasticNet model missing")
        missing = [c for c in self._selected_features if c not in x.columns]
        if missing:
            from ncaa_quant.models.heads.base import FeatureSignatureError

            msg = f"ElasticNet selected features missing at predict: {missing}"
            raise FeatureSignatureError(msg)
        x_sel = x[self._selected_features].to_numpy(dtype=float)
        x_scaled = self._scaler.transform(x_sel)
        return np.asarray(self._model.predict(x_scaled), dtype=float)

    def _get_estimator(self) -> Any:
        return {
            "model": self._model,
            "scaler": self._scaler,
            "selected_features": self._selected_features,
        }

    def _set_estimator(self, estimator: Any) -> None:
        payload = dict(estimator)
        self._model = payload["model"]
        self._scaler = payload["scaler"]
        self._selected_features = list(payload["selected_features"])
