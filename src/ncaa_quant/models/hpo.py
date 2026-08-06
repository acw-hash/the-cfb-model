"""Optuna hyperparameter optimization for mapping-layer heads (DESIGN §6).

HPO runs **inside** the outer training window only. Outer walk-forward test
seasons are never stored on the data handle passed to trial objectives —
requesting them raises :class:`NestedIsolationError` (API-level nesting, not
convention).

Objective: mean walk-forward validation loss over the last
``n_validation_seasons`` (default 3) seasons in the window — never a single
season. Losses: MSE (μ), mean pinball (quantile), Gaussian CRPS
(distributional).

Studies use TPE (``multivariate=True``) + Hyperband pruning, SQLite/journal
storage (resume-safe), deterministic ``seed = f(study_name, trial_number)``,
MLflow trial logging, wall-clock + ``MaxTrialsCallback`` guards, and a
quarantine-season top-5 regularization tiebreak.
"""

from __future__ import annotations

import hashlib
import math
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd  # type: ignore[import-untyped]
import structlog
from optuna.samplers import TPESampler
from optuna.study import MaxTrialsCallback
from optuna.trial import TrialState
from scipy import stats  # type: ignore[import-untyped]

from ncaa_quant.models.heads.base import (
    HeadTrainConfig,
    TargetName,
    feature_columns,
    label_column_for,
    monotone_constraints_for,
)
from ncaa_quant.models.heads.quantile import QUANTILES
from ncaa_quant.models.heads.weights import resolve_sample_weight
from ncaa_quant.utils.seeding import set_global_seed

log = structlog.get_logger(__name__)

HeadKind = Literal[
    "lgbm_mu",
    "lgbm_quantile",
    "lgbm_sigma",
    "xgb_mu",
    "cat_mu",
    "enet_mu",
    "ngboost",
]

DEFAULT_N_VALIDATION_SEASONS = 3
DEFAULT_LGBM_N_JOBS = 4
DEFAULT_EARLY_STOPPING_ROUNDS = 200
DEFAULT_TOP_K_TIEBREAK = 5

# Quarantine tiebreak: prefer more regularized configs in this lexicographic
# order when Optuna vs quarantine rankings disagree (DESIGN §6).
# Each entry is (param_name, "higher"|"lower").
REGULARIZATION_TIEBREAK_ORDER: tuple[tuple[str, Literal["higher", "lower"]], ...] = (
    ("min_child_samples", "higher"),
    ("num_leaves", "lower"),
    ("lambda_l2", "higher"),
)

# Aliases accepted when comparing regularization (HeadTrainConfig uses reg_lambda).
_LAMBDA_ALIASES: frozenset[str] = frozenset({"lambda_l2", "reg_lambda", "reg_lambda_l2"})


class HPOError(ValueError):
    """Invalid HPO configuration or inputs."""


class NestedIsolationError(HPOError):
    """Raised when an objective attempts to read an outer test season.

    This is the structural guarantee of nested CV: the restricted handle
    never contains outer-test rows, and any explicit request for a forbidden
    season fails loudly.
    """


# ---------------------------------------------------------------------------
# Restricted data handle (nested isolation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeasonSlice:
    """One season's feature/label rows for inner walk-forward folds.

    ``features`` must include ``game_id`` plus numeric feature columns.
    ``labels`` must include ``game_id``, ``season``, and the target label
    column for the head under study.
    """

    season: int
    features: pd.DataFrame
    labels: pd.DataFrame

    def __post_init__(self) -> None:
        if "game_id" not in self.features.columns:
            msg = f"SeasonSlice({self.season}) features missing game_id"
            raise HPOError(msg)
        if "game_id" not in self.labels.columns:
            msg = f"SeasonSlice({self.season}) labels missing game_id"
            raise HPOError(msg)


