"""CatBoost μ diversity member (DESIGN §5.2 item 4).

GPU is enabled via ``train.use_gpu`` (``task_type=GPU``). Defaults stay on CPU.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from catboost import CatBoostRegressor  # type: ignore[import-untyped]

from ncaa_quant.models.heads.base import (
    BasePredictor,
    HeadTrainConfig,
    NotFittedError,
    TargetName,
    monotone_constraints_for,
)


@dataclass
class CatBoostMuHead(BasePredictor):
    """CatBoost regressor for margin or total μ."""

    target: TargetName = "margin"
    model_version: str = "catboost-mu-v0"
    train: HeadTrainConfig = field(default_factory=HeadTrainConfig)
    _model: CatBoostRegressor | None = field(default=None, init=False, repr=False)

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
        params: dict[str, Any] = {
            "iterations": self.train.n_estimators,
            "learning_rate": self.train.learning_rate,
            "depth": max(self.train.max_depth, 1),
            "l2_leaf_reg": self.train.reg_lambda,
            "loss_function": "RMSE",
            "random_seed": self.seed,
            "verbose": False,
            "allow_writing_files": False,
            "monotone_constraints": mono,
        }
        if self.train.use_gpu:
            params["task_type"] = "GPU"
        else:
            params["task_type"] = "CPU"
        self._model = CatBoostRegressor(**params)
        self._model.fit(x, y, sample_weight=sample_weight)

    def _predict_estimator(self, x: pd.DataFrame) -> np.ndarray:
        if self._model is None:
            raise NotFittedError("CatBoost model missing")
        return np.asarray(self._model.predict(x), dtype=float)

    def _get_estimator(self) -> Any:
        return self._model

    def _set_estimator(self, estimator: Any) -> None:
        self._model = estimator
