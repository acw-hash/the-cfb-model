"""P1-5 / Task 23-FIX-CLOSE: production feature path vs roster is_missing."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from ncaa_quant.evaluation.production_stack import ProductionFeatureProvider
from ncaa_quant.evaluation.walkforward import WalkForwardConfig
from ncaa_quant.features.builders.roster import (
    RosterFeatureBuilder,
    build_roster_frame,
    preseason_event_time,
)
from ncaa_quant.features.registry import FeatureSpec


def _roster_spec(name: str) -> FeatureSpec:
    return FeatureSpec(
        name=name,
        version="1",
        dtype="float64",
        builder="ncaa_quant.features.builders.roster:RosterFeatureBuilder",
        dependencies=("raw:teams",),
        as_of_semantics="strict_lt",
        null_policy="indicator",
        lookback_window="preseason",
        hypothesis="Roster priors predict early-season margins.",
    )


def test_production_feature_provider_does_not_read_feature_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 3A: ProductionFeatureProvider never opens data/features/."""
    features_root = tmp_path / "features"
    features_root.mkdir()
    poison = features_root / "roster_task23_fix.parquet"
    pd.DataFrame({"team_id": [1], "returning_offense_pct": [0.0]}).to_parquet(poison)

    reads: list[Path] = []
    real_read = pd.read_parquet

    def _spy(path: object, *args: object, **kwargs: object) -> pd.DataFrame:
        reads.append(Path(str(path)))
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", _spy)

    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(),
        market_features_available=False,
        seed=0,
        run_id="p15",
        ablation_id="fundamental",
        min_train_games=1,
        max_zero_mu_rate=1.0,
        enforce_prediction_quality_gate=False,
    )
    provider = ProductionFeatureProvider(config=cfg)
    games = pd.DataFrame(
        {
            "game_id": [1],
            "home_team_id": [10],
            "away_team_id": [20],
            "season": [2023],
            "week": [1],
        }
    )
    frame = provider.compute_game_features(
        games,
        datetime(2023, 9, 1, tzinfo=UTC),
        rating_state={"10:off_epa": 0.1, "20:off_epa": -0.1},
        market_features=False,
    )
    assert not frame.empty
    assert not any("roster_task23_fix" in str(p) for p in reads)
    assert not any(str(features_root) in str(p) for p in reads)


def test_roster_feature_builder_emits_is_missing_indicators() -> None:
    """Step 3B-staged: roster path that feeds priors uses null-with-indicator."""
    teams = pd.DataFrame([{"team_id": 7, "season": 2023, "school": "X"}])
    history = build_roster_frame(
        teams=teams,
        returning=pd.DataFrame(),
        talent=pd.DataFrame(),
        recruiting=pd.DataFrame(),
        portal=pd.DataFrame(),
        coaches=pd.DataFrame(),
        seasons=(2023,),
        coordinators=(),
    )
    builder = RosterFeatureBuilder(_roster_spec("portal_net_rating"), history)
    out = builder.build([7], preseason_event_time(2023))
    assert "is_missing" in out.columns
    assert bool(out.iloc[0]["is_missing"]) is True
    assert math.isnan(float(out.iloc[0]["value"]))
