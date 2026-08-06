"""Uniform Predictor interface, feature-signature contract, and fit helpers.

All mapping-layer members implement :class:`BasePredictor`. ``predict`` returns
a frame with at least ``game_id`` plus the head's predicted quantities.
``save`` / ``load`` persist the estimator **and** the exact feature signature
(names, dtypes, order); ``predict`` raises :class:`FeatureSignatureError` on
mismatch rather than silently realigning columns.

Task 16's walk-forward harness currently passes an empty feature frame at
retrain gates. :class:`BasePredictor` maintains a ``game_id`` → feature-row
bank populated on every ``predict`` so ``fit`` can reconstruct training rows
when the harness supplies labels without features. That bank is an adapter for
the known harness gap — not a substitute for information-set-correct feature
frames once Task 16 wires them.
"""

from __future__ import annotations

import pickle
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.models.heads.weights import (
    DEFAULT_SEASON_HALF_LIFE,
    resolve_sample_weight,
)

TargetName = Literal["margin", "total", "sigma_margin", "sigma_total"]

META_COLUMNS: frozenset[str] = frozenset(
    {
        "game_id",
        "game_key",
        "season",
        "week",
        "as_of",
        "event_time",
        "realized_margin",
        "realized_total",
        "abs_residual_margin",
        "abs_residual_total",
        "home_points",
        "away_points",
        "home_team_id",
        "away_team_id",
        "sample_weight",
    }
)

# Feature names matching these tokens receive monotone +1 on margin μ heads
# (higher differential ⇒ higher predicted home margin). DESIGN §5.2 item 1.
RATING_DIFF_TOKENS: tuple[str, ...] = (
    "rating_diff",
    "elo_diff",
    "offense_rating_diff",
    "defense_rating_diff",
)


class PredictorError(ValueError):
    """Base error for mapping-layer predictors."""


class FeatureSignatureError(PredictorError):
    """Raised when predict-time features disagree with the fitted signature."""


class NotFittedError(PredictorError):
    """Raised when predict/save is called before a successful fit."""


