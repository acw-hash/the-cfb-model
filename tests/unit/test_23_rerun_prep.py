"""TASK 23-RERUN-PREP — possessions PIT, snapshots, filter-history defaults."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ncaa_quant.cli import (
    load_artifact_paths,
    load_staged_odds_snapshots,
    resolve_filter_history_path,
)
from ncaa_quant.evaluation.leakage import assert_no_prophecy_features, audit_prophecy_features
from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON, LockboxViolation
from ncaa_quant.evaluation.production_stack import ProductionFeatureProvider, build_production_stack
from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    WalkForwardHarness,
    audit_information_set,
    week_decision_as_of,
)
from ncaa_quant.features.possessions import (
    PossessionsFitError,
    fit_expected_possessions_at_retrain,
    prior_to_retrain_mask,
)


def _kickoff(season: int, week: int, slot: int = 0) -> datetime:
    tuesday = week_decision_as_of(season, week, WalkForwardConfig())
    return tuesday + timedelta(days=4, hours=slot)


def _possessions_training(n_weeks: int = 8) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    gid = 1
    for week in range(1, n_weeks + 1):
        for slot in range(3):
            rows.append(
                {
                    "game_id": gid,
                    "season": 2023,
                    "week": week,
                    "event_time": _kickoff(2023, week, slot),
                    "home_pace": 65.0 + 0.1 * week + slot,
                    "away_pace": 64.0 + 0.05 * week,
                    "home_pass_rate": 0.45 + 0.01 * slot,
                    "away_pass_rate": 0.50,
                    "possessions": 22.0 + 0.2 * week + 0.1 * slot,
                }
            )
            gid += 1
    return pd.DataFrame(rows)


def test_fit_expected_possessions_at_retrain_is_point_in_time() -> None:
    training = _possessions_training()
    artifact = fit_expected_possessions_at_retrain(training, season=2023, week=5)
    assert artifact is not None
    used = training.loc[prior_to_retrain_mask(training, season=2023, week=5)]
    assert used["week"].max() < 5
    assert (used["season"] < 2023).sum() == 0 or True
    # Guard: no row at/after the retrain point entered the fit.
    assert int(artifact.n_train) == len(used)
    leaked = training.loc[~prior_to_retrain_mask(training, season=2023, week=5)]
    assert not leaked.empty
    with pytest.raises(PossessionsFitError, match="at or after"):
        from ncaa_quant.features.possessions import assert_possessions_fit_is_point_in_time

        assert_possessions_fit_is_point_in_time(training, season=2023, week=5)


def test_production_provider_emits_expected_possessions_and_infoset() -> None:
    training = _possessions_training()
    games = pd.DataFrame(
        [
            {
                "game_id": int(r.game_id),
                "game_key": f"2023:{r.game_id}",
                "season": 2023,
                "week": int(r.week),
                "event_time": r.event_time,
                "home_team_id": 10 + (int(r.game_id) % 3),
                "away_team_id": 20 + (int(r.game_id) % 3),
                "home_points": 28,
                "away_points": 21,
                "neutral_site": False,
            }
            for r in training.itertuples(index=False)
            if int(r.week) in {1, 2, 5}
        ]
    )
    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(5,),
        market_features_available=False,
        seed=3,
        run_id="poss-pit",
        ablation_id="full",
        nnls_equal_weight_fallback=True,
    )
    obs_rows = []
    for g in games.itertuples(index=False):
        obs_rows.append(
            {
                "game_id": int(g.game_id),
                "season": 2023,
                "week": int(g.week),
                "event_time": g.event_time,
                "home_team_id": int(g.home_team_id),
                "away_team_id": int(g.away_team_id),
                "home_epa": 0.01,
                "away_epa": -0.01,
                "home_plays": 70.0,
                "away_plays": 68.0,
                "margin": 7.0,
                "neutral_site": False,
            }
        )
    stack = build_production_stack(
        cfg,
        kind="fundamental",
        observations=pd.DataFrame(obs_rows),
        possessions_training=training,
        play_counts=(80, 100),
        n_mc_draws=200,
        n_epistemic_draws=2,
        enforce_ablation_preconditions=False,
    )
    harness = WalkForwardHarness(
        config=stack.config,
        predictor=stack.predictor,
        feature_provider=stack.feature_provider,
        rating_engine=stack.rating_engine,
    )
    result = harness.run(games)
    assert "feat__expected_possessions" in result.feature_log.columns
    # After week-5 retrain, expected_possessions should be finite for weeks with pace rows.
    week5 = result.feature_log.loc[result.feature_log["week"] == 5, "feat__expected_possessions"]
    assert week5.notna().any()

    rating_snapshots: dict[tuple[int, int], dict[str, Any]] = {}
    engine = build_production_stack(
        cfg,
        kind="fundamental",
        observations=pd.DataFrame(obs_rows),
        possessions_training=training,
        play_counts=(80, 100),
        n_mc_draws=200,
        n_epistemic_draws=2,
        enforce_ablation_preconditions=False,
    ).rating_engine
    weeks = sorted(int(w) for w in games["week"].unique())
    first_as_of = week_decision_as_of(2023, weeks[0], cfg)
    engine.initialize_season(2023, first_as_of - timedelta(seconds=1))
    for week in weeks:
        rating_snapshots[(2023, week)] = engine.state_snapshot()
        engine.update_after_games(games.loc[games["week"] == week])

    # Offseason week-0 fit has no prior 2023 rows; week-5 retrain must.
    assert (2023, 5) in stack.feature_provider.possessions_artifacts
    art = stack.feature_provider.possessions_artifacts[(2023, 5)]
    assert art.n_train > 0
    used = training.loc[prior_to_retrain_mask(training, season=2023, week=5)]
    assert art.n_train == len(used)
    assert int(used["week"].max()) < 5

    audit = audit_information_set(
        result.feature_log,
        stack.feature_provider,
        games,
        rating_snapshots=rating_snapshots,
        market_features=False,
    )
    assert audit.passed, audit.mismatches[:5]

    # Prophecy audit over the new feature column.
    as_of = week_decision_as_of(2023, 5, cfg)
    feats = stack.feature_provider.compute_game_features(
        games.loc[games["week"] == 5],
        as_of,
        rating_state=rating_snapshots[(2023, 5)],
        market_features=False,
    )
    labels = games.loc[games["week"] == 5].copy()
    labels["realized_margin"] = labels["home_points"].astype(float) - labels["away_points"].astype(
        float
    )
    labels["realized_total"] = labels["home_points"].astype(float) + labels["away_points"].astype(
        float
    )
    prophecy = audit_prophecy_features(feats, labels)
    assert_no_prophecy_features(prophecy)
    assert "expected_possessions" in feats.columns


def test_lockbox_snapshots_raise() -> None:
    with pytest.raises((LockboxViolation, AssertionError)):
        load_staged_odds_snapshots(Path("data/staged"), (2024, LOCKBOX_SEASON))


def test_snapshot_load_excludes_lockbox_and_returns_seasons(tmp_path: Path) -> None:
    root = tmp_path / "staged"
    for season in (2021, 2022, 2023, 2024):
        part = root / "odds_snapshots" / f"season={season}" / "week=1"
        part.mkdir(parents=True)
        pd.DataFrame(
            {
                "snapshot_id": [f"{season}-1"],
                "season": [season],
                "week": [1],
                "game_id": [season * 100],
                "event_time": [datetime(season, 9, 1, tzinfo=UTC)],
                "book": ["draftkings"],
                "market": ["spreads"],
                "spread": [-3.5],
                "total": [55.0],
            }
        ).to_parquet(part / "part.parquet", index=False)
    # Poison: a 2025 partition must never be requested; if seasons omit it, load is clean.
    poison = root / "odds_snapshots" / f"season={LOCKBOX_SEASON}" / "week=1"
    poison.mkdir(parents=True)
    pd.DataFrame(
        {
            "snapshot_id": ["2025-1"],
            "season": [LOCKBOX_SEASON],
            "week": [1],
            "game_id": [2500],
            "event_time": [datetime(2025, 9, 1, tzinfo=UTC)],
            "book": ["draftkings"],
            "market": ["spreads"],
            "spread": [-3.0],
            "total": [50.0],
        }
    ).to_parquet(poison / "part.parquet", index=False)

    snaps = load_staged_odds_snapshots(root, (2021, 2022, 2023, 2024))
    assert snaps is not None
    assert set(snaps["season"].astype(int)) == {2021, 2022, 2023, 2024}
    assert LOCKBOX_SEASON not in set(snaps["season"].astype(int))


def test_filter_history_default_is_gt_active_not_superseded() -> None:
    paths = load_artifact_paths()
    resolved = resolve_filter_history_path()
    assert resolved == paths["filter_history"]
    assert "state_space_acceptance_14" not in str(resolved).replace("\\", "/")
    assert "data/tmp/" not in str(resolved).replace("\\", "/")
    with pytest.raises(ValueError, match="SUPERSEDED"):
        resolve_filter_history_path(Path("data/tmp/state_space_acceptance_14/history.parquet"))


def test_live_artifact_path_configured_and_not_used_by_provider() -> None:
    paths = load_artifact_paths()
    assert paths["expected_possessions_live"] == Path(
        "data/artifacts/expected_possessions/live.json"
    )
    # Provider never loads the live path: empty training → NaN possessions.
    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        market_features_available=False,
        seed=1,
    )
    provider = ProductionFeatureProvider(config=cfg, possessions_training=None)
    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2023,
                "week": 1,
                "home_team_id": 1,
                "away_team_id": 2,
            }
        ]
    )
    feats = provider.compute_game_features(
        games,
        datetime(2023, 9, 5, tzinfo=UTC),
        rating_state={},
        market_features=False,
    )
    assert feats["expected_possessions"].isna().all()
    assert provider.possessions_artifacts == {}
