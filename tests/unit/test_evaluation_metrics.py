"""Unit tests for evaluation metrics, significance, and reports (Task 21)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from ncaa_quant.evaluation.metrics import (
    DIAGNOSTIC_LABEL,
    brier_score,
    build_slice_table,
    compute_metric_suite,
    crps_gaussian,
    log_loss,
    simulate_economics,
)
from ncaa_quant.evaluation.reports import (
    BacktestReportInput,
    WeeklyReportInput,
    build_backtest_artifacts,
    render_backtest_report,
    write_backtest_report,
    write_weekly_report,
)
from ncaa_quant.evaluation.significance import (
    BareRateError,
    RateWithCI,
    block_bootstrap,
    ci_width,
    format_rate_with_ci,
    iid_bootstrap,
    paired_block_bootstrap,
)

# ---------------------------------------------------------------------------
# Hand-computed CRPS / Brier fixtures
# ---------------------------------------------------------------------------


def test_crps_gaussian_hand_computed_standard_normal_at_mean() -> None:
    # CRPS(N(0,1), y=0) = 2φ(0) − 1/√π = √(2/π) − 1/√π = (√2 − 1)/√π
    expected = (np.sqrt(2.0) - 1.0) / np.sqrt(np.pi)
    got = crps_gaussian(np.array([0.0]), np.array([0.0]), np.array([1.0]))
    assert got == pytest.approx(expected, rel=1e-12)


def test_crps_gaussian_hand_computed_shifted() -> None:
    # y=1, μ=0, σ=1: z=1
    z = 1.0
    phi = float(norm.pdf(z))
    Phi = float(norm.cdf(z))
    expected = z * (2.0 * Phi - 1.0) + 2.0 * phi - 1.0 / np.sqrt(np.pi)
    got = crps_gaussian(np.array([1.0]), np.array([0.0]), np.array([1.0]))
    assert got == pytest.approx(expected, rel=1e-12)


def test_brier_score_hand_computed() -> None:
    # p=[0.7, 0.2], y=[1, 0] → (0.3² + 0.2²)/2 = 0.065
    assert brier_score(np.array([0.7, 0.2]), np.array([1.0, 0.0])) == pytest.approx(0.065)


def test_log_loss_hand_computed() -> None:
    assert log_loss(np.array([0.75]), np.array([1.0])) == pytest.approx(-np.log(0.75))


# ---------------------------------------------------------------------------
# Block bootstrap vs iid on correlated data
# ---------------------------------------------------------------------------


def test_block_bootstrap_wider_than_iid_on_correlated_data() -> None:
    rng = np.random.default_rng(42)
    # 20 weeks × 8 games; strong within-week shared shock
    weeks: list[int] = []
    values: list[float] = []
    for w in range(20):
        shock = float(rng.normal(0.0, 1.0))
        for _ in range(8):
            weeks.append(w)
            values.append(shock + float(rng.normal(0.0, 0.05)))

    block = block_bootstrap(values, weeks, n_boot=1_000, seed=7)
    iid = iid_bootstrap(values, n_boot=1_000, seed=7)
    assert ci_width(block) > ci_width(iid)
    assert block.estimate == pytest.approx(iid.estimate, abs=1e-12)


def test_paired_block_bootstrap_zero_when_identical() -> None:
    x = np.array([0.1, 0.2, 0.3, 0.4], dtype=float)
    weeks = [1, 1, 2, 2]
    ci = paired_block_bootstrap(x, x, weeks, n_boot=200, seed=1)
    assert ci.estimate == pytest.approx(0.0)
    assert ci.ci_low <= 0.0 <= ci.ci_high


# ---------------------------------------------------------------------------
# Anti-metric rule
# ---------------------------------------------------------------------------


def test_bare_rate_formatter_refuses_float() -> None:
    with pytest.raises(BareRateError, match="refuses bare rates"):
        format_rate_with_ci(0.55)  # type: ignore[arg-type]


def test_bare_rate_construction_requires_finite_ci() -> None:
    with pytest.raises(BareRateError):
        RateWithCI(rate=0.55, ci_low=float("nan"), ci_high=0.6, n=100)


def test_format_rate_with_ci_renders_interval() -> None:
    rate = RateWithCI(rate=0.52, ci_low=0.48, ci_high=0.56, n=500, label="ATS")
    text = format_rate_with_ci(rate)
    assert "52.0%" in text
    assert "48.0%" in text
    assert "56.0%" in text
    assert "n=500" in text
    assert "ATS:" in text


def test_bypass_via_object_new_still_caught_at_format() -> None:
    # Hostile construction skipping __post_init__ via object.__new__
    bare = object.__new__(RateWithCI)
    object.__setattr__(bare, "rate", 0.99)
    object.__setattr__(bare, "ci_low", float("nan"))
    object.__setattr__(bare, "ci_high", float("nan"))
    object.__setattr__(bare, "n", 10)
    object.__setattr__(bare, "label", "cheat")
    object.__setattr__(bare, "alpha", 0.05)
    with pytest.raises(BareRateError, match="finite confidence interval"):
        format_rate_with_ci(bare)


# ---------------------------------------------------------------------------
# Fixture season helpers
# ---------------------------------------------------------------------------


def _fixture_season(
    season: int = 2023, n_weeks: int = 8, games_per_week: int = 6
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synthetic season with enough structure for a golden backtest report."""
    rng = np.random.default_rng(2023)
    rows: list[dict[str, object]] = []
    bets: list[dict[str, object]] = []
    gid = 0
    for week in range(1, n_weeks + 1):
        for g in range(games_per_week):
            gid += 1
            true_m = float(rng.normal(0.0, 14.0))
            true_t = float(rng.normal(52.0, 10.0))
            sigma_m = 14.0
            sigma_t = 10.0
            pred_m = true_m + float(rng.normal(0.0, 4.0))
            pred_t = true_t + float(rng.normal(0.0, 3.0))
            spread = -np.round(pred_m * 2) / 2.0
            total_line = np.round(pred_t * 2) / 2.0
            home_pts = max(0.0, (true_t + true_m) / 2.0)
            away_pts = max(0.0, (true_t - true_m) / 2.0)
            p_ml = float(1.0 / (1.0 + np.exp(-pred_m / 10.0)))
            p_mkt = float(np.clip(p_ml + rng.normal(0, 0.03), 0.05, 0.95))
            p_ats = float(1.0 / (1.0 + np.exp(-(pred_m + spread) / 8.0)))
            p_mkt_ats = float(np.clip(p_ats + rng.normal(0, 0.03), 0.05, 0.95))
            p_ou = float(1.0 / (1.0 + np.exp(-(pred_t - total_line) / 8.0)))
            p_mkt_ou = float(np.clip(p_ou + rng.normal(0, 0.03), 0.05, 0.95))
            conf = "SEC" if g % 2 == 0 else "MAC"
            rows.append(
                {
                    "game_id": f"{season}-{gid}",
                    "season": season,
                    "week": week,
                    "pred_margin": pred_m,
                    "pred_total": pred_t,
                    "sigma_m": sigma_m,
                    "sigma_t": sigma_t,
                    "realized_margin": true_m,
                    "realized_total": true_t,
                    "home_points": home_pts,
                    "away_points": away_pts,
                    "spread_close": spread,
                    "total_close": total_line,
                    "p_ml_home": p_ml,
                    "p_mkt_ml_home": p_mkt,
                    "p_ats_home": p_ats,
                    "p_mkt_ats_home": p_mkt_ats,
                    "p_ou_over": p_ou,
                    "p_mkt_ou_over": p_mkt_ou,
                    "conference_slice": conf,
                    "p5_g5": "P5" if conf == "SEC" else "G5",
                    "favorite_dog": "favorite" if spread < 0 else "dog",
                    "totals_bucket": "high" if total_line >= 52 else "low",
                    "ranked": "ranked" if g == 0 else "unranked",
                    "bowl": "reg",
                    "rivalry": "rivalry" if g == 1 else "other",
                    "weather": "adverse" if g == 2 else "neutral",
                    "exclude_from_headline": False,
                }
            )
            if g % 2 == 0:
                won = bool(rng.random() < p_ats)
                bets.append(
                    {
                        "bet_id": f"b-{gid}",
                        "game_id": f"{season}-{gid}",
                        "season": season,
                        "week": week,
                        "won": won,
                        "american_odds": -110.0,
                        "p_win": p_ats,
                        "clv": float(rng.normal(0.01, 0.02)),
                        "side": "home_ats",
                        "confidence": float(abs(p_ats - 0.5)),
                    }
                )
    return pd.DataFrame(rows), pd.DataFrame(bets)


