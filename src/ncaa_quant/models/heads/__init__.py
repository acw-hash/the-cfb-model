"""Model heads for margin, total, sigma, and quantile targets.

Level-0 ensemble members per DESIGN §5.2. Ensembling / HPO / calibration are
out of scope (Tasks 18–19).
"""

from __future__ import annotations

from ncaa_quant.models.heads.base import (
    META_COLUMNS,
    RATING_DIFF_TOKENS,
    BasePredictor,
    FeatureSignature,
    FeatureSignatureError,
    HeadTrainConfig,
    NotFittedError,
    PredictorError,
    TargetName,
    feature_columns,
    is_rating_differential_feature,
    label_column_for,
    monotone_constraints_for,
    prediction_column_for,
)
from ncaa_quant.models.heads.catboost_mu import CatBoostMuHead
from ncaa_quant.models.heads.elasticnet import (
    DEFAULT_TOP_K,
    ElasticNetMuHead,
    select_top_k_features,
)
from ncaa_quant.models.heads.margin import LightGBMMarginMuHead, LightGBMMuHead
from ncaa_quant.models.heads.ngboost_dist import NGBoostNormalHead
from ncaa_quant.models.heads.quantile import (
    QUANTILES,
    LightGBMQuantileHead,
    enforce_quantile_order,
    quantile_column,
)
from ncaa_quant.models.heads.sigma import (
    HALF_NORMAL_MAD_TO_SIGMA,
    HALF_NORMAL_SIGMA_TO_MAD,
    LightGBMSigmaHead,
    abs_residual_labels,
    abs_residual_to_sigma,
    sigma_to_abs_residual,
)
from ncaa_quant.models.heads.total import LightGBMTotalMuHead
from ncaa_quant.models.heads.weights import (
    DEFAULT_SEASON_HALF_LIFE,
    resolve_sample_weight,
    time_decay_weights,
)
from ncaa_quant.models.heads.xgboost_mu import XGBoostMuHead

__all__ = [
    "DEFAULT_SEASON_HALF_LIFE",
    "DEFAULT_TOP_K",
    "HALF_NORMAL_MAD_TO_SIGMA",
    "HALF_NORMAL_SIGMA_TO_MAD",
    "META_COLUMNS",
    "QUANTILES",
    "RATING_DIFF_TOKENS",
    "BasePredictor",
    "CatBoostMuHead",
    "ElasticNetMuHead",
    "FeatureSignature",
    "FeatureSignatureError",
    "HeadTrainConfig",
    "LightGBMMarginMuHead",
    "LightGBMMuHead",
    "LightGBMQuantileHead",
    "LightGBMSigmaHead",
    "LightGBMTotalMuHead",
    "NGBoostNormalHead",
    "NotFittedError",
    "PredictorError",
    "TargetName",
    "XGBoostMuHead",
    "abs_residual_labels",
    "abs_residual_to_sigma",
    "enforce_quantile_order",
    "feature_columns",
    "is_rating_differential_feature",
    "label_column_for",
    "monotone_constraints_for",
    "prediction_column_for",
    "quantile_column",
    "resolve_sample_weight",
    "select_top_k_features",
    "sigma_to_abs_residual",
    "time_decay_weights",
]
