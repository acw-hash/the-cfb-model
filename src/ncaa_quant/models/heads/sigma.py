"""LightGBM σ heads trained on absolute OOF residuals (DESIGN §5.2 item 7).

The training pipeline supplies labels ``abs_residual_margin`` /
``abs_residual_total`` (absolute out-of-fold μ residuals). For Gaussian
residuals with scale σ, ``E[|r|] = σ · √(2/π)``. The head therefore predicts
mean absolute deviation; ``predict`` multiplies by ``√(π/2)`` so downstream
consumers (``ensemble_sigma``, ``distribution/simulate``) receive a true σ.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.models.heads.base import (
    BasePredictor,
    HeadTrainConfig,
    NotFittedError,
    TargetName,
)

# Half-normal: E[|Z|] = √(2/π) for Z ~ N(0,1), so σ = MAD · √(π/2).
HALF_NORMAL_MAD_TO_SIGMA: float = math.sqrt(math.pi / 2.0)
HALF_NORMAL_SIGMA_TO_MAD: float = math.sqrt(2.0 / math.pi)


def abs_residual_to_sigma(abs_residual: np.ndarray | float) -> np.ndarray | float:
    """Convert mean-absolute-deviation (half-normal) predictions to Gaussian σ."""
    return np.asarray(abs_residual, dtype=float) * HALF_NORMAL_MAD_TO_SIGMA


def sigma_to_abs_residual(sigma: np.ndarray | float) -> np.ndarray | float:
    """Gaussian σ → expected |residual| under a centered Normal."""
    return np.asarray(sigma, dtype=float) * HALF_NORMAL_SIGMA_TO_MAD


@dataclass
class LightGBMSigmaHead(BasePredictor):
    """Heteroskedasticity head: trained on E[|residual|], emits Gaussian σ."""

    target: TargetName = "sigma_margin"
    model_version: str = "lgbm-sigma-v0"
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
        self._monotone_constraints = None
        # Absolute residuals are non-negative; clip labels for safety.
        y_fit = np.maximum(np.asarray(y, dtype=float), 0.0)
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
        }
        if self.train.use_gpu:
            params["device"] = "gpu"
        dataset = lgb.Dataset(
            x,
            label=y_fit,
            weight=sample_weight,
            feature_name=list(x.columns),
            free_raw_data=False,
        )
        self._booster = lgb.train(
            params,
            dataset,
            num_boost_round=self.train.n_estimators,
        )

    def _predict_estimator(self, x: pd.DataFrame) -> np.ndarray:
        if self._booster is None:
            raise NotFittedError("sigma booster missing")
        # Head is fit on |residual| = MAD; convert to Gaussian σ.
        mad = np.asarray(self._booster.predict(x), dtype=float)
        mad = np.maximum(mad, 1e-6)
        sigma = np.asarray(abs_residual_to_sigma(mad), dtype=float)
        out: np.ndarray = np.maximum(sigma, 1e-6)
        return out

    def _get_estimator(self) -> Any:
        return self._booster

    def _set_estimator(self, estimator: Any) -> None:
        self._booster = estimator


def abs_residual_labels(
    labels: pd.DataFrame,
    mu_predictions: pd.DataFrame,
    *,
    target: TargetName = "margin",
) -> pd.DataFrame:
    """Build σ-head labels ``|y − μ|`` joined on ``game_id``.

    Parameters
    ----------
    labels:
        Must include ``game_id`` and ``realized_margin`` / ``realized_total``.
    mu_predictions:
        Must include ``game_id`` and ``pred_margin`` / ``pred_total``.
    target:
        ``margin`` or ``total`` — selects which residual column to build.
    """
    if target not in ("margin", "total"):
        msg = f"abs_residual_labels target must be margin|total, got {target}"
        raise ValueError(msg)
    y_col = "realized_margin" if target == "margin" else "realized_total"
    p_col = "pred_margin" if target == "margin" else "pred_total"
    out_col = "abs_residual_margin" if target == "margin" else "abs_residual_total"
    merged = labels[["game_id", y_col]].merge(
        mu_predictions[["game_id", p_col]],
        on="game_id",
        how="inner",
    )
    result = labels.drop(
        columns=[c for c in labels.columns if c == out_col], errors="ignore"
    ).merge(
        merged[["game_id"]].assign(
            **{out_col: (merged[y_col] - merged[p_col]).abs().astype(float)}
        ),
        on="game_id",
        how="left",
    )
    return result
