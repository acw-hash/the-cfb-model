"""D4 gates: generalized degeneracy + void-conclusion rule."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.d4_eval import encompassing_regression, residual_on_residual
from ncaa_quant.evaluation.production_stack import (
    DistributionDegeneracyError,
    validate_prediction_distribution,
)
from ncaa_quant.evaluation.reports import (
    VoidAblationConclusionError,
    assert_component_varies_before_conclusion,
)


def test_gate_trips_on_planted_constant_sigma() -> None:
    """Acceptance: generalized gate demonstrably trips on constant-σ table."""
    n = 24
    frame = pd.DataFrame(
        {
            "season": np.repeat([2023, 2024], n // 2),
            "week": np.tile(np.arange(1, 7), 4),
            "pred_margin": np.linspace(-12, 12, n),
            "sigma_m": np.full(n, 14.0),
            "sigma_t": np.linspace(10, 16, n),
            "p_ml_home": np.linspace(0.2, 0.8, n),
            "exclude_from_headline": False,
        }
    )
    with pytest.raises(DistributionDegeneracyError, match="sigma_m.*constant|zero variance"):
        validate_prediction_distribution(frame)


def test_gate_trips_on_zero_variance_within_week_block() -> None:
    n = 16
    frame = pd.DataFrame(
        {
            "season": np.full(n, 2023),
            "week": np.repeat([1, 2], 8),
            "pred_margin": np.linspace(-5, 5, n),
            "sigma_m": np.concatenate([np.full(8, 12.0), np.linspace(10, 16, 8)]),
            "p_ml_home": np.linspace(0.3, 0.7, n),
            "exclude_from_headline": False,
        }
    )
    with pytest.raises(DistributionDegeneracyError, match="season=2023 week=1"):
        validate_prediction_distribution(frame)


def test_void_conclusion_rule_blocks_inert_component() -> None:
    with pytest.raises(VoidAblationConclusionError, match="constant"):
        assert_component_varies_before_conclusion(
            np.full(30, 14.0),
            component_name="sigma_m",
            conclusion="sigma head does not help",
        )
    # Varying component is allowed through.
    assert_component_varies_before_conclusion(
        np.linspace(10, 20, 30),
        component_name="sigma_m",
        conclusion="sigma head does not help",
    )


def test_encompassing_regression_smoke() -> None:
    rng = np.random.default_rng(0)
    n = 200
    market = rng.normal(0, 15, n)
    stack = 0.3 * market + rng.normal(0, 10, n)
    y = 0.7 * market + 0.25 * stack + rng.normal(0, 8, n)
    blocks = [(2023, 1 + i % 12) for i in range(n)]
    enc = encompassing_regression(y, market, stack, blocks, n_boot=200, seed=0)
    assert enc.n == n
    assert np.isfinite(enc.b2)
    ror = residual_on_residual(y, market, stack)
    assert ror["n"] == n