@dataclass(frozen=True)
class HPOTrainWindow:
    """Training-window-only data handle for Optuna objectives.

    Construction is the isolation boundary: only ``train_seasons`` rows are
    retained. ``outer_test_seasons`` are recorded solely so
    :meth:`get` / :meth:`require_season` raise :class:`NestedIsolationError`
    rather than silently returning empty data — the objective is structurally
    incapable of reading outer-test rows because they are never stored.
    """

    _slices: Mapping[int, SeasonSlice]
    _forbidden_seasons: frozenset[int]
    _train_seasons: tuple[int, ...]

    @classmethod
    def from_frames(
        cls,
        features: pd.DataFrame,
        labels: pd.DataFrame,
        *,
        train_seasons: Sequence[int],
        outer_test_seasons: Sequence[int],
    ) -> HPOTrainWindow:
        """Build a window that retains **only** ``train_seasons`` rows.

        Parameters
        ----------
        features, labels:
            Full frames (may contain outer-test rows). Outer-test rows are
            dropped here and never stored on the returned handle.
        train_seasons:
            Seasons visible to HPO (inner walk-forward window).
        outer_test_seasons:
            Seasons that must remain unreadable inside the objective.
        """
        train = tuple(sorted({int(s) for s in train_seasons}))
        forbidden = frozenset(int(s) for s in outer_test_seasons)
        overlap = set(train) & set(forbidden)
        if overlap:
            msg = f"train_seasons overlap outer_test_seasons: {sorted(overlap)}"
            raise HPOError(msg)
        if not train:
            msg = "train_seasons must be non-empty"
            raise HPOError(msg)

        if "season" not in labels.columns:
            msg = "labels must include a season column"
            raise HPOError(msg)

        # Drop any feature/label rows whose season is outside the train window
        # (including outer test) before storing — structural isolation.
        label_train = labels.loc[labels["season"].astype(int).isin(train)].copy()
        feat_ids = set(label_train["game_id"].astype(int))
        feat_train = features.loc[features["game_id"].astype(int).isin(feat_ids)].copy()

        slices: dict[int, SeasonSlice] = {}
        for season in train:
            lab = label_train.loc[label_train["season"].astype(int) == season].copy()
            ids = set(lab["game_id"].astype(int))
            feat = feat_train.loc[feat_train["game_id"].astype(int).isin(ids)].copy()
            if lab.empty:
                msg = f"no label rows for train season {season}"
                raise HPOError(msg)
            slices[season] = SeasonSlice(season=season, features=feat, labels=lab)

        return cls(
            _slices=slices,
            _forbidden_seasons=forbidden,
            _train_seasons=train,
        )

    @property
    def train_seasons(self) -> tuple[int, ...]:
        return self._train_seasons

    @property
    def forbidden_seasons(self) -> frozenset[int]:
        return self._forbidden_seasons

    def seasons(self) -> tuple[int, ...]:
        """Seasons available inside the objective (training window only)."""
        return self._train_seasons

    def get(self, season: int) -> SeasonSlice:
        """Return a season slice, or raise if forbidden / absent.

        Outer test seasons raise :class:`NestedIsolationError` even though
        (and because) their rows were never loaded onto this handle.
        """
        s = int(season)
        if s in self._forbidden_seasons:
            msg = (
                f"nested isolation: season {s} is an outer test season and "
                "is unreadable inside the HPO objective"
            )
            raise NestedIsolationError(msg)
        if s not in self._slices:
            msg = (
                f"nested isolation: season {s} is outside the HPO training "
                f"window {list(self._train_seasons)}"
            )
            raise NestedIsolationError(msg)
        return self._slices[s]

    def require_season(self, season: int) -> SeasonSlice:
        """Alias of :meth:`get` — used by isolation tests / objectives."""
        return self.get(season)

    def validation_seasons(self, n: int = DEFAULT_N_VALIDATION_SEASONS) -> tuple[int, ...]:
        """Last ``n`` seasons in the training window (sorted ascending)."""
        if n < 1:
            msg = f"n_validation_seasons must be >= 1, got {n}"
            raise HPOError(msg)
        if len(self._train_seasons) < n:
            msg = (
                f"need at least {n} train seasons for walk-forward objective, "
                f"have {len(self._train_seasons)}: {list(self._train_seasons)}"
            )
            raise HPOError(msg)
        return self._train_seasons[-n:]

    def concat_before(self, season: int) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Concatenate features/labels for all window seasons strictly before ``season``."""
        s = int(season)
        # Touch get() so a forbidden season still raises.
        if s in self._forbidden_seasons:
            self.get(s)
        prior = [self._slices[y] for y in self._train_seasons if y < s]
        if not prior:
            msg = f"no training seasons before validation season {s}"
            raise HPOError(msg)
        features = pd.concat([p.features for p in prior], ignore_index=True)
        labels = pd.concat([p.labels for p in prior], ignore_index=True)
        return features, labels


# ---------------------------------------------------------------------------
# Losses
# ---------------------------------------------------------------------------


def mse_loss(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean squared error (μ-head objective)."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    if yt.size == 0:
        return float("nan")
    return float(np.mean((yt - yp) ** 2))


def pinball_loss(
    y_true: np.ndarray,
    quantile_preds: Mapping[float, np.ndarray],
) -> float:
    """Mean pinball loss averaged over provided quantiles."""
    yt = np.asarray(y_true, dtype=float)
    if yt.size == 0 or not quantile_preds:
        return float("nan")
    losses: list[float] = []
    for q, pred in quantile_preds.items():
        yp = np.asarray(pred, dtype=float)
        err = yt - yp
        losses.append(float(np.mean(np.maximum(q * err, (q - 1.0) * err))))
    return float(np.mean(losses))


def crps_gaussian(y_true: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> float:
    """Mean CRPS for Normal(μ, σ) forecasts (closed form).

    CRPS(N(μ,σ), y) = σ [ z (2Φ(z)−1) + 2φ(z) − 1/√π ] with z = (y−μ)/σ.
    """
    yt = np.asarray(y_true, dtype=float)
    m = np.asarray(mu, dtype=float)
    s = np.maximum(np.asarray(sigma, dtype=float), 1e-8)
    if yt.size == 0:
        return float("nan")
    z = (yt - m) / s
    phi = stats.norm.pdf(z)
    Phi = stats.norm.cdf(z)
    crps = s * (z * (2.0 * Phi - 1.0) + 2.0 * phi - 1.0 / math.sqrt(math.pi))
    return float(np.mean(crps))


def loss_for_head_kind(kind: HeadKind) -> Literal["mse", "pinball", "crps"]:
    """Map head kind → objective loss family (§6)."""
    if kind in ("lgbm_mu", "xgb_mu", "cat_mu", "enet_mu", "lgbm_sigma"):
        return "mse"
    if kind == "lgbm_quantile":
        return "pinball"
    if kind == "ngboost":
        return "crps"
    msg = f"unknown head kind: {kind}"
    raise HPOError(msg)


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------


def trial_seed(study_name: str, trial_number: int) -> int:
    """Deterministic seed ``f(study_name, trial_number)`` for a trial.

    Uses a stable SHA-256 digest truncated to a positive 31-bit int so it is
    safe for NumPy / LightGBM RNG APIs.
    """
    payload = f"{study_name}:{int(trial_number)}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    return int(digest[:8], 16) % (2**31 - 1)


# ---------------------------------------------------------------------------
# Regularization tiebreak (quarantine season)
# ---------------------------------------------------------------------------


def regularization_key(params: Mapping[str, Any]) -> tuple[float, ...]:
    """Sort key for "more regularized" — lower tuple is preferred.

    Codified rule (DESIGN §6 / Task 18):
    1. higher ``min_child_samples``
    2. then lower ``num_leaves``
    3. then higher ``lambda_l2`` (also accepts ``reg_lambda``)

    Missing keys sort as least regularized for that component so incomplete
    configs lose to fully specified ones when ties reach that slot.
    """
    key: list[float] = []
    for name, direction in REGULARIZATION_TIEBREAK_ORDER:
        raw = _param_lookup(params, name)
        if raw is None:
            # Least regularized sentinel → large sort key (loses min()).
            key.append(float("inf"))
            continue
        value = float(raw)
        # Sort ascending on key; more regularized → smaller key.
        key.append(-value if direction == "higher" else value)
    return tuple(key)


def _param_lookup(params: Mapping[str, Any], name: str) -> Any | None:
    if name in params:
        return params[name]
    if name == "lambda_l2":
        for alias in _LAMBDA_ALIASES:
            if alias in params:
                return params[alias]
    return None


def rankings_unstable(
    study_order: Sequence[int],
    quarantine_order: Sequence[int],
) -> bool:
    """True when quarantine ranking of the same trial ids disagrees with study order."""
    return list(study_order) != list(quarantine_order)


def select_by_regularization(candidates: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Return the most regularized param dict among ``candidates``."""
    if not candidates:
        msg = "no candidates for regularization tiebreak"
        raise HPOError(msg)
    return min(candidates, key=lambda p: regularization_key(p))


