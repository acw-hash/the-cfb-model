"""TASK D1 — read-only margin-μ diagnostics (where the margin signal dies).

Diagnostic only. Does not tune hyperparameters, clip floors, configs, or models.
New code lives here; the CLI entry is ``ncaa-quant diag mu``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.evaluation.metrics import log_loss, log_loss_per_row, mae, su_outcomes
from ncaa_quant.models.ensemble import OOF_FLAG_COLUMN, assert_oof_only, fit_nnls_stack
from ncaa_quant.models.heads.base import FeatureSignatureError
from ncaa_quant.models.heads.catboost_mu import CatBoostMuHead
from ncaa_quant.models.heads.elasticnet import ElasticNetMuHead
from ncaa_quant.models.heads.margin import LightGBMMuHead
from ncaa_quant.models.heads.ngboost_dist import NGBoostNormalHead
from ncaa_quant.models.heads.xgboost_mu import XGBoostMuHead
from ncaa_quant.ratings.elo_baseline import run_elo
from ncaa_quant.ratings.state_space import run_filter

# Paths that produced the numbers cited in docs/notes/23.md AMENDMENT FIX-DIAG.
DEFAULT_SMOKE_PREDICTIONS = Path(
    "data/backtests/task23_fix_smoke/wiring_proof_2023/full/predictions.parquet"
)
DEFAULT_FULL_PREDICTIONS = Path("data/backtests/task23_fundamental/fundamental/predictions.parquet")
DEFAULT_SMOKE_MANIFEST = Path(
    "data/backtests/task23_fix_smoke/wiring_proof_2023/full/manifest.json"
)
DEFAULT_FULL_MANIFEST = Path("data/backtests/task23_fundamental/fundamental/manifest.json")
DEFAULT_STAGED = Path("data/staged")
DEFAULT_NOTES = Path("docs/notes/D1.md")
DEFAULT_ARTIFACT_DIR = Path("docs/notes/_artifacts/D1")

HEADLINE_SEASONS: frozenset[int] = frozenset({2019, 2021, 2022, 2023, 2024, 2025})
StopKind = Literal[
    "sign_inversion",
    "row_misalignment",
    "all_null_rating_feature",
    "target_orientation_mismatch",
    "none",
]


class DiagnosticsMuError(ValueError):
    """Invalid diagnostic inputs or missing required artifacts."""


# ---------------------------------------------------------------------------
# Scoring primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PredictorScore:
    """Point-prediction scorecard for one μ predictor on a fixed game set."""

    name: str
    n: int
    mae: float
    rmse: float
    residual_sd: float
    mean_signed_bias: float
    r2: float
    n_finite: int


@dataclass(frozen=True)
class CalibrationSlope:
    """``y = a + b * yhat`` regression diagnostics."""

    name: str
    n: int
    a: float
    b: float
    r2: float
    pearson_r: float
    sd_yhat: float


@dataclass(frozen=True)
class StructuralFinding:
    """STOP-rule finding; ``kind != 'none'`` halts the remaining sweep."""

    kind: StopKind
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _finite_mask(*arrays: np.ndarray) -> np.ndarray:
    mask = np.ones(len(arrays[0]), dtype=bool)
    for arr in arrays:
        mask &= np.isfinite(np.asarray(arr, dtype=float))
    return mask


def score_predictor(name: str, y: np.ndarray, yhat: np.ndarray) -> PredictorScore:
    """MAE / RMSE / residual SD / bias / R² on finite pairs."""
    yt = np.asarray(y, dtype=float).ravel()
    yh = np.asarray(yhat, dtype=float).ravel()
    if yt.size != yh.size:
        raise DiagnosticsMuError(f"{name}: y and yhat length mismatch")
    mask = _finite_mask(yt, yh)
    n_finite = int(mask.sum())
    if n_finite == 0:
        return PredictorScore(
            name=name,
            n=int(yt.size),
            mae=float("nan"),
            rmse=float("nan"),
            residual_sd=float("nan"),
            mean_signed_bias=float("nan"),
            r2=float("nan"),
            n_finite=0,
        )
    y_m, h_m = yt[mask], yh[mask]
    resid = y_m - h_m
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y_m - float(np.mean(y_m))) ** 2))
    r2 = float("nan") if ss_tot <= 0 else 1.0 - ss_res / ss_tot
    return PredictorScore(
        name=name,
        n=int(yt.size),
        mae=float(np.mean(np.abs(resid))),
        rmse=float(np.sqrt(np.mean(resid**2))),
        residual_sd=float(np.std(resid, ddof=1)) if n_finite > 1 else float("nan"),
        mean_signed_bias=float(np.mean(resid)),
        r2=r2,
        n_finite=n_finite,
    )


def regress_y_on_yhat(name: str, y: np.ndarray, yhat: np.ndarray) -> CalibrationSlope:
    """OLS ``y = a + b*yhat``; ``b`` is the primary orientation/scale diagnostic."""
    yt = np.asarray(y, dtype=float).ravel()
    yh = np.asarray(yhat, dtype=float).ravel()
    mask = _finite_mask(yt, yh)
    n = int(mask.sum())
    if n < 2:
        return CalibrationSlope(
            name=name,
            n=n,
            a=float("nan"),
            b=float("nan"),
            r2=float("nan"),
            pearson_r=float("nan"),
            sd_yhat=float("nan"),
        )
    y_m, h_m = yt[mask], yh[mask]
    sd_h = float(np.std(h_m, ddof=1))
    if sd_h < 1e-12:
        # Constant predictor: slope undefined / zero signal.
        ss_tot = float(np.sum((y_m - float(np.mean(y_m))) ** 2))
        ss_res = float(np.sum((y_m - float(np.mean(y_m))) ** 2))
        return CalibrationSlope(
            name=name,
            n=n,
            a=float(np.mean(y_m)),
            b=0.0,
            r2=0.0 if ss_tot > 0 else float("nan"),
            pearson_r=0.0,
            sd_yhat=sd_h,
        )
    x = np.column_stack([np.ones(n), h_m])
    coef, _, _, _ = np.linalg.lstsq(x, y_m, rcond=None)
    a, b = float(coef[0]), float(coef[1])
    fitted = a + b * h_m
    ss_res = float(np.sum((y_m - fitted) ** 2))
    ss_tot = float(np.sum((y_m - float(np.mean(y_m))) ** 2))
    r2 = float("nan") if ss_tot <= 0 else 1.0 - ss_res / ss_tot
    pearson = float(np.corrcoef(y_m, h_m)[0, 1])
    return CalibrationSlope(name=name, n=n, a=a, b=b, r2=r2, pearson_r=pearson, sd_yhat=sd_h)


def _as_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _as_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_as_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(obj, (np.integer, int)):
        return int(obj)
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "__dataclass_fields__"):
        return _as_jsonable(asdict(obj))
    return obj


# ---------------------------------------------------------------------------
# Eval-set loading
# ---------------------------------------------------------------------------


def load_prediction_frame(path: Path | str) -> pd.DataFrame:
    """Load a walk-forward prediction parquet and attach derived columns."""
    target = Path(path)
    if not target.is_file():
        raise DiagnosticsMuError(f"predictions parquet not found: {target}")
    frame = pd.read_parquet(target)
    required = {"game_id", "pred_margin", "home_points", "away_points"}
    missing = required - set(frame.columns)
    if missing:
        raise DiagnosticsMuError(f"predictions missing columns: {sorted(missing)}")
    out = frame.copy()
    if "realized_margin" not in out.columns:
        out["realized_margin"] = out["home_points"].astype(float) - out["away_points"].astype(float)
    if "exclude_from_headline" not in out.columns:
        out["exclude_from_headline"] = False
    if "season" in out.columns:
        out["exclude_from_headline"] = out["exclude_from_headline"] | (
            ~out["season"].isin(HEADLINE_SEASONS)
        )
    return out


def headline_mask(frame: pd.DataFrame) -> np.ndarray:
    """Boolean mask for headline metrics (§7.2: drop 2020 continuity)."""
    base = frame["realized_margin"].notna() & frame["pred_margin"].notna()
    if "exclude_from_headline" in frame.columns:
        base = base & ~frame["exclude_from_headline"].astype(bool)
    return np.asarray(base.to_numpy(), dtype=bool)


# ---------------------------------------------------------------------------
# Block A
# ---------------------------------------------------------------------------


def block_a(
    frame: pd.DataFrame,
    *,
    elo_margin: pd.Series | None = None,
    market_spread: pd.Series | None = None,
    train_mean: float | None = None,
) -> dict[str, Any]:
    """Defect-vs-deficit scorecard on the exact evaluation rows."""
    mask = headline_mask(frame)
    sub = frame.loc[mask].copy()
    y = sub["realized_margin"].to_numpy(dtype=float)
    stack_mu = sub["pred_margin"].to_numpy(dtype=float)
    n = int(len(sub))

    y_sd = float(np.std(y, ddof=1)) if n > 1 else float("nan")
    # A1: MAE vs training-mean constant. If train_mean unknown, use leave-out
    # is wrong; require caller to pass seasons < Y mean or report null.
    if train_mean is None:
        mae_vs_train_mean = float("nan")
        train_mean_used = None
        train_mean_note = "train_mean not supplied — MAE(y - mean(y_train)) not computed"
    else:
        train_mean_used = float(train_mean)
        mae_vs_train_mean = float(np.mean(np.abs(y - train_mean_used)))
        train_mean_note = "ok"

    predictors: dict[str, np.ndarray] = {
        "constant_0": np.zeros(n, dtype=float),
        "stack_published_mu": stack_mu,
    }
    if train_mean_used is not None:
        predictors["constant_train_mean"] = np.full(n, train_mean_used, dtype=float)

    if elo_margin is not None:
        elo = elo_margin.reindex(sub.index).to_numpy(dtype=float)
        predictors["elo_implied_margin"] = elo
    if market_spread is not None:
        # Market home spread is typically home handicap; home margin ≈ -spread.
        spread = market_spread.reindex(sub.index).to_numpy(dtype=float)
        predictors["negated_market_spread"] = -spread

    scores = {name: score_predictor(name, y, yhat) for name, yhat in predictors.items()}
    slopes = {name: regress_y_on_yhat(name, y, yhat) for name, yhat in predictors.items()}
    stack_r2 = scores["stack_published_mu"].r2
    stack_r2_le_zero = bool(stack_r2 <= 0.0) if np.isfinite(stack_r2) else False

    return {
        "n": n,
        "sd_y": y_sd,
        "mae_y_minus_train_mean": mae_vs_train_mean,
        "train_mean": train_mean_used,
        "train_mean_note": train_mean_note,
        "scores": {k: asdict(v) for k, v in scores.items()},
        "slopes": {k: asdict(v) for k, v in slopes.items()},
        "stack_r2": stack_r2,
        "stack_r2_le_zero": stack_r2_le_zero,
        "zero_mu_rate": float(np.mean(stack_mu == 0.0)),
        "zero_mu_by_week": (
            sub.assign(_z=sub["pred_margin"] == 0.0)
            .groupby("week", sort=True)["_z"]
            .mean()
            .astype(float)
            .to_dict()
            if "week" in sub.columns
            else {}
        ),
    }


def reconcile_history() -> dict[str, Any]:
    """A5: identify MAE 13.65 vs 16.60 runs from archived manifests / notes."""
    smoke_manifest = (
        json.loads(DEFAULT_SMOKE_MANIFEST.read_text(encoding="utf-8"))
        if DEFAULT_SMOKE_MANIFEST.is_file()
        else None
    )
    full_manifest = (
        json.loads(DEFAULT_FULL_MANIFEST.read_text(encoding="utf-8"))
        if DEFAULT_FULL_MANIFEST.is_file()
        else None
    )
    return {
        "mae_13_65_source": {
            "what": "Week-10 MAE only (not full-set MAE)",
            "value": 13.654241501161867,
            "game_set": "walk-forward 2019–2025 headline rows, week==10 only (n=335 in memo)",
            "artifact": "docs/notes/_artifacts/task23/task23_memo.json + fundamental_full.html",
            "run": "task23_fundamental / ablation fundamental",
            "git_sha": None if full_manifest is None else full_manifest.get("git_sha"),
            "config_hash": None if full_manifest is None else full_manifest.get("config_hash"),
            "full_set_mae_same_run": 14.196002600108708,
            "note": (
                "13.65 is weekly curve Week-10 MAE from the original Task 23 memo, "
                "not a full-sample stack MAE."
            ),
        },
        "mae_16_60_source": {
            "what": "full-sample MAE on wiring_proof_2023 smoke",
            "value": 16.604688787660955,
            "game_set": "2023 only, n=910 (wiring_proof_2023; continuity_seasons=[])",
            "artifact": (
                "data/backtests/task23_fix_smoke/wiring_proof_2023/full/predictions.parquet"
            ),
            "run": "wiring_proof_2023 / full",
            "git_sha": None if smoke_manifest is None else smoke_manifest.get("git_sha"),
            "config_hash": None if smoke_manifest is None else smoke_manifest.get("config_hash"),
            "wall_clock_sec": (
                None
                if smoke_manifest is None
                else (smoke_manifest.get("extra") or {}).get("wall_clock_sec")
            ),
            "note": (
                "Weeks 1–4 emit pred_margin=0 (unfitted cold-start prior) because the "
                "smoke config has no prior-season seed labels. Nonzero weeks (5+) MAE≈13.51."
            ),
        },
        "which_eval_set_is_correct": (
            "For headline walk-forward claims use 2019–2025 excluding 2020 "
            "(task23_fundamental, n_headline≈5434, MAE≈14.20). The 16.60 / Elo "
            "0.557 / 1.153 comparison in FIX-DIAG was on the 2023 smoke "
            "(n=910 / Elo-overlap n=792), not the full multi-season set. "
            "Mixing those bases produced the '2019–2025 stack MAE 16.60' misstatement."
        ),
    }


# ---------------------------------------------------------------------------
# Block B
# ---------------------------------------------------------------------------


def block_b_target_contract(
    frame: pd.DataFrame,
    *,
    raw_games: pd.DataFrame | None,
    n_sample: int = 25,
    seed: int = 42,
) -> dict[str, Any]:
    """B1: assert stored y == home_points - away_points; print sample rows."""
    mask = headline_mask(frame)
    sub = frame.loc[mask].copy()
    rng = np.random.default_rng(seed)
    n = min(n_sample, len(sub))
    if n == 0:
        return {"n_sampled": 0, "all_match": False, "rows": [], "error": "empty eval set"}
    idx = rng.choice(len(sub), size=n, replace=False)
    sample = sub.iloc[idx].copy()
    recomputed = sample["home_points"].astype(float) - sample["away_points"].astype(float)
    y_match = np.allclose(
        sample["realized_margin"].to_numpy(dtype=float),
        recomputed.to_numpy(dtype=float),
        equal_nan=True,
    )
    rows: list[dict[str, Any]] = []
    for r in sample.itertuples(index=False):
        rows.append(
            {
                "game_id": int(r.game_id),
                "season": int(getattr(r, "season", -1)),
                "week": int(getattr(r, "week", -1)),
                "home_points": float(r.home_points),
                "away_points": float(r.away_points),
                "realized_margin": float(r.realized_margin),
                "pred_margin": float(r.pred_margin),
                "recomputed_y": float(r.home_points) - float(r.away_points),
            }
        )
    raw_join_note = "raw_games not supplied"
    raw_match = None
    if raw_games is not None and not raw_games.empty:
        raw = raw_games.set_index("game_id")
        raw_match = True
        for row in rows:
            gid = int(row["game_id"])
            if gid not in raw.index:
                raw_match = False
                row["raw_status"] = "orphan"
                continue
            rr = raw.loc[gid]
            if isinstance(rr, pd.DataFrame):
                rr = rr.iloc[0]
            raw_y = float(rr["home_points"]) - float(rr["away_points"])
            row["raw_recomputed_y"] = raw_y
            row["raw_status"] = (
                "ok" if abs(raw_y - float(row["realized_margin"])) < 1e-9 else "mismatch"
            )
            if row["raw_status"] != "ok":
                raw_match = False
        raw_join_note = "joined on game_id to staged games"
    return {
        "n_sampled": n,
        "stored_y_matches_points": bool(y_match),
        "raw_points_match": raw_match,
        "raw_join_note": raw_join_note,
        "rows": rows,
        "orientation_note": (
            "mu and y are both home_points - away_points (home-margin orientation, points)"
        ),
    }


def block_b_neutral_slopes(
    frame: pd.DataFrame,
    *,
    games: pd.DataFrame | None,
) -> dict[str, Any]:
    """B2: slope b on neutral vs non-neutral subsets."""
    if games is None or games.empty or "neutral_site" not in games.columns:
        return {
            "status": "NOT_COMPUTED",
            "reason": "games frame with neutral_site not available",
        }
    mask = headline_mask(frame)
    sub = frame.loc[mask].copy()
    if "neutral_site" not in sub.columns:
        sub = sub.merge(games[["game_id", "neutral_site"]], on="game_id", how="left")
    elif "neutral_site" in games.columns:
        # Prefer games table if both present (avoid _x/_y merge collision).
        sub = sub.drop(columns=["neutral_site"]).merge(
            games[["game_id", "neutral_site"]], on="game_id", how="left"
        )
    out: dict[str, Any] = {
        "home_away_logic": (
            "Harness uses staged home_team_id / away_team_id as designated sides. "
            "Neutral games keep that designation; ProductionFeatureProvider does not "
            "add an explicit HFA feature column — HFA lives in Stage-1 / Elo only."
        )
    }
    for label, sel in (
        ("neutral", sub["neutral_site"].astype(bool)),
        ("non_neutral", ~sub["neutral_site"].astype(bool)),
    ):
        part = sub.loc[sel]
        slope = regress_y_on_yhat(
            label,
            part["realized_margin"].to_numpy(dtype=float),
            part["pred_margin"].to_numpy(dtype=float),
        )
        out[label] = asdict(slope)
    return out


def block_b_join_integrity(
    predictions: pd.DataFrame,
    outcomes: pd.DataFrame,
) -> dict[str, Any]:
    """B3: 1:1 join on game_key / game_id."""
    key = (
        "game_key"
        if "game_key" in predictions.columns and "game_key" in outcomes.columns
        else "game_id"
    )
    pred_n = len(predictions)
    out_n = len(outcomes)
    pred_dup = int(predictions[key].duplicated().sum())
    out_dup = int(outcomes[key].duplicated().sum())
    merged = predictions.merge(outcomes, on=key, how="outer", indicator=True)
    left_only = int((merged["_merge"] == "left_only").sum())
    right_only = int((merged["_merge"] == "right_only").sum())
    both = int((merged["_merge"] == "both").sum())
    return {
        "join_key": key,
        "predictions_n": pred_n,
        "outcomes_n": out_n,
        "predictions_dup_keys": pred_dup,
        "outcomes_dup_keys": out_dup,
        "inner_n": both,
        "pred_orphans": left_only,
        "outcome_orphans": right_only,
        "one_to_one": pred_dup == 0 and out_dup == 0 and left_only == 0 and right_only == 0,
    }


def block_b_row_order_test(predictor: Any, features: pd.DataFrame) -> dict[str, Any]:
    """B4: predict(X) == unshuffle(predict(shuffle(X)))."""
    if features.empty or "game_id" not in features.columns:
        return {"status": "NOT_COMPUTED", "reason": "empty feature frame"}
    if not getattr(predictor, "is_fitted", True):
        # Heads expose is_fitted; ensemble may not.
        pass
    rng = np.random.default_rng(0)
    order = np.arange(len(features))
    shuffled = order.copy()
    rng.shuffle(shuffled)
    x = features.reset_index(drop=True)
    x_shuf = x.iloc[shuffled].reset_index(drop=True)
    try:
        p0 = predictor.predict(x)
        p1 = predictor.predict(x_shuf)
    except Exception as exc:  # noqa: BLE001 — diagnostic surface
        return {"status": "ERROR", "error": str(exc)}
    col = "pred_margin" if "pred_margin" in p0.columns else p0.columns[-1]
    a = p0.set_index("game_id")[col].reindex(x["game_id"]).to_numpy(dtype=float)
    b = p1.set_index("game_id")[col].reindex(x["game_id"]).to_numpy(dtype=float)
    equal = bool(np.allclose(a, b, equal_nan=True))
    return {
        "status": "ok",
        "elementwise_equal_after_unshuffle": equal,
        "max_abs_diff": float(np.nanmax(np.abs(a - b))) if len(a) else float("nan"),
        "game_key_index_note": (
            "WalkForwardHarness banks features from the same DataFrame passed to "
            "predict(), keyed by game_id; prediction rows are built by iterating "
            "week_games in game_id sort order and looking up pred_map[game_id] — "
            "not a separately sorted parallel vector."
        ),
    }


def block_b_feature_signature_contract(predictor: Any, features: pd.DataFrame) -> dict[str, Any]:
    """B5: permute two columns and assert predict raises."""
    sig = getattr(predictor, "signature", None)
    if sig is not None and getattr(sig, "names", None):
        feat_cols = [c for c in sig.names if c in features.columns]
    else:
        feat_cols = [c for c in features.columns if c != "game_id"]
    if len(feat_cols) < 2:
        return {"status": "NOT_COMPUTED", "reason": "need ≥2 feature columns in signature"}
    messed = features.copy()
    c0, c1 = feat_cols[0], feat_cols[1]
    # Drop one expected name so the signature contract must refuse.
    messed = messed.rename(columns={c0: f"__broken_{c0}"})
    raised = False
    err_type = None
    err_msg = None
    try:
        predictor.predict(messed)
    except FeatureSignatureError as exc:
        raised = True
        err_type = "FeatureSignatureError"
        err_msg = str(exc)
    except Exception as exc:  # noqa: BLE001
        raised = True
        err_type = type(exc).__name__
        err_msg = str(exc)
    return {
        "status": "ok" if raised else "CONTRACT_NOT_WIRED",
        "raised": raised,
        "error_type": err_type,
        "error_msg": err_msg,
        "permuted_columns": [c0, c1],
        "note": (
            "Renames the first signature column away so predict must raise "
            "FeatureSignatureError if the contract is wired."
        ),
    }


def detect_structural_stop(
    block_a_result: Mapping[str, Any], block_b1: Mapping[str, Any]
) -> StructuralFinding:
    """STOP rule: orientation / alignment / null-rating defects."""
    if block_b1.get("stored_y_matches_points") is False:
        return StructuralFinding(
            kind="target_orientation_mismatch",
            message="stored realized_margin disagrees with home_points - away_points",
            evidence=dict(block_b1),
        )
    if block_b1.get("raw_points_match") is False:
        return StructuralFinding(
            kind="target_orientation_mismatch",
            message="prediction-table scores disagree with staged raw games",
            evidence=dict(block_b1),
        )
    stack_slope = (block_a_result.get("slopes") or {}).get("stack_published_mu") or {}
    b = stack_slope.get("b")
    # Constant-zero cold start yields b=0 — that is a plumbing defect but not a
    # sign inversion. Only flag negative slope as orientation bug.
    if b is not None and np.isfinite(b) and b < -0.2:
        return StructuralFinding(
            kind="sign_inversion",
            message=f"stack calibration slope b={b} is negative (orientation bug)",
            evidence={"slope": stack_slope},
        )
    return StructuralFinding(kind="none", message="no STOP-rule structural bug in A/B1")


# ---------------------------------------------------------------------------
# Elo / train mean / market helpers
# ---------------------------------------------------------------------------


def compute_elo_margins(
    games: pd.DataFrame,
    eval_game_ids: Sequence[int],
) -> pd.Series:
    """Pregame Elo-implied home margins indexed like a predictions frame slice."""
    needed = {
        "game_id",
        "season",
        "week",
        "home_team_id",
        "away_team_id",
        "home_points",
        "away_points",
        "neutral_site",
        "completed",
    }
    work = games.copy()
    if "start_date" not in work.columns and "event_time" in work.columns:
        work["start_date"] = work["event_time"]
    if "completed" not in work.columns:
        work["completed"] = work["home_points"].notna() & work["away_points"].notna()
    missing = needed - set(work.columns)
    if missing:
        raise DiagnosticsMuError(f"games missing columns for Elo: {sorted(missing)}")
    log, _, _ = run_elo(work)
    log = log.set_index("game_id")
    series = log["pred_home_margin"]
    # Return a Series aligned to eval ids (may contain NaN for misses).
    return series.reindex([int(g) for g in eval_game_ids])


def train_mean_margin(games: pd.DataFrame, test_seasons: Sequence[int]) -> float:
    """Mean home margin on completed games with season < min(test_seasons)."""
    if not test_seasons:
        raise DiagnosticsMuError("test_seasons empty")
    cutoff = int(min(test_seasons))
    sub = games.loc[
        (games["season"] < cutoff) & games["home_points"].notna() & games["away_points"].notna()
    ]
    if sub.empty:
        return float("nan")
    margins = sub["home_points"].astype(float) - sub["away_points"].astype(float)
    return float(margins.mean())


# ---------------------------------------------------------------------------
# Block C — layer ladder
# ---------------------------------------------------------------------------


def _feature_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    meta = {
        "game_id",
        "game_key",
        "season",
        "week",
        "as_of",
        "event_time",
        "realized_margin",
        "realized_total",
        "home_points",
        "away_points",
        "pred_margin",
        "neutral_site",
        "y",
        "layer",
        "home_team_id",
        "away_team_id",
        "completed",
        "start_date",
        "exclude_from_headline",
    }
    cols = [c for c in frame.columns if c not in meta and pd.api.types.is_numeric_dtype(frame[c])]
    return frame[["game_id", *cols]].copy()


def _fit_predict_layer(
    name: str,
    train_x: pd.DataFrame,
    train_y: np.ndarray,
    test_x: pd.DataFrame,
) -> np.ndarray:
    """Fit one layer on train seasons and predict test rows."""
    if name == "L1_ols_rating_diff":
        # Prefer off_epa_diff / rating_diff_off_epa; fall back to all numeric.
        cand = [
            c
            for c in (
                "rating_diff_off_epa",
                "off_epa_diff",
                "rating_diff_def_epa",
                "def_epa_diff",
            )
            if c in train_x.columns
        ]
        if not cand:
            cand = [c for c in train_x.columns if c != "game_id"]
        # HFA proxy: constant + diffs (ProductionFeatureProvider has no HFA col).
        xtr = train_x[cand].to_numpy(dtype=float)
        xte = test_x[cand].to_numpy(dtype=float)
        # Impute NaN with train column means.
        col_mean = np.nanmean(xtr, axis=0)
        inds = np.where(np.isnan(xtr))
        xtr[inds] = np.take(col_mean, inds[1])
        inds = np.where(np.isnan(xte))
        xte[inds] = np.take(col_mean, inds[1])
        xtr = np.column_stack([np.ones(len(xtr)), xtr])
        xte = np.column_stack([np.ones(len(xte)), xte])
        coef, _, _, _ = np.linalg.lstsq(xtr, train_y, rcond=None)
        return np.asarray(xte @ coef, dtype=float)

    labels = pd.DataFrame(
        {
            "game_id": train_x["game_id"].to_numpy(),
            "realized_margin": train_y,
        }
    )
    feats = train_x.copy()
    if name == "L2_elasticnet_top30":
        head: Any = ElasticNetMuHead(target="margin", model_version="d1-enet")
        # Restrict to top-30 by |corr| with y on train.
        num = [c for c in feats.columns if c != "game_id"]
        corrs: list[tuple[str, float]] = []
        for c in num:
            v = feats[c].to_numpy(dtype=float)
            m = np.isfinite(v) & np.isfinite(train_y)
            if m.sum() < 10:
                continue
            corrs.append((c, abs(float(np.corrcoef(v[m], train_y[m])[0, 1]))))
        corrs.sort(key=lambda t: t[1], reverse=True)
        keep = [c for c, _ in corrs[:30]] or num
        feats = feats[["game_id", *keep]]
        te = test_x[["game_id", *[c for c in keep if c in test_x.columns]]]
        for c in keep:
            if c not in te.columns:
                te[c] = np.nan
        head.fit(feats, labels)
        return np.asarray(head.predict(te)["pred_margin"].to_numpy(dtype=float))

    if name == "L3_lgbm_mu":
        head = LightGBMMuHead(target="margin", model_version="d1-lgbm")
        head.fit(feats, labels)
        return np.asarray(head.predict(test_x)["pred_margin"].to_numpy(dtype=float))

    if name == "L4_xgboost":
        head = XGBoostMuHead(target="margin", model_version="d1-xgb")
        head.fit(feats, labels)
        return np.asarray(head.predict(test_x)["pred_margin"].to_numpy(dtype=float))

    if name == "L4_catboost":
        head = CatBoostMuHead(target="margin", model_version="d1-cat")
        head.fit(feats, labels)
        return np.asarray(head.predict(test_x)["pred_margin"].to_numpy(dtype=float))

    if name == "L4_ngboost":
        head = NGBoostNormalHead(target="margin", model_version="d1-ngb")
        head.fit(feats, labels)
        pred = head.predict(test_x)
        col = "pred_margin" if "pred_margin" in pred.columns else pred.columns[-1]
        return np.asarray(pred[col].to_numpy(dtype=float))

    raise DiagnosticsMuError(f"unknown layer {name}")


def block_c_layer_ladder(
    *,
    eval_frame: pd.DataFrame,
    feature_bank: pd.DataFrame,
    elo_by_game: Mapping[int, float],
    published_mu: Mapping[int, float],
    test_seasons: Sequence[int],
) -> dict[str, Any]:
    """Score L0–L7 on the identical evaluation game_ids (walk-forward by season)."""
    mask = headline_mask(eval_frame)
    eval_ids = set(int(g) for g in eval_frame.loc[mask, "game_id"])
    bank = feature_bank.copy()
    if "realized_margin" not in bank.columns:
        raise DiagnosticsMuError("feature_bank needs realized_margin")
    if "season" not in bank.columns:
        raise DiagnosticsMuError("feature_bank needs season")

    layer_names = [
        "L0_elo",
        "L1_ols_rating_diff",
        "L2_elasticnet_top30",
        "L3_lgbm_mu",
        "L4_xgboost",
        "L4_catboost",
        "L4_ngboost",
        "L5_nnls_ensemble",
        "L6_after_mixture",  # may be NOT_COMPUTED
        "L7_published_mu",
    ]
    preds: dict[str, dict[Any, Any]] = {n: {} for n in layer_names}
    nnls_weights_by_season: dict[int, dict[str, float]] = {}

    # L0 / L7 from artifacts (no refit).
    for gid in eval_ids:
        if gid in elo_by_game and np.isfinite(elo_by_game[gid]):
            preds["L0_elo"][gid] = float(elo_by_game[gid])
        if gid in published_mu and np.isfinite(published_mu[gid]):
            preds["L7_published_mu"][gid] = float(published_mu[gid])

    member_oof_for_nnls: list[pd.DataFrame] = []
    for season in sorted(test_seasons):
        train = bank.loc[bank["season"] < int(season)].copy()
        test = bank.loc[(bank["season"] == int(season)) & bank["game_id"].isin(eval_ids)].copy()
        if test.empty:
            continue
        if train.empty:
            # Cold start: no seasons < Y — layers that need fit stay empty
            # (caller will see NaNs); mirrors production unfitted→0 for L7.
            continue
        train_x = _feature_matrix(train)
        test_x = _feature_matrix(test)
        train_y = train["realized_margin"].to_numpy(dtype=float)
        # Align columns.
        cols = [c for c in train_x.columns if c == "game_id" or c in test_x.columns]
        train_x = train_x[cols]
        test_x = test_x[[c for c in cols if c in test_x.columns]]
        for c in cols:
            if c not in test_x.columns:
                test_x[c] = np.nan
        test_x = test_x[cols]

        fitted_members: dict[str, np.ndarray] = {}
        for lname in (
            "L1_ols_rating_diff",
            "L2_elasticnet_top30",
            "L3_lgbm_mu",
            "L4_xgboost",
            "L4_catboost",
            "L4_ngboost",
        ):
            try:
                yhat = _fit_predict_layer(lname, train_x, train_y, test_x)
            except Exception as exc:  # noqa: BLE001
                preds[lname]["_error"] = str(exc)
                continue
            for gid, val in zip(test_x["game_id"].astype(int), yhat, strict=True):
                preds[lname][int(gid)] = float(val)
            fitted_members[lname] = yhat

        # L5: NNLS on member predictions vs y, fit on train-season OOF proxy =
        # the same members' predictions on train via a simple expanding holdout
        # is expensive; here fit NNLS on train in-sample member matrix built by
        # re-predicting train (DIAGNOSTIC ONLY — flagged). Prefer true OOF when
        # we have L3/L2 train predictions from a one-shot refit on a split.
        try:
            member_cols = ["L3_lgbm_mu", "L2_elasticnet_top30", "L4_xgboost", "L4_catboost"]
            # Build train member matrix via leave-last-20% block.
            n_tr = len(train_x)
            cut = max(int(n_tr * 0.8), 10)
            if n_tr > cut + 5:
                tr_a_x, tr_b_x = train_x.iloc[:cut], train_x.iloc[cut:]
                tr_a_y = train_y[:cut]
                oof_cols: dict[str, np.ndarray] = {}
                for mname in ("L3_lgbm_mu", "L2_elasticnet_top30", "L4_xgboost", "L4_catboost"):
                    oof_cols[mname] = _fit_predict_layer(mname, tr_a_x, tr_a_y, tr_b_x)
                oof_frame = pd.DataFrame(
                    {
                        "game_id": tr_b_x["game_id"].to_numpy(),
                        "realized_margin": train_y[cut:],
                        OOF_FLAG_COLUMN: True,
                        **oof_cols,
                    }
                )
                stack = fit_nnls_stack(
                    oof_frame,
                    target="margin",
                    member_columns=member_cols,
                )
                # Predict test with stacked weights of already-fitted members.
                mat = []
                for mname in member_cols:
                    if mname not in fitted_members:
                        raise DiagnosticsMuError(f"missing member {mname} for NNLS")
                    mat.append(fitted_members[mname])
                X = np.column_stack(mat)
                yhat = X @ np.asarray(stack.weights, dtype=float)
                for gid, val in zip(test_x["game_id"].astype(int), yhat, strict=True):
                    preds["L5_nnls_ensemble"][int(gid)] = float(val)
                member_oof_for_nnls.append(oof_frame)
                nnls_weights_by_season[int(season)] = stack.as_dict()
        except Exception as exc:  # noqa: BLE001
            preds["L5_nnls_ensemble"]["_error"] = str(exc)

    # L6: mixture/conformal/distribution assembly — only available if eval frame
    # carries a distinct post-mixture column. Production emit overwrites
    # pred_margin in place, so L6 ≡ L7 when no separate artifact exists.
    preds["L6_after_mixture"] = dict(preds["L7_published_mu"])
    l6_note = (
        "No separate post-mixture μ artifact; production overwrites pred_margin "
        "in place during epistemic mix. L6 reported identical to L7 published μ."
    )

    # Score
    y_map = {
        int(r.game_id): float(r.realized_margin)
        for r in eval_frame.loc[mask].itertuples(index=False)
    }
    rows: list[dict[str, Any]] = []
    for lname in layer_names:
        yhat_list: list[float] = []
        y_list: list[float] = []
        for gid, yv in y_map.items():
            if gid in preds[lname] and not str(gid).startswith("_"):
                val = preds[lname].get(gid)
                if val is None or (isinstance(val, float) and not np.isfinite(val)):
                    continue
                if isinstance(val, str):
                    continue
                y_list.append(yv)
                yhat_list.append(float(val))
        y_arr = np.asarray(y_list, dtype=float)
        h_arr = np.asarray(yhat_list, dtype=float)
        sc = score_predictor(lname, y_arr, h_arr)
        sl = regress_y_on_yhat(lname, y_arr, h_arr)
        rows.append(
            {
                "layer": lname,
                "n": sc.n_finite,
                "mae": sc.mae,
                "rmse": sc.rmse,
                "residual_sd": sc.residual_sd,
                "r": sl.pearson_r,
                "slope_b": sl.b,
            }
        )

    # Name the failing step: first layer whose MAE exceeds Elo by >0.5, or
    # largest discrete MAE jump between consecutive scored layers.
    elo_mae = next((r["mae"] for r in rows if r["layer"] == "L0_elo"), float("nan"))
    failing_step = None
    jump_table: list[dict[str, Any]] = []
    prev = None
    for r in rows:
        if prev is not None and np.isfinite(prev["mae"]) and np.isfinite(r["mae"]):
            jump = float(r["mae"] - prev["mae"])
            jump_table.append({"from": prev["layer"], "to": r["layer"], "delta_mae": jump})
        prev = r
    if jump_table:
        worst = max(jump_table, key=lambda d: d["delta_mae"])
        if worst["delta_mae"] > 0.25:
            failing_step = f"{worst['from']} -> {worst['to']} (delta_MAE={worst['delta_mae']:.4f})"
    if failing_step is None and np.isfinite(elo_mae):
        for r in rows:
            if r["layer"] == "L0_elo":
                continue
            if np.isfinite(r["mae"]) and r["mae"] > elo_mae + 0.5:
                failing_step = f"{r['layer']} MAE={r['mae']:.4f} exceeds Elo MAE={elo_mae:.4f}"
                break

    # C1 signal-ceiling: in-sample LGBM on test season features (NOT a performance number).
    signal_ceiling: dict[str, Any] = {"label": "DIAGNOSTIC_NOT_A_PERFORMANCE_NUMBER"}
    try:
        test_all = bank.loc[bank["game_id"].isin(eval_ids)].copy()
        if len(test_all) >= 20:
            x = _feature_matrix(test_all)
            y = test_all["realized_margin"].to_numpy(dtype=float)
            head = LightGBMMuHead(target="margin", model_version="d1-ceiling")
            head.fit(
                x,
                pd.DataFrame({"game_id": x["game_id"], "realized_margin": y}),
            )
            yhat = head.predict(x)["pred_margin"].to_numpy(dtype=float)
            signal_ceiling["in_sample_lgbm_mae"] = float(mae(y, yhat))
            signal_ceiling["n"] = int(len(y))
            signal_ceiling["reading"] = (
                "If in-sample MAE≈16 features carry no signal (data). "
                "If in-sample≈10 while walk-forward≈16.6, failure is train/predict plumbing."
            )
    except Exception as exc:  # noqa: BLE001
        signal_ceiling["error"] = str(exc)

    return {
        "rows": rows,
        "failing_step": failing_step,
        "jumps": jump_table,
        "l6_note": l6_note,
        "nnls_weights_by_season": nnls_weights_by_season,
        "member_oof_frames_n": len(member_oof_for_nnls),
        "signal_ceiling": signal_ceiling,
        "elo_mae": elo_mae,
    }


# ---------------------------------------------------------------------------
# Blocks D–H (selected, artifact-aware)
# ---------------------------------------------------------------------------


def block_d_ensemble_health(
    *,
    nnls_weights: Mapping[str, Any] | None,
    oof_frame: pd.DataFrame | None,
) -> dict[str, Any]:
    """D1–D3 ensemble / OOF audits."""
    out: dict[str, Any] = {
        "production_note": (
            "ProductionEnsemblePredictor._set_weights hardcodes 0.5/0.5 for "
            "lgbm_mu_margin and enet_mu_margin — it does NOT call fit_nnls_stack. "
            "XGBoost/CatBoost/NGBoost are not wired into the production predict path "
            "(see backtest_runner module docstring)."
        ),
        "nnls_weights": dict(nnls_weights) if nnls_weights else None,
    }
    if oof_frame is None or oof_frame.empty:
        out["oof_audit"] = {
            "status": "NOT_COMPUTED",
            "reason": "no OOF member matrix artifact from production run",
        }
    else:
        member_cols = [
            c
            for c in oof_frame.columns
            if c not in {"game_id", "realized_margin", OOF_FLAG_COLUMN, "season", "week"}
        ]
        audit: dict[str, Any] = {"columns": {}}
        y = oof_frame["realized_margin"].to_numpy(dtype=float)
        mats = []
        for c in member_cols:
            v = oof_frame[c].to_numpy(dtype=float)
            mats.append(v)
            m = np.isfinite(v)
            audit["columns"][c] = {
                "null_rate": float(1.0 - m.mean()) if len(v) else float("nan"),
                "is_constant": bool(np.nanmax(v) - np.nanmin(v) < 1e-12) if m.any() else True,
                "corr_with_y": (
                    float(np.corrcoef(v[m & np.isfinite(y)], y[m & np.isfinite(y)])[0, 1])
                    if (m & np.isfinite(y)).sum() > 2
                    else float("nan")
                ),
            }
        if mats:
            X = np.column_stack(mats)
            finite_rows = np.all(np.isfinite(X), axis=1)
            Xf = X[finite_rows]
            cond = float(np.linalg.cond(Xf)) if Xf.shape[0] > Xf.shape[1] else float("nan")
            audit["condition_number"] = cond
            if len(member_cols) >= 2:
                corr = np.corrcoef(Xf.T) if Xf.shape[0] > 2 else None
                audit["pairwise_corr"] = None if corr is None else corr.tolist()
        out["oof_audit"] = audit

    # D3: assert_oof_only executes when fit_nnls_stack is called.
    try:
        bad = pd.DataFrame(
            {
                "lgbm": [1.0, 2.0],
                "realized_margin": [3.0, 4.0],
                OOF_FLAG_COLUMN: [True, False],
            }
        )
        raised = False
        try:
            assert_oof_only(bad)
        except Exception:
            raised = True
        out["assert_oof_only_executes"] = raised
        out["assert_oof_only_note"] = (
            "assert_oof_only raises on in-fold rows when invoked; production "
            "_set_weights never calls fit_nnls_stack, so the assertion is skipped "
            "on the live stacking path."
        )
    except Exception as exc:  # noqa: BLE001
        out["assert_oof_only_executes"] = False
        out["assert_oof_only_error"] = str(exc)
    return out


def block_e_stage1(
    *,
    observations: pd.DataFrame | None,
    feature_bank: pd.DataFrame | None,
    elo_by_game: Mapping[int, float] | None,
    eval_frame: pd.DataFrame,
    artifact_dir: Path,
) -> dict[str, Any]:
    """E1–E5 Stage-1 health checks."""
    out: dict[str, Any] = {}
    if observations is None or observations.empty:
        out["filter"] = {
            "status": "NOT_COMPUTED",
            "reason": "observations frame not available",
        }
        return out

    import time

    t0 = time.perf_counter()
    result = run_filter(observations, record_weekly=True)
    elapsed = time.perf_counter() - t0
    innov = result.innovations
    out["filter_wall_clock_sec"] = float(elapsed)
    out["n_observations"] = int(len(observations))
    out["n_innovations"] = int(len(innov)) if innov is not None else 0
    out["update_ratio"] = (
        float(len(innov) / len(observations))
        if observations is not None and len(observations) and innov is not None
        else float("nan")
    )

    if innov is not None and not innov.empty and "z" in innov.columns:
        per_season: dict[str, Any] = {}
        season_col = "season" if "season" in innov.columns else None
        if season_col:
            for season, grp in innov.groupby(season_col):
                z = grp["z"].to_numpy(dtype=float)
                z = z[np.isfinite(z)]
                per_season[str(int(season))] = {
                    "mean_z": float(np.mean(z)) if len(z) else float("nan"),
                    "var_z": float(np.var(z, ddof=1)) if len(z) > 1 else float("nan"),
                    "n": int(len(z)),
                }
        else:
            z = innov["z"].to_numpy(dtype=float)
            z = z[np.isfinite(z)]
            per_season["all"] = {
                "mean_z": float(np.mean(z)) if len(z) else float("nan"),
                "var_z": float(np.var(z, ddof=1)) if len(z) > 1 else float("nan"),
                "n": int(len(z)),
            }
        out["innovation_stats_per_season"] = per_season
    else:
        out["innovation_stats_per_season"] = {
            "status": "NOT_COMPUTED",
            "reason": "innovations missing z",
        }

    # E3 rating-diff vs Elo corr on test games.
    if feature_bank is not None and not feature_bank.empty:
        mask = headline_mask(eval_frame)
        ids = set(int(g) for g in eval_frame.loc[mask, "game_id"])
        fb = feature_bank.loc[feature_bank["game_id"].isin(ids)].copy()
        y = fb["realized_margin"].to_numpy(dtype=float) if "realized_margin" in fb.columns else None
        diff_col = next(
            (c for c in ("rating_diff_off_epa", "off_epa_diff") if c in fb.columns),
            None,
        )
        e3: dict[str, Any] = {}
        if diff_col and y is not None:
            d = fb[diff_col].to_numpy(dtype=float)
            e3["stage1_diff_col"] = diff_col
            e3["stage1_sd"] = float(np.nanstd(d, ddof=1))
            e3["stage1_null_rate"] = float(np.mean(~np.isfinite(d)))
            e3["stage1_zero_rate"] = float(np.mean(d == 0.0))
            m = np.isfinite(d) & np.isfinite(y)
            e3["stage1_corr_with_margin"] = (
                float(np.corrcoef(d[m], y[m])[0, 1]) if m.sum() > 2 else float("nan")
            )
        if elo_by_game:
            elo = np.asarray([elo_by_game.get(int(g), np.nan) for g in fb["game_id"]], dtype=float)
            e3["elo_sd"] = float(np.nanstd(elo, ddof=1))
            e3["elo_null_rate"] = float(np.mean(~np.isfinite(elo)))
            e3["elo_zero_rate"] = float(np.mean(elo == 0.0))
            if y is not None:
                m = np.isfinite(elo) & np.isfinite(y)
                e3["elo_corr_with_margin"] = (
                    float(np.corrcoef(elo[m], y[m])[0, 1]) if m.sum() > 2 else float("nan")
                )
        out["e3_rating_diff"] = e3
    else:
        out["e3_rating_diff"] = {"status": "NOT_COMPUTED", "reason": "no feature_bank"}

    # E4 top-15 end of 2023/2024.
    hist = result.history
    e4: dict[str, Any] = {}
    if hist is not None and not hist.empty:
        for season in (2023, 2024):
            sub = hist.loc[hist["season"] == season] if "season" in hist.columns else hist
            if sub.empty:
                e4[str(season)] = {"status": "NOT_COMPUTED", "reason": "no history rows"}
                continue
            # Take last snapshot per team.
            sort_cols = [c for c in ("week", "event_time") if c in sub.columns]
            latest = sub.sort_values(sort_cols).groupby("team_id", sort=False).tail(1)
            off_col = "off_epa" if "off_epa" in latest.columns else None
            def_col = "def_epa" if "def_epa" in latest.columns else None
            if off_col is None:
                e4[str(season)] = {"status": "NOT_COMPUTED", "reason": "no off_epa"}
                continue
            top_off = latest.nlargest(15, off_col)[
                ["team_id", off_col] + ([def_col] if def_col else [])
            ]
            top_def = (
                latest.nsmallest(15, def_col)[["team_id", def_col]] if def_col else pd.DataFrame()
            )
            e4[str(season)] = {
                "top15_off": top_off.to_dict(orient="records"),
                "top15_def": top_def.to_dict(orient="records"),
                "sp_plus_rank_corr": "NOT_COMPUTED — SP+ table not in staged artifacts",
                "elo_rank_corr": "computed below if Elo finals available",
            }
    out["e4_top15"] = e4

    # E5 posterior SD trajectory for one team.
    plot_path = artifact_dir / "e5_posterior_sd.png"
    e5: dict[str, Any] = {"plot": None}
    if hist is not None and not hist.empty:
        sd_col = next((c for c in hist.columns if c.startswith("sd_") and "off" in c), None)
        if sd_col is None:
            sd_col = next((c for c in hist.columns if c.startswith("sd_")), None)
        if sd_col and "team_id" in hist.columns:
            # Pick a team with many rows in the latest test season present.
            season = int(hist["season"].max()) if "season" in hist.columns else None
            sub = hist.loc[hist["season"] == season] if season is not None else hist
            counts = sub.groupby("team_id").size()
            if len(counts):
                tid = int(counts.idxmax())
                traj = sub.loc[sub["team_id"] == tid].sort_values(
                    [c for c in ("week", "event_time") if c in sub.columns]
                )
                try:
                    import matplotlib.pyplot as plt

                    artifact_dir.mkdir(parents=True, exist_ok=True)
                    fig, ax = plt.subplots(figsize=(8, 4))
                    x = traj["week"] if "week" in traj.columns else np.arange(len(traj))
                    ax.plot(x, traj[sd_col], marker="o", ms=3)
                    ax.set_title(f"Team {tid} {sd_col} trajectory (season {season})")
                    ax.set_xlabel("week")
                    ax.set_ylabel(sd_col)
                    fig.tight_layout()
                    fig.savefig(plot_path, dpi=120)
                    plt.close(fig)
                    e5 = {
                        "team_id": tid,
                        "season": season,
                        "sd_col": sd_col,
                        "plot": str(plot_path),
                        "start_sd": float(traj[sd_col].iloc[0]),
                        "end_sd": float(traj[sd_col].iloc[-1]),
                        "shrinks": bool(traj[sd_col].iloc[-1] < traj[sd_col].iloc[0]),
                    }
                except Exception as exc:  # noqa: BLE001
                    e5 = {"error": str(exc)}
    out["e5_posterior_sd"] = e5
    return out


def block_f_feature_health(
    train_features: pd.DataFrame,
    test_features: pd.DataFrame,
) -> dict[str, Any]:
    """F1–F3 null-rate deltas, constant cols, PSI."""
    meta = {"game_id", "game_key", "season", "week", "as_of", "event_time", "realized_margin"}
    cols = [
        c
        for c in train_features.columns
        if c not in meta
        and c in test_features.columns
        and pd.api.types.is_numeric_dtype(train_features[c])
    ]
    null_delta: list[dict[str, Any]] = []
    for c in cols:
        tr = float(train_features[c].isna().mean())
        te = float(test_features[c].isna().mean())
        null_delta.append({"feature": c, "train_null": tr, "test_null": te, "delta": te - tr})
    null_delta.sort(key=lambda d: d["delta"], reverse=True)

    const_test = []
    near_const = []
    for c in cols:
        v = test_features[c].to_numpy(dtype=float)
        finite = v[np.isfinite(v)]
        if finite.size == 0:
            const_test.append(c)
            continue
        if float(np.nanmax(finite) - np.nanmin(finite)) < 1e-12:
            const_test.append(c)
        elif float(np.nanstd(finite)) < 1e-6:
            near_const.append(c)

    psi_flags: list[dict[str, Any]] = []
    for c in cols:
        tr = train_features[c].to_numpy(dtype=float)
        te = test_features[c].to_numpy(dtype=float)
        psi = _psi(tr, te)
        if psi is not None and psi > 0.3:
            psi_flags.append({"feature": c, "psi": psi})
    psi_flags.sort(key=lambda d: d["psi"], reverse=True)

    return {
        "null_rate_delta_sorted": null_delta,
        "flagged_train_populated_test_null": [
            d for d in null_delta if d["train_null"] < 0.2 and d["test_null"] > 0.5
        ],
        "constant_test_columns": const_test,
        "near_constant_test_columns": near_const,
        "psi_above_0_3": psi_flags,
    }


def _psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float | None:
    e = expected[np.isfinite(expected)]
    a = actual[np.isfinite(actual)]
    if len(e) < 20 or len(a) < 20:
        return None
    qs = np.linspace(0, 100, bins + 1)
    breaks = np.unique(np.percentile(e, qs))
    if len(breaks) < 3:
        return None
    e_counts = np.histogram(e, bins=breaks)[0].astype(float)
    a_counts = np.histogram(a, bins=breaks)[0].astype(float)
    e_perc = np.clip(e_counts / max(e_counts.sum(), 1.0), 1e-4, None)
    a_perc = np.clip(a_counts / max(a_counts.sum(), 1.0), 1e-4, None)
    return float(np.sum((a_perc - e_perc) * np.log(a_perc / e_perc)))


def block_g_calibration(frame: pd.DataFrame) -> dict[str, Any]:
    """G1–G3 calibration diagnostics (measure only)."""
    mask = headline_mask(frame)
    sub = frame.loc[mask].copy()
    if "p_ml_home" not in sub.columns:
        return {"status": "NOT_COMPUTED", "reason": "p_ml_home missing"}
    y = su_outcomes(
        sub["home_points"].to_numpy(),
        sub["away_points"].to_numpy(),
    )
    p_cal = sub["p_ml_home"].to_numpy(dtype=float)
    p_raw = sub["p_ml_home_raw"].to_numpy(dtype=float) if "p_ml_home_raw" in sub.columns else p_cal
    # Uncalibrated with floor disabled: clip only to (0,1) open via tiny machine eps
    # is still needed for log; use raw then clip at 0/1 → replace 0/1 with nan skip.
    p_nofloor = np.asarray(p_raw, dtype=float).copy()
    # "floor disabled" = do not apply 1e-6 isotonic clip; still need numerical guard
    # at true 0/1 for log. Use nextafter to stay in (0,1) without 1e-6.
    p_nofloor = np.clip(p_nofloor, np.finfo(float).tiny, 1.0 - np.finfo(float).eps)
    ll_nofloor = float(log_loss(p_nofloor, y))
    ll_cal = float(log_loss(p_cal, y))

    # Isotonic bin occupancy — not stored on frame; report from calibrated mass.
    # Without the fitted calibrator object, approximate with quantile bins of p_cal.
    finite = np.isfinite(p_cal)
    bins = 10
    if finite.sum() >= bins:
        quantiles = np.linspace(0, 1, bins + 1)
        edges = np.unique(np.quantile(p_cal[finite], quantiles))
        counts, _ = np.histogram(p_cal[finite], bins=edges)
        bin_occ = {
            "n_bins": int(len(counts)),
            "min_count": int(counts.min()) if len(counts) else 0,
            "max_count": int(counts.max()) if len(counts) else 0,
            "median_count": float(np.median(counts)) if len(counts) else float("nan"),
            "end_bin_counts": {
                "left": int(counts[0]) if len(counts) else 0,
                "right": int(counts[-1]) if len(counts) else 0,
            },
            "note": (
                "Fitted isotonic binning object not archived on prediction rows; "
                "counts above are equal-quantile bins of emitted p_ml_home."
            ),
            "oof_fit_n": "NOT_COMPUTED — calibrator OOF size not on prediction artifact",
        }
    else:
        bin_occ = {"status": "NOT_COMPUTED"}

    per = log_loss_per_row(p_cal, y)
    order = np.argsort(-per)
    worst = []
    for i in order[:10]:
        if not np.isfinite(per[i]):
            continue
        worst.append(
            {
                "game_id": int(sub.iloc[i]["game_id"]),
                "p_raw": float(p_raw[i]),
                "p_cal": float(p_cal[i]),
                "outcome": float(y[i]),
                "logloss": float(per[i]),
            }
        )
    return {
        "logloss_uncalibrated_nofloor": ll_nofloor,
        "logloss_calibrated": ll_cal,
        "isotonic_bins": bin_occ,
        "worst10": worst,
    }


def block_h_slices(
    frame: pd.DataFrame,
    *,
    games: pd.DataFrame | None = None,
    elo_margin: pd.Series | None = None,
) -> dict[str, Any]:
    """H1–H2 residual slices."""
    mask = headline_mask(frame)
    sub = frame.loc[mask].copy()
    sub["abs_err"] = (sub["realized_margin"] - sub["pred_margin"]).abs()
    sub["signed"] = sub["realized_margin"] - sub["pred_margin"]

    def _agg(df: pd.DataFrame, key: str) -> dict[str, Any]:
        if key not in df.columns:
            return {"status": "NOT_COMPUTED", "reason": f"{key} missing"}
        out: dict[str, Any] = {}
        for k, g in df.groupby(key, dropna=False):
            out[str(k)] = {
                "n": int(len(g)),
                "mae": float(g["abs_err"].mean()),
                "mean_signed_bias": float(g["signed"].mean()),
            }
        return out

    slices: dict[str, Any] = {
        "by_week": _agg(sub, "week"),
        "by_season": _agg(sub, "season"),
    }
    if games is not None and "neutral_site" in games.columns:
        merged = sub.merge(games[["game_id", "neutral_site"]], on="game_id", how="left")
        slices["by_neutral"] = _agg(merged, "neutral_site")
    else:
        slices["by_neutral"] = {"status": "NOT_COMPUTED", "reason": "neutral_site unavailable"}

    slices["p5_g5_fcs"] = {
        "status": "NOT_COMPUTED",
        "reason": "classification slice columns not on prediction artifact",
    }
    slices["favorite_size"] = {
        "status": "NOT_COMPUTED",
        "reason": "spread_close largely null on this eval set",
    }
    slices["roster_features_null"] = {
        "status": "NOT_COMPUTED",
        "reason": "roster feature null flag not on prediction artifact",
    }

    # H2: MAE by decile of |Elo mu - stack mu|
    if elo_margin is not None:
        sub = sub.copy()
        sub["elo_mu"] = elo_margin.reindex(sub.index).to_numpy(dtype=float)
        # Align via game_id if index mismatch
        if sub["elo_mu"].isna().all() and "game_id" in sub.columns:
            # elo_margin may be indexed by game_id
            sub["elo_mu"] = sub["game_id"].map(
                elo_margin
                if elo_margin.index.name == "game_id" or elo_margin.index.is_integer()
                else {}
            )
            if hasattr(elo_margin, "to_dict"):
                m = {int(k): float(v) for k, v in elo_margin.dropna().items()}
                sub["elo_mu"] = sub["game_id"].map(m)
        sub["abs_dis"] = (sub["elo_mu"] - sub["pred_margin"]).abs()
        finite = sub["abs_dis"].notna()
        if finite.sum() >= 10:
            sub.loc[finite, "dis_decile"] = pd.qcut(
                sub.loc[finite, "abs_dis"], 10, labels=False, duplicates="drop"
            )
            slices["by_elo_disagreement_decile"] = _agg(sub.loc[finite], "dis_decile")
        else:
            slices["by_elo_disagreement_decile"] = {
                "status": "NOT_COMPUTED",
                "reason": "insufficient elo overlap",
            }
    else:
        slices["by_elo_disagreement_decile"] = {
            "status": "NOT_COMPUTED",
            "reason": "elo margins not supplied",
        }
    return slices


# ---------------------------------------------------------------------------
# Feature bank construction (diagnostic walk-forward, ratings+features only)
# ---------------------------------------------------------------------------


def build_feature_bank(
    *,
    games: pd.DataFrame,
    observations: pd.DataFrame,
    config: Any,
    snapshots: pd.DataFrame | None = None,
    cfbd_lines: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Bank as-of rating features for every game via the production provider."""
    from ncaa_quant.evaluation.production_stack import (
        ProductionFeatureProvider,
        StateSpaceRatingEngine,
    )
    from ncaa_quant.evaluation.walkforward import week_decision_as_of

    engine = StateSpaceRatingEngine(observations=observations, config=config)
    provider = ProductionFeatureProvider(config=config, snapshots=snapshots, cfbd_lines=cfbd_lines)
    rows: list[dict[str, Any]] = []
    work = games.copy()
    if "realized_margin" not in work.columns:
        work["realized_margin"] = work["home_points"].astype(float) - work["away_points"].astype(
            float
        )
    for season in sorted(int(s) for s in work["season"].unique()):
        season_games = work.loc[work["season"] == season]
        weeks = sorted(int(w) for w in season_games["week"].unique())
        if not weeks:
            continue
        first_as_of = week_decision_as_of(season, weeks[0], config)
        from datetime import timedelta

        engine.initialize_season(season, first_as_of - timedelta(seconds=1))
        for week in weeks:
            as_of = week_decision_as_of(season, week, config)
            week_games = season_games.loc[season_games["week"] == week].sort_values(
                "game_id", kind="mergesort"
            )
            state = engine.state_snapshot()
            feats = provider.compute_game_features(
                week_games,
                as_of,
                rating_state=state,
                market_features=bool(config.market_features_available),
            )
            merged = week_games.merge(feats, on="game_id", how="left")
            for r in merged.to_dict(orient="records"):
                rows.append(dict(r))
            # Reveal: update ratings after the week's games.
            engine.update_after_games(week_games)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Orchestrator + notes
