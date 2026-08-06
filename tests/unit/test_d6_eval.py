"""D6 unit tests: close join, encompassing slices, sigma CIs, sign gate helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.d5_eval import EncompassingEvalConfig
from ncaa_quant.evaluation.d6_eval import (
    CloseAsFeatureError,
    assert_closes_eval_only,
    detectable_b2_at_power,
    diagnose_expected_possessions,
    join_cfbd_closes_for_evaluation,
    load_cfbd_lines,
    post_join_line_coverage,
    priced_vs_pooled_season_note,
    run_powered_encompassing,
    sigma_bakeoff_paired_cis,
    validate_joined_closes,
)


def _toy_frame(n: int = 240, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    seasons = np.array([2019, 2021, 2022, 2023, 2024, 2025] * (n // 6 + 1))[:n]
    market = rng.normal(0, 14, n)
    stack = 0.25 * market + rng.normal(0, 9, n)
    y = 0.75 * market + 0.2 * stack + rng.normal(0, 10, n)
    return pd.DataFrame(
        {
            "game_id": np.arange(n),
            "season": seasons,
            "week": 1 + (np.arange(n) % 14),
            "realized_margin": y,
            "pred_margin": stack,
            "sigma_m": np.full(n, 15.0),
            "spread_close": np.where(seasons == 2019, -market, np.nan),
            "total_close": np.where(seasons == 2019, 55.0, np.nan),
            "line_source_close": np.where(seasons == 2019, "cfbd_close", "null"),
            "n_books_close": np.where(seasons == 2019, 2, 0),
        }
    )


def test_join_and_coverage_and_validate() -> None:
    frame = _toy_frame(60)
    # Start with all-null closes so the join owns every row (spot-check vs CFBD).
    frame["spread_close"] = np.nan
    frame["total_close"] = np.nan
    frame["line_source_close"] = "null"
    spreads = -np.linspace(-20, 20, 60)
    lines = pd.DataFrame(
        {
            "game_id": list(range(60)) * 2,
            "line_type": ["close"] * 120,
            "book": ["a"] * 60 + ["b"] * 60,
            "spread": list(spreads) * 2,
            "total": [48.0] * 120,
            "season": list(frame["season"]) * 2,
        }
    )
    joined, meta = join_cfbd_closes_for_evaluation(frame, lines)
    assert meta["n_filled"] == 60
    assert meta["eval_only"] is True
    cov = post_join_line_coverage(joined)
    assert cov["total_n_with_spread_close"] == 60
    val = validate_joined_closes(joined, lines, n_spot=10, seed=1)
    assert val["passed"]
    assert val["spot_check"]["match_rate"] == 1.0


def test_assert_closes_eval_only_and_load_empty(tmp_path: Path) -> None:
    assert_closes_eval_only(["home_off_epa", "week"])
    with pytest.raises(CloseAsFeatureError):
        assert_closes_eval_only(["spread_close", "home_off_epa"])
    empty = load_cfbd_lines(tmp_path)
    assert empty.empty


def test_diagnose_expected_possessions(tmp_path: Path) -> None:
    out = diagnose_expected_possessions(feature_store_root=tmp_path, registry_has_name=True)
    assert out["death_point"] == "registered_and_not_materialized"
    assert out["deferred"] is True


def test_priced_vs_pooled_and_detectable() -> None:
    frame = _toy_frame(48)
    note = priced_vs_pooled_season_note(frame)
    assert "2019" in note["note"]
    assert detectable_b2_at_power(0.05) > 0.10


def test_sigma_bakeoff_paired_cis_smoke() -> None:
    rng = np.random.default_rng(3)
    rows = []
    for season in range(2019, 2025):
        for week in range(1, 9):
            for _i in range(8):
                mu = float(rng.normal(0, 10))
                y = mu + float(rng.normal(0, 14))
                rows.append(
                    {
                        "season": season,
                        "week": week,
                        "pred_margin": mu,
                        "realized_margin": y,
                        "sigma_m": 14.0 + 0.1 * week,
                    }
                )
    frame = pd.DataFrame(rows)
    out = sigma_bakeoff_paired_cis(frame, n_boot=40, seed=0)
    assert "S4_minus_S0" in out["pairwise_crps_delta"]
    assert out["prefer"] in {"S4", "S4_parsimony", "S0", "S1"}


def test_run_powered_encompassing_smoke() -> None:
    frame = _toy_frame(300, seed=4)
    # Fill all closes so every season contributes.
    frame["spread_close"] = -pd.to_numeric(frame["pred_margin"]) + np.random.default_rng(4).normal(
        0, 3, len(frame)
    )
    cfg = EncompassingEvalConfig(
        seasons=(2019, 2021, 2022, 2023, 2024, 2025),
        n_boot=40,
        min_games_per_season=20,
        stability_min_seasons_positive=3,
    )
    fbs = np.ones(len(frame), dtype=bool)
    out = run_powered_encompassing(frame, cfg, fbs_mask=fbs)
    assert out["joint"]["n"] == len(frame)
    assert "ci95" in out["joint"]
    assert "detectable_b2_80pct" in out["power"]
    assert out["stop_rule"]["status"] in {"met", "missed", "inconclusive"}
    assert out["fbs_vs_fbs"] is not None
    assert out["exclude_2019"] is not None
    assert "1-4" in out["by_week_bucket"]
