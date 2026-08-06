"""D2: silent-failure removal, prediction quality gate, NNLS wiring, smoke refuse."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.canonical_eval import gate_task23_fundamental
from ncaa_quant.evaluation.production_stack import ProductionEnsemblePredictor
from ncaa_quant.evaluation.reports import (
    BacktestReportInput,
    SmokeRunMetricsError,
    assert_not_smoke_for_headline_metrics,
    build_backtest_artifacts,
)
from ncaa_quant.evaluation.walkforward import (
    PredictionQualityGateError,
    WalkForwardConfig,
    assert_prediction_quality_gate,
)
from ncaa_quant.models.ensemble import EnsembleError, NNLSStackResult, fit_nnls_stack
from ncaa_quant.models.heads.base import NotFittedError
from ncaa_quant.models.heads.margin import LightGBMMuHead


def test_unfitted_head_raises_not_fitted() -> None:
    head = LightGBMMuHead(target="margin")
    feats = pd.DataFrame(
        {
            "game_id": [1, 2],
            "rating_diff_off_epa": [0.1, -0.2],
            "rating_uncertainty": [0.05, 0.06],
        }
    )
    with pytest.raises(NotFittedError, match="has not been fit"):
        head.predict(feats)


def test_zero_inflated_table_trips_quality_gate() -> None:
    rng = np.random.default_rng(0)
    n = 200
    mu = rng.normal(3.0, 8.0, size=n)
    mu[:80] = 0.0  # planted zero inflation (~40%)
    frame = pd.DataFrame(
        {
            "season": np.repeat([2021, 2022, 2023, 2024], n // 4),
            "week": np.tile(np.arange(1, 11), n // 10),
            "pred_margin": mu,
            "realized_margin": rng.normal(3.0, 16.0, size=n),
            "exclude_from_headline": False,
            "n_train_games": 500,
            "run_kind": "backtest",
        }
    )
    with pytest.raises(PredictionQualityGateError, match="zero_mu_rate"):
        assert_prediction_quality_gate(frame, raise_on_fail=True)


def test_smoke_tagged_table_refused_by_reporter() -> None:
    frame = pd.DataFrame(
        {
            "pred_margin": [3.0, -2.0, 5.0],
            "realized_margin": [7.0, -1.0, 4.0],
            "home_points": [28, 17, 31],
            "away_points": [21, 18, 27],
            "run_kind": ["smoke", "smoke", "smoke"],
            "exclude_from_headline": [False, False, False],
        }
    )
    with pytest.raises(SmokeRunMetricsError, match="smoke"):
        assert_not_smoke_for_headline_metrics(frame)
    with pytest.raises(SmokeRunMetricsError):
        build_backtest_artifacts(
            BacktestReportInput(season=2023, predictions=frame, n_boot=10, seed=0)
        )


def test_production_set_weights_calls_nnls() -> None:
    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        mapping_layer="ensemble",
        market_features_available=False,
        nnls_equal_weight_fallback=False,
    )
    pred = ProductionEnsemblePredictor(config=cfg, seed=0)
    oof = pd.DataFrame(
        {
            "game_id": np.arange(40),
            "lgbm_mu_margin": np.linspace(-10, 10, 40),
            "enet_mu_margin": np.linspace(-8, 9, 40) + 0.5,
            "realized_margin": np.linspace(-9, 11, 40),
            "is_out_of_fold": True,
        }
    )
    sentinel = NNLSStackResult(
        target="margin",
        member_columns=("lgbm_mu_margin", "enet_mu_margin"),
        weights=(0.73, 0.27),
        condition_number=12.5,
        n_oof_rows=40,
    )
    with patch(
        "ncaa_quant.evaluation.production_stack.fit_nnls_stack",
        return_value=sentinel,
    ) as mocked:
        pred._set_weights(oof)
        assert mocked.called, "production path must call fit_nnls_stack"
        assert mocked.call_count == 1
    # Weights come from NNLS return value — hardcoding 0.5/0.5 would fail this.
    assert pred.ensemble_weights == {"lgbm_mu_margin": 0.73, "enet_mu_margin": 0.27}
    assert pred.nnls_fold_reports[-1]["condition_number"] == 12.5


def test_nnls_degenerate_raises_without_fallback() -> None:
    oof = pd.DataFrame(
        {
            "lgbm_mu_margin": [0.0, 0.0, 0.0, 0.0],
            "enet_mu_margin": [0.0, 0.0, 0.0, 0.0],
            "realized_margin": [1.0, -1.0, 2.0, -2.0],
            "is_out_of_fold": True,
        }
    )
    with pytest.raises(EnsembleError, match="degenerate|all-zero"):
        fit_nnls_stack(
            oof,
            target="margin",
            member_columns=["lgbm_mu_margin", "enet_mu_margin"],
            allow_equal_weight_fallback=False,
        )


def test_gate_passes_on_clean_task23_fundamental_subset() -> None:
    path = "data/backtests/task23_fundamental/fundamental/predictions.parquet"
    try:
        report = gate_task23_fundamental(path, raise_on_fail=False)
    except FileNotFoundError:
        pytest.skip("task23_fundamental predictions not present")
    clean = report["clean_excluding_2019_w1_4"]
    assert clean["passed"] is True
    assert clean["zero_mu_rate"] <= 0.001
    # Full archived table still shows the legacy poison.
    assert report["full_table"]["passed"] is False
