"""D7 unit tests: RE meta-analysis, season-power, interaction, early-w, ROI."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.d6_eval import D5_STOP_RULE_VERBATIM
from ncaa_quant.evaluation.d7_eval import (
    D6_JOINT_B2,
    HOLDOUT_SEASON,
    early_week_edge_roi,
    optimal_w_early_weeks,
    posthoc_season_criterion_power,
    preregister_holdout,
    random_effects_b2,
    record_stop_rule_stands,
    run_d7_diagnostics,
    run_holdout_early_week,
    week_bucket_interaction,
)


def test_record_stop_rule_stands() -> None:
    out = record_stop_rule_stands(
        {"triggered": True, "status": "missed", "n_reliable_positive_seasons": 2}
    )
    assert out["stands"] is True
    assert out["rule_amended"] is False
    assert out["rule_verbatim"] == D5_STOP_RULE_VERBATIM
    assert "no betting layer" in out["operational_conclusion"]


def test_random_effects_homogeneous() -> None:
    # Identical estimates → Q≈0, τ²=0, I²=0
    out = random_effects_b2([0.2, 0.2, 0.2], [0.05, 0.05, 0.05], labels=["a", "b", "c"])
    assert out["tau2"] == 0.0
    assert out["i2"] == 0.0
    assert abs(out["random_effect_b2"] - 0.2) < 1e-9
    assert out["between_season_variance_distinguishable_from_zero"] is False


def test_random_effects_heterogeneous() -> None:
    # Far-apart estimates with tiny SEs → large Q
    out = random_effects_b2([0.0, 1.0], [0.01, 0.01], labels=["x", "y"])
    assert out["cochrans_q"] > 10
    assert out["p_heterogeneity"] < 0.05
    assert out["tau2"] > 0
    assert out["between_season_variance_distinguishable_from_zero"] is True


def test_posthoc_season_power_underpowered() -> None:
    # Large SEs relative to b2=0.211 → criterion not capable
    per = {
        str(s): {"b2": 0.2, "se_b2": 0.15, "n": 800} for s in (2019, 2021, 2022, 2023, 2024, 2025)
    }
    out = posthoc_season_criterion_power(per, true_b2=D6_JOINT_B2)
    assert out["n_seasons"] == 6
    assert "POST-HOC" in out["label"]
    assert out["p_ge_k_clear"] < 0.5
    assert out["criterion_capable_of_passing"] is False
    for row in out["per_season"].values():
        assert row["detectable_b2_80pct"] > D6_JOINT_B2


def test_posthoc_season_power_capable() -> None:
    per = {
        str(s): {"b2": 0.2, "se_b2": 0.05, "n": 2000} for s in (2019, 2021, 2022, 2023, 2024, 2025)
    }
    out = posthoc_season_criterion_power(per, true_b2=D6_JOINT_B2)
    assert out["p_ge_k_clear"] > 0.9
    assert out["criterion_capable_of_passing"] is True


def test_preregister_before_holdout() -> None:
    plan = preregister_holdout()
    assert plan["holdout_season"] == HOLDOUT_SEASON
    assert plan["fitted_before_registration"] is False


def _toy_arrays(n: int = 600, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    seasons = np.array([2019, 2021, 2022, 2023, 2024, 2025] * (n // 6 + 1))[:n]
    weeks = 1 + (np.arange(n) % 14)
    market = rng.normal(0, 14, n)
    # Early weeks: stack informative; late weeks: not
    early = weeks <= 4
    stack = market + rng.normal(0, 8, n)
    y = np.where(
        early,
        0.7 * market + 0.35 * stack + rng.normal(0, 10, n),
        1.0 * market + 0.0 * stack + rng.normal(0, 10, n),
    )
    blocks = list(zip(seasons.tolist(), weeks.tolist(), strict=True))
    return {
        "y": y,
        "market": market,
        "stack": stack,
        "seasons": seasons,
        "weeks": weeks,
        "blocks": blocks,
    }


def test_week_bucket_interaction_smoke() -> None:
    a = _toy_arrays(720, seed=1)
    out = week_bucket_interaction(
        a["y"], a["market"], a["stack"], a["weeks"], a["blocks"], n_boot=80, seed=0
    )
    assert "b2_1-4" in out["coefficients"]
    assert "p_b2_equal_across_buckets" in out
    assert out["n"] == 720


def test_holdout_and_early_w_and_roi() -> None:
    a = _toy_arrays(900, seed=2)
    hold = run_holdout_early_week(
        a["y"],
        a["market"],
        a["stack"],
        a["seasons"],
        a["weeks"],
        a["blocks"],
        n_boot=60,
        seed=0,
    )
    assert hold["holdout_season"] == HOLDOUT_SEASON
    assert hold["status"] in {"confirmed", "refuted", "insufficient_n"}

    w = optimal_w_early_weeks(
        a["y"], a["market"], a["stack"], a["weeks"], a["blocks"], n_boot=60, seed=0
    )
    assert 0.0 <= w["w"] <= 1.0
    assert w["n"] > 0
    assert "delta_ci" in w

    edge = early_week_edge_roi(
        a["y"],
        a["market"],
        a["stack"],
        a["seasons"],
        a["weeks"],
        a["blocks"],
        b2=0.211,
        n_boot=60,
        seed=0,
    )
    assert edge["mean_bettable_games_per_season"] > 0
    assert "roi_ci95" in edge
    assert 0.0 < edge["breakeven_p_at_american"] < 1.0


def test_run_d7_diagnostics_smoke() -> None:
    n = 480
    rng = np.random.default_rng(5)
    seasons = np.array([2019, 2021, 2022, 2023, 2024, 2025] * (n // 6 + 1))[:n]
    market = rng.normal(0, 12, n)
    stack = 0.3 * market + rng.normal(0, 8, n)
    y = 0.8 * market + 0.2 * stack + rng.normal(0, 10, n)
    frame = pd.DataFrame(
        {
            "season": seasons,
            "week": 1 + (np.arange(n) % 14),
            "realized_margin": y,
            "pred_margin": stack,
            "spread_close": -market,
        }
    )
    per_season = {
        str(s): {"b2": 0.2, "se_b2": 0.12, "n": 80, "n_games": 80}
        for s in (2019, 2021, 2022, 2023, 2024, 2025)
    }
    d6 = {
        "canonical_v2_sha": "deadbeef",
        "config": {
            "seasons": [2019, 2021, 2022, 2023, 2024, 2025],
            "line_column": "spread_close",
            "market_implied_sign": -1.0,
        },
        "per_season": per_season,
        "stop_rule": {
            "triggered": True,
            "status": "missed",
            "n_reliable_positive_seasons": 2,
        },
    }
    out = run_d7_diagnostics(frame, d6, n_boot=40, seed=0)
    assert out["diagnostic_phase_closed"] is True
    assert "i2" in out["opening_summary"]
    assert "tau2" in out["opening_summary"]
    assert "re_ci95" in out["opening_summary"]
    assert "interaction_p" in out["opening_summary"]
