"""XGBoost μ diversity member (DESIGN §5.2 item 4).

GPU is enabled via ``train.use_gpu`` (``device=cuda``). Defaults stay on CPU
for CI / native Phase-1 runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import xgboost as xgb

from ncaa_quant.models.heads.base import (
    BasePredictor,
    HeadTrainConfig,
    NotFittedError,
    TargetName,
    monotone_constraints_for,
)


@dataclass
class XGBoostMuHead(BasePredictor):
    """XGBoost regressor for margin or total μ."""

    target: TargetName = "margin"
    model_version: str = "xgb-mu-v0"
    train: HeadTrainConfig = field(default_factory=HeadTrainConfig)
    _model: xgb.XGBRegressor | None = field(default=None, init=False, repr=False)

    def _serializable_state(self) -> dict[str, Any]:
        return {"train": self.train}

    def _fit_estimator(
        self,
        x: pd.DataFrame,
        y: np.ndarray,
        *,
        sample_weight: np.ndarray,
    ) -> None:
        mono = monotone_constraints_for(list(x.columns), target=self.target)
        self._monotone_constraints = list(mono)
        # XGBoost expects a tuple string like "(1,0,0)" for monotone_constraints.
        mono_tuple = "(" + ",".join(str(c) for c in mono) + ")"
        kwargs: dict[str, Any] = {
            "n_estimators": self.train.n_estimators,
            "learning_rate": self.train.learning_rate,
            "max_depth": self.train.max_depth,
            "subsample": self.train.subsample,
            "colsample_bytree": self.train.colsample_bytree,
            "reg_lambda": self.train.reg_lambda,
            "objective": "reg:squarederror",
            "random_state": self.seed,
            "verbosity": 0,
            "monotone_constraints": mono_tuple,
        }
        if self.train.use_gpu:
            kwargs["device"] = "cuda"
            kwargs["tree_method"] = "hist"
        else:
            kwargs["device"] = "cpu"
            kwargs["tree_method"] = "hist"
        self._model = xgb.XGBRegressor(**kwargs)
        self._model.fit(x, y, sample_weight=sample_weight)

    def _predict_estimator(self, x: pd.DataFrame) -> np.ndarray:
        if self._model is None:
            raise NotFittedError("XGBoost model missing")
        return np.asarray(self._model.predict(x), dtype=float)

    def _get_estimator(self) -> Any:
        return self._model

    def _set_estimator(self, estimator: Any) -> None:
        self._model = estimator
