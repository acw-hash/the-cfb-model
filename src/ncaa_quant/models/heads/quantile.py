"""LightGBM quantile heads for margin and total (DESIGN §5.2 items 2–3).

Trains one booster per quantile in
``QUANTILES = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)``.
At predict time, if quantile crossing is detected (e.g. q05 > q50), the
quantile vector is sorted ascending and a warning is logged.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import structlog

from ncaa_quant.models.heads.base import (
    BasePredictor,
    HeadTrainConfig,
    NotFittedError,
    TargetName,
    prediction_column_for,
)

log = structlog.get_logger(__name__)

QUANTILES: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)


def quantile_column(target: TargetName, q: float) -> str:
    """Column name for a quantile prediction, e.g. ``pred_margin_q05``."""
    base = "margin" if target == "margin" else "total"
    pct = int(round(q * 100))
    return f"pred_{base}_q{pct:02d}"


def enforce_quantile_order(
    quantile_matrix: np.ndarray,
    *,
    quantiles: Sequence[float] = QUANTILES,
) -> tuple[np.ndarray, bool]:
    """Sort each row ascending; return ``(ordered, crossing_detected)``.

    Crossing means the raw predictions violated ``q_i <= q_{i+1}`` for some i.
    """
    if quantile_matrix.size == 0:
        return quantile_matrix, False
    ordered = np.sort(quantile_matrix, axis=1)
    crossed = bool(np.any(quantile_matrix != ordered))
    # Also check specifically q05 <= q50 <= q95 when those exist.
    q_list = list(quantiles)
    if 0.05 in q_list and 0.5 in q_list and 0.95 in q_list:
        i05, i50, i95 = q_list.index(0.05), q_list.index(0.5), q_list.index(0.95)
        band = quantile_matrix[:, [i05, i50, i95]]
        if np.any(band[:, 0] > band[:, 1]) or np.any(band[:, 1] > band[:, 2]):
            crossed = True
    return ordered, crossed


@dataclass
class LightGBMQuantileHead(BasePredictor):
    """Multi-quantile LightGBM head for margin or total."""

    target: TargetName = "margin"
    model_version: str = "lgbm-quantile-v0"
    quantiles: tuple[float, ...] = QUANTILES
    train: HeadTrainConfig = field(default_factory=HeadTrainConfig)
    _boosters: dict[float, lgb.Booster] = field(default_factory=dict, init=False, repr=False)

    def _serializable_state(self) -> dict[str, Any]:
        return {"train": self.train, "quantiles": self.quantiles}

    def _empty_prediction_frame(self) -> pd.DataFrame:
        cols = ["game_id", "pred_margin", "pred_total"]
        cols.extend(quantile_column(self.target, q) for q in self.quantiles)
        return pd.DataFrame(columns=cols)

    def _fit_estimator(
        self,
        x: pd.DataFrame,
        y: np.ndarray,
        *,
        sample_weight: np.ndarray,
    ) -> None:
        self._monotone_constraints = None
        self._boosters = {}
        for q in self.quantiles:
            params: dict[str, Any] = {
                "objective": "quantile",
                "alpha": float(q),
                "metric": "quantile",
                "learning_rate": self.train.learning_rate,
                "num_leaves": self.train.num_leaves,
                "max_depth": self.train.max_depth,
                "min_child_samples": self.train.min_child_samples,
                "subsample": self.train.subsample,
                "colsample_bytree": self.train.colsample_bytree,
                "reg_lambda": self.train.reg_lambda,
                "verbosity": -1,
                "seed": self.seed + int(round(q * 1000)),
                "deterministic": True,
                "force_row_wise": True,
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
            self._boosters[float(q)] = lgb.train(
                params,
                dataset,
                num_boost_round=self.train.n_estimators,
            )

    def _predict_estimator(self, x: pd.DataFrame) -> dict[str, np.ndarray]:
        if not self._boosters:
            raise NotFittedError("quantile boosters missing")
        cols: list[str] = []
        mat = np.column_stack(
            [np.asarray(self._boosters[float(q)].predict(x), dtype=float) for q in self.quantiles]
        )
        ordered, crossed = enforce_quantile_order(mat, quantiles=self.quantiles)
        if crossed:
            warnings.warn(
                "quantile crossing detected; sorting predictions ascending",
                UserWarning,
                stacklevel=2,
            )
            log.warning(
                "quantile_crossing",
                target=self.target,
                n_rows=int(mat.shape[0]),
            )
        out: dict[str, np.ndarray] = {}
        for i, q in enumerate(self.quantiles):
            name = quantile_column(self.target, q)
            cols.append(name)
            out[name] = ordered[:, i]
        # Also emit the median as the point prediction for harness metrics.
        if 0.5 in self.quantiles:
            med = ordered[:, list(self.quantiles).index(0.5)]
        else:
            med = ordered.mean(axis=1)
        out[prediction_column_for(self.target)] = med
        return out

    def _get_estimator(self) -> Any:
        return self._boosters

    def _set_estimator(self, estimator: Any) -> None:
        self._boosters = {float(k): v for k, v in dict(estimator).items()}