def apply_quarantine_tiebreak(
    top_trials: Sequence[optuna.trial.FrozenTrial],
    quarantine_losses: Mapping[int, float],
) -> optuna.trial.FrozenTrial:
    """Compare top-K on quarantine losses; stabilize via regularization if needed.

    Parameters
    ----------
    top_trials:
        Top-K Optuna trials ordered by study objective (best first).
    quarantine_losses:
        ``trial.number → loss`` on the quarantine season (lower is better).

    Returns
    -------
    The selected trial. If quarantine ranking matches study ranking, keep the
    study winner; otherwise pick the more regularized config among the top-K
    per :func:`regularization_key`.
    """
    if not top_trials:
        msg = "top_trials is empty"
        raise HPOError(msg)
    study_order = [t.number for t in top_trials]
    missing = [n for n in study_order if n not in quarantine_losses]
    if missing:
        msg = f"quarantine_losses missing trial numbers {missing}"
        raise HPOError(msg)
    quarantine_order = sorted(study_order, key=lambda n: quarantine_losses[n])
    if not rankings_unstable(study_order, quarantine_order):
        return top_trials[0]
    log.warning(
        "hpo.quarantine_ranking_unstable",
        study_order=study_order,
        quarantine_order=quarantine_order,
    )
    best_params = select_by_regularization([dict(t.params) for t in top_trials])
    for trial in top_trials:
        if dict(trial.params) == dict(best_params):
            return trial
        # Param equality can fail on float identity; compare via key + loss.
    # Fall back: among top-K, min regularization key, then min quarantine loss.
    return min(
        top_trials,
        key=lambda t: (regularization_key(t.params), quarantine_losses[t.number]),
    )


# ---------------------------------------------------------------------------
# Config / result
# ---------------------------------------------------------------------------


@dataclass
class HPOConfig:
    """Study configuration for one mapping-layer head."""

    study_name: str
    head_kind: HeadKind
    target: TargetName = "margin"
    storage: str | None = None
    n_trials: int = 300
    n_jobs: int | None = None
    use_gpu: bool = False
    max_wall_clock_seconds: float | None = None
    n_validation_seasons: int = DEFAULT_N_VALIDATION_SEASONS
    early_stopping_rounds: int = DEFAULT_EARLY_STOPPING_ROUNDS
    top_k_tiebreak: int = DEFAULT_TOP_K_TIEBREAK
    mlflow_experiment: str | None = "ncaa-quant-hpo"
    mlflow_tracking_uri: str | None = None
    sampler_seed: int = 42
    # When True, shrink the search space / boost rounds for unit tests.
    fast: bool = False

    def resolved_n_jobs(self) -> int:
        if self.n_jobs is not None:
            return int(self.n_jobs)
        if self.head_kind in ("xgb_mu", "cat_mu") and self.use_gpu:
            # Single GPU — serialize trials.
            return 1
        if self.head_kind in ("lgbm_mu", "lgbm_quantile", "lgbm_sigma"):
            return DEFAULT_LGBM_N_JOBS
        return 1


@dataclass
class HPOResult:
    """Outcome of a completed (or resumed) study."""

    study: optuna.Study
    best_params: dict[str, Any]
    best_value: float
    selected_trial_number: int
    per_season_losses_best: dict[int, float]
    quarantine_losses: dict[int, float]
    tiebreak_applied: bool
    head_train_config: HeadTrainConfig
    default_value: float | None = None
    improvement_over_default: float | None = None


# ---------------------------------------------------------------------------
# Search spaces
# ---------------------------------------------------------------------------


def suggest_params(trial: optuna.Trial, config: HPOConfig) -> dict[str, Any]:
    """Suggest a ~15-dim GBDT (or member-specific) hyperparameter dict."""
    fast = config.fast
    if config.head_kind in ("lgbm_mu", "lgbm_quantile", "lgbm_sigma"):
        return _suggest_lgbm(trial, fast=fast, use_gpu=config.use_gpu)
    if config.head_kind == "xgb_mu":
        return _suggest_xgb(trial, fast=fast, use_gpu=config.use_gpu)
    if config.head_kind == "cat_mu":
        return _suggest_cat(trial, fast=fast, use_gpu=config.use_gpu)
    if config.head_kind == "enet_mu":
        return {
            "alpha": trial.suggest_float("alpha", 1e-4 if not fast else 1e-3, 10.0, log=True),
            "l1_ratio": trial.suggest_float("l1_ratio", 0.05, 0.95),
            "top_k": trial.suggest_int("top_k", 5 if fast else 10, 15 if fast else 40),
        }
    if config.head_kind == "ngboost":
        return {
            "n_estimators": trial.suggest_int(
                "n_estimators", 20 if fast else 50, 60 if fast else 400
            ),
            "learning_rate": trial.suggest_float(
                "learning_rate", 1e-3, 0.2 if not fast else 0.15, log=True
            ),
            "max_depth": trial.suggest_int("max_depth", 1, 3 if fast else 5),
            "minibatch_frac": trial.suggest_float("minibatch_frac", 0.5, 1.0),
        }
    msg = f"unknown head kind: {config.head_kind}"
    raise HPOError(msg)


def _suggest_lgbm(trial: optuna.Trial, *, fast: bool, use_gpu: bool) -> dict[str, Any]:
    params: dict[str, Any] = {
        "learning_rate": trial.suggest_float("learning_rate", 1e-3, 0.3, log=True),
        "num_leaves": trial.suggest_int("num_leaves", 8 if fast else 16, 32 if fast else 128),
        "max_depth": trial.suggest_int("max_depth", 3, 6 if fast else 10),
        "min_child_samples": trial.suggest_int(
            "min_child_samples", 5 if fast else 10, 40 if fast else 100
        ),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10.0, log=True),
        "lambda_l1": trial.suggest_float("lambda_l1", 1e-3, 10.0, log=True),
        "min_split_gain": trial.suggest_float("min_split_gain", 0.0, 1.0),
        "n_estimators": trial.suggest_int("n_estimators", 30 if fast else 100, 80 if fast else 800),
        "use_gpu": use_gpu,
    }
    return params