def test_metric_suite_includes_market_baselines() -> None:
    preds, bets = _fixture_season()
    suite = compute_metric_suite(preds, bets=bets)
    assert suite.n_games == len(preds)
    assert suite.brier_ml is not None
    assert np.isfinite(suite.brier_ml.model)
    assert np.isfinite(suite.brier_ml.market)
    assert suite.logloss_ats is not None
    assert suite.crps_margin is not None
    assert np.isfinite(suite.crps_margin.model)
    assert suite.n_clv == len(bets)
    rows = suite.to_rows()
    names = {r["metric"] for r in rows}
    assert "brier_ml" in names
    assert "mean_clv" in names


def test_slice_table_labeled_diagnostic() -> None:
    preds, _ = _fixture_season()
    table = build_slice_table(preds)
    assert table.label == DIAGNOSTIC_LABEL
    assert "diagnostic_label" in table.table.columns
    assert (table.table["diagnostic_label"] == DIAGNOSTIC_LABEL).all()
    assert "conference" in set(table.table["slice_family"])


def test_economic_simulation_paths() -> None:
    _, bets = _fixture_season()
    econ = simulate_economics(bets, n_boot=100, seed=3)
    assert econ.flat.bankroll.size == len(bets) + 1
    assert econ.quarter_kelly.bankroll.size == len(bets) + 1
    assert econ.half_kelly.bankroll.size == len(bets) + 1
    assert np.isfinite(econ.roi_ci_flat.ci_low)
    assert econ.max_drawdown_distribution_quarter.size == 100


