"""Level-1 NNLS stacking and ensemble σ (DESIGN §5.2).

Non-negative least squares over level-0 out-of-fold μ predictions, constrained
to weights summing to 1. Ensemble predictive variance follows the law of total
variance across members plus the σ-head.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy.optimize import nnls  # type: ignore[import-untyped]

TargetKind = Literal["margin", "total"]

# Column the caller must set True on every stacking row (OOF-only contract).
OOF_FLAG_COLUMN: str = "is_out_of_fold"


class EnsembleError(ValueError):
    """Raised for stacking / ensemble contract violations."""


@dataclass(frozen=True)
class NNLSStackResult:
    """Fitted non-negative stacking weights for one target."""

    target: TargetKind
    member_columns: tuple[str, ...]
    weights: tuple[float, ...]
    intercept: float = 0.0  # unused; kept for serialization symmetry
    condition_number: float = float("nan")
    n_oof_rows: int = 0
    fallback: str | None = None

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.member_columns, self.weights, strict=True))


@dataclass
class EnsembleMu:
    """Level-1 stacked μ with optional member-disagreement σ²."""

    stack: NNLSStackResult
    sigma_floor: float = 1e-6


@dataclass
class EnsembleSigmaResult:
    """Law-of-total-variance ensemble σ.

    ``sigma_ens**2 = var_across_members(mu_k) + sigma_head**2``
    (aleatoric from the σ-head, epistemic/disagreement from member spread).

    Optional ``stage1_var`` is the Var(μ) term from the Stage-1 posterior
    mixture (§2.6); when absent it is treated as zero for decomposition.
    """

    sigma: np.ndarray
    member_var: np.ndarray
    sigma_head: np.ndarray
    stage1_var: np.ndarray | None = None

    def variance_decomposition(self) -> dict[str, float]:
        """Mean aleatoric / member-epistemic / Stage-1 mixture variance shares.

        Returns three non-negative numbers that sum to mean total predictive
        variance (within floating-point tolerance).
        """
        aleatoric = np.asarray(self.sigma_head, dtype=float) ** 2
        epistemic = np.asarray(self.member_var, dtype=float)
        if self.stage1_var is None:
            stage1 = np.zeros_like(aleatoric)
        else:
            stage1 = np.asarray(self.stage1_var, dtype=float)
        mask = np.isfinite(aleatoric) & np.isfinite(epistemic) & np.isfinite(stage1)
        if not np.any(mask):
            return {
                "aleatoric_mean_var": float("nan"),
                "epistemic_member_mean_var": float("nan"),
                "stage1_mixture_mean_var": float("nan"),
                "total_mean_var": float("nan"),
            }
        a = float(np.mean(aleatoric[mask]))
        e = float(np.mean(epistemic[mask]))
        s = float(np.mean(stage1[mask]))
        return {
            "aleatoric_mean_var": a,
            "epistemic_member_mean_var": e,
            "stage1_mixture_mean_var": s,
            "total_mean_var": a + e + s,
        }


def assert_oof_only(frame: pd.DataFrame, *, flag_column: str = OOF_FLAG_COLUMN) -> None:
    """Raise if ``frame`` contains any in-fold (non-OOF) rows.

    Structural stacking contract (§5.2 / Task 19): NNLS must never see
    in-fold predictions. Callers mark OOF rows with ``is_out_of_fold=True``.
    """
    if flag_column not in frame.columns:
        msg = (
            f"stacking input missing required '{flag_column}' column — "
            "refusing to fit without an explicit OOF guarantee"
        )
        raise EnsembleError(msg)
    flags = frame[flag_column].to_numpy()
    if len(flags) == 0:
        msg = "stacking input is empty"
        raise EnsembleError(msg)
    if not np.all(np.asarray(flags, dtype=bool)):
        n_bad = int(np.sum(~np.asarray(flags, dtype=bool)))
        msg = (
            f"stacking input contains {n_bad} in-fold row(s); "
            "NNLS may only be fit on out-of-fold predictions"
        )
        raise EnsembleError(msg)


def _oof_condition_number(x: np.ndarray) -> float:
    """Spectral condition number of the OOF member matrix (∞ if rank-deficient)."""
    if x.size == 0 or x.shape[1] == 0:
        return float("inf")
    try:
        singular = np.linalg.svd(x, compute_uv=False)
    except np.linalg.LinAlgError:
        return float("inf")
    finite = singular[np.isfinite(singular) & (singular > 0)]
    if finite.size == 0:
        return float("inf")
    return float(finite.max() / finite.min())


def fit_nnls_stack(
    oof: pd.DataFrame,
    *,
    target: TargetKind,
    member_columns: Sequence[str],
    label_column: str | None = None,
    flag_column: str = OOF_FLAG_COLUMN,
    allow_equal_weight_fallback: bool = False,
) -> NNLSStackResult:
    """Fit non-negative least-squares stacking weights (sum to 1).

    Solves ``min ||X w - y||_2`` s.t. ``w >= 0``, then renormalizes ``w`` to
    sum to 1. Degenerate OOF (thin data, all-zero NNLS weights) raises unless
    ``allow_equal_weight_fallback`` is explicitly True — never silent.
    """
    assert_oof_only(oof, flag_column=flag_column)
    cols = tuple(str(c) for c in member_columns)
    if not cols:
        msg = "member_columns must be non-empty"
        raise EnsembleError(msg)
    missing = [c for c in cols if c not in oof.columns]
    if missing:
        msg = f"missing member columns: {missing}"
        raise EnsembleError(msg)

    y_col = label_column or ("realized_margin" if target == "margin" else "realized_total")
    if y_col not in oof.columns:
        msg = f"label column '{y_col}' missing from stacking frame"
        raise EnsembleError(msg)

    x = np.asarray(oof.loc[:, list(cols)], dtype=float)
    y = np.asarray(oof[y_col], dtype=float)
    mask = np.isfinite(y) & np.all(np.isfinite(x), axis=1)
    x, y = x[mask], y[mask]
    if x.shape[0] < 2:
        msg = f"need ≥2 finite OOF rows to stack, got {x.shape[0]}"
        raise EnsembleError(msg)

    cond = _oof_condition_number(x)
    raw, _residual = nnls(x, y)
    total = float(np.sum(raw))
    fallback: str | None = None
    if total <= 0.0 or not np.isfinite(total):
        if not allow_equal_weight_fallback:
            msg = (
                "NNLS produced all-zero weights (degenerate OOF member matrix); "
                f"condition_number={cond:.4g}, n_oof={x.shape[0]}. "
                "Set allow_equal_weight_fallback=True in config for an explicit "
                "equal-weight fallback — silent 0.5/0.5 is forbidden."
            )
            raise EnsembleError(msg)
        weights = np.full(len(cols), 1.0 / len(cols), dtype=float)
        fallback = "equal_weight"
    else:
        weights = raw / total

    return NNLSStackResult(
        target=target,
        member_columns=cols,
        weights=tuple(float(w) for w in weights),
        condition_number=cond,
        n_oof_rows=int(x.shape[0]),
        fallback=fallback,
    )


def predict_stacked_mu(
    frame: pd.DataFrame,
    stack: NNLSStackResult,
) -> np.ndarray:
    """Apply fitted NNLS weights to member μ columns."""
    missing = [c for c in stack.member_columns if c not in frame.columns]
    if missing:
        msg = f"missing member columns at predict: {missing}"
        raise EnsembleError(msg)
    x = np.asarray(frame.loc[:, list(stack.member_columns)], dtype=float)
    w = np.asarray(stack.weights, dtype=float)
    out: np.ndarray = np.asarray(x @ w, dtype=float)
    return out


def ensemble_sigma(
    member_mus: np.ndarray | pd.DataFrame,
    sigma_head: np.ndarray | pd.Series,
    *,
    weights: Sequence[float] | None = None,
) -> EnsembleSigmaResult:
    """Law-of-total-variance ensemble σ.

    Parameters
    ----------
    member_mus:
        Shape ``(n, k)`` member μ predictions.
    sigma_head:
        Shape ``(n,)`` aleatoric σ from the heteroskedasticity head.
    weights:
        Optional member weights (default uniform) used only for the weighted
        mean when computing disagreement variance around the ensemble mean.
    """
    mu = np.asarray(member_mus, dtype=float)
    if mu.ndim == 1:
        mu = mu.reshape(-1, 1)
    if mu.ndim != 2:
        msg = f"member_mus must be 2-D (n, k), got shape {mu.shape}"
        raise EnsembleError(msg)
    n, k = mu.shape
    sig = np.asarray(sigma_head, dtype=float).reshape(-1)
    if sig.shape[0] != n:
        msg = f"sigma_head length {sig.shape[0]} != n_rows {n}"
        raise EnsembleError(msg)
    sig = np.maximum(sig, 1e-8)

    if k == 1:
        member_var = np.zeros(n, dtype=float)
    elif weights is None:
        member_var = np.var(mu, axis=1, ddof=0)
    else:
        w = np.asarray(weights, dtype=float)
        if w.shape[0] != k:
            msg = f"weights length {w.shape[0]} != n_members {k}"
            raise EnsembleError(msg)
        w = w / max(float(np.sum(w)), 1e-15)
        mean = mu @ w
        member_var = np.sum(w * (mu - mean[:, None]) ** 2, axis=1)

    total_var = member_var + sig**2
    return EnsembleSigmaResult(
        sigma=np.sqrt(np.maximum(total_var, 1e-12)),
        member_var=member_var,
        sigma_head=sig,
        stage1_var=None,
    )


def attach_stage1_mixture_variance(
    ens: EnsembleSigmaResult,
    stage1_var: np.ndarray | pd.Series,
) -> EnsembleSigmaResult:
    """Fold Stage-1 posterior Var(μ) into ensemble σ via law of total variance.

    ``σ²_total = σ²_ens + Var_stage1(μ)``. Returns a new result; does not mutate.
    """
    s1 = np.asarray(stage1_var, dtype=float).reshape(-1)
    if s1.shape[0] != ens.sigma.shape[0]:
        msg = f"stage1_var length {s1.shape[0]} != n_rows {ens.sigma.shape[0]}"
        raise EnsembleError(msg)
    s1 = np.maximum(s1, 0.0)
    total_var = ens.member_var + ens.sigma_head**2 + s1
    return EnsembleSigmaResult(
        sigma=np.sqrt(np.maximum(total_var, 1e-12)),
        member_var=ens.member_var,
        sigma_head=ens.sigma_head,
        stage1_var=s1,
    )


@dataclass
class FittedEnsemble:
    """Fitted stacking weights for margin and/or total plus helpers."""

    margin: NNLSStackResult | None = None
    total: NNLSStackResult | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def predict_mu(
        self,
        frame: pd.DataFrame,
        *,
        target: TargetKind,
    ) -> np.ndarray:
        stack = self.margin if target == "margin" else self.total
        if stack is None:
            msg = f"no stack fitted for target={target}"
            raise EnsembleError(msg)
        return predict_stacked_mu(frame, stack)

    def predict_sigma(
        self,
        frame: pd.DataFrame,
        *,
        target: TargetKind,
        sigma_column: str,
        member_columns: Sequence[str] | None = None,
    ) -> EnsembleSigmaResult:
        stack = self.margin if target == "margin" else self.total
        if stack is None:
            msg = f"no stack fitted for target={target}"
            raise EnsembleError(msg)
        cols = tuple(member_columns) if member_columns is not None else stack.member_columns
        if sigma_column not in frame.columns:
            msg = f"sigma column '{sigma_column}' missing"
            raise EnsembleError(msg)
        return ensemble_sigma(
            frame.loc[:, list(cols)],
            frame[sigma_column],
            weights=stack.weights,
        )


def single_lgbm_stack(
    *,
    target: TargetKind,
    lgbm_column: str,
) -> NNLSStackResult:
    """A4 ablation: unit weight on the LightGBM member (all others zero).

    Configures the ensemble path rather than forking a separate predictor.
    """
    return NNLSStackResult(
        target=target,
        member_columns=(lgbm_column,),
        weights=(1.0,),
    )


def fit_ensemble(
    oof: pd.DataFrame,
    *,
    margin_members: Sequence[str] | None = None,
    total_members: Sequence[str] | None = None,
    flag_column: str = OOF_FLAG_COLUMN,
    mapping_layer: Literal["ensemble", "single_lgbm"] = "ensemble",
    lgbm_margin_column: str = "lgbm_mu_margin",
    lgbm_total_column: str = "lgbm_mu_total",
    allow_equal_weight_fallback: bool = False,
) -> FittedEnsemble:
    """Fit margin and/or total NNLS stacks on an OOF-only frame.

    When ``mapping_layer='single_lgbm'`` (ablation A4), returns unit weight on
    the configured LGBM column instead of NNLS over all members.
    """
    assert_oof_only(oof, flag_column=flag_column)
    if mapping_layer == "single_lgbm":
        margin = (
            single_lgbm_stack(target="margin", lgbm_column=lgbm_margin_column)
            if margin_members
            else None
        )
        total = (
            single_lgbm_stack(target="total", lgbm_column=lgbm_total_column)
            if total_members
            else None
        )
        if margin is None and total is None:
            msg = "provide margin_members and/or total_members"
            raise EnsembleError(msg)
        return FittedEnsemble(
            margin=margin,
            total=total,
            meta={"mapping_layer": "single_lgbm"},
        )

    margin = (
        fit_nnls_stack(
            oof,
            target="margin",
            member_columns=margin_members,
            flag_column=flag_column,
            allow_equal_weight_fallback=allow_equal_weight_fallback,
        )
        if margin_members
        else None
    )
    total = (
        fit_nnls_stack(
            oof,
            target="total",
            member_columns=total_members,
            flag_column=flag_column,
            allow_equal_weight_fallback=allow_equal_weight_fallback,
        )
        if total_members
        else None
    )
    if margin is None and total is None:
        msg = "provide margin_members and/or total_members"
        raise EnsembleError(msg)
    return FittedEnsemble(
        margin=margin,
        total=total,
        meta={
            "mapping_layer": "ensemble",
            "allow_equal_weight_fallback": allow_equal_weight_fallback,
        },
    )


def stack_weights_valid(stack: NNLSStackResult, *, atol: float = 1e-8) -> bool:
    """Property helper: weights ≥ 0 and sum to 1."""
    w = np.asarray(stack.weights, dtype=float)
    return bool(np.all(w >= -atol) and abs(float(np.sum(w)) - 1.0) <= atol)


def describe_stack(stack: NNLSStackResult) -> Mapping[str, float]:
    """Human-readable weight map."""
    return stack.as_dict()
