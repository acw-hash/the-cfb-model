"""Task 23-FIX-CLOSE Item 4 — A1 CLI priors_frame wiring."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ncaa_quant.cli import load_fitted_priors_frame_for_backtest
from ncaa_quant.evaluation.production_stack import (
    assert_a1_priors_precondition,
    build_production_stack,
)
from ncaa_quant.evaluation.walkforward import WalkForwardConfig


def _priors_fixture(seasons: tuple[int, ...] = (2023,)) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for season in seasons:
        for i, tid in enumerate((10, 20, 30)):
            for dim in ("off_epa", "def_epa", "st_value", "pace"):
                rows.append(
                    {
                        "team_id": tid,
                        "season": season,
                        "dim": dim,
                        "prior_mean": 0.1 + 0.05 * i,
                        "prior_var": 0.05,
                    }
                )
    return pd.DataFrame(rows)


def test_load_fitted_priors_frame_from_explicit_path(tmp_path: Path) -> None:
    path = tmp_path / "priors.parquet"
    frame = _priors_fixture()
    frame.to_parquet(path, index=False)
    loaded = load_fitted_priors_frame_for_backtest(
        tmp_path / "staged",
        (2023,),
        priors_path=path,
    )
    assert loaded is not None
    assert len(loaded) == len(frame)
    assert_a1_priors_precondition(loaded)


def test_cli_a1_path_reaches_populated_precondition(tmp_path: Path) -> None:
    """CLI-constructed A1 run reaches the precondition with a populated frame."""
    path = tmp_path / "priors.parquet"
    priors = _priors_fixture()
    priors.to_parquet(path, index=False)

    loaded = load_fitted_priors_frame_for_backtest(
        tmp_path / "staged",
        (2023,),
        priors_path=path,
    )
    assert loaded is not None and not loaded.empty

    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(),
        market_features_available=False,
        preseason_priors="league_mean",
        seed=23,
        run_id="a1_cli",
        ablation_id="A1_league_mean",
        min_train_games=1,
        max_zero_mu_rate=1.0,
        enforce_prediction_quality_gate=False,
        nnls_equal_weight_fallback=True,
    )
    # Same call shape as backtest run → run_backtest → build_production_stack.
    stack = build_production_stack(
        cfg,
        kind="fundamental",
        priors_frame=loaded,
        play_counts=None,
        n_mc_draws=50,
        n_epistemic_draws=1,
        enforce_ablation_preconditions=True,
    )
    assert stack.rating_engine.priors_frame is not None
    assert not stack.rating_engine.priors_frame.empty


def test_a1_still_raises_when_priors_missing() -> None:
    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(),
        market_features_available=False,
        preseason_priors="league_mean",
        seed=0,
        run_id="a1_missing",
        ablation_id="A1_league_mean",
        min_train_games=1,
        max_zero_mu_rate=1.0,
        enforce_prediction_quality_gate=False,
    )
    with pytest.raises(Exception, match="priors"):
        build_production_stack(
            cfg,
            kind="fundamental",
            priors_frame=None,
            enforce_ablation_preconditions=True,
        )
