"""NGBoost Normal(μ, σ) heads for margin and total (DESIGN §5.2 item 6).

Provides both a point μ (``pred_margin`` / ``pred_total``) and a σ column
(``pred_sigma_margin`` / ``pred_sigma_total``) from the fitted Normal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from ngboost import NGBRegressor  # type: ignore[import-untyped]
from ngboost.distns import Normal  # type: ignore[import-untyped]
from sklearn.tree import DecisionTreeRegressor  # type: ignore[import-untyped]

from ncaa_quant.models.heads.base import (
    BasePredictor,
    HeadTrainConfig,
    NotFittedError,
    TargetName,
    prediction_column_for,
)


@dataclass
class NGBoostNormalHead(BasePredictor):
    """NGBoost distributional regressor (Normal) for margin or total."""

    target: TargetName = "margin"
    model_version: str = "ngboost-normal-v0"
    train: HeadTrainConfig = field(
        default_factory=lambda: HeadTrainConfig(n_estimators=50, learning_rate=0.05, max_depth=3)
    )
    _model: NGBRegressor | None = field(default=None, init=False, repr=False)

    def _serializable_state(self) -> dict[str, Any]:
        return {"train": self.train}

    def _empty_prediction_frame(self) -> pd.DataFrame:
        sigma_col = "pred_sigma_margin" if self.target == "margin" else "pred_sigma_total"
        return pd.DataFrame(columns=["game_id", "pred_margin", "pred_total", sigma_col])

    def _fit_estimator(
        self,
        x: pd.DataFrame,
        y: np.ndarray,
        *,
        sample_weight: np.ndarray,
    ) -> None:
        self._monotone_constraints = None
        base = DecisionTreeRegressor(
            max_depth=self.train.max_depth,
            random_state=self.seed,
        )
        self._model = NGBRegressor(
            Dist=Normal,
            Base=base,
            n_estimators=self.train.n_estimators,
            learning_rate=self.train.learning_rate,
            natural_gradient=True,
            verbose=False,
            random_state=self.seed,
        )
        # NGBoost sample_weight support varies by version; pass when accepted.
        try:
            self._model.fit(x.to_numpy(dtype=float), y, sample_weight=sample_weight)
        except TypeError:
            self._model.fit(x.to_numpy(dtype=float), y)

    def _predict_estimator(self, x: pd.DataFrame) -> dict[str, np.ndarray]:
        if self._model is None:
            raise NotFittedError("NGBoost model missing")
        arr = x.to_numpy(dtype=float)
        dist = self._model.pred_dist(arr)
        mu = np.asarray(dist.loc, dtype=float)
        sigma = np.maximum(np.asarray(dist.scale, dtype=float), 1e-6)
        point = prediction_column_for(self.target)
        sigma_col = "pred_sigma_margin" if self.target == "margin" else "pred_sigma_total"
        return {point: mu, sigma_col: sigma}

    def _get_estimator(self) -> Any:
        return self._model

    def _set_estimator(self, estimator: Any) -> None:
        self._model = estimator
