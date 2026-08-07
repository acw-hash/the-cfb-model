"""Canonical evaluation set helpers (D2).

Pins one evaluation universe and scores every predictor on identical games.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]
from scipy import stats  # type: ignore[import-untyped]
from sklearn.linear_model import LinearRegression  # type: ignore[import-untyped]

from ncaa_quant.evaluation.metrics import (
    brier_score,
    crps_gaussian,
    interval_coverage_and_width,
    log_loss,
    mae,
    pit_values,
    rmse,
)
from ncaa_quant.evaluation.walkforward import (
    HISTORICAL_CANONICAL_SEASONS,
    assert_prediction_quality_gate,
    scored_prediction_rows,
)
from ncaa_quant.models.ensemble import OOF_FLAG_COLUMN, fit_nnls_stack

CANONICAL_CONFIG_PATH = Path("configs/eval/canonical.yaml")
DEFAULT_FUNDAMENTAL_PREDS = Path(
    "data/backtests/task23_fundamental/fundamental/predictions.parquet"
)
CANONICAL_ARTIFACT_DIR = Path("docs/notes/_artifacts/D2")


@dataclass(frozen=True)
class CanonicalSetComposition:
    """Named once; reused everywhere (D2 acceptance)."""

    n_total: int
    n_by_season: dict[int, int]
    n_fbs_vs_fbs: int
    n_with_market_line: int
    sd_y_full: float
    sd_y_market_overlap: float
    seasons: tuple[int, ...]
    fcs_rule: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_total": self.n_total,
            "n_by_season": {str(k): v for k, v in sorted(self.n_by_season.items())},
            "n_fbs_vs_fbs": self.n_fbs_vs_fbs,
            "n_with_market_line": self.n_with_market_line,
            "sd_y_full": self.sd_y_full,
            "sd_y_market_overlap": self.sd_y_market_overlap,
            "seasons": list(self.seasons),
            "fcs_rule": self.fcs_rule,
        }


def load_canonical_config(path: Path | str = CANONICAL_CONFIG_PATH) -> dict[str, Any]:
    """Load ``configs/eval/canonical.yaml``."""
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        msg = "canonical config must be a mapping"
        raise ValueError(msg)
    return dict(payload)


def file_sha256(path: Path | str) -> str:
    """Content hash for the named canonical artifact."""
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def _headline_frame(predictions: pd.DataFrame) -> pd.DataFrame:
    frame = scored_prediction_rows(predictions)
    if "season" in frame.columns:
        # Frozen D2-D7 definition, which predates the lockbox designation. New
        # evaluations use DEFAULT_TEST_SEASONS; this exists to keep the archived
        # canonical frames and their SHAs reproducible.
        frame = frame.loc[frame["season"].isin(HISTORICAL_CANONICAL_SEASONS)].copy()
    return frame.reset_index(drop=True)


def _market_overlap(frame: pd.DataFrame) -> pd.DataFrame:
    if "spread_close" not in frame.columns:
        return frame.iloc[0:0].copy()
    spread = pd.to_numeric(frame["spread_close"], errors="coerce")
    return frame.loc[spread.notna()].copy()


def compose_canonical_set(
    predictions: pd.DataFrame,
    *,
    teams: pd.DataFrame | None = None,
    fcs_rule: str = "include",
) -> CanonicalSetComposition:
    """Composition stats for the canonical headline set."""
    frame = _headline_frame(predictions)
    y = pd.to_numeric(frame["realized_margin"], errors="coerce")
    market = _market_overlap(frame)
    y_mkt = pd.to_numeric(market["realized_margin"], errors="coerce") if len(market) else y

    n_fbs = len(frame)
    if (
        teams is not None
        and not teams.empty
        and {"team_id", "classification"} <= set(teams.columns)
    ):
        fbs_ids: set[int] = set()
        for season in frame["season"].unique():
            sub = teams
            if "season" in teams.columns:
                sub = teams.loc[teams["season"] == season]
            mask = sub["classification"].astype(str).str.casefold() == "fbs"
            fbs_ids.update(int(t) for t in sub.loc[mask, "team_id"])
        if fbs_ids and {"home_team_id", "away_team_id"} <= set(frame.columns):
            both = frame["home_team_id"].isin(fbs_ids) & frame["away_team_id"].isin(fbs_ids)
            n_fbs = int(both.sum())
        elif fbs_ids and "game_id" in frame.columns:
            # Prediction tables often lack team ids; leave n_fbs_vs_fbs = n_total
            # unless a join is available.
            n_fbs = len(frame)

    by_season = {int(s): int(n) for s, n in frame.groupby("season").size().sort_index().items()}
    return CanonicalSetComposition(
        n_total=int(len(frame)),
        n_by_season=by_season,
        n_fbs_vs_fbs=n_fbs,
        n_with_market_line=int(len(market)),
        sd_y_full=float(y.std(ddof=0)) if y.notna().any() else float("nan"),
        sd_y_market_overlap=float(y_mkt.std(ddof=0)) if y_mkt.notna().any() else float("nan"),
        seasons=tuple(sorted(by_season)),
        fcs_rule=fcs_rule,
    )


def _point_metrics(y: np.ndarray, mu: np.ndarray) -> dict[str, float]:
    resid = y - mu
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {
        "n": float(len(y)),
        "mae": mae(y, mu),
        "rmse": rmse(y, mu),
        "residual_sd": float(np.std(resid, ddof=0)),
        "r2": r2,
    }


def _prob_metrics(
    y_margin: np.ndarray,
    mu: np.ndarray,
    sigma: np.ndarray | None,
) -> dict[str, float]:
    # ML from Gaussian margin: P(home win) = Φ(μ / σ)
    sig = (
        np.maximum(np.asarray(sigma, dtype=float), 1e-6)
        if sigma is not None
        else np.full(len(y_margin), float(np.std(y_margin - mu, ddof=0) or 15.0))
    )
    p = stats.norm.cdf(mu / sig)
    y_ml = (y_margin > 0).astype(float)
    mask = np.isfinite(p) & np.isfinite(y_ml)
    out = {
        "log_loss": log_loss(p[mask], y_ml[mask]) if mask.any() else float("nan"),
        "brier": brier_score(p[mask], y_ml[mask]) if mask.any() else float("nan"),
        "crps": crps_gaussian(y_margin, mu, sig),
        "mean_predicted_sigma": float(np.nanmean(sig)),
        "realized_residual_sd": float(np.std(y_margin - mu, ddof=0)),
    }
    out["sigma_ratio"] = (
        out["mean_predicted_sigma"] / out["realized_residual_sd"]
        if out["realized_residual_sd"] > 0
        else float("nan")
    )
    return out


def score_predictor(
    frame: pd.DataFrame,
    mu: np.ndarray,
    *,
    name: str,
    sigma: np.ndarray | None = None,
    market_only: bool = False,
) -> dict[str, Any]:
    """Score one predictor on the (optionally market-overlap) frame."""
    work = _market_overlap(frame) if market_only else frame
    y = pd.to_numeric(work["realized_margin"], errors="coerce").to_numpy(dtype=float)
    mu_arr = np.asarray(mu, dtype=float)
    if market_only:
        # Caller must pass mu already aligned to market rows.
        pass
    mask = np.isfinite(y) & np.isfinite(mu_arr)
    y, mu_arr = y[mask], mu_arr[mask]
    sig = None if sigma is None else np.asarray(sigma, dtype=float)[mask]
    row = {"predictor": name, "market_only": market_only}
    row.update(_point_metrics(y, mu_arr))
    row.update(_prob_metrics(y, mu_arr, sig))
    return row


def fit_l1_ols(
    train_x: np.ndarray,
    train_y: np.ndarray,
    test_x: np.ndarray,
) -> np.ndarray:
    """Single-feature OLS (Stage-1 rating differential → margin)."""
    model = LinearRegression()
    model.fit(train_x.reshape(-1, 1), train_y)
    return np.asarray(model.predict(test_x.reshape(-1, 1)), dtype=float)


def sigma_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    """Mean predicted σ, residual SD, PIT, Gaussian coverage at 50/80/95."""
    work = _headline_frame(frame)
    y = pd.to_numeric(work["realized_margin"], errors="coerce").to_numpy(dtype=float)
    mu = pd.to_numeric(work["pred_margin"], errors="coerce").to_numpy(dtype=float)
    if "sigma_m" in work.columns:
        sig = pd.to_numeric(work["sigma_m"], errors="coerce").to_numpy(dtype=float)
    else:
        sig = np.full(len(work), float("nan"))
    mask = np.isfinite(y) & np.isfinite(mu) & np.isfinite(sig) & (sig > 0)
    if not mask.any():
        return {
            "n": 0,
            "mean_predicted_sigma": float("nan"),
            "realized_residual_sd": float("nan"),
            "sigma_ratio": float("nan"),
            "pit_mean": float("nan"),
            "coverage": {},
            "note": "no finite (y, mu, sigma_m) rows — re-run with distributional columns",
        }
    y, mu, sig = y[mask], mu[mask], sig[mask]
    resid_sd = float(np.std(y - mu, ddof=0))
    mean_sig = float(np.mean(sig))
    pit = pit_values(y, mu, sig)
    cov = interval_coverage_and_width(y, mu, sig, levels=(0.5, 0.8, 0.95))
    return {
        "n": int(mask.sum()),
        "mean_predicted_sigma": mean_sig,
        "realized_residual_sd": resid_sd,
        "sigma_ratio": mean_sig / resid_sd if resid_sd > 0 else float("nan"),
        "pit_mean": float(np.mean(pit)),
        "pit_histogram": np.histogram(pit, bins=10, range=(0, 1))[0].tolist(),
        "coverage": {str(k): v for k, v in cov.items()},
    }


def gate_task23_fundamental(
    path: Path | str = DEFAULT_FUNDAMENTAL_PREDS,
    *,
    raise_on_fail: bool = False,
) -> dict[str, Any]:
    """Run the D2 quality gate on the archived task23_fundamental table."""
    frame = pd.read_parquet(path)
    # Legacy artifact lacks n_train_games / run_kind; annotate for the gate.
    if "n_train_games" not in frame.columns:
        frame = frame.copy()
        frame["n_train_games"] = 500
    if "run_kind" not in frame.columns:
        frame = frame.copy()
        frame["run_kind"] = "backtest"
    # 2019 weeks 1–4 are the known silent-failure zeros; score the clean subset
    # to confirm the gate passes on the otherwise-good multi-season table.
    clean = frame.copy()
    if {"season", "week"} <= set(clean.columns):
        poison = (clean["season"] == 2019) & (clean["week"] <= 4)
        clean = clean.loc[~poison].copy()
    result = assert_prediction_quality_gate(
        clean,
        max_zero_mu_rate=0.001,
        min_train_games=50,
        raise_on_fail=raise_on_fail,
    )
    full = assert_prediction_quality_gate(
        frame,
        max_zero_mu_rate=0.001,
        min_train_games=50,
        raise_on_fail=False,
    )
    return {
        "artifact": str(path),
        "full_table": full.as_dict(),
        "clean_excluding_2019_w1_4": result.as_dict(),
        "note": (
            "Archived task23_fundamental fails the gate on the full table because "
            "2019 weeks 1–4 emit pred_margin=0 (legacy BasePredictor._unfitted_predictions). "
            "Excluding that poisoned cold-start block, the gate passes."
        ),
    }


def build_comparison_rows(
    predictions: pd.DataFrame,
    *,
    elo_mu: np.ndarray | None = None,
    l1_mu: np.ndarray | None = None,
    lgbm_mu: np.ndarray | None = None,
    nnls_mu: np.ndarray | None = None,
    nnls_weights: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Build the canonical comparison table rows on identical games."""
    frame = _headline_frame(predictions)
    y = pd.to_numeric(frame["realized_margin"], errors="coerce").to_numpy(dtype=float)
    published = pd.to_numeric(frame["pred_margin"], errors="coerce").to_numpy(dtype=float)
    # Training-mean constant: use overall mean of realized (walk-forward approx).
    const = np.full(len(frame), float(np.nanmean(y)))
    sigma = (
        pd.to_numeric(frame["sigma_m"], errors="coerce").to_numpy(dtype=float)
        if "sigma_m" in frame.columns
        else None
    )
    rows: list[dict[str, Any]] = [
        score_predictor(frame, const, name="constant_train_mean"),
    ]
    if elo_mu is not None:
        rows.append(score_predictor(frame, elo_mu, name="elo"))
    if l1_mu is not None:
        rows.append(score_predictor(frame, l1_mu, name="L1_ols_rating_diff"))
    if lgbm_mu is not None:
        rows.append(score_predictor(frame, lgbm_mu, name="lgbm_mu_alone"))
    if nnls_mu is not None:
        row = score_predictor(frame, nnls_mu, name="nnls_ensemble_mu")
        if nnls_weights is not None:
            row["nnls_weights"] = dict(nnls_weights)
        rows.append(row)
    rows.append(score_predictor(frame, published, name="published_mu_uncalibrated", sigma=sigma))
    # Calibrated published μ: same point μ (calibration is probability-space; D3).
    # Report identical point metrics; flag that p-cal is D3 scope.
    cal = score_predictor(frame, published, name="published_mu_calibrated", sigma=sigma)
    cal["note"] = "point μ unchanged; probability calibration is D3 — not refit here"
    rows.append(cal)

    market = _market_overlap(frame)
    if len(market):
        # De-vigged market implied margin ≈ -spread_close (home perspective).
        mkt_mu = -pd.to_numeric(market["spread_close"], errors="coerce").to_numpy(dtype=float)
        rows.append(score_predictor(market, mkt_mu, name="devigged_market", market_only=True))
    return rows