def _suggest_xgb(trial: optuna.Trial, *, fast: bool, use_gpu: bool) -> dict[str, Any]:
    return {
        "learning_rate": trial.suggest_float("learning_rate", 1e-3, 0.3, log=True),
        "max_depth": trial.suggest_int("max_depth", 3, 6 if fast else 10),
        "min_child_weight": trial.suggest_float("min_child_weight", 1.0, 20.0, log=True),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-3, 10.0, log=True),
        "gamma": trial.suggest_float("gamma", 0.0, 5.0),
        "n_estimators": trial.suggest_int("n_estimators", 30 if fast else 100, 80 if fast else 800),
        # Map into tiebreak aliases
        "min_child_samples": trial.suggest_int("min_child_samples", 5, 40 if fast else 100),
        "num_leaves": trial.suggest_int("num_leaves", 8, 32 if fast else 128),
        "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10.0, log=True),
        "use_gpu": use_gpu,
    }


def _suggest_cat(trial: optuna.Trial, *, fast: bool, use_gpu: bool) -> dict[str, Any]:
    return {
        "learning_rate": trial.suggest_float("learning_rate", 1e-3, 0.3, log=True),
        "depth": trial.suggest_int("depth", 3, 6 if fast else 10),
        "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1e-3, 10.0, log=True),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "n_estimators": trial.suggest_int("n_estimators", 30 if fast else 100, 80 if fast else 800),
        "min_child_samples": trial.suggest_int("min_child_samples", 5, 40 if fast else 100),
        "num_leaves": trial.suggest_int("num_leaves", 8, 32 if fast else 128),
        "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10.0, log=True),
        "use_gpu": use_gpu,
    }


def params_to_head_train_config(
    params: Mapping[str, Any], *, use_gpu: bool = False
) -> HeadTrainConfig:
    """Map best Optuna params onto :class:`HeadTrainConfig` overlapping fields."""
    return HeadTrainConfig(
        n_estimators=int(params.get("n_estimators", 100)),
        learning_rate=float(params.get("learning_rate", 0.05)),
        max_depth=int(params.get("max_depth", params.get("depth", 4))),
        num_leaves=int(params.get("num_leaves", 31)),
        min_child_samples=int(params.get("min_child_samples", 20)),
        subsample=float(params.get("subsample", 0.9)),
        colsample_bytree=float(params.get("colsample_bytree", 0.9)),
        reg_lambda=float(params.get("lambda_l2", params.get("reg_lambda", 1.0))),
        use_gpu=bool(params.get("use_gpu", use_gpu)),
    )


# ---------------------------------------------------------------------------
# Trial fitting (direct estimators — enables early stopping + pruning)
# ---------------------------------------------------------------------------


class _LGBMPruningCallback:
    """Optuna pruning callback analog (no ``optuna-integration`` dependency)."""

    def __init__(self, trial: optuna.Trial, *, valid_name: str = "valid") -> None:
        self._trial = trial
        self._valid_name = valid_name

    def __call__(self, env: Any) -> None:
        # evaluation_result_list: list of (dataset_name, metric_name, value, _)
        score: float | None = None
        for item in env.evaluation_result_list:
            if item[0] == self._valid_name:
                score = float(item[2])
                break
        if score is None and env.evaluation_result_list:
            score = float(env.evaluation_result_list[-1][2])
        if score is None:
            return
        self._trial.report(score, step=int(env.iteration))
        if self._trial.should_prune():
            raise optuna.TrialPruned()


def _prepare_xy(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    target: TargetName,
) -> tuple[pd.DataFrame, np.ndarray, pd.Series | None]:
    label_col = label_column_for(target)
    if label_col not in labels.columns:
        msg = f"labels missing '{label_col}'"
        raise HPOError(msg)
    merged = labels[
        ["game_id", label_col] + (["season"] if "season" in labels.columns else [])
    ].merge(
        features,
        on="game_id",
        how="inner",
    )
    feat_cols = feature_columns(merged)
    if not feat_cols:
        msg = "no numeric feature columns for HPO fit"
        raise HPOError(msg)
    y = merged[label_col].astype(float)
    mask = y.notna()
    merged = merged.loc[mask].reset_index(drop=True)
    x = merged[feat_cols].apply(pd.to_numeric, errors="coerce")
    seasons = merged["season"] if "season" in merged.columns else None
    return x, merged[label_col].astype(float).to_numpy(), seasons


def _fit_predict_lgbm_mu(
    trial: optuna.Trial | None,
    params: Mapping[str, Any],
    *,
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    w_train: np.ndarray,
    x_valid: pd.DataFrame,
    y_valid: np.ndarray,
    target: TargetName,
    seed: int,
    early_stopping_rounds: int,
    fold_step_base: int,
) -> tuple[np.ndarray, dict[str, float]]:
    mono = monotone_constraints_for(list(x_train.columns), target=target)
    lgb_params: dict[str, Any] = {
        "objective": "regression",
        "metric": "l2",
        "learning_rate": float(params["learning_rate"]),
        "num_leaves": int(params["num_leaves"]),
        "max_depth": int(params["max_depth"]),
        "min_child_samples": int(params["min_child_samples"]),
        "subsample": float(params["subsample"]),
        "colsample_bytree": float(params["colsample_bytree"]),
        "reg_lambda": float(params.get("lambda_l2", params.get("reg_lambda", 1.0))),
        "reg_alpha": float(params.get("lambda_l1", 0.0)),
        "min_split_gain": float(params.get("min_split_gain", 0.0)),
        "verbosity": -1,
        "seed": seed,
        "deterministic": True,
        "force_row_wise": True,
        "monotone_constraints": mono,
    }
    if params.get("use_gpu"):
        lgb_params["device"] = "gpu"

    dtrain = lgb.Dataset(
        x_train,
        label=y_train,
        weight=w_train,
        feature_name=list(x_train.columns),
        free_raw_data=False,
    )
    dvalid = lgb.Dataset(
        x_valid,
        label=y_valid,
        feature_name=list(x_valid.columns),
        free_raw_data=False,
        reference=dtrain,
    )
    callbacks: list[Any] = [
        lgb.early_stopping(early_stopping_rounds, verbose=False),
        lgb.log_evaluation(period=0),
    ]
    if trial is not None:
        callbacks.append(_FoldAwarePruningCallback(trial, fold_step_base=fold_step_base))

    booster = lgb.train(
        lgb_params,
        dtrain,
        num_boost_round=int(params["n_estimators"]),
        valid_sets=[dvalid],
        valid_names=["valid"],
        callbacks=callbacks,
    )
    pred = np.asarray(booster.predict(x_valid), dtype=float)
    names = list(x_train.columns)
    gains = booster.feature_importance(importance_type="gain")
    importance = {n: float(g) for n, g in zip(names, gains, strict=True)}
    return pred, importance