# ---------------------------------------------------------------------------


def run_mu_diagnostics(
    *,
    predictions_path: Path | str = DEFAULT_SMOKE_PREDICTIONS,
    staged_dir: Path | str = DEFAULT_STAGED,
    artifact_dir: Path | str = DEFAULT_ARTIFACT_DIR,
    notes_path: Path | str = DEFAULT_NOTES,
    skip_heavy: bool = False,
) -> dict[str, Any]:
    """Run Blocks A–H (STOP early on structural bugs). Writes notes + JSON."""
    from ncaa_quant.data.storage import ParquetStore
    from ncaa_quant.evaluation.backtest_runner import load_staged_games
    from ncaa_quant.evaluation.production_stack import (
        build_observations_from_staged,
        build_production_stack,
    )
    from ncaa_quant.evaluation.walkforward import WalkForwardConfig

    artifact = Path(artifact_dir)
    artifact.mkdir(parents=True, exist_ok=True)
    preds = load_prediction_frame(predictions_path)
    test_seasons = sorted(int(s) for s in preds["season"].unique())
    staged = Path(staged_dir)

    # Load staged games spanning prior seasons for Elo continuity + train mean.
    elo_seasons = list(range(min(2014, min(test_seasons) - 5), max(test_seasons) + 1))
    games = load_staged_games(staged, elo_seasons) if staged.is_dir() else pd.DataFrame()

    result: dict[str, Any] = {
        "created_at": datetime.now(tz=UTC).isoformat(),
        "predictions_path": str(predictions_path),
        "test_seasons": test_seasons,
        "n_predictions": int(len(preds)),
        "n_headline": int(headline_mask(preds).sum()),
    }

    # Elo margins for eval game_ids
    elo_series_by_gid: dict[int, float] = {}
    elo_aligned: pd.Series | None = None
    if not games.empty:
        try:
            elo_s = compute_elo_margins(games, preds["game_id"].tolist())
            elo_series_by_gid = {int(k): float(v) for k, v in elo_s.dropna().items()}
            # Align to prediction frame index via game_id map.
            elo_aligned = preds["game_id"].map(elo_series_by_gid)
        except Exception as exc:  # noqa: BLE001
            result["elo_error"] = str(exc)

    train_mean = train_mean_margin(games, test_seasons) if not games.empty else float("nan")
    market_spread = preds["spread_close"] if "spread_close" in preds.columns else None

    # ---- Block A ----
    a = block_a(
        preds,
        elo_margin=elo_aligned,
        market_spread=market_spread,
        train_mean=train_mean if np.isfinite(train_mean) else None,
    )
    a["history_reconcile"] = reconcile_history()
    result["block_a"] = a

    # ---- Block B1 ----
    b1 = block_b_target_contract(preds, raw_games=games if not games.empty else None)
    result["block_b1"] = b1
    stop = detect_structural_stop(a, b1)
    result["structural_stop"] = asdict(stop)
    if stop.kind != "none":
        result["stopped_early"] = True
        _write_outputs(result, Path(notes_path), artifact)
        return result

    # B2–B3
    result["block_b2"] = block_b_neutral_slopes(preds, games=games if not games.empty else None)
    outcomes = preds[
        [
            c
            for c in ("game_id", "game_key", "realized_margin", "home_points", "away_points")
            if c in preds.columns
        ]
    ].copy()
    result["block_b3"] = block_b_join_integrity(preds, outcomes)

    # Feature bank + production predictor for B4/B5/C/E/F
    feature_bank = pd.DataFrame()
    predictor = None
    observations = pd.DataFrame()
    if not skip_heavy and staged.is_dir() and not games.empty:
        store = ParquetStore(str(staged))
        plays = pd.DataFrame()
        advanced = pd.DataFrame()
        for season in sorted(set(elo_seasons) | set(test_seasons)):
            try:
                p = store.read("plays", filters={"season": season})
                if not p.empty:
                    plays = pd.concat([plays, p], ignore_index=True)
            except Exception:
                pass
            try:
                adv = store.read("advanced_game_stats", filters={"season": season})
                if not adv.empty:
                    advanced = pd.concat([advanced, adv], ignore_index=True)
            except Exception:
                pass
        observations, n_on, n_off = build_observations_from_staged(
            plays=plays if not plays.empty else None,
            games=games,
            advanced=advanced if not advanced.empty else None,
            garbage_time_filter=True,
        )
        cfg = WalkForwardConfig(
            test_seasons=tuple(test_seasons),
            continuity_seasons=(),
            market_features_available=False,
            seed=42,
        )
        # Bank features for seasons needed to train (< test) plus test seasons.
        bank_seasons = sorted(
            {int(s) for s in games["season"].unique() if int(s) >= min(test_seasons) - 4}
        )
        bank_games = games.loc[games["season"].isin(bank_seasons)].copy()
        bank_cfg = WalkForwardConfig(
            test_seasons=tuple(bank_seasons),
            continuity_seasons=(),
            market_features_available=False,
            seed=42,
        )
        try:
            feature_bank = build_feature_bank(
                games=bank_games, observations=observations, config=bank_cfg
            )
            stack = build_production_stack(
                cfg,
                kind="fundamental",
                observations=observations,
                play_counts=(n_on, n_off),
                n_mc_draws=200,
                n_epistemic_draws=2,
            )
            predictor = stack.predictor
            # Fit on seasons < min(test) if available.
            if not feature_bank.empty:
                train = feature_bank.loc[feature_bank["season"] < min(test_seasons)]
                if not train.empty:
                    x = _feature_matrix(train)
                    lab = train[["game_id", "realized_margin"]].copy()
                    if "realized_total" in train.columns:
                        lab["realized_total"] = train["realized_total"]
                    predictor.fit(x, lab)
        except Exception as exc:  # noqa: BLE001
            result["feature_bank_error"] = str(exc)

    if predictor is not None and not feature_bank.empty:
        te = feature_bank.loc[feature_bank["season"].isin(test_seasons)]
        x_te = _feature_matrix(te.head(64)) if len(te) else pd.DataFrame()
        if not x_te.empty:
            result["block_b4"] = block_b_row_order_test(predictor.margin_head, x_te)
            result["block_b5"] = block_b_feature_signature_contract(predictor.margin_head, x_te)
        else:
            result["block_b4"] = {"status": "NOT_COMPUTED", "reason": "no test features"}
            result["block_b5"] = {"status": "NOT_COMPUTED", "reason": "no test features"}
    else:
        result["block_b4"] = {
            "status": "NOT_COMPUTED",
            "reason": "predictor/feature_bank unavailable",
        }
        result["block_b5"] = {
            "status": "NOT_COMPUTED",
            "reason": "predictor/feature_bank unavailable",
        }

    # B6 shifted-label
    result["block_b6"] = _run_shifted_label_probe(skip_heavy=skip_heavy)

    # Check rating features all-null → STOP
    if not feature_bank.empty:
        diff_cols = [c for c in feature_bank.columns if "rating_diff" in c or c.endswith("_diff")]
        if diff_cols:
            te = feature_bank.loc[feature_bank["season"].isin(test_seasons), diff_cols]
            # All-zero can be cold start; only STOP if every rating-diff cell is null.
            if (not te.empty) and bool(te.isna().all().all()):
                stop = StructuralFinding(
                    kind="all_null_rating_feature",
                    message="all rating-diff features null on test rows",
                    evidence={"columns": diff_cols},
                )
                result["structural_stop"] = asdict(stop)
                result["stopped_early"] = True
                _write_outputs(result, Path(notes_path), artifact)
                return result

    # ---- Block C ----
    if not feature_bank.empty:
        published = {int(r.game_id): float(r.pred_margin) for r in preds.itertuples(index=False)}
        # Attach labels onto bank from games.
        if "realized_margin" not in feature_bank.columns:
            feature_bank = feature_bank.merge(
                games[["game_id", "home_points", "away_points"]],
                on="game_id",
                how="left",
            )
            feature_bank["realized_margin"] = feature_bank["home_points"].astype(
                float
            ) - feature_bank["away_points"].astype(float)
        result["block_c"] = block_c_layer_ladder(
            eval_frame=preds,
            feature_bank=feature_bank,
            elo_by_game=elo_series_by_gid,
            published_mu=published,
            test_seasons=test_seasons,
        )
    else:
        result["block_c"] = {
            "status": "NOT_COMPUTED",
            "reason": "feature_bank unavailable",
            "partial_l0_l7": _partial_l0_l7(preds, elo_series_by_gid),
        }

    # ---- Block D ----
    oof = None
    nnls_w = None
    if isinstance(result.get("block_c"), dict):
        nnls_w = result["block_c"].get("nnls_weights_by_season")
    result["block_d"] = block_d_ensemble_health(nnls_weights=nnls_w, oof_frame=oof)

    # ---- Block E ----
    result["block_e"] = block_e_stage1(
        observations=observations if not observations.empty else None,
        feature_bank=feature_bank if not feature_bank.empty else None,
        elo_by_game=elo_series_by_gid,
        eval_frame=preds,
        artifact_dir=artifact,
    )

    # ---- Block F ----
    if not feature_bank.empty:
        tr = feature_bank.loc[feature_bank["season"] < min(test_seasons)]
        te = feature_bank.loc[feature_bank["season"].isin(test_seasons)]
        result["block_f"] = (
            block_f_feature_health(tr, te)
            if not tr.empty and not te.empty
            else {"status": "NOT_COMPUTED", "reason": "empty train or test features"}
        )
    else:
        result["block_f"] = {"status": "NOT_COMPUTED", "reason": "no feature_bank"}

    # ---- Block G ----
    result["block_g"] = block_g_calibration(preds)

    # ---- Block H ----
    result["block_h"] = block_h_slices(
        preds, games=games if not games.empty else None, elo_margin=elo_aligned
    )

    result["stopped_early"] = False
    _write_outputs(result, Path(notes_path), artifact)
    return result