def test_golden_backtest_report_fixture_season(tmp_path: Path) -> None:
    preds, bets = _fixture_season(season=2023)
    shap = pd.DataFrame(
        {
            "feature": ["rating_offense", "tempo", "weather_wind"],
            "mean_abs_shap": [0.42, 0.18, 0.07],
        }
    )
    data = BacktestReportInput(
        season=2023,
        predictions=preds,
        bets=bets,
        shap_summary=shap,
        model_version="fixture-v0",
        n_boot=80,
        seed=11,
        title="Golden backtest · 2023 fixture",
    )
    art = build_backtest_artifacts(data)
    html = render_backtest_report(data, artifacts=art)
    path = write_backtest_report(data, tmp_path / "backtest_2023.html")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert html == text
    assert "Metric tables" in text
    assert "DIAGNOSTIC" in text
    assert "equity" in text.lower()
    assert "SHAP" in text
    assert "rating_offense" in text
    assert "Reliability" in text
    assert "Weekly error" in text
    assert "[" in text and "]" in text
    assert art.suite.n_games == len(preds)


def test_weekly_report_writes(tmp_path: Path) -> None:
    preds, bets = _fixture_season(n_weeks=1, games_per_week=4)
    edges = bets[["game_id", "side", "clv", "confidence"]].assign(edge=0.03)
    path = write_weekly_report(
        WeeklyReportInput(
            season=2023,
            week=1,
            predictions=preds,
            edges=edges,
            rating_movements=pd.DataFrame({"team_id": ["A", "B"], "delta_offense": [0.2, -0.1]}),
            model_version="fixture",
        ),
        tmp_path / "week1.html",
    )
    text = path.read_text(encoding="utf-8")
    assert "Predictions" in text
    assert "Edges" in text
    assert "Rating movements" in text


# ---------------------------------------------------------------------------
# Market baseline resolution (Task 23-FIX-DIAG D-1)
# ---------------------------------------------------------------------------


def test_ml_market_constant_half_struck_as_not_computed() -> None:
    """Constant 0.5 is a coin-flip strawman — never a de-vigged ML market."""
    from ncaa_quant.evaluation.metrics import (
        MARKET_ML_NOT_COMPUTED,
        resolve_market_baselines,
    )

    frame = pd.DataFrame(
        {
            "p_mkt_ml_home": [0.5, 0.5, 0.5],
            "p_mkt_ats_home": [0.5, 0.5, 0.5],
            "p_mkt_ou_over": [0.5, 0.5, 0.5],
        }
    )
    res = resolve_market_baselines(frame)
    assert res.ml_status == MARKET_ML_NOT_COMPUTED
    assert res.p_mkt_ml_home is None
    assert "coin-flip" in res.ml_reason or "0.5" in res.ml_reason
    # ATS/OU fair-at-−110 constant 0.5 remains valid.
    assert res.p_mkt_ats_home is not None
    assert np.allclose(res.p_mkt_ats_home, 0.5)


def test_ml_market_all_null_struck_as_not_computed() -> None:
    from ncaa_quant.evaluation.metrics import MARKET_ML_NOT_COMPUTED, resolve_market_baselines

    frame = pd.DataFrame(
        {
            "p_mkt_ml_home": [np.nan, np.nan],
            "spread_close": [np.nan, np.nan],
        }
    )
    res = resolve_market_baselines(frame)
    assert res.ml_status == MARKET_ML_NOT_COMPUTED
    assert res.p_mkt_ml_home is None


def test_ml_market_from_american_odds_devigged() -> None:
    """−150 / +130 → proportional de-vig fair home ≈ 0.579."""
    from ncaa_quant.betting.devig import american_to_raw_implied, proportional_devig
    from ncaa_quant.evaluation.metrics import (
        MARKET_ML_DEVIGGED_AMERICAN,
        resolve_market_baselines,
    )

    q_h = american_to_raw_implied(-150.0)
    q_a = american_to_raw_implied(130.0)
    expected = float(proportional_devig([q_h, q_a])[0])
    frame = pd.DataFrame(
        {
            "home_ml": [-150.0, -110.0],
            "away_ml": [130.0, -110.0],
            "p_mkt_ml_home": [0.5, 0.5],  # strawman ignored when Americans present
        }
    )
    res = resolve_market_baselines(frame)
    assert res.ml_status == MARKET_ML_DEVIGGED_AMERICAN
    assert res.p_mkt_ml_home is not None
    assert res.p_mkt_ml_home[0] == pytest.approx(expected, rel=1e-9)
    assert res.n_ml_finite == 2


def test_metric_suite_ml_market_nan_when_strawman() -> None:
    preds, _ = _fixture_season(n_weeks=2, games_per_week=4)
    preds = preds.copy()
    preds["p_mkt_ml_home"] = 0.5
    suite = compute_metric_suite(preds)
    assert suite.logloss_ml is not None
    assert np.isfinite(suite.logloss_ml.model)
    assert not np.isfinite(suite.logloss_ml.market)
    assert suite.extras["market_baseline"]["ml_status"] == "NOT_COMPUTED"