class _FoldAwarePruningCallback:
    """Report per-boosting-round valid loss with a fold offset for Hyperband."""

    def __init__(self, trial: optuna.Trial, *, fold_step_base: int) -> None:
        self._inner = _LGBMPruningCallback(trial)
        self._fold_step_base = fold_step_base
        self._trial = trial

    def __call__(self, env: Any) -> None:
        score: float | None = None
        for item in env.evaluation_result_list:
            if item[0] == "valid":
                score = float(item[2])
                break
        if score is None and env.evaluation_result_list:
            score = float(env.evaluation_result_list[-1][2])
        if score is None:
            return
        step = self._fold_step_base + int(env.iteration)
        self._trial.report(score, step=step)
        if self._trial.should_prune():
            raise optuna.TrialPruned()


def _fit_predict_lgbm_quantile(
    params: Mapping[str, Any],
    *,
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    w_train: np.ndarray,
    x_valid: pd.DataFrame,
    seed: int,
    quantiles: Sequence[float] = QUANTILES,
) -> dict[float, np.ndarray]:
    out: dict[float, np.ndarray] = {}
    for q in quantiles:
        lgb_params: dict[str, Any] = {
            "objective": "quantile",
            "alpha": float(q),
            "metric": "quantile",
            "learning_rate": float(params["learning_rate"]),
            "num_leaves": int(params["num_leaves"]),
            "max_depth": int(params["max_depth"]),
            "min_child_samples": int(params["min_child_samples"]),
            "subsample": float(params["subsample"]),
            "colsample_bytree": float(params["colsample_bytree"]),
            "reg_lambda": float(params.get("lambda_l2", 1.0)),
            "verbosity": -1,
            "seed": seed,
            "deterministic": True,
            "force_row_wise": True,
        }
        dtrain = lgb.Dataset(x_train, label=y_train, weight=w_train, free_raw_data=False)
        booster = lgb.train(lgb_params, dtrain, num_boost_round=int(params["n_estimators"]))
        out[float(q)] = np.asarray(booster.predict(x_valid), dtype=float)
    return out