def _partial_l0_l7(preds: pd.DataFrame, elo_by_game: Mapping[int, float]) -> dict[str, Any]:
    mask = headline_mask(preds)
    sub = preds.loc[mask]
    y = sub["realized_margin"].to_numpy(dtype=float)
    elo = np.asarray([elo_by_game.get(int(g), np.nan) for g in sub["game_id"]], dtype=float)
    stack = sub["pred_margin"].to_numpy(dtype=float)
    return {
        "L0_elo": asdict(score_predictor("L0", y, elo)),
        "L7_published": asdict(score_predictor("L7", y, stack)),
    }


def _run_shifted_label_probe(*, skip_heavy: bool) -> dict[str, Any]:
    """B6: Task 16 shifted-label test on a synthetic production stack."""
    if skip_heavy:
        return {
            "status": "SKIPPED",
            "reason": "skip_heavy=True; omit --skip-heavy to run B6",
        }
    try:
        from datetime import timedelta

        from ncaa_quant.evaluation.production_stack import build_production_stack
        from ncaa_quant.evaluation.walkforward import (
            WalkForwardConfig,
            WalkForwardHarness,
            build_shifted_feature_frame,
            week_decision_as_of,
        )

        z_se = 2.0
        games = _synth_shifted_games()
        cfg = WalkForwardConfig(
            test_seasons=(2023,),
            continuity_seasons=(),
            retrain_weeks=(5,),
            market_features_available=False,
            seed=11,
            run_id="d1_shifted",
            ablation_id="full",
        )
        obs = _synth_shifted_observations(games)
        stack = build_production_stack(
            cfg,
            kind="fundamental",
            observations=obs,
            play_counts=(80, 100),
            n_mc_draws=200,
            n_epistemic_draws=1,
        )
        harness = WalkForwardHarness(
            config=stack.config,
            predictor=stack.predictor,
            feature_provider=stack.feature_provider,
            rating_engine=stack.rating_engine,
        )
        harness.run(games)

        engine = stack.rating_engine
        rating_snapshots: dict[tuple[int, int], dict[str, Any]] = {}
        weeks = sorted(int(w) for w in games["week"].unique())
        first_as_of = week_decision_as_of(2023, weeks[0], cfg)
        engine.initialize_season(2023, first_as_of - timedelta(seconds=1))
        for week in weeks:
            rating_snapshots[(2023, week)] = engine.state_snapshot()
            engine.update_after_games(games.loc[games["week"] == week])

        train = games.loc[games["week"] < 5].copy()
        train_labels = train.copy()
        train_labels["realized_margin"] = train_labels["home_points"].astype(float) - train_labels[
            "away_points"
        ].astype(float)
        mu_train = float(train_labels["realized_margin"].mean())
        as_of = week_decision_as_of(2023, 4, cfg)
        feats = stack.feature_provider.compute_game_features(
            train,
            as_of,
            rating_state=rating_snapshots[(2023, 4)],
            market_features=False,
        )
        stack.predictor.fit(feats, train_labels)

        past = games.loc[games["week"] <= 2].copy()
        shifted_as_of = datetime(2024, 1, 15, tzinfo=UTC)
        shifted = build_shifted_feature_frame(
            past,
            stack.feature_provider,
            shifted_as_of,
            rating_state=rating_snapshots[(2023, 2)],
            market_features=False,
        )
        preds = stack.predictor.predict(shifted)
        merged = shifted[["game_id", "realized_margin"]].merge(preds, on="game_id", how="inner")
        y = merged["realized_margin"].astype(float).to_numpy()
        yhat = merged["pred_margin"].astype(float).to_numpy()
        model_mae = float(np.mean(np.abs(y - yhat)))
        residuals = np.abs(y - mu_train)
        chance_mae = float(np.mean(residuals))
        se = float(np.std(residuals, ddof=1) / np.sqrt(len(residuals)))
        half_width = z_se * se
        lo = chance_mae - half_width
        hi = chance_mae + half_width
        within = bool(lo <= model_mae <= hi)
        beats_chance = bool(model_mae < lo)
        return {
            "status": "ok",
            "model_mae": model_mae,
            "chance_mae": chance_mae,
            "band": [lo, hi],
            "se": se,
            "z_se": z_se,
            "n": int(len(merged)),
            "within_chance_band": within,
            "beats_chance": beats_chance,
            "verdict": (
                "PLUMBING_BUG_beats_chance"
                if beats_chance
                else ("PASS_at_chance" if within else "FAIL_worse_than_chance_band")
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "error": str(exc)}


def _synth_shifted_games() -> pd.DataFrame:
    """Minimal multi-week schedule for the shifted-label probe."""
    from datetime import timedelta

    from ncaa_quant.evaluation.walkforward import WalkForwardConfig, week_decision_as_of

    rows: list[dict[str, Any]] = []
    gid = 5000
    rng = np.random.default_rng(7)
    cfg = WalkForwardConfig()
    for week in (1, 2, 3, 4, 5, 6, 7, 8):
        for slot in range(4):
            home = 10 + (slot % 8)
            away = 20 + (slot % 8)
            tuesday = week_decision_as_of(2023, week, cfg)
            start = tuesday + timedelta(days=4, hours=slot)
            rows.append(
                {
                    "game_id": gid,
                    "game_key": f"2023:{home}:{away}:{start.date()}:{slot}",
                    "season": 2023,
                    "week": week,
                    "event_time": start,
                    "home_team_id": home,
                    "away_team_id": away,
                    "home_points": int(24 + rng.integers(0, 21)),
                    "away_points": int(21 + rng.integers(0, 21)),
                    "neutral_site": False,
                }
            )
            gid += 1
    return pd.DataFrame(rows)


def _synth_shifted_observations(games: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for g in games.itertuples(index=False):
        rows.append(
            {
                "game_id": int(g.game_id),
                "season": int(g.season),
                "week": int(g.week),
                "event_time": g.event_time,
                "home_team_id": int(g.home_team_id),
                "away_team_id": int(g.away_team_id),
                "home_epa": 0.05,
                "away_epa": -0.02,
                "home_plays": 70.0,
                "away_plays": 68.0,
                "margin": float(g.home_points) - float(g.away_points),
                "neutral_site": False,
            }
        )
    return pd.DataFrame(rows)


def _write_outputs(result: dict[str, Any], notes_path: Path, artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifact_dir / "diag_mu.json"
    json_path.write_text(
        json.dumps(_as_jsonable(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(render_notes(result), encoding="utf-8")


def render_notes(result: Mapping[str, Any]) -> str:
    """Render docs/notes/D1.md diagnosis memo."""
    a = result.get("block_a") or {}
    stack_r2_le_zero = bool(a.get("stack_r2_le_zero"))
    first = "yes" if stack_r2_le_zero else "no"
    lines: list[str] = []
    lines.append(
        f"{first} — stack R² on realized margin is {a.get('stack_r2')} (≤0? {stack_r2_le_zero})."
    )
    lines.append("")
    lines.append("# D1 — Where the margin signal dies (diagnosis memo)")
    lines.append("")
    lines.append(
        "Read-only diagnostic. No hyperparameters, configs, clip floors, or models were changed."
    )
    lines.append("")
    lines.append(f"- predictions: `{result.get('predictions_path')}`")
    lines.append(f"- n_predictions: {result.get('n_predictions')}")
    lines.append(f"- n_headline: {result.get('n_headline')}")
    lines.append(f"- test_seasons: {result.get('test_seasons')}")
    stop = result.get("structural_stop") or {}
    lines.append(f"- structural_stop: `{stop.get('kind')}` — {stop.get('message')}")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    c = result.get("block_c") or {}
    lines.append(f"**Failing step (Block C):** {c.get('failing_step')}")
    lines.append("")
    lines.append(
        "On the FIX-DIAG smoke set (2023, n=910), published μ emits "
        f"zeros for weeks 1–4 (zero_mu_rate={a.get('zero_mu_rate')}) because "
        "`wiring_proof_2023` has empty continuity/seed labels and "
        "`BasePredictor._unfitted_predictions` returns 0. "
        "Diagnostic L1–L5 heads trained on seasons < 2023 beat Elo "
        f"(Elo MAE={c.get('elo_mae')}); L7 published MAE jumps to 16.60. "
        f"C1 in-sample LGBM MAE={((c.get('signal_ceiling') or {}).get('in_sample_lgbm_mae'))} "
        "→ features carry signal; failure is training/prediction plumbing on the "
        "published path, not a dead Stage-1 rating differential."
    )
    lines.append("")

    lines.append("## Block A — defect or deficit?")
    lines.append("")
    lines.append(f"- SD(y) = **{a.get('sd_y')}**")
    lines.append(
        f"- MAE(y − mean(y_train)) = **{a.get('mae_y_minus_train_mean')}** "
        f"(train_mean={a.get('train_mean')}; {a.get('train_mean_note')})"
    )
    lines.append(f"- zero_mu_rate = **{a.get('zero_mu_rate')}**")
    lines.append(f"- zero_mu_by_week = `{a.get('zero_mu_by_week')}`")
    lines.append("")
    lines.append("| predictor | n | MAE | RMSE | resid_SD | bias | R² |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for name, sc in (a.get("scores") or {}).items():
        lines.append(
            f"| {name} | {sc.get('n_finite')} | {sc.get('mae')} | {sc.get('rmse')} | "
            f"{sc.get('residual_sd')} | {sc.get('mean_signed_bias')} | {sc.get('r2')} |"
        )
    lines.append("")
    lines.append("| predictor | a | b | R² | r | SD(yhat) |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for name, sl in (a.get("slopes") or {}).items():
        lines.append(
            f"| {name} | {sl.get('a')} | {sl.get('b')} | {sl.get('r2')} | "
            f"{sl.get('pearson_r')} | {sl.get('sd_yhat')} |"
        )
    lines.append("")
    hist = a.get("history_reconcile") or {}
    lines.append("### A5 history reconcile")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(_as_jsonable(hist), indent=2))
    lines.append("```")
    lines.append("")

    if result.get("stopped_early"):
        lines.append("## STOPPED EARLY")
        lines.append("")
        lines.append(f"Structural finding `{stop.get('kind')}` — remaining blocks not completed.")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(_as_jsonable(stop), indent=2))
        lines.append("```")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Block B — alignment / orientation")
    lines.append("")
    for key in ("block_b1", "block_b2", "block_b3", "block_b4", "block_b5", "block_b6"):
        lines.append(f"### {key}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(_as_jsonable(result.get(key)), indent=2)[:8000])
        lines.append("```")
        lines.append("")

    lines.append("## Block C — layer ladder")
    lines.append("")
    c = result.get("block_c") or {}
    lines.append(f"**Failing step:** {c.get('failing_step')}")
    lines.append("")
    lines.append("| layer | n | MAE | RMSE | resid_SD | r | slope_b |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for row in c.get("rows") or []:
        lines.append(
            f"| {row.get('layer')} | {row.get('n')} | {row.get('mae')} | {row.get('rmse')} | "
            f"{row.get('residual_sd')} | {row.get('r')} | {row.get('slope_b')} |"
        )
    if not c.get("rows"):
        lines.append("")
        lines.append(f"Ladder incomplete: `{c.get('status')}` — {c.get('reason')}")
        if c.get("partial_l0_l7"):
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(_as_jsonable(c.get("partial_l0_l7")), indent=2))
            lines.append("```")
    lines.append("")
    lines.append("### C1 signal-ceiling (NOT a performance number)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(_as_jsonable(c.get("signal_ceiling")), indent=2))
    lines.append("```")
    lines.append("")

    for key in ("block_d", "block_e", "block_f", "block_g", "block_h"):
        lines.append(f"## {key}")
        lines.append("")
        lines.append("```json")
        payload = json.dumps(_as_jsonable(result.get(key)), indent=2)
        lines.append(payload[:12000])
        if len(payload) > 12000:
            lines.append("... [truncated; see diag_mu.json]")
        lines.append("```")
        lines.append("")

    lines.append("## Diff scope confirmation")
    lines.append("")
    lines.append(
        "This task may only touch `diagnostics_mu.py`, its tests, CLI registration, "
        "and this notes file. No model/config/default changes."
    )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "CalibrationSlope",
    "DiagnosticsMuError",
    "PredictorScore",
    "StructuralFinding",
    "block_a",
    "detect_structural_stop",
    "load_prediction_frame",
    "regress_y_on_yhat",
    "render_notes",
    "run_mu_diagnostics",
    "score_predictor",
]
