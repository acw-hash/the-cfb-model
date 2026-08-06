"""LightGBM μ heads for margin and total (DESIGN §5.2 items 1, 3).

Margin heads apply monotone constraints on rating-differential features so a
rating increase may never decrease predicted home margin. Constraints are
verified post-fit against the Booster model dump — LightGBM silently ignores
malformed specs, so we refuse a fit whose dump does not echo the vector we
passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.models.heads.base import (
    BasePredictor,
    HeadTrainConfig,
    NotFittedError,
    PredictorError,
    TargetName,
    monotone_constraints_for,
)


def _verify_monotone_applied(
    booster: lgb.Booster,
    expected: list[int],
) -> None:
    """Raise if the fitted booster did not record our monotone constraints."""
    dumped = booster.dump_model()
    applied = dumped.get("monotone_constraints")
    if applied is None:
        if any(c != 0 for c in expected):
            msg = (
                "LightGBM silent-drop: monotone_constraints missing from model "
                f"dump (expected {expected})"
            )
            raise PredictorError(msg)
        return
    applied_list = [int(c) for c in applied]
    if applied_list != list(expected):
        msg = (
            "LightGBM monotone_constraints not applied as requested: "
            f"expected {expected}, dump has {applied_list}"
        )
        raise PredictorError(msg)


@dataclass
class LightGBMMuHead(BasePredictor):
    """LightGBM regression head for ``μ_M`` or ``μ_T``."""

    target: TargetName = "margin"
    model_version: str = "lgbm-mu-v0"
    train: HeadTrainConfig = field(default_factory=HeadTrainConfig)
    _booster: lgb.Booster | None = field(default=None, init=False, repr=False)

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
            "objective": "regression",
            "metric": "l2",
            "learning_rate": self.train.learning_rate,
            "num_leaves": self.train.num_leaves,
            "max_depth": self.train.max_depth,
            "min_child_samples": self.train.min_child_samples,
            "subsample": self.train.subsample,
            "colsample_bytree": self.train.colsample_bytree,
            "reg_lambda": self.train.reg_lambda,
            "verbosity": -1,
            "seed": self.seed,
            "deterministic": True,
            "force_row_wise": True,
            "monotone_constraints": mono,
        }
        if self.train.use_gpu:
            params["device"] = "gpu"

        dataset = lgb.Dataset(
            x,
            label=y,
            weight=sample_weight,
            feature_name=list(x.columns),
            free_raw_data=False,
        )
        self._booster = lgb.train(
            params,
            dataset,
            num_boost_round=self.train.n_estimators,
        )
        _verify_monotone_applied(self._booster, mono)

    def _predict_estimator(self, x: pd.DataFrame) -> np.ndarray:
        if self._booster is None:
            raise NotFittedError("LightGBM booster missing")
        return np.asarray(self._booster.predict(x), dtype=float)

    def _get_estimator(self) -> Any:
        return self._booster

    def _set_estimator(self, estimator: Any) -> None:
        self._booster = estimator


@dataclass
class LightGBMMarginMuHead(LightGBMMuHead):
    """Margin μ head with monotone rating-diff constraints."""

    target: TargetName = "margin"
    model_version: str = "lgbm-mu-margin-v0"


@dataclass
class LightGBMTotalMuHead(LightGBMMuHead):
    """Total μ head (no monotone constraints on rating diffs)."""

    target: TargetName = "total"
    model_version: str = "lgbm-mu-total-v0"