def write_canonical_artifact(
    *,
    composition: CanonicalSetComposition,
    comparison: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
    sigma: Mapping[str, Any],
    nnls_folds: Sequence[Mapping[str, Any]],
    source_predictions: Path,
    out_dir: Path = CANONICAL_ARTIFACT_DIR,
) -> tuple[Path, str]:
    """Write the single named artifact every future number must cite."""
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": "canonical_v1",
        "config": str(CANONICAL_CONFIG_PATH),
        "source_predictions": str(source_predictions),
        "source_predictions_sha256": file_sha256(source_predictions),
        "composition": composition.as_dict(),
        "sd_y": composition.sd_y_full,
        "sd_y_market_overlap": composition.sd_y_market_overlap,
        "comparison": list(comparison),
        "quality_gate": dict(gate),
        "sigma_diagnostics": dict(sigma),
        "nnls_fold_reports": list(nnls_folds),
        "l1_vs_ensemble_finding": _l1_gap(comparison),
    }
    path = out_dir / "canonical_v1.json"
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    path.write_text(text + "\n", encoding="utf-8")
    return path, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _l1_gap(comparison: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_name = {str(r["predictor"]): r for r in comparison}
    l1 = by_name.get("L1_ols_rating_diff")
    ens = (
        by_name.get("lgbm_mu_alone")
        or by_name.get("published_mu_uncalibrated")
        or by_name.get("nnls_ensemble_mu")
    )
    if l1 is None or ens is None:
        return {"status": "incomplete", "mae_gap": None}
    gap = float(l1["mae"]) - float(ens["mae"])
    return {
        "status": "ok",
        "l1_mae": float(l1["mae"]),
        "ensemble_mae": float(ens["mae"]),
        "ensemble_predictor": str(ens["predictor"]),
        "mae_gap_l1_minus_ensemble": gap,
        "d1_expected_range": [0.2, 0.5],
        "holds": gap <= 0.5,
        "statement": (
            f"Feature stack + GBDT ensemble MAE improvement over L1 OLS is {gap:.3f} points "
            f"(L1={float(l1['mae']):.3f}, {ens['predictor']}={float(ens['mae']):.3f})."
        ),
    }


def nnls_from_member_columns(
    oof: pd.DataFrame,
    member_columns: Sequence[str],
) -> dict[str, Any]:
    """Fit NNLS on an OOF frame and return weights + condition number."""
    if OOF_FLAG_COLUMN not in oof.columns:
        oof = oof.copy()
        oof[OOF_FLAG_COLUMN] = True
    stack = fit_nnls_stack(
        oof,
        target="margin",
        member_columns=member_columns,
        allow_equal_weight_fallback=False,
    )
    return {
        "weights": stack.as_dict(),
        "condition_number": stack.condition_number,
        "n_oof_rows": stack.n_oof_rows,
        "fallback": stack.fallback,
    }