@dataclass(frozen=True)
class FeatureSignature:
    """Exact expected feature schema at inference time.

    Order is significant: columns are selected and reordered to ``names``
    before the underlying estimator sees them. Dtypes are pandas dtype strings
    recorded at fit (e.g. ``float64``, ``Int64``).
    """

    names: tuple[str, ...]
    dtypes: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.names) != len(self.dtypes):
            msg = "FeatureSignature names/dtypes length mismatch"
            raise ValueError(msg)
        if len(set(self.names)) != len(self.names):
            msg = "FeatureSignature names must be unique"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        return {"names": list(self.names), "dtypes": list(self.dtypes)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FeatureSignature:
        return cls(
            names=tuple(str(n) for n in payload["names"]),
            dtypes=tuple(str(d) for d in payload["dtypes"]),
        )

    @classmethod
    def from_frame(cls, frame: pd.DataFrame, feature_cols: Sequence[str]) -> FeatureSignature:
        names = tuple(feature_cols)
        dtypes = tuple(str(frame[c].dtype) for c in names)
        return cls(names=names, dtypes=dtypes)


def is_rating_differential_feature(name: str) -> bool:
    """Return True when ``name`` is a rating-differential feature (§5.2)."""
    lower = name.lower()
    return any(tok in lower for tok in RATING_DIFF_TOKENS)


def feature_columns(frame: pd.DataFrame) -> list[str]:
    """Numeric / bool feature columns, excluding known metadata."""
    cols: list[str] = []
    for c in frame.columns:
        if c in META_COLUMNS:
            continue
        if str(c).startswith("feat__"):
            continue
        if pd.api.types.is_numeric_dtype(frame[c]) or pd.api.types.is_bool_dtype(frame[c]):
            cols.append(str(c))
    return cols


def monotone_constraints_for(
    feature_names: Sequence[str],
    *,
    target: TargetName,
) -> list[int]:
    """Build LightGBM/XGB-style monotone constraint vector.

    Margin μ: rating differentials constrained to +1. Totals and σ heads: all 0
    (no guaranteed monotone relationship on differentials for those targets).
    """
    if target != "margin":
        return [0] * len(feature_names)
    return [1 if is_rating_differential_feature(n) else 0 for n in feature_names]


def label_column_for(target: TargetName) -> str:
    """Map a head target to the labels-frame column name."""
    if target == "margin":
        return "realized_margin"
    if target == "total":
        return "realized_total"
    if target == "sigma_margin":
        return "abs_residual_margin"
    if target == "sigma_total":
        return "abs_residual_total"
    msg = f"unknown target: {target}"
    raise PredictorError(msg)


def prediction_column_for(target: TargetName) -> str:
    """Primary prediction column emitted by a μ / σ head."""
    if target == "margin":
        return "pred_margin"
    if target == "total":
        return "pred_total"
    if target == "sigma_margin":
        return "pred_sigma_margin"
    if target == "sigma_total":
        return "pred_sigma_total"
    msg = f"unknown target: {target}"
    raise PredictorError(msg)


@dataclass
class BasePredictor(ABC):
    """Shared fit/predict/save surface for every mapping-layer member.

    Parameters
    ----------
    target:
        Which label the head predicts.
    model_version:
        Written onto walk-forward prediction rows.
    season_half_life:
        Time-decay half-life in seasons for automatic sample weights when the
        caller does not pass ``sample_weight``. Default
        :data:`~ncaa_quant.models.heads.weights.DEFAULT_SEASON_HALF_LIFE`.
    seed:
        Estimator RNG seed (also read from the global seed manifest when set).
    """

    target: TargetName
    model_version: str = "head-v0"
    season_half_life: float = DEFAULT_SEASON_HALF_LIFE
    seed: int = 42
    _signature: FeatureSignature | None = field(default=None, init=False, repr=False)
    _fitted: bool = field(default=False, init=False, repr=False)
    _feature_bank: dict[int, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _monotone_constraints: list[int] | None = field(default=None, init=False, repr=False)

    @property
    def signature(self) -> FeatureSignature | None:
        return self._signature

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def monotone_constraints(self) -> list[int] | None:
        """Constraints actually applied at the last fit (None if N/A)."""
        return None if self._monotone_constraints is None else list(self._monotone_constraints)

    def fit(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame,
        *,
        sample_weight: pd.Series | None = None,
    ) -> None:
        """Fit on ``features`` / ``labels``.

        When ``features`` is empty or lacks feature columns (Task 16 harness
        retrain), rows are reconstructed from the predict-time feature bank
        joined to ``labels.game_id``.
        """
        x, y, seasons, game_ids = self._prepare_xy(features, labels)
        if x.empty:
            # Nothing usable yet (cold start before any predict+reveal).
            self._fitted = False
            self._signature = None
            return

        weights = resolve_sample_weight(
            n=len(x),
            seasons=seasons,
            sample_weight=sample_weight,
            season_half_life=self.season_half_life,
        )
        self._signature = FeatureSignature.from_frame(x, list(x.columns))
        self._fit_estimator(x, y, sample_weight=weights)
        self._fitted = True
        # Keep bank entries for games we just trained on (idempotent).
        for gid, row in zip(game_ids, x.to_dict(orient="records"), strict=True):
            self._feature_bank[int(gid)] = dict(row)

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        """Predict; bank feature rows by ``game_id`` for later harness retrains."""
        if features.empty:
            return self._empty_prediction_frame()
        if "game_id" not in features.columns:
            msg = "features must include game_id"
            raise PredictorError(msg)

        self._bank_features(features)
        if not self._fitted or self._signature is None:
            raise NotFittedError(
                f"{type(self).__name__} has not been fit; refusing to emit predictions"
            )

        x = self._align_features(features)
        raw = self._predict_estimator(x)
        return self._format_predictions(features["game_id"].to_numpy(), raw)

    def save(self, path: Path | str) -> Path:
        """Persist estimator + signature via pickle. Returns the written path."""
        if not self._fitted:
            raise NotFittedError("cannot save an unfitted predictor")
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "class_name": type(self).__name__,
            "state": self._serializable_state(),
            "signature": None if self._signature is None else self._signature.to_dict(),
            "monotone_constraints": self._monotone_constraints,
            "fitted": self._fitted,
            "estimator": self._get_estimator(),
            "feature_bank": self._feature_bank,
            "target": self.target,
            "model_version": self.model_version,
            "season_half_life": self.season_half_life,
            "seed": self.seed,
        }
        with out.open("wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        return out

    @classmethod
    def load(cls, path: Path | str) -> BasePredictor:
        """Load a predictor saved by :meth:`save`."""
        with Path(path).open("rb") as fh:
            payload = pickle.load(fh)  # noqa: S301 — artifact controlled by us
        obj = cls._from_serializable_state(payload)
        return obj

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def _fit_estimator(
        self,
        x: pd.DataFrame,
        y: np.ndarray,
        *,
        sample_weight: np.ndarray,
    ) -> None: ...

    @abstractmethod
    def _predict_estimator(self, x: pd.DataFrame) -> np.ndarray | dict[str, np.ndarray]:
        """Return a 1-d array or a dict of named arrays (quantile heads)."""
        ...

    @abstractmethod
    def _get_estimator(self) -> Any: ...

    @abstractmethod
    def _set_estimator(self, estimator: Any) -> None: ...

    def _empty_prediction_frame(self) -> pd.DataFrame:
        col = prediction_column_for(self.target)
        cols = ["game_id", "pred_margin", "pred_total"]
        if col not in cols:
            cols.append(col)
        return pd.DataFrame(columns=cols)

    def _format_predictions(
        self,
        game_ids: np.ndarray,
        raw: np.ndarray | dict[str, np.ndarray],
    ) -> pd.DataFrame:
        if isinstance(raw, dict):
            out: dict[str, Any] = {"game_id": game_ids}
            out.update({k: np.asarray(v, dtype=float) for k, v in raw.items()})
            # Harness requires pred_margin; emit NaN when this head is total-only.
            if "pred_margin" not in out:
                out["pred_margin"] = np.full(len(game_ids), np.nan)
            if "pred_total" not in out:
                out["pred_total"] = np.full(len(game_ids), np.nan)
            return pd.DataFrame(out)

        col = prediction_column_for(self.target)
        values = np.asarray(raw, dtype=float)
        frame = pd.DataFrame({"game_id": game_ids, col: values})
        if col != "pred_margin":
            frame["pred_margin"] = np.nan
        if col != "pred_total":
            frame["pred_total"] = np.nan
        return frame

    def _serializable_state(self) -> dict[str, Any]:
        """Extra constructor kwargs beyond the shared dataclass fields."""
        return {}

    @classmethod
    def _from_serializable_state(cls, payload: Mapping[str, Any]) -> BasePredictor:
        state = dict(payload.get("state") or {})
        obj = cls(
            target=payload["target"],
            model_version=str(payload.get("model_version", "head-v0")),
            season_half_life=float(payload.get("season_half_life", DEFAULT_SEASON_HALF_LIFE)),
            seed=int(payload.get("seed", 42)),
            **state,
        )
        sig = payload.get("signature")
        obj._signature = None if sig is None else FeatureSignature.from_dict(sig)
        mono = payload.get("monotone_constraints")
        obj._monotone_constraints = None if mono is None else list(mono)
        obj._fitted = bool(payload.get("fitted", False))
        obj._feature_bank = {
            int(k): dict(v) for k, v in (payload.get("feature_bank") or {}).items()
        }
        obj._set_estimator(payload["estimator"])
        return obj

    # ------------------------------------------------------------------
    # Feature prep / signature enforcement
    # ------------------------------------------------------------------

    def _prepare_xy(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame,
    ) -> tuple[pd.DataFrame, np.ndarray, pd.Series | None, np.ndarray]:
        if labels.empty:
            return (
                pd.DataFrame(),
                np.asarray([], dtype=float),
                None,
                np.asarray([], dtype=int),
            )

        label_col = label_column_for(self.target)
        if label_col not in labels.columns:
            msg = f"labels missing required column '{label_col}' for target={self.target}"
            raise PredictorError(msg)
        if "game_id" not in labels.columns:
            msg = "labels must include game_id"
            raise PredictorError(msg)

        resolved = self._resolve_feature_frame(features, labels)
        if resolved.empty:
            return (
                pd.DataFrame(),
                np.asarray([], dtype=float),
                None,
                np.asarray([], dtype=int),
            )

        merged = labels[
            ["game_id", label_col] + (["season"] if "season" in labels.columns else [])
        ].merge(
            resolved,
            on="game_id",
            how="inner",
        )
        if merged.empty:
            return (
                pd.DataFrame(),
                np.asarray([], dtype=float),
                None,
                np.asarray([], dtype=int),
            )

        feat_cols = feature_columns(merged)
        if not feat_cols:
            msg = "no numeric feature columns available for fit"
            raise PredictorError(msg)

        y_series = merged[label_col].astype(float)
        mask = y_series.notna()
        merged = merged.loc[mask].reset_index(drop=True)
        y = merged[label_col].astype(float).to_numpy()
        x = merged[feat_cols].apply(pd.to_numeric, errors="coerce")
        seasons = merged["season"] if "season" in merged.columns else None
        game_ids = merged["game_id"].astype(int).to_numpy()
        return x, y, seasons, game_ids

    def _resolve_feature_frame(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame,
    ) -> pd.DataFrame:
        """Use ``features`` when usable; else rebuild from the predict-time bank."""
        if not features.empty and feature_columns(features):
            if "game_id" not in features.columns:
                msg = "features must include game_id"
                raise PredictorError(msg)
            cols = ["game_id", *feature_columns(features)]
            return features[cols].copy()

        if not self._feature_bank:
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        for gid in labels["game_id"].astype(int):
            cached = self._feature_bank.get(int(gid))
            if cached is None:
                continue
            rows.append({"game_id": int(gid), **cached})
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def _bank_features(self, features: pd.DataFrame) -> None:
        cols = feature_columns(features)
        if not cols:
            return
        for row in features[["game_id", *cols]].to_dict(orient="records"):
            gid = int(row.pop("game_id"))
            self._feature_bank[gid] = row

    def _align_features(self, features: pd.DataFrame) -> pd.DataFrame:
        if self._signature is None:
            raise NotFittedError("predictor has no feature signature")
        missing = [n for n in self._signature.names if n not in features.columns]
        if missing:
            msg = f"feature signature mismatch: missing columns {missing}"
            raise FeatureSignatureError(msg)

        # Exact name set required — extras beyond the signature are refused so a
        # swapped/renamed schema cannot silently pass.
        present = feature_columns(features)
        expected_set = set(self._signature.names)
        present_set = set(present)
        if present_set != expected_set:
            msg = (
                "feature signature mismatch: "
                f"got {sorted(present_set)}, expected {list(self._signature.names)}"
            )
            raise FeatureSignatureError(msg)

        ordered = features.loc[:, list(self._signature.names)].copy()
        for name, expected_dtype in zip(self._signature.names, self._signature.dtypes, strict=True):
            actual = str(ordered[name].dtype)
            if actual != expected_dtype:
                compatible = (
                    expected_dtype.startswith("float")
                    and actual.startswith(("float", "int", "Int", "UInt"))
                ) or (
                    expected_dtype.startswith(("int", "Int", "UInt"))
                    and actual.startswith(("int", "Int", "UInt"))
                )
                if not compatible:
                    msg = (
                        f"feature signature mismatch on '{name}': "
                        f"dtype {actual!r} != expected {expected_dtype!r}"
                    )
                    raise FeatureSignatureError(msg)
            ordered[name] = pd.to_numeric(ordered[name], errors="coerce")
        return ordered


@dataclass(frozen=True)
class HeadTrainConfig:
    """Shared GBDT defaults (HPO lands in Task 18)."""

    n_estimators: int = 100
    learning_rate: float = 0.05
    max_depth: int = 4
    num_leaves: int = 31
    min_child_samples: int = 20
    subsample: float = 0.9
    colsample_bytree: float = 0.9
    reg_lambda: float = 1.0
    use_gpu: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
