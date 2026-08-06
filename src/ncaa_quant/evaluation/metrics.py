"""Tier 1–4 evaluation metrics with market baselines (DESIGN §7.3).
Every probabilistic score is reported alongside the de-vigged market baseline.
Slice tables are labeled DIAGNOSTIC — not model-selection inputs (§7.2 item 3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy import stats  # type: ignore[import-untyped]

from ncaa_quant.evaluation.significance import (
    DEFAULT_ALPHA,
    DEFAULT_N_BOOT,
    ConfidenceInterval,
    RateWithCI,
    block_bootstrap,
    bootstrap_distribution,
    rate_ci_block,
    summarize_bootstrap,
)
from ncaa_quant.models.calibrate import CoxCalibrationStats, cox_recalibration

DIAGNOSTIC_LABEL: str = "DIAGNOSTIC — not a model-selection input"
SliceName = Literal[
    "conference",
    "p5_g5",
    "favorite_dog",
    "totals_bucket",
    "ranked",
    "bowl",
    "rivalry",
    "weather",
]
DEFAULT_SLICE_COLUMNS: dict[SliceName, str] = {
    "conference": "conference_slice",
    "p5_g5": "p5_g5",
    "favorite_dog": "favorite_dog",
    "totals_bucket": "totals_bucket",
    "ranked": "ranked",
    "bowl": "bowl",
    "rivalry": "rivalry",
    "weather": "weather",
}
DEFAULT_INTERVAL_LEVELS: tuple[float, ...] = (0.50, 0.80, 0.95)


class MetricsError(ValueError):
    """Invalid metric inputs."""


# ---------------------------------------------------------------------------
# Primitive scorers (hand-testable)
# ---------------------------------------------------------------------------
def crps_gaussian(y_true: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> float:
    """Mean Gaussian CRPS: ``σ [z(2Φ(z)−1) + 2φ(z) − 1/√π]``, ``z=(y−μ)/σ``."""
    yt = np.asarray(y_true, dtype=float).ravel()
    m = np.asarray(mu, dtype=float).ravel()
    s = np.maximum(np.asarray(sigma, dtype=float).ravel(), 1e-8)
    if yt.size == 0:
        return float("nan")
    if not (yt.size == m.size == s.size):
        raise MetricsError("crps_gaussian: y_true, mu, sigma must share length")
    z = (yt - m) / s
    phi = stats.norm.pdf(z)
    phi_cdf = stats.norm.cdf(z)
    crps = s * (z * (2.0 * phi_cdf - 1.0) + 2.0 * phi - 1.0 / np.sqrt(np.pi))
    return float(np.mean(crps))


def crps_gaussian_per_row(y_true: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """Per-observation Gaussian CRPS (for block bootstrap)."""
    yt = np.asarray(y_true, dtype=float).ravel()
    m = np.asarray(mu, dtype=float).ravel()
    s = np.maximum(np.asarray(sigma, dtype=float).ravel(), 1e-8)
    if yt.size == 0:
        return np.asarray([], dtype=float)
    z = (yt - m) / s
    phi = stats.norm.pdf(z)
    phi_cdf = stats.norm.cdf(z)
    out: np.ndarray = s * (z * (2.0 * phi_cdf - 1.0) + 2.0 * phi - 1.0 / np.sqrt(np.pi))
    return out


def brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean Brier score ``(p − y)²`` for binary outcomes in {0, 1}."""
    p = np.asarray(probs, dtype=float).ravel()
    y = np.asarray(outcomes, dtype=float).ravel()
    mask = np.isfinite(p) & np.isfinite(y)
    if not np.any(mask):
        return float("nan")
    return float(np.mean((p[mask] - y[mask]) ** 2))