def _fit_predict_ngboost(
    params: Mapping[str, Any],
    *,
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    x_valid: pd.DataFrame,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    from ngboost import NGBRegressor  # type: ignore[import-untyped]
    from ngboost.distns import Normal  # type: ignore[import-untyped]
    from sklearn.tree import DecisionTreeRegressor  # type: ignore[import-untyped]

    base = DecisionTreeRegressor(max_depth=int(params["max_depth"]), random_state=seed)
    model = NGBRegressor(
        Dist=Normal,
        Base=base,
        n_estimators=int(params["n_estimators"]),
        learning_rate=float(params["learning_rate"]),
        minibatch_frac=float(params.get("minibatch_frac", 1.0)),
        natural_gradient=True,
        verbose=False,
        random_state=seed,
    )
    model.fit(x_train.to_numpy(dtype=float), y_train)
    dist = model.pred_dist(x_valid.to_numpy(dtype=float))
    return np.asarray(dist.loc, dtype=float), np.maximum(np.asarray(dist.scale, dtype=float), 1e-6)


def _fit_predict_enet(
    params: Mapping[str, Any],
    *,
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    w_train: np.ndarray,
    x_valid: pd.DataFrame,
) -> np.ndarray:
    from sklearn.linear_model import ElasticNet  # type: ignore[import-untyped]

    top_k = int(params.get("top_k", 30))
    corr = x_train.apply(lambda c: float(np.corrcoef(c.to_numpy(dtype=float), y_train)[0, 1]))
    corr = corr.replace([np.inf, -np.inf], np.nan).fillna(0.0).abs()
    cols = list(corr.sort_values(ascending=False).head(top_k).index)
    model = ElasticNet(
        alpha=float(params["alpha"]),
        l1_ratio=float(params["l1_ratio"]),
        max_iter=5000,
        random_state=0,
    )
    model.fit(x_train[cols].to_numpy(dtype=float), y_train, sample_weight=w_train)
    return np.asarray(model.predict(x_valid[cols].to_numpy(dtype=float)), dtype=float)


def _fit_predict_xgb(
    params: Mapping[str, Any],
    *,
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    w_train: np.ndarray,
    x_valid: pd.DataFrame,
    target: TargetName,
    seed: int,
) -> np.ndarray:
    import xgboost as xgb

    mono = monotone_constraints_for(list(x_train.columns), target=target)
    model = xgb.XGBRegressor(
        n_estimators=int(params["n_estimators"]),
        learning_rate=float(params["learning_rate"]),
        max_depth=int(params["max_depth"]),
        min_child_weight=float(params.get("min_child_weight", 1.0)),
        subsample=float(params["subsample"]),
        colsample_bytree=float(params["colsample_bytree"]),
        reg_lambda=float(params.get("reg_lambda", params.get("lambda_l2", 1.0))),
        reg_alpha=float(params.get("reg_alpha", 0.0)),
        gamma=float(params.get("gamma", 0.0)),
        monotone_constraints=tuple(mono),
        random_state=seed,
        n_jobs=1,
        device="cuda" if params.get("use_gpu") else "cpu",
        verbosity=0,
    )
    model.fit(x_train, y_train, sample_weight=w_train, verbose=False)
    return np.asarray(model.predict(x_valid), dtype=float)


def _fit_predict_cat(
    params: Mapping[str, Any],
    *,
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    w_train: np.ndarray,
    x_valid: pd.DataFrame,
    target: TargetName,
    seed: int,
) -> np.ndarray:
    from catboost import CatBoostRegressor  # type: ignore[import-untyped]

    mono = monotone_constraints_for(list(x_train.columns), target=target)
    model = CatBoostRegressor(
        iterations=int(params["n_estimators"]),
        learning_rate=float(params["learning_rate"]),
        depth=int(params.get("depth", params.get("max_depth", 4))),
        l2_leaf_reg=float(params.get("l2_leaf_reg", params.get("lambda_l2", 1.0))),
        subsample=float(params["subsample"]),
        random_seed=seed,
        verbose=False,
        allow_writing_files=False,
        monotone_constraints=mono,
        task_type="GPU" if params.get("use_gpu") else "CPU",
    )
    model.fit(x_train, y_train, sample_weight=w_train)
    return np.asarray(model.predict(x_valid), dtype=float)


# ---------------------------------------------------------------------------
# Objective
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardObjective:
    """Optuna objective closed over a :class:`HPOTrainWindow` only.

    The objective never receives outer-test frames. Any call to
    ``window.get(outer_test_season)`` raises :class:`NestedIsolationError`.
    """

    window: HPOTrainWindow
    config: HPOConfig

    def __call__(self, trial: optuna.Trial) -> float:
        seed = trial_seed(self.config.study_name, trial.number)
        trial.set_user_attr("seed", seed)
        set_global_seed(seed)

        params = suggest_params(trial, self.config)
        val_seasons = self.window.validation_seasons(self.config.n_validation_seasons)
        per_season: dict[int, float] = {}
        importance_acc: dict[str, float] = {}
        loss_kind = loss_for_head_kind(self.config.head_kind)

        # Cap boost-round steps per fold so Hyperband step indices stay bounded.
        step_stride = int(params.get("n_estimators", 100)) + 1

        for fold_i, val_season in enumerate(val_seasons):
            # Structural isolation: validation seasons come from window.seasons().
            val_slice = self.window.get(val_season)
            x_tr_raw, y_lab = self.window.concat_before(val_season)
            x_train, y_train, seasons = _prepare_xy(x_tr_raw, y_lab, target=self.config.target)
            x_valid, y_valid, _ = _prepare_xy(
                val_slice.features, val_slice.labels, target=self.config.target
            )
            if len(y_train) == 0 or len(y_valid) == 0:
                msg = f"empty fold for validation season {val_season}"
                raise HPOError(msg)

            w_train = resolve_sample_weight(
                n=len(x_train),
                seasons=seasons,
                sample_weight=None,
                season_half_life=2.0,
            )
            fold_base = fold_i * step_stride

            if self.config.head_kind in ("lgbm_mu", "lgbm_sigma"):
                pred, imp = _fit_predict_lgbm_mu(
                    trial,
                    params,
                    x_train=x_train,
                    y_train=y_train,
                    w_train=w_train,
                    x_valid=x_valid,
                    y_valid=y_valid,
                    target=self.config.target,
                    seed=seed + fold_i,
                    early_stopping_rounds=self.config.early_stopping_rounds,
                    fold_step_base=fold_base,
                )
                for k, v in imp.items():
                    importance_acc[k] = importance_acc.get(k, 0.0) + v
                season_loss = mse_loss(y_valid, pred)
            elif self.config.head_kind == "lgbm_quantile":
                qpred = _fit_predict_lgbm_quantile(
                    params,
                    x_train=x_train,
                    y_train=y_train,
                    w_train=w_train,
                    x_valid=x_valid,
                    seed=seed + fold_i,
                )
                season_loss = pinball_loss(y_valid, qpred)
                trial.report(season_loss, step=fold_base)
                if trial.should_prune():
                    raise optuna.TrialPruned()
            elif self.config.head_kind == "ngboost":
                mu, sigma = _fit_predict_ngboost(
                    params,
                    x_train=x_train,
                    y_train=y_train,
                    x_valid=x_valid,
                    seed=seed + fold_i,
                )
                season_loss = crps_gaussian(y_valid, mu, sigma)
                trial.report(season_loss, step=fold_base)
                if trial.should_prune():
                    raise optuna.TrialPruned()
            elif self.config.head_kind == "enet_mu":
                pred = _fit_predict_enet(
                    params,
                    x_train=x_train,
                    y_train=y_train,
                    w_train=w_train,
                    x_valid=x_valid,
                )
                season_loss = mse_loss(y_valid, pred)
                trial.report(season_loss, step=fold_base)
                if trial.should_prune():
                    raise optuna.TrialPruned()
            elif self.config.head_kind == "xgb_mu":
                pred = _fit_predict_xgb(
                    params,
                    x_train=x_train,
                    y_train=y_train,
                    w_train=w_train,
                    x_valid=x_valid,
                    target=self.config.target,
                    seed=seed + fold_i,
                )
                season_loss = mse_loss(y_valid, pred)
                trial.report(season_loss, step=fold_base)
                if trial.should_prune():
                    raise optuna.TrialPruned()
            elif self.config.head_kind == "cat_mu":
                pred = _fit_predict_cat(
                    params,
                    x_train=x_train,
                    y_train=y_train,
                    w_train=w_train,
                    x_valid=x_valid,
                    target=self.config.target,
                    seed=seed + fold_i,
                )
                season_loss = mse_loss(y_valid, pred)
                trial.report(season_loss, step=fold_base)
                if trial.should_prune():
                    raise optuna.TrialPruned()
            else:
                msg = f"unsupported head kind: {self.config.head_kind}"
                raise HPOError(msg)

            if not math.isfinite(season_loss):
                raise optuna.TrialPruned()
            per_season[int(val_season)] = float(season_loss)

        mean_loss = float(np.mean(list(per_season.values())))
        trial.set_user_attr("per_season_losses", {str(k): v for k, v in per_season.items()})
        trial.set_user_attr("loss_kind", loss_kind)
        if importance_acc:
            # Average gain across folds.
            n_folds = max(len(per_season), 1)
            trial.set_user_attr(
                "feature_importance",
                {k: v / n_folds for k, v in importance_acc.items()},
            )
        return mean_loss


# ---------------------------------------------------------------------------
# MLflow + study callbacks
# ---------------------------------------------------------------------------


def _maybe_log_trial_mlflow(config: HPOConfig, trial: optuna.trial.FrozenTrial) -> None:
    if config.mlflow_experiment is None:
        return
    if trial.state != TrialState.COMPLETE:
        return
    try:
        import mlflow
    except ImportError:  # pragma: no cover
        return

    if config.mlflow_tracking_uri:
        mlflow.set_tracking_uri(config.mlflow_tracking_uri)
    mlflow.set_experiment(config.mlflow_experiment)
    run_name = f"{config.study_name}__trial_{trial.number}"
    with mlflow.start_run(run_name=run_name, nested=False):
        mlflow.log_param("study_name", config.study_name)
        mlflow.log_param("head_kind", config.head_kind)
        mlflow.log_param("target", config.target)
        mlflow.log_param(
            "seed", trial.user_attrs.get("seed", trial_seed(config.study_name, trial.number))
        )
        for k, v in trial.params.items():
            mlflow.log_param(k, v)
        mlflow.log_metric(
            "objective_mean_loss", float(trial.value) if trial.value is not None else float("nan")
        )
        per = trial.user_attrs.get("per_season_losses") or {}
        for season_s, loss in per.items():
            mlflow.log_metric(f"loss_season_{season_s}", float(loss))
        importance = trial.user_attrs.get("feature_importance")
        if isinstance(importance, dict) and importance:
            mlflow.log_dict(importance, "feature_importance.json")


class WallClockCallback:
    """Stop the study when wall-clock budget is exhausted (§6)."""

    def __init__(self, max_seconds: float) -> None:
        if max_seconds <= 0:
            msg = f"max_wall_clock_seconds must be positive, got {max_seconds}"
            raise HPOError(msg)
        self._deadline = time.monotonic() + float(max_seconds)

    def __call__(self, study: optuna.Study, trial: optuna.trial.FrozenTrial) -> None:
        if time.monotonic() >= self._deadline:
            log.info("hpo.wall_clock_stop", study=study.study_name, trial=trial.number)
            study.stop()


class MLflowTrialCallback:
    """Log each completed trial to MLflow."""

    def __init__(self, config: HPOConfig) -> None:
        self._config = config

    def __call__(self, study: optuna.Study, trial: optuna.trial.FrozenTrial) -> None:
        _maybe_log_trial_mlflow(self._config, trial)


def _quarantine_eval(
    trial_params: Mapping[str, Any],
    *,
    window: HPOTrainWindow,
    quarantine: SeasonSlice,
    config: HPOConfig,
    seed: int,
) -> float:
    """Train on the full HPO window; score on the quarantine season."""
    # Refuse quarantine seasons that are somehow forbidden (should not happen).
    if quarantine.season in window.forbidden_seasons:
        msg = f"quarantine season {quarantine.season} collides with outer test seasons"
        raise NestedIsolationError(msg)

    features = pd.concat([window.get(s).features for s in window.train_seasons], ignore_index=True)
    labels = pd.concat([window.get(s).labels for s in window.train_seasons], ignore_index=True)
    x_train, y_train, seasons = _prepare_xy(features, labels, target=config.target)
    x_valid, y_valid, _ = _prepare_xy(quarantine.features, quarantine.labels, target=config.target)
    w_train = resolve_sample_weight(
        n=len(x_train), seasons=seasons, sample_weight=None, season_half_life=2.0
    )
    params = dict(trial_params)

    if config.head_kind in ("lgbm_mu", "lgbm_sigma"):
        pred, _ = _fit_predict_lgbm_mu(
            None,
            params,
            x_train=x_train,
            y_train=y_train,
            w_train=w_train,
            x_valid=x_valid,
            y_valid=y_valid,
            target=config.target,
            seed=seed,
            early_stopping_rounds=config.early_stopping_rounds,
            fold_step_base=0,
        )
        return mse_loss(y_valid, pred)
    if config.head_kind == "lgbm_quantile":
        qpred = _fit_predict_lgbm_quantile(
            params,
            x_train=x_train,
            y_train=y_train,
            w_train=w_train,
            x_valid=x_valid,
            seed=seed,
        )
        return pinball_loss(y_valid, qpred)
    if config.head_kind == "ngboost":
        mu, sigma = _fit_predict_ngboost(
            params, x_train=x_train, y_train=y_train, x_valid=x_valid, seed=seed
        )
        return crps_gaussian(y_valid, mu, sigma)
    if config.head_kind == "enet_mu":
        pred = _fit_predict_enet(
            params, x_train=x_train, y_train=y_train, w_train=w_train, x_valid=x_valid
        )
        return mse_loss(y_valid, pred)
    if config.head_kind == "xgb_mu":
        pred = _fit_predict_xgb(
            params,
            x_train=x_train,
            y_train=y_train,
            w_train=w_train,
            x_valid=x_valid,
            target=config.target,
            seed=seed,
        )
        return mse_loss(y_valid, pred)
    if config.head_kind == "cat_mu":
        pred = _fit_predict_cat(
            params,
            x_train=x_train,
            y_train=y_train,
            w_train=w_train,
            x_valid=x_valid,
            target=config.target,
            seed=seed,
        )
        return mse_loss(y_valid, pred)
    msg = f"unsupported head kind: {config.head_kind}"
    raise HPOError(msg)


def evaluate_default_params(
    window: HPOTrainWindow,
    config: HPOConfig,
    *,
    params: Mapping[str, Any] | None = None,
) -> tuple[float, dict[int, float]]:
    """Score Task-17-style defaults with the same walk-forward objective."""
    defaults = (
        dict(params)
        if params is not None
        else {
            "learning_rate": 0.05,
            "num_leaves": 31,
            "max_depth": 4,
            "min_child_samples": 20,
            "subsample": 0.9,
            "colsample_bytree": 0.9,
            "lambda_l2": 1.0,
            "lambda_l1": 1e-3,
            "min_split_gain": 0.0,
            "n_estimators": 100,
            "use_gpu": False,
        }
    )
    # Build a one-shot synthetic trial-less evaluation.
    val_seasons = window.validation_seasons(config.n_validation_seasons)
    per_season: dict[int, float] = {}
    seed = trial_seed(config.study_name, -1)
    for val_season in val_seasons:
        val_slice = window.get(val_season)
        x_tr_raw, y_lab = window.concat_before(val_season)
        x_train, y_train, seasons = _prepare_xy(x_tr_raw, y_lab, target=config.target)
        x_valid, y_valid, _ = _prepare_xy(
            val_slice.features, val_slice.labels, target=config.target
        )
        w_train = resolve_sample_weight(
            n=len(x_train), seasons=seasons, sample_weight=None, season_half_life=2.0
        )
        if config.head_kind in ("lgbm_mu", "lgbm_sigma"):
            pred, _ = _fit_predict_lgbm_mu(
                None,
                defaults,
                x_train=x_train,
                y_train=y_train,
                w_train=w_train,
                x_valid=x_valid,
                y_valid=y_valid,
                target=config.target,
                seed=seed,
                early_stopping_rounds=config.early_stopping_rounds,
                fold_step_base=0,
            )
            per_season[int(val_season)] = mse_loss(y_valid, pred)
        else:
            msg = "evaluate_default_params currently supports lgbm_mu/sigma only"
            raise HPOError(msg)
    return float(np.mean(list(per_season.values()))), per_season


# ---------------------------------------------------------------------------
# Study runner
# ---------------------------------------------------------------------------


def create_study(config: HPOConfig) -> optuna.Study:
    """Create or resume a persistent Optuna study (TPE + Hyperband)."""
    sampler = TPESampler(seed=config.sampler_seed, multivariate=True)
    # Hyperband approximates ASHA-style successive halving (§6).
    pruner = optuna.pruners.HyperbandPruner(
        min_resource=1,
        max_resource="auto",
        reduction_factor=3,
    )
    storage = config.storage
    return optuna.create_study(
        study_name=config.study_name,
        storage=storage,
        load_if_exists=True,
        direction="minimize",
        sampler=sampler,
        pruner=pruner,
    )


def run_hpo(
    window: HPOTrainWindow,
    config: HPOConfig,
    *,
    quarantine: SeasonSlice | None = None,
    show_progress_bar: bool = False,
) -> HPOResult:
    """Run (or resume) an HPO study and apply quarantine-season selection.

    Parameters
    ----------
    window:
        Restricted training-window handle — the **only** data the objective
        can see.
    config:
        Study settings (trials, parallelism, GPU, wall-clock, …).
    quarantine:
        Optional season **never used in the study**. Top-5 Optuna configs are
        re-scored here; unstable rankings trigger the regularization tiebreak.
    """
    if quarantine is not None:
        if quarantine.season in window.train_seasons:
            msg = (
                f"quarantine season {quarantine.season} must not be in the "
                f"HPO training window {list(window.train_seasons)}"
            )
            raise HPOError(msg)
        if quarantine.season in window.forbidden_seasons:
            msg = (
                f"quarantine season {quarantine.season} collides with outer "
                "test seasons (use a held-out season inside the outer train "
                "span, not an outer test season)"
            )
            raise NestedIsolationError(msg)

    # Validate that the walk-forward objective can form last-N folds.
    window.validation_seasons(config.n_validation_seasons)

    study = create_study(config)
    objective = WalkForwardObjective(window=window, config=config)

    callbacks: list[Callable[[optuna.Study, optuna.trial.FrozenTrial], None]] = [
        MaxTrialsCallback(config.n_trials, states=(TrialState.COMPLETE, TrialState.PRUNED)),
        MLflowTrialCallback(config),
    ]
    if config.max_wall_clock_seconds is not None:
        callbacks.append(WallClockCallback(config.max_wall_clock_seconds))

    # Remaining trials if resuming.
    done = len(
        [
            t
            for t in study.trials
            if t.state in (TrialState.COMPLETE, TrialState.PRUNED, TrialState.FAIL)
        ]
    )
    remaining = max(config.n_trials - done, 0)
    if remaining > 0:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            study.optimize(
                objective,
                n_trials=remaining,
                n_jobs=config.resolved_n_jobs(),
                callbacks=callbacks,
                show_progress_bar=show_progress_bar,
                gc_after_trial=True,
            )

    complete = [t for t in study.trials if t.state == TrialState.COMPLETE]
    if not complete:
        msg = f"study {config.study_name!r} finished with no COMPLETE trials"
        raise HPOError(msg)

    top = sorted(complete, key=lambda t: float(t.value) if t.value is not None else math.inf)[
        : config.top_k_tiebreak
    ]
    quarantine_losses: dict[int, float] = {}
    tiebreak_applied = False
    selected = top[0]

    if quarantine is not None:
        for t in top:
            seed = int(t.user_attrs.get("seed", trial_seed(config.study_name, t.number)))
            quarantine_losses[t.number] = _quarantine_eval(
                t.params,
                window=window,
                quarantine=quarantine,
                config=config,
                seed=seed,
            )
        study_order = [t.number for t in top]
        q_order = sorted(study_order, key=lambda n: quarantine_losses[n])
        tiebreak_applied = rankings_unstable(study_order, q_order)
        selected = apply_quarantine_tiebreak(top, quarantine_losses)

    best_params = dict(selected.params)
    best_value = float(selected.value) if selected.value is not None else float("nan")
    per_best = {
        int(k): float(v) for k, v in (selected.user_attrs.get("per_season_losses") or {}).items()
    }
    head_cfg = params_to_head_train_config(best_params, use_gpu=config.use_gpu)

    return HPOResult(
        study=study,
        best_params=best_params,
        best_value=best_value,
        selected_trial_number=int(selected.number),
        per_season_losses_best=per_best,
        quarantine_losses=quarantine_losses,
        tiebreak_applied=tiebreak_applied,
        head_train_config=head_cfg,
    )


def default_storage_path(study_name: str, directory: Path | str) -> str:
    """SQLite URL for a study file under ``directory``."""
    path = Path(directory) / f"{study_name}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Absolute path works on Windows for SQLAlchemy.
    return f"sqlite:///{path.resolve().as_posix()}"
