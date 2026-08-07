"""Task 22B-FIX — run-set plan / measured cost estimator."""

from __future__ import annotations

from ncaa_quant.evaluation.backtest_runner import (
    COST_MEASUREMENT_BASIS,
    SEC_PER_RETRAIN_FULL,
    SEC_PER_WEEK_FULL,
    BacktestRunSetPlan,
    plan_backtest,
)
from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON


def test_task23_run_set_plan_eight_runs_with_basis() -> None:
    plan = plan_backtest("task23_run_set")
    assert isinstance(plan, BacktestRunSetPlan)
    assert len(plan.plans) == 8
    assert plan.estimated_wall_clock_sec == sum(p.estimated_wall_clock_sec for p in plan.plans)
    assert plan.estimated_wall_clock_sec > 0
    assert "wired=" in plan.measurement_basis
    assert "WIRING FINDING" in plan.measurement_basis
    assert "measurement_basis=" in plan.format_text()

    names = [p.config_name for p in plan.plans]
    assert names[0] == "fundamental_full"
    assert names[1] == "market_aware_full"
    assert "A6_cfbd_open_close" in names

    # A6 is snapshot-regime only — fewer week units than a full run. Neither
    # touches 2025, which is the lockbox (§7.2 item 9).
    a6 = next(p for p in plan.plans if p.config_name == "A6_cfbd_open_close")
    full = next(p for p in plan.plans if p.config_name == "fundamental_full")
    assert a6.n_week_units < full.n_week_units
    assert a6.seasons == (2021, 2022, 2023, 2024)
    assert LOCKBOX_SEASON not in full.seasons

    expected = full.n_week_units * SEC_PER_WEEK_FULL + full.n_retrain_points * SEC_PER_RETRAIN_FULL
    assert abs(full.estimated_wall_clock_sec - expected) < 1e-6
    assert plan.measurement_basis == COST_MEASUREMENT_BASIS
