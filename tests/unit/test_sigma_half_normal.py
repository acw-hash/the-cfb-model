"""Half-normal σ correction tests (D3 Part 1)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.models.heads.base import HeadTrainConfig
from ncaa_quant.models.heads.sigma import (
    HALF_NORMAL_MAD_TO_SIGMA,
    HALF_NORMAL_SIGMA_TO_MAD,
    LightGBMSigmaHead,
    abs_residual_to_sigma,
    sigma_to_abs_residual,
)


def test_half_normal_constants() -> None:
    assert pytest.approx(math.sqrt(math.pi / 2.0)) == HALF_NORMAL_MAD_TO_SIGMA
    assert pytest.approx(math.sqrt(2.0 / math.pi)) == HALF_NORMAL_SIGMA_TO_MAD
    assert abs_residual_to_sigma(1.0) == pytest.approx(HALF_NORMAL_MAD_TO_SIGMA)
    assert sigma_to_abs_residual(HALF_NORMAL_MAD_TO_SIGMA) == pytest.approx(1.0)


def test_sigma_head_recovers_generating_sigma_not_mad() -> None:
    """Head trained on |r| from N(0, σ) must emit σ, not E[|r|]=σ√(2/π)."""
    rng = np.random.default_rng(42)
    true_sigma = 12.0
    n = 2_000
    # Heteroskedastic cue: feature correlates weakly with a constant σ world
    # (constant target) so the tree learns the MAD level; correction lifts to σ.
    x = rng.normal(0.0, 1.0, size=(n, 3))
    resid = rng.normal(0.0, true_sigma, size=n)
    mad_labels = np.abs(resid)
    expected_mad = true_sigma * HALF_NORMAL_SIGMA_TO_MAD

    features = pd.DataFrame(
        {
            "game_id": np.arange(n),
            "f0": x[:, 0],
            "f1": x[:, 1],
            "f2": x[:, 2],
            "rating_uncertainty": np.abs(x[:, 0]),
        }
    )
    labels = pd.DataFrame(
        {
            "game_id": np.arange(n),
            "abs_residual_margin": mad_labels,
            "season": np.full(n, 2023),
            "week": (np.arange(n) % 12) + 1,
        }
    )
    head = LightGBMSigmaHead(
        target="sigma_margin",
        train=HeadTrainConfig(
            n_estimators=80,
            learning_rate=0.08,
            num_leaves=16,
            min_child_samples=20,
        ),
        seed=0,
    )
    head.fit(features, labels)
    pred = head.predict(features)
    col = "pred_sigma_margin"
    assert col in pred.columns
    mean_pred = float(pred[col].mean())
    # Must recover generating σ (±15%), not the MAD (which is ~0.80 σ).
    assert mean_pred == pytest.approx(true_sigma, rel=0.15)
    assert mean_pred > expected_mad * 1.1
    assert abs(mean_pred - expected_mad) > abs(mean_pred - true_sigma)