def brier_per_row(probs: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    """Per-row Brier contributions (NaN where inputs missing)."""
    p = np.asarray(probs, dtype=float).ravel()
    y = np.asarray(outcomes, dtype=float).ravel()
    out = np.full(p.shape, np.nan, dtype=float)
    mask = np.isfinite(p) & np.isfinite(y)
    out[mask] = (p[mask] - y[mask]) ** 2
    return out


def log_loss(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean Bernoulli log-loss."""
    p = np.clip(np.asarray(probs, dtype=float).ravel(), 1e-15, 1.0 - 1e-15)
    y = np.asarray(outcomes, dtype=float).ravel()
    mask = np.isfinite(p) & np.isfinite(y)
    if not np.any(mask):
        return float("nan")
    p_m, y_m = p[mask], y[mask]
    return float(-np.mean(y_m * np.log(p_m) + (1.0 - y_m) * np.log(1.0 - p_m)))


def log_loss_per_row(probs: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    """Per-row log-loss contributions."""
    p = np.clip(np.asarray(probs, dtype=float).ravel(), 1e-15, 1.0 - 1e-15)
    y = np.asarray(outcomes, dtype=float).ravel()
    out = np.full(p.shape, np.nan, dtype=float)
    mask = np.isfinite(p) & np.isfinite(y)
    out[mask] = -(y[mask] * np.log(p[mask]) + (1.0 - y[mask]) * np.log(1.0 - p[mask]))
    return out


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean absolute error."""
    yt = np.asarray(y_true, dtype=float).ravel()
    yp = np.asarray(y_pred, dtype=float).ravel()
    mask = np.isfinite(yt) & np.isfinite(yp)
    if not np.any(mask):
        return float("nan")
    return float(np.mean(np.abs(yt[mask] - yp[mask])))


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Root mean squared error."""
    yt = np.asarray(y_true, dtype=float).ravel()
    yp = np.asarray(y_pred, dtype=float).ravel()
    mask = np.isfinite(yt) & np.isfinite(yp)
    if not np.any(mask):
        return float("nan")
    return float(np.sqrt(np.mean((yt[mask] - yp[mask]) ** 2)))


def pit_values(y_true: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """Probability integral transform under Normal(μ, σ)."""
    yt = np.asarray(y_true, dtype=float).ravel()
    m = np.asarray(mu, dtype=float).ravel()
    s = np.maximum(np.asarray(sigma, dtype=float).ravel(), 1e-8)
    pit: np.ndarray = np.asarray(stats.norm.cdf((yt - m) / s), dtype=float)
    return pit


def calibration_slope_intercept(probs: np.ndarray, outcomes: np.ndarray) -> CoxCalibrationStats:
    """Cox recalibration slope/intercept (ideal: slope≈1, intercept≈0)."""
    return cox_recalibration(np.asarray(probs, dtype=float), np.asarray(outcomes, dtype=float))


def interval_coverage_and_width(
    y_true: np.ndarray,
    mu: np.ndarray,
    sigma: np.ndarray,
    *,
    levels: Sequence[float] = DEFAULT_INTERVAL_LEVELS,
) -> dict[float, dict[str, float]]:
    """Nominal Gaussian predictive-interval coverage and mean width.
    For level ``ℓ``, interval is ``μ ± z_{(1+ℓ)/2} · σ``.
    """
    yt = np.asarray(y_true, dtype=float).ravel()
    m = np.asarray(mu, dtype=float).ravel()
    s = np.maximum(np.asarray(sigma, dtype=float).ravel(), 1e-8)
    mask = np.isfinite(yt) & np.isfinite(m) & np.isfinite(s)
    yt, m, s = yt[mask], m[mask], s[mask]
    out: dict[float, dict[str, float]] = {}
    if yt.size == 0:
        for lev in levels:
            out[float(lev)] = {
                "coverage": float("nan"),
                "mean_width": float("nan"),
                "nominal": float(lev),
                "n": 0.0,
            }
        return out
    for lev in levels:
        z = float(stats.norm.ppf(0.5 + float(lev) / 2.0))
        half = z * s
        covered = (yt >= m - half) & (yt <= m + half)
        out[float(lev)] = {
            "coverage": float(np.mean(covered)),
            "mean_width": float(np.mean(2.0 * half)),
            "nominal": float(lev),
            "n": float(yt.size),
        }
    return out


# ---------------------------------------------------------------------------
# Market outcomes / accuracy
# ---------------------------------------------------------------------------
def su_outcomes(home_points: np.ndarray, away_points: np.ndarray) -> np.ndarray:
    """Straight-up home win indicators (1 home win, 0 away win; ties → NaN)."""
    h = np.asarray(home_points, dtype=float).ravel()
    a = np.asarray(away_points, dtype=float).ravel()
    out = np.full(h.shape, np.nan, dtype=float)
    decided = np.isfinite(h) & np.isfinite(a) & (h != a)
    out[decided] = (h[decided] > a[decided]).astype(float)
    return out


def ats_home_outcomes(
    realized_margin: np.ndarray,
    spread_close: np.ndarray,
) -> np.ndarray:
    """ATS home cover vs close spread (home perspective: margin + spread > 0).
    ``spread_close`` is the home spread (negative when home is favored).
    Pushes (margin + spread == 0) are NaN and excluded from accuracy.
    """
    m = np.asarray(realized_margin, dtype=float).ravel()
    sp = np.asarray(spread_close, dtype=float).ravel()
    edge = m + sp
    out = np.full(m.shape, np.nan, dtype=float)
    decided = np.isfinite(edge) & (np.abs(edge) > 1e-12)
    out[decided] = (edge[decided] > 0.0).astype(float)
    return out


def ou_over_outcomes(
    realized_total: np.ndarray,
    total_close: np.ndarray,
) -> np.ndarray:
    """Over outcomes vs closing total; pushes → NaN."""
    t = np.asarray(realized_total, dtype=float).ravel()
    line = np.asarray(total_close, dtype=float).ravel()
    edge = t - line
    out = np.full(t.shape, np.nan, dtype=float)
    decided = np.isfinite(edge) & (np.abs(edge) > 1e-12)
    out[decided] = (edge[decided] > 0.0).astype(float)
    return out


def binary_accuracy(preds_side: np.ndarray, outcomes: np.ndarray) -> float:
    """Accuracy of hard classifications ``preds_side`` (0/1) vs outcomes."""
    p = np.asarray(preds_side, dtype=float).ravel()
    y = np.asarray(outcomes, dtype=float).ravel()
    mask = np.isfinite(p) & np.isfinite(y)
    if not np.any(mask):
        return float("nan")
    return float(np.mean((p[mask] >= 0.5).astype(float) == y[mask]))


def clv_summary(clv_values: Sequence[float] | np.ndarray) -> dict[str, float]:
    """Tier-1 CLV: mean and % positive."""
    c = np.asarray(clv_values, dtype=float).ravel()
    c = c[np.isfinite(c)]
    if c.size == 0:
        return {"mean_clv": float("nan"), "pct_positive": float("nan"), "n": 0.0}
    return {
        "mean_clv": float(np.mean(c)),
        "pct_positive": float(np.mean(c > 0.0)),
        "n": float(c.size),
    }


# ---------------------------------------------------------------------------
# Aggregated metric suite
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ProbMetricPair:
    """Model metric next to the de-vigged market baseline."""

    model: float
    market: float
    delta: float
    """``model − market`` (negative is better for Brier / log-loss / CRPS)."""


@dataclass
class MetricSuite:
    """Full Tier 1–4 snapshot for one evaluation frame (point estimates)."""

    # Tier 1
    mean_clv: float = float("nan")
    pct_positive_clv: float = float("nan")
    n_clv: int = 0
    crps_margin: ProbMetricPair | None = None
    crps_total: ProbMetricPair | None = None
    logloss_ml: ProbMetricPair | None = None
    logloss_ats: ProbMetricPair | None = None
    logloss_ou: ProbMetricPair | None = None
    brier_ml: ProbMetricPair | None = None
    brier_ats: ProbMetricPair | None = None
    brier_ou: ProbMetricPair | None = None
    # Tier 2
    calibration_ml: CoxCalibrationStats | None = None
    calibration_ats: CoxCalibrationStats | None = None
    calibration_ou: CoxCalibrationStats | None = None
    pit_margin: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    pit_total: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    interval_margin: dict[float, dict[str, float]] = field(default_factory=dict)
    interval_total: dict[float, dict[str, float]] = field(default_factory=dict)
    # Tier 3
    mae_margin: float = float("nan")
    rmse_margin: float = float("nan")
    mae_total: float = float("nan")
    rmse_total: float = float("nan")
    ats_accuracy: float = float("nan")
    ou_accuracy: float = float("nan")
    su_accuracy: float = float("nan")
    n_games: int = 0
    season: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_rows(self) -> list[dict[str, Any]]:
        """Flatten into report-table rows (model + market where applicable)."""
        rows: list[dict[str, Any]] = []

        def add(
            name: str,
            value: float,
            *,
            market: float | None = None,
            tier: int,
            notes: str = "",
        ) -> None:
            rows.append(
                {
                    "metric": name,
                    "tier": tier,
                    "model": value,
                    "market": market if market is not None else float("nan"),
                    "delta": (
                        float(value - market)
                        if market is not None and np.isfinite(value) and np.isfinite(market)
                        else float("nan")
                    ),
                    "notes": notes,
                }
            )

        add("mean_clv", self.mean_clv, tier=1, notes=f"n={self.n_clv}")
        add("pct_positive_clv", self.pct_positive_clv, tier=1, notes=f"n={self.n_clv}")
        for label, pair in (
            ("crps_margin", self.crps_margin),
            ("crps_total", self.crps_total),
            ("logloss_ml", self.logloss_ml),
            ("logloss_ats", self.logloss_ats),
            ("logloss_ou", self.logloss_ou),
            ("brier_ml", self.brier_ml),
            ("brier_ats", self.brier_ats),
            ("brier_ou", self.brier_ou),
        ):
            if pair is not None:
                add(label, pair.model, market=pair.market, tier=1)
        for label, cal in (
            ("calibration_ml", self.calibration_ml),
            ("calibration_ats", self.calibration_ats),
            ("calibration_ou", self.calibration_ou),
        ):
            if cal is not None:
                add(f"{label}_slope", cal.slope, tier=2, notes=f"n={cal.n}")
                add(f"{label}_intercept", cal.intercept, tier=2, notes=f"n={cal.n}")
        for lev, stats_d in sorted(self.interval_margin.items()):
            add(
                f"interval_margin_{int(100 * lev)}",
                stats_d["coverage"],
                tier=2,
                notes=f"width={stats_d['mean_width']:.3f}",
            )
        for lev, stats_d in sorted(self.interval_total.items()):
            add(
                f"interval_total_{int(100 * lev)}",
                stats_d["coverage"],
                tier=2,
                notes=f"width={stats_d['mean_width']:.3f}",
            )
        add("mae_margin", self.mae_margin, tier=3)
        add("rmse_margin", self.rmse_margin, tier=3)
        add("mae_total", self.mae_total, tier=3)
        add("rmse_total", self.rmse_total, tier=3)
        add("ats_accuracy", self.ats_accuracy, tier=3)
        add("ou_accuracy", self.ou_accuracy, tier=3)
        add("su_accuracy", self.su_accuracy, tier=3)
        return rows


def _pair(model: float, market: float) -> ProbMetricPair:
    delta = float(model - market) if np.isfinite(model) and np.isfinite(market) else float("nan")
    return ProbMetricPair(model=float(model), market=float(market), delta=delta)


def _col(frame: pd.DataFrame, name: str) -> np.ndarray | None:
    if name not in frame.columns:
        return None
    return np.asarray(frame[name], dtype=float)


def _require(frame: pd.DataFrame, cols: Sequence[str]) -> None:
    missing = [c for c in cols if c not in frame.columns]
    if missing:
        raise MetricsError(f"evaluation frame missing columns: {missing}")


# Status strings for market baseline resolution (DESIGN §7.3).
MARKET_ML_DEVIGGED_AMERICAN: str = "devigged_american"
MARKET_ML_PROVIDED: str = "provided_finite"
MARKET_ML_NOT_COMPUTED: str = "NOT_COMPUTED"
MARKET_ATS_FAIR_MINUS_110: str = "fair_minus_110"
MARKET_OU_FAIR_MINUS_110: str = "fair_minus_110"
MARKET_SIDE_PROVIDED: str = "provided_finite"
MARKET_SIDE_NOT_COMPUTED: str = "NOT_COMPUTED"

# Coin-flip constant: ln(2) log-loss. Never report this as a de-vigged ML market.
_COIN_FLIP_TOL: float = 1e-6


@dataclass(frozen=True, slots=True)
class MarketBaselineResolution:
    """Resolved market probabilities plus provenance for each derived market."""

    p_mkt_ml_home: np.ndarray | None
    p_mkt_ats_home: np.ndarray | None
    p_mkt_ou_over: np.ndarray | None
    ml_status: str
    ml_reason: str
    ats_status: str
    ats_reason: str
    ou_status: str
    ou_reason: str
    n_ml_finite: int = 0


def _is_coin_flip_constant(p: np.ndarray) -> bool:
    """True when every finite entry is ≈0.5 (strawman, not a moneyline market)."""
    finite = p[np.isfinite(p)]
    if finite.size == 0:
        return False
    return bool(np.all(np.abs(finite - 0.5) <= _COIN_FLIP_TOL))


def _devig_home_ml_from_american(
    home_ml: np.ndarray,
    away_ml: np.ndarray,
) -> np.ndarray:
    """Proportional de-vig of two-way American moneylines → fair P(home)."""
    from ncaa_quant.betting.devig import DevigError, american_to_raw_implied, proportional_devig

    out = np.full(home_ml.shape, np.nan, dtype=float)
    for i in range(home_ml.size):
        h, a = float(home_ml[i]), float(away_ml[i])
        if not (np.isfinite(h) and np.isfinite(a)):
            continue
        try:
            q_h = american_to_raw_implied(h)
            q_a = american_to_raw_implied(a)
            fair = proportional_devig([q_h, q_a])
            out[i] = float(fair[0])
        except (DevigError, ValueError, ZeroDivisionError):
            continue
    return out


def resolve_market_baselines(frame: pd.DataFrame) -> MarketBaselineResolution:
    """Resolve de-vigged market baselines for ML / ATS / OU.

    Moneyline (DESIGN §7.3): must come from real closing prices — preferentially
    American ``home_ml`` / ``away_ml`` (proportional de-vig), else a finite
    caller-supplied ``p_mkt_ml_home`` that is *not* a constant-0.5 strawman.
    A constant 0.5 (or all-null) ML baseline is struck as ``NOT_COMPUTED``;
    reporting it as the market is the same error as reporting CLV≡0.

    ATS / OU at −110/−110: fair probability is 0.5 by construction. That
    constant is valid for those markets and is *not* treated as a strawman.
    """
    n = len(frame)
    # --- Moneyline ---
    ml_status = MARKET_ML_NOT_COMPUTED
    ml_reason = (
        "no finite de-vigged closing moneyline prices on the evaluation frame "
        "(need home_ml/away_ml American odds, or a non-constant p_mkt_ml_home)"
    )
    p_mkt_ml: np.ndarray | None = None
    n_ml = 0

    home_ml = _col(frame, "home_ml")
    away_ml = _col(frame, "away_ml")
    if home_ml is not None and away_ml is not None:
        derived = _devig_home_ml_from_american(home_ml, away_ml)
        n_ml = int(np.isfinite(derived).sum())
        if n_ml > 0:
            p_mkt_ml = derived
            ml_status = MARKET_ML_DEVIGGED_AMERICAN
            ml_reason = (
                f"proportional de-vig of home_ml/away_ml American odds (n_finite={n_ml}/{n})"
            )

    if p_mkt_ml is None:
        provided = _col(frame, "p_mkt_ml_home")
        if provided is not None:
            n_fin = int(np.isfinite(provided).sum())
            if n_fin == 0:
                ml_reason = (
                    "p_mkt_ml_home present but all-null "
                    "(typical when spread_close is null for seasons without "
                    "Odds snapshots and CFBD close is excluded from the line ladder)"
                )
            elif _is_coin_flip_constant(provided):
                ml_reason = (
                    "p_mkt_ml_home is constant 0.5 (ln(2)~0.693 log-loss) - "
                    "that is a coin-flip strawman, not a de-vigged moneyline market; "
                    "struck as NOT_COMPUTED"
                )
            else:
                p_mkt_ml = provided
                n_ml = n_fin
                ml_status = MARKET_ML_PROVIDED
                ml_reason = f"caller-supplied finite p_mkt_ml_home (n_finite={n_ml}/{n})"

    # --- ATS ---
    # Equal-juice two-way → fair 0.5. Prefer caller-supplied finite probs.
    ats_provided = _col(frame, "p_mkt_ats_home")
    if ats_provided is not None and int(np.isfinite(ats_provided).sum()) > 0:
        p_mkt_ats: np.ndarray | None = ats_provided
        ats_status = MARKET_SIDE_PROVIDED
        ats_reason = "caller-supplied p_mkt_ats_home (fair ~0.5 at -110/-110 is expected)"
    else:
        p_mkt_ats = np.full(n, 0.5, dtype=float) if n else None
        ats_status = MARKET_ATS_FAIR_MINUS_110
        ats_reason = "default fair 0.5 for -110/-110 ATS (not a moneyline baseline)"

    # --- OU ---
    ou_provided = _col(frame, "p_mkt_ou_over")
    if ou_provided is not None and int(np.isfinite(ou_provided).sum()) > 0:
        p_mkt_ou: np.ndarray | None = ou_provided
        ou_status = MARKET_SIDE_PROVIDED
        ou_reason = "caller-supplied p_mkt_ou_over (fair ~0.5 at -110/-110 is expected)"
    else:
        p_mkt_ou = np.full(n, 0.5, dtype=float) if n else None
        ou_status = MARKET_OU_FAIR_MINUS_110
        ou_reason = "default fair 0.5 for -110/-110 OU (not a moneyline baseline)"

    return MarketBaselineResolution(
        p_mkt_ml_home=p_mkt_ml,
        p_mkt_ats_home=p_mkt_ats,
        p_mkt_ou_over=p_mkt_ou,
        ml_status=ml_status,
        ml_reason=ml_reason,
        ats_status=ats_status,
        ats_reason=ats_reason,
        ou_status=ou_status,
        ou_reason=ou_reason,
        n_ml_finite=n_ml,
    )


def compute_metric_suite(
    predictions: pd.DataFrame,
    *,
    bets: pd.DataFrame | None = None,
    market_crps_margin: float | None = None,
    market_crps_total: float | None = None,
) -> MetricSuite:
    """Compute the full Tier 1–3 suite on a predictions frame (+ optional bets).
    Expected prediction columns (subset ok; missing markets skip those rows):
    ``pred_margin``, ``pred_total``, ``sigma_m``, ``sigma_t``,
    ``realized_margin``, ``realized_total``, ``home_points``, ``away_points``,
    ``spread_close``, ``total_close``,
    ``p_ml_home``, ``p_ats_home``, ``p_ou_over``,
    ``p_mkt_ml_home``, ``p_mkt_ats_home``, ``p_mkt_ou_over``,
    and optionally ``home_ml`` / ``away_ml`` (American) for de-vigged ML baseline.
    Optional ``bets`` frame with ``clv`` for Tier-1 CLV.
    Market CRPS: if the market does not publish a full predictive distribution,
    pass ``market_crps_*`` explicitly (e.g. CRPS of a Normal centered on the
    closing line with historical residual σ). When omitted, market CRPS is
    NaN but still present in the pair for schema uniformity.

    Moneyline market baseline: resolved via :func:`resolve_market_baselines`.
    A constant-0.5 / all-null ML column is **NOT COMPUTED** (market=NaN), never
    reported as ln(2).
    """
    frame = predictions
    if "exclude_from_headline" in frame.columns:
        frame = frame.loc[~frame["exclude_from_headline"].fillna(False).astype(bool)].copy()
    suite = MetricSuite(n_games=int(len(frame)))
    if "season" in frame.columns and len(frame):
        seasons = frame["season"].dropna().unique()
        if len(seasons) == 1:
            suite.season = int(seasons[0])
    # --- CLV from bets ---
    if bets is not None and len(bets) and "clv" in bets.columns:
        clv = clv_summary(bets["clv"].to_numpy())
        suite.mean_clv = clv["mean_clv"]
        suite.pct_positive_clv = clv["pct_positive"]
        suite.n_clv = int(clv["n"])
    if len(frame) == 0:
        return suite
    # --- Continuous ---
    rm = _col(frame, "realized_margin")
    rt = _col(frame, "realized_total")
    pm = _col(frame, "pred_margin")
    pt = _col(frame, "pred_total")
    sm = _col(frame, "sigma_m")
    st = _col(frame, "sigma_t")
    if rm is not None and pm is not None:
        suite.mae_margin = mae(rm, pm)
        suite.rmse_margin = rmse(rm, pm)
        if sm is not None:
            suite.crps_margin = _pair(
                crps_gaussian(rm, pm, sm),
                float(market_crps_margin) if market_crps_margin is not None else float("nan"),
            )
            suite.pit_margin = pit_values(rm, pm, sm)
            suite.interval_margin = interval_coverage_and_width(rm, pm, sm)
    if rt is not None and pt is not None:
        suite.mae_total = mae(rt, pt)
        suite.rmse_total = rmse(rt, pt)
        if st is not None:
            suite.crps_total = _pair(
                crps_gaussian(rt, pt, st),
                float(market_crps_total) if market_crps_total is not None else float("nan"),
            )
            suite.pit_total = pit_values(rt, pt, st)
            suite.interval_total = interval_coverage_and_width(rt, pt, st)
    # --- Probabilistic vs market (resolved; ML never coin-flip strawman) ---
    mkt = resolve_market_baselines(frame)
    suite.extras["market_baseline"] = {
        "ml_status": mkt.ml_status,
        "ml_reason": mkt.ml_reason,
        "ats_status": mkt.ats_status,
        "ats_reason": mkt.ats_reason,
        "ou_status": mkt.ou_status,
        "ou_reason": mkt.ou_reason,
        "n_ml_finite": mkt.n_ml_finite,
    }
    y_su = None
    if "home_points" in frame.columns and "away_points" in frame.columns:
        y_su = su_outcomes(frame["home_points"].to_numpy(), frame["away_points"].to_numpy())
    y_ats = None
    if rm is not None and "spread_close" in frame.columns:
        y_ats = ats_home_outcomes(rm, frame["spread_close"].to_numpy())
    y_ou = None
    if rt is not None and "total_close" in frame.columns:
        y_ou = ou_over_outcomes(rt, frame["total_close"].to_numpy())
    p_ml = _col(frame, "p_ml_home")
    p_mkt_ml = mkt.p_mkt_ml_home
    if p_ml is not None and y_su is not None:
        suite.logloss_ml = _pair(
            log_loss(p_ml, y_su),
            log_loss(p_mkt_ml, y_su) if p_mkt_ml is not None else float("nan"),
        )
        suite.brier_ml = _pair(
            brier_score(p_ml, y_su),
            brier_score(p_mkt_ml, y_su) if p_mkt_ml is not None else float("nan"),
        )
        suite.calibration_ml = calibration_slope_intercept(p_ml, y_su)
        suite.su_accuracy = binary_accuracy(p_ml, y_su)
    p_ats = _col(frame, "p_ats_home")
    p_mkt_ats = mkt.p_mkt_ats_home
    if p_ats is not None and y_ats is not None:
        suite.logloss_ats = _pair(
            log_loss(p_ats, y_ats),
            log_loss(p_mkt_ats, y_ats) if p_mkt_ats is not None else float("nan"),
        )
        suite.brier_ats = _pair(
            brier_score(p_ats, y_ats),
            brier_score(p_mkt_ats, y_ats) if p_mkt_ats is not None else float("nan"),
        )
        suite.calibration_ats = calibration_slope_intercept(p_ats, y_ats)
        suite.ats_accuracy = binary_accuracy(p_ats, y_ats)
    p_ou = _col(frame, "p_ou_over")
    p_mkt_ou = mkt.p_mkt_ou_over
    if p_ou is not None and y_ou is not None:
        suite.logloss_ou = _pair(
            log_loss(p_ou, y_ou),
            log_loss(p_mkt_ou, y_ou) if p_mkt_ou is not None else float("nan"),
        )
        suite.brier_ou = _pair(
            brier_score(p_ou, y_ou),
            brier_score(p_mkt_ou, y_ou) if p_mkt_ou is not None else float("nan"),
        )
        suite.calibration_ou = calibration_slope_intercept(p_ou, y_ou)
        suite.ou_accuracy = binary_accuracy(p_ou, y_ou)
    return suite


# ---------------------------------------------------------------------------
# Slice analysis (§7.2 item 3) — DIAGNOSTIC
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SliceTable:
    """Heatmap-ready slice metrics, explicitly labeled diagnostic."""

    label: str = DIAGNOSTIC_LABEL
    table: pd.DataFrame = field(default_factory=pd.DataFrame)

    def __post_init__(self) -> None:
        # Ensure the diagnostic banner is always present on the frame metadata.
        object.__setattr__(self, "label", DIAGNOSTIC_LABEL)


def build_slice_table(
    predictions: pd.DataFrame,
    *,
    slice_columns: Mapping[SliceName, str] | None = None,
    metric: Literal["mae_margin", "brier_ml", "ats_accuracy"] = "mae_margin",
) -> SliceTable:
    """Per-slice diagnostic table (conference, P5/G5, favorite/dog, …).
    Output columns: ``slice_family``, ``slice_value``, ``n``, ``metric``,
    ``value``, ``diagnostic_label``. Suitable for heatmap pivoting on
    ``(slice_family, slice_value)``.
    """
    cols = dict(DEFAULT_SLICE_COLUMNS)
    if slice_columns is not None:
        cols.update(dict(slice_columns))
    frame = predictions
    if "exclude_from_headline" in frame.columns:
        frame = frame.loc[~frame["exclude_from_headline"].fillna(False).astype(bool)]
    rows: list[dict[str, Any]] = []
    for family, col in cols.items():
        if col not in frame.columns:
            continue
        for value, grp in frame.groupby(frame[col].astype(str), dropna=False):
            if metric == "mae_margin":
                if "realized_margin" not in grp.columns or "pred_margin" not in grp.columns:
                    continue
                val = mae(grp["realized_margin"].to_numpy(), grp["pred_margin"].to_numpy())
            elif metric == "brier_ml":
                if "p_ml_home" not in grp.columns:
                    continue
                y = su_outcomes(grp["home_points"].to_numpy(), grp["away_points"].to_numpy())
                val = brier_score(grp["p_ml_home"].to_numpy(), y)
            else:  # ats_accuracy
                if "p_ats_home" not in grp.columns or "spread_close" not in grp.columns:
                    continue
                y_ats = ats_home_outcomes(
                    grp["realized_margin"].to_numpy(), grp["spread_close"].to_numpy()
                )
                val = binary_accuracy(grp["p_ats_home"].to_numpy(), y_ats)
            rows.append(
                {
                    "slice_family": family,
                    "slice_value": value,
                    "n": int(len(grp)),
                    "metric": metric,
                    "value": float(val),
                    "diagnostic_label": DIAGNOSTIC_LABEL,
                }
            )
    table = pd.DataFrame(rows)
    if len(table):
        table = table.sort_values(["slice_family", "slice_value"]).reset_index(drop=True)
    return SliceTable(table=table)


def weekly_error_curve(
    predictions: pd.DataFrame,
    *,
    target: Literal["margin", "total"] = "margin",
) -> pd.DataFrame:
    """MAE by week-of-season (within-season curves per §7.2 item 2)."""
    frame = predictions
    if "exclude_from_headline" in frame.columns:
        frame = frame.loc[~frame["exclude_from_headline"].fillna(False).astype(bool)]
    _require(frame, ["week", f"pred_{target}", f"realized_{target}"])
    rows: list[dict[str, Any]] = []
    for week, grp in frame.groupby("week"):
        yt = grp[f"realized_{target}"].to_numpy()
        yp = grp[f"pred_{target}"].to_numpy()
        rows.append(
            {
                "week": int(week),
                "n": int(len(grp)),
                "mae": mae(yt, yp),
                "rmse": rmse(yt, yp),
                "target": target,
            }
        )
    return pd.DataFrame(rows).sort_values("week").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Tier 4 — economic simulation
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class BankrollPath:
    """Simulated bankroll trajectory for one staking policy."""

    policy: str
    stakes: np.ndarray
    pnl: np.ndarray
    bankroll: np.ndarray
    roi: float
    max_drawdown: float
    sharpe_per_bet: float


@dataclass(frozen=True, slots=True)
class EconomicSimulation:
    """Flat-stake + fractional-Kelly bankroll sims with bootstrap CIs."""

    flat: BankrollPath
    quarter_kelly: BankrollPath
    half_kelly: BankrollPath
    roi_ci_flat: ConfidenceInterval
    roi_ci_quarter: ConfidenceInterval
    roi_ci_half: ConfidenceInterval
    max_drawdown_ci_flat: ConfidenceInterval
    max_drawdown_ci_quarter: ConfidenceInterval
    max_drawdown_ci_half: ConfidenceInterval
    max_drawdown_distribution_quarter: np.ndarray


def _american_profit_per_unit(american: float, won: bool) -> float:
    """Profit on a 1-unit stake at American odds (lose → −1)."""
    if not won:
        return -1.0
    a = float(american)
    if a > 0:
        return a / 100.0
    return 100.0 / (-a)


def _max_drawdown(bankroll: np.ndarray) -> float:
    """Max peak-to-trough drawdown as a fraction of the running peak."""
    if bankroll.size == 0:
        return float("nan")
    peak = np.maximum.accumulate(bankroll)
    dd = (peak - bankroll) / np.maximum(peak, 1e-12)
    return float(np.max(dd))


def _sharpe_per_bet(pnl: np.ndarray) -> float:
    """Mean/std of per-bet PnL (Sharpe-like; not annualized)."""
    x = np.asarray(pnl, dtype=float).ravel()
    if x.size < 2:
        return float("nan")
    sd = float(np.std(x, ddof=1))
    if sd < 1e-15:
        return float("nan")
    return float(np.mean(x) / sd)


def _simulate_flat(
    won: np.ndarray,
    american: np.ndarray,
    *,
    unit: float = 1.0,
    initial_bankroll: float = 100.0,
) -> BankrollPath:
    n = int(won.size)
    stakes = np.full(n, unit, dtype=float)
    pnl = np.asarray(
        [_american_profit_per_unit(american[i], bool(won[i])) * unit for i in range(n)],
        dtype=float,
    )
    bankroll = initial_bankroll + np.cumsum(pnl)
    total_staked = float(np.sum(stakes))
    roi = float(np.sum(pnl) / total_staked) if total_staked > 0 else float("nan")
    return BankrollPath(
        policy="flat",
        stakes=stakes,
        pnl=pnl,
        bankroll=np.concatenate([[initial_bankroll], bankroll]),
        roi=roi,
        max_drawdown=_max_drawdown(np.concatenate([[initial_bankroll], bankroll])),
        sharpe_per_bet=_sharpe_per_bet(pnl),
    )


def _full_kelly_fraction(p_win: float, american: float) -> float:
    a = float(american)
    b = a / 100.0 if a > 0 else 100.0 / (-a)
    q = 1.0 - float(p_win)
    f = (b * float(p_win) - q) / b
    return float(max(0.0, f))


def _simulate_kelly(
    won: np.ndarray,
    american: np.ndarray,
    p_win: np.ndarray,
    *,
    kelly_fraction: float,
    initial_bankroll: float = 100.0,
    policy: str,
) -> BankrollPath:
    n = int(won.size)
    bank = float(initial_bankroll)
    path = [bank]
    stakes = np.zeros(n, dtype=float)
    pnl = np.zeros(n, dtype=float)
    for i in range(n):
        f_full = _full_kelly_fraction(float(p_win[i]), float(american[i]))
        frac = min(kelly_fraction * f_full, 0.015)  # hard 1.5% reporting guard
        stake = frac * bank
        profit_units = _american_profit_per_unit(float(american[i]), bool(won[i]))
        profit = stake * profit_units
        stakes[i] = stake
        pnl[i] = profit
        bank = bank + profit
        path.append(bank)
    total_staked = float(np.sum(stakes))
    roi = float(np.sum(pnl) / total_staked) if total_staked > 0 else float("nan")
    br = np.asarray(path, dtype=float)
    return BankrollPath(
        policy=policy,
        stakes=stakes,
        pnl=pnl,
        bankroll=br,
        roi=roi,
        max_drawdown=_max_drawdown(br),
        sharpe_per_bet=_sharpe_per_bet(pnl),
    )


def simulate_economics(
    bets: pd.DataFrame,
    *,
    initial_bankroll: float = 100.0,
    flat_unit: float = 1.0,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
) -> EconomicSimulation:
    """Flat-stake + ¼/½-Kelly bankroll paths with block-bootstrap CIs.
    Required columns: ``won`` (bool/0-1), ``american_odds``, ``week``.
    Kelly paths also need ``p_win``. Optional ``season`` ignored for blocking
    (week blocks within the provided frame).
    """
    _require(bets, ["won", "american_odds", "week"])
    if len(bets) == 0:
        empty = BankrollPath(
            policy="empty",
            stakes=np.asarray([], dtype=float),
            pnl=np.asarray([], dtype=float),
            bankroll=np.asarray([initial_bankroll], dtype=float),
            roi=float("nan"),
            max_drawdown=float("nan"),
            sharpe_per_bet=float("nan"),
        )
        nan_ci = ConfidenceInterval(
            estimate=float("nan"),
            ci_low=float("nan"),
            ci_high=float("nan"),
            n=0,
            alpha=alpha,
            method="block_bootstrap",
        )
        return EconomicSimulation(
            flat=empty,
            quarter_kelly=empty,
            half_kelly=empty,
            roi_ci_flat=nan_ci,
            roi_ci_quarter=nan_ci,
            roi_ci_half=nan_ci,
            max_drawdown_ci_flat=nan_ci,
            max_drawdown_ci_quarter=nan_ci,
            max_drawdown_ci_half=nan_ci,
            max_drawdown_distribution_quarter=np.asarray([], dtype=float),
        )
    won = np.asarray(bets["won"], dtype=float).ravel() > 0.5
    american = np.asarray(bets["american_odds"], dtype=float).ravel()
    weeks = list(bets["week"].tolist())
    p_win = (
        np.asarray(bets["p_win"], dtype=float).ravel()
        if "p_win" in bets.columns
        else np.full(won.shape, 0.55, dtype=float)
    )
    flat = _simulate_flat(won, american, unit=flat_unit, initial_bankroll=initial_bankroll)
    qk = _simulate_kelly(
        won,
        american,
        p_win,
        kelly_fraction=0.25,
        initial_bankroll=initial_bankroll,
        policy="quarter_kelly",
    )
    hk = _simulate_kelly(
        won,
        american,
        p_win,
        kelly_fraction=0.50,
        initial_bankroll=initial_bankroll,
        policy="half_kelly",
    )
    # Bootstrap ROI on per-bet returns (profit / stake for flat; profit/stake for kelly).
    flat_ret = flat.pnl / np.maximum(flat.stakes, 1e-12)
    qk_ret = qk.pnl / np.maximum(qk.stakes, 1e-12)
    hk_ret = hk.pnl / np.maximum(hk.stakes, 1e-12)
    roi_ci_flat = block_bootstrap(flat_ret, weeks, n_boot=n_boot, alpha=alpha, seed=seed)
    roi_ci_quarter = block_bootstrap(qk_ret, weeks, n_boot=n_boot, alpha=alpha, seed=seed + 1)
    roi_ci_half = block_bootstrap(hk_ret, weeks, n_boot=n_boot, alpha=alpha, seed=seed + 2)
    dd_flat_reps = bootstrap_distribution(
        flat.pnl,
        weeks,
        statistic=lambda arr: _max_drawdown(
            np.concatenate([[initial_bankroll], initial_bankroll + np.cumsum(arr)])
        ),
        n_boot=n_boot,
        seed=seed + 10,
        block=True,
    )
    dd_q_reps = bootstrap_distribution(
        qk.pnl,
        weeks,
        statistic=lambda arr: _max_drawdown(
            np.concatenate([[initial_bankroll], initial_bankroll + np.cumsum(arr)])
        ),
        n_boot=n_boot,
        seed=seed + 11,
        block=True,
    )
    dd_h_reps = bootstrap_distribution(
        hk.pnl,
        weeks,
        statistic=lambda arr: _max_drawdown(
            np.concatenate([[initial_bankroll], initial_bankroll + np.cumsum(arr)])
        ),
        n_boot=n_boot,
        seed=seed + 12,
        block=True,
    )
    return EconomicSimulation(
        flat=flat,
        quarter_kelly=qk,
        half_kelly=hk,
        roi_ci_flat=ConfidenceInterval(
            estimate=flat.roi,
            ci_low=roi_ci_flat.ci_low,
            ci_high=roi_ci_flat.ci_high,
            n=roi_ci_flat.n,
            alpha=alpha,
            method="block_bootstrap",
        ),
        roi_ci_quarter=ConfidenceInterval(
            estimate=qk.roi,
            ci_low=roi_ci_quarter.ci_low,
            ci_high=roi_ci_quarter.ci_high,
            n=roi_ci_quarter.n,
            alpha=alpha,
            method="block_bootstrap",
        ),
        roi_ci_half=ConfidenceInterval(
            estimate=hk.roi,
            ci_low=roi_ci_half.ci_low,
            ci_high=roi_ci_half.ci_high,
            n=roi_ci_half.n,
            alpha=alpha,
            method="block_bootstrap",
        ),
        max_drawdown_ci_flat=summarize_bootstrap(
            dd_flat_reps, point=flat.max_drawdown, n=int(won.size), alpha=alpha
        ),
        max_drawdown_ci_quarter=summarize_bootstrap(
            dd_q_reps, point=qk.max_drawdown, n=int(won.size), alpha=alpha
        ),
        max_drawdown_ci_half=summarize_bootstrap(
            dd_h_reps, point=hk.max_drawdown, n=int(won.size), alpha=alpha
        ),
        max_drawdown_distribution_quarter=dd_q_reps,
    )


def rate_with_block_ci(
    outcomes: Sequence[float] | np.ndarray,
    weeks: Sequence[Any],
    *,
    label: str,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
) -> RateWithCI:
    """Convenience: block-bootstrap a rate into the anti-metric type."""
    return rate_ci_block(outcomes, weeks, n_boot=n_boot, alpha=alpha, seed=seed, label=label)


def naive_proportion_ci(
    outcomes: Sequence[float] | np.ndarray,
    *,
    label: str = "",
    alpha: float = DEFAULT_ALPHA,
) -> RateWithCI:
    """Normal-approximation Wald interval for a binary rate (side-by-side with bootstrap)."""
    y = np.asarray(outcomes, dtype=float).ravel()
    y = y[np.isfinite(y)]
    n = int(y.size)
    if n == 0:
        raise MetricsError("naive_proportion_ci: empty outcomes")
    rate = float(np.mean(y))
    z = float(stats.norm.ppf(1.0 - alpha / 2.0))
    se = float(np.sqrt(max(rate * (1.0 - rate), 1e-16) / n))
    return RateWithCI(
        rate=rate,
        ci_low=float(max(0.0, rate - z * se)),
        ci_high=float(min(1.0, rate + z * se)),
        n=n,
        label=label,
        alpha=alpha,
    )


def attach_metric_cis(
    suite: MetricSuite,
    predictions: pd.DataFrame,
    *,
    bets: pd.DataFrame | None = None,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
) -> dict[str, ConfidenceInterval | RateWithCI]:
    """Block-bootstrap CIs for rate and key loss metrics on the suite.

    For every headline proportion, also attaches a ``*_naive`` Wald interval
    so coverage can be compared (Task 23-FIX P2-6). Confidence level is
    ``1 - alpha`` (default 95%). Blocks are whole week-label groups with
    variable length (games or bets per week).
    """
    frame = predictions
    if "exclude_from_headline" in frame.columns:
        frame = frame.loc[~frame["exclude_from_headline"].fillna(False).astype(bool)]
    if "week" not in frame.columns:
        raise MetricsError("predictions need a 'week' column for block bootstrap")
    weeks = list(frame["week"].tolist())
    out: dict[str, ConfidenceInterval | RateWithCI] = {}
    if suite.crps_margin is not None and {"realized_margin", "pred_margin", "sigma_m"} <= set(
        frame.columns
    ):
        per = crps_gaussian_per_row(
            frame["realized_margin"].to_numpy(),
            frame["pred_margin"].to_numpy(),
            frame["sigma_m"].to_numpy(),
        )
        out["crps_margin"] = block_bootstrap(per, weeks, n_boot=n_boot, alpha=alpha, seed=seed)
    if "p_ats_home" in frame.columns and "spread_close" in frame.columns:
        y_ats = ats_home_outcomes(
            frame["realized_margin"].to_numpy(), frame["spread_close"].to_numpy()
        )
        p = frame["p_ats_home"].to_numpy()
        hits = ((p >= 0.5).astype(float) == y_ats).astype(float)
        hits[~np.isfinite(y_ats)] = np.nan
        mask = np.isfinite(hits)
        hit_vals = hits[mask]
        week_vals = [weeks[i] for i, m in enumerate(mask) if m]
        if hit_vals.size >= 2 and len(set(week_vals)) >= 1:
            out["ats_accuracy"] = rate_ci_block(
                hit_vals,
                week_vals,
                n_boot=n_boot,
                alpha=alpha,
                seed=seed + 1,
                label="ATS accuracy",
            )
            out["ats_accuracy_naive"] = naive_proportion_ci(
                hit_vals, label="ATS accuracy (naive Wald)", alpha=alpha
            )
    if bets is not None and len(bets) and "clv" in bets.columns and "week" in bets.columns:
        # Degenerate CLV (identically zero) must not be reported as a finding.
        clv = bets["clv"].to_numpy(dtype=float)
        if np.all(np.isfinite(clv)) and float(np.nanmax(clv) - np.nanmin(clv)) < 1e-15:
            raise MetricsError(
                "CLV NOT COMPUTED: bet-time and closing prices resolve to the same "
                "instrument (zero variance by construction)"
            )
        bw = list(bets["week"].tolist())
        out["mean_clv"] = block_bootstrap(clv, bw, n_boot=n_boot, alpha=alpha, seed=seed + 2)
        pos = (clv > 0.0).astype(float)
        out["pct_positive_clv"] = rate_ci_block(
            pos, bw, n_boot=n_boot, alpha=alpha, seed=seed + 3, label="% positive CLV"
        )
        out["pct_positive_clv_naive"] = naive_proportion_ci(
            pos, label="% positive CLV (naive Wald)", alpha=alpha
        )
    return out


def reliability_bins(
    probs: np.ndarray,
    outcomes: np.ndarray,
    *,
    n_bins: int = 10,
) -> pd.DataFrame:
    """10-bin reliability diagram data (§7.3 Tier 2)."""
    p = np.asarray(probs, dtype=float).ravel()
    y = np.asarray(outcomes, dtype=float).ravel()
    mask = np.isfinite(p) & np.isfinite(y)
    p, y = p[mask], y[mask]
    if p.size == 0:
        return pd.DataFrame(columns=["bin_left", "bin_right", "mean_pred", "mean_outcome", "count"])
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows: list[dict[str, Any]] = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        sel = (p >= lo) & (p <= hi) if i == n_bins - 1 else (p >= lo) & (p < hi)
        if not np.any(sel):
            continue
        rows.append(
            {
                "bin_left": float(lo),
                "bin_right": float(hi),
                "mean_pred": float(np.mean(p[sel])),
                "mean_outcome": float(np.mean(y[sel])),
                "count": int(np.sum(sel)),
            }
        )
    return pd.DataFrame(rows)
