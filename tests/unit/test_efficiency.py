"""Efficiency builders: ridge recovery, shrinkage, EWMA, PIT (Task 10)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.features.builder import FeatureBuildError
from ncaa_quant.features.builders.efficiency import (
    DEFAULT_RIDGE_LAMBDA,
    FCS_TIER_ENTITY,
    EfficiencyConfig,
    EfficiencyFeatureBuilder,
    bayesian_shrink,
    build_play_game_observations,
    efficiency_config_from_data,
    ewma_final,
    ewma_sequence,
    last_n_delta,
    pool_entity_id,
    resolve_priors,
    ridge_opponent_adjust,
    season_end_adjusted_epa,
)
from ncaa_quant.features.materialize import materialize_partition, read_partition
from ncaa_quant.features.pit_audit import assert_partition_pit_clean
from ncaa_quant.features.registry import FeatureSpec, load_registry


def _eff_spec(name: str = "adj_off_epa_std", **overrides: Any) -> FeatureSpec:
    base: dict[str, Any] = {
        "name": name,
        "version": "1",
        "dtype": "float64",
        "builder": "ncaa_quant.features.builders.efficiency:EfficiencyFeatureBuilder",
        "dependencies": ("raw:plays",),
        "as_of_semantics": "strict_lt",
        "null_policy": "allow",
        "lookback_window": "season_to_date",
        "hypothesis": "Opponent-adjusted efficiency predicts future margins.",
    }
    base.update(overrides)
    return FeatureSpec(
        name=str(base["name"]),
        version=str(base["version"]),
        dtype=str(base["dtype"]),
        builder=str(base["builder"]),
        dependencies=tuple(base["dependencies"]),
        as_of_semantics=str(base["as_of_semantics"]),
        null_policy=base["null_policy"],  # type: ignore[arg-type]
        lookback_window=str(base["lookback_window"]),
        hypothesis=str(base["hypothesis"]),
    )


# ---------------------------------------------------------------------------
# Shrinkage / EWMA unit tests
# ---------------------------------------------------------------------------


def test_bayesian_shrink_at_n_zero_equals_prior() -> None:
    assert bayesian_shrink(observed_mean=10.0, prior=2.0, n=0.0, k=8.0) == pytest.approx(2.0)


def test_bayesian_shrink_at_n_equals_k_is_midpoint() -> None:
    assert bayesian_shrink(observed_mean=10.0, prior=2.0, n=8.0, k=8.0) == pytest.approx(6.0)


def test_bayesian_shrink_n_much_greater_than_k_near_mean() -> None:
    got = bayesian_shrink(observed_mean=10.0, prior=2.0, n=800.0, k=8.0)
    assert got == pytest.approx(10.0 * 800 / 808 + 2.0 * 8 / 808)
    assert abs(got - 10.0) < abs(got - 2.0)


def test_ewma_against_hand_computed_sequence() -> None:
    # half_life=1 â†’ alpha = 1 - 0.5 = 0.5
    values = [1.0, 3.0, 5.0]
    seq = ewma_sequence(values, half_life=1.0)
    assert seq[0] == pytest.approx(1.0)
    assert seq[1] == pytest.approx(0.5 * 3.0 + 0.5 * 1.0)  # 2.0
    assert seq[2] == pytest.approx(0.5 * 5.0 + 0.5 * 2.0)  # 3.5
    assert ewma_final(values, half_life=1.0) == pytest.approx(3.5)


def test_last_n_delta_hand_computed() -> None:
    values = [1.0, 2.0, 3.0, 10.0, 11.0, 12.0]
    # last 3 mean = 11; season mean = 6.5; delta = 4.5
    assert last_n_delta(values, n=3) == pytest.approx(11.0 - 6.5)


def test_fcs_pooling() -> None:
    fbs = {1, 2, 3}
    assert pool_entity_id(1, fbs_team_ids=fbs) == "1"
    assert pool_entity_id(99, fbs_team_ids=fbs) == FCS_TIER_ENTITY


# ---------------------------------------------------------------------------
# Synthetic recovery (critical)
# ---------------------------------------------------------------------------


def _planted_season(
    *,
    n_teams: int = 32,
    games_per_team: int = 12,
    hfa_true: float = 0.04,
    noise_sd: float = 0.02,
    seed: int = 0,
) -> tuple[pd.DataFrame, dict[str, float], dict[str, float]]:
    """Generate a fake season with known off/def strengths and a schedule."""
    rng = np.random.default_rng(seed)
    entities = [f"T{i:02d}" for i in range(n_teams)]
    off_noise = rng.normal(0.0, 0.18, size=n_teams)
    def_noise = rng.normal(0.0, 0.18, size=n_teams)
    off_true = {e: float(x) for e, x in zip(entities, off_noise, strict=True)}
    def_true = {e: float(x) for e, x in zip(entities, def_noise, strict=True)}

    # Round-robin-ish random schedule: each team plays ``games_per_team`` games.
    pairings: list[tuple[str, str, bool]] = []
    for home in entities:
        opponents = [e for e in entities if e != home]
        chosen = rng.choice(opponents, size=games_per_team, replace=False)
        for away in chosen:
            # Avoid duplicate unordered pairs roughly by only keeping half via coin flip
            # when both orderings might appear â€” keep all directed home games.
            pairings.append((home, str(away), True))

    rows: list[dict[str, Any]] = []
    for i, (home, away, _) in enumerate(pairings):
        # Home offense vs away defense
        y_h = off_true[home] - def_true[away] + hfa_true + float(rng.normal(0.0, noise_sd))
        rows.append(
            {
                "offense_id": home,
                "defense_id": away,
                "is_home": True,
                "y": y_h,
                "event_time": pd.Timestamp("2023-09-01", tz="UTC") + pd.Timedelta(days=i % 90),
                "game_id": i,
            }
        )
        # Away offense vs home defense (no HFA)
        y_a = off_true[away] - def_true[home] + float(rng.normal(0.0, noise_sd))
        rows.append(
            {
                "offense_id": away,
                "defense_id": home,
                "is_home": False,
                "y": y_a,
                "event_time": pd.Timestamp("2023-09-01", tz="UTC") + pd.Timedelta(days=i % 90),
                "game_id": i,
            }
        )
    return pd.DataFrame(rows), off_true, def_true


def _rank_overlap(true_map: dict[str, float], est_map: dict[str, float], *, top: int = 10) -> float:
    true_top = {e for e, _ in sorted(true_map.items(), key=lambda kv: kv[1], reverse=True)[:top]}
    est_top = {e for e, _ in sorted(est_map.items(), key=lambda kv: kv[1], reverse=True)[:top]}
    true_bot = {e for e, _ in sorted(true_map.items(), key=lambda kv: kv[1])[:top]}
    est_bot = {e for e, _ in sorted(est_map.items(), key=lambda kv: kv[1])[:top]}
    top_hit = len(true_top & est_top) / top
    bot_hit = len(true_bot & est_bot) / top
    return (top_hit + bot_hit) / 2.0


def test_ridge_synthetic_recovery_correlation_and_ranks() -> None:
    obs, off_true, def_true = _planted_season()
    result = ridge_opponent_adjust(obs, y_col="y", ridge_lambda=DEFAULT_RIDGE_LAMBDA)

    off_est = np.array([result.off_ratings[e] for e in off_true])
    off_t = np.array([off_true[e] for e in off_true])
    def_est = np.array([result.def_ratings[e] for e in def_true])
    def_t = np.array([def_true[e] for e in def_true])

    off_corr = float(np.corrcoef(off_t, off_est)[0, 1])
    def_corr = float(np.corrcoef(def_t, def_est)[0, 1])
    assert off_corr > 0.95, f"off correlation {off_corr:.4f} <= 0.95"
    assert def_corr > 0.95, f"def correlation {def_corr:.4f} <= 0.95"

    # Top and bottom 10 essentially preserved (â‰¥70% set overlap each end, avg).
    off_overlap = _rank_overlap(off_true, result.off_ratings, top=10)
    def_overlap = _rank_overlap(def_true, result.def_ratings, top=10)
    assert off_overlap >= 0.7, f"off rank overlap {off_overlap:.2f}"
    assert def_overlap >= 0.7, f"def rank overlap {def_overlap:.2f}"


def test_ridge_pools_fcs_into_single_entity() -> None:
    rows = [
        {
            "offense_id": 1,
            "defense_id": 100,
            "is_home": True,
            "y": 0.2,
        },
        {
            "offense_id": 1,
            "defense_id": 101,
            "is_home": True,
            "y": 0.25,
        },
        {
            "offense_id": 2,
            "defense_id": 100,
            "is_home": False,
            "y": -0.1,
        },
        {
            "offense_id": 2,
            "defense_id": 1,
            "is_home": False,
            "y": 0.0,
        },
    ]
    result = ridge_opponent_adjust(
        pd.DataFrame(rows),
        y_col="y",
        ridge_lambda=1.0,
        fbs_team_ids={1, 2},
    )
    assert FCS_TIER_ENTITY in result.entities
    assert "100" not in result.entities
    assert "101" not in result.entities


# ---------------------------------------------------------------------------
# Registry + builder PIT
# ---------------------------------------------------------------------------


def test_registry_loads_efficiency_features_with_hypotheses() -> None:
    registry = load_registry()
    assert "adj_off_epa_std" in registry.specs
    assert "adj_def_epa_std" in registry.specs
    assert registry.get("adj_off_epa_std").hypothesis.strip()
    assert (
        registry.get("adj_off_epa_std").builder
        == "ncaa_quant.features.builders.efficiency:EfficiencyFeatureBuilder"
    )


def _history_frame() -> pd.DataFrame:
    """Small two-team history with a planted future game after week-2 as_of."""
    return pd.DataFrame(
        [
            {
                "game_id": 1,
                "offense_id": "A",
                "defense_id": "B",
                "is_home": True,
                "epa_per_play": 0.20,
                "event_time": pd.Timestamp("2023-09-02T17:00:00Z"),
            },
            {
                "game_id": 1,
                "offense_id": "B",
                "defense_id": "A",
                "is_home": False,
                "epa_per_play": -0.05,
                "event_time": pd.Timestamp("2023-09-02T17:00:00Z"),
            },
            {
                "game_id": 2,
                "offense_id": "A",
                "defense_id": "B",
                "is_home": False,
                "epa_per_play": 0.10,
                "event_time": pd.Timestamp("2023-09-09T17:00:00Z"),
            },
            {
                "game_id": 2,
                "offense_id": "B",
                "defense_id": "A",
                "is_home": True,
                "epa_per_play": 0.00,
                "event_time": pd.Timestamp("2023-09-09T17:00:00Z"),
            },
            # Future relative to as_of 2023-09-10 â€” must not affect features.
            {
                "game_id": 3,
                "offense_id": "A",
                "defense_id": "B",
                "is_home": True,
                "epa_per_play": 5.0,
                "event_time": pd.Timestamp("2023-09-16T17:00:00Z"),
            },
            {
                "game_id": 3,
                "offense_id": "B",
                "defense_id": "A",
                "is_home": False,
                "epa_per_play": -5.0,
                "event_time": pd.Timestamp("2023-09-16T17:00:00Z"),
            },
        ]
    )


def test_pit_audit_passes_on_materialized_efficiency(tmp_path: Any) -> None:
    history = _history_frame()
    as_of = datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC)
    builder = EfficiencyFeatureBuilder(
        _eff_spec("adj_off_epa_std"),
        history,
        config=EfficiencyConfig(ridge_lambda=1.0, shrinkage_k=1.0),
    )
    result = materialize_partition(
        builder,
        entity_ids=["A", "B"],
        as_of=as_of,
        season=2023,
        week=2,
        output_root=tmp_path / "features",
    )
    stored = read_partition(tmp_path / "features", result.partition)
    audit = assert_partition_pit_clean(stored, builder, history, sample_size=10, seed=0)
    assert audit.passed is True


def test_builder_ignores_future_games() -> None:
    history = _history_frame()
    as_of = datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC)
    builder = EfficiencyFeatureBuilder(
        _eff_spec("adj_off_epa_std"),
        history,
        config=EfficiencyConfig(ridge_lambda=1.0, shrinkage_k=0.0),
    )
    out = builder.build(["A"], as_of)
    # Recompute with history truncated manually â€” must match.
    truncated = history.loc[history["event_time"] < pd.Timestamp(as_of)].copy()
    builder2 = EfficiencyFeatureBuilder(
        _eff_spec("adj_off_epa_std"),
        truncated,
        config=EfficiencyConfig(ridge_lambda=1.0, shrinkage_k=0.0),
    )
    out2 = builder2.build(["A"], as_of)
    assert float(out["value"].iloc[0]) == pytest.approx(float(out2["value"].iloc[0]))


def test_build_play_game_observations_smoke() -> None:
    plays = pd.DataFrame(
        [
            {
                "game_id": 1,
                "offense_id": 10,
                "defense_id": 20,
                "offense_team": "10",
                "defense_team": "20",
                "epa": 0.5,
                "is_rush": True,
                "is_pass": False,
                "is_special_teams": False,
                "is_penalty": False,
                "is_havoc": False,
                "is_success": True,
                "garbage_time": False,
                "down": 1,
                "distance": 10,
                "yards_gained": 6,
                "wp_before": 0.5,
                "period": 1,
                "score_margin": 0,
            },
            {
                "game_id": 1,
                "offense_id": 10,
                "defense_id": 20,
                "offense_team": "10",
                "defense_team": "20",
                "epa": -0.1,
                "is_rush": False,
                "is_pass": True,
                "is_special_teams": False,
                "is_penalty": False,
                "is_havoc": False,
                "is_success": False,
                "garbage_time": False,
                "down": 2,
                "distance": 4,
                "yards_gained": 1,
                "wp_before": 0.5,
                "period": 1,
                "score_margin": 0,
            },
        ]
    )
    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "home_team_id": 10,
                "neutral_site": False,
                "start_date": pd.Timestamp("2023-09-02T17:00:00Z"),
                "season": 2023,
                "week": 1,
            }
        ]
    )
    teams = pd.DataFrame(
        [
            {"team_id": 10, "school": "Home", "classification": "fbs"},
            {"team_id": 20, "school": "Away", "classification": "fbs"},
        ]
    )
    obs = build_play_game_observations(plays, games, teams, drop_garbage=False)
    assert not obs.empty
    assert "epa_per_play" in obs.columns
    assert bool(obs.loc[obs["offense_id"] == "10", "is_home"].iloc[0]) is True


def test_season_end_adjusted_epa_excludes_fcs_tier() -> None:
    obs, _, _ = _planted_season(n_teams=8, games_per_team=4, seed=1)
    # Inject an FCS-tier row.
    extra = obs.iloc[[0]].copy()
    extra["offense_id"] = FCS_TIER_ENTITY
    obs2 = pd.concat([obs, extra], ignore_index=True)
    table = season_end_adjusted_epa(obs2.rename(columns={"y": "epa_per_play"}))
    assert FCS_TIER_ENTITY not in set(table["entity_id"])


def test_shrink_and_ewma_reject_bad_args() -> None:
    with pytest.raises(ValueError, match="k must"):
        bayesian_shrink(1.0, 0.0, n=1.0, k=-1.0)
    with pytest.raises(ValueError, match="n must"):
        bayesian_shrink(1.0, 0.0, n=-1.0, k=1.0)
    assert bayesian_shrink(1.0, 2.0, n=0.0, k=0.0) == pytest.approx(2.0)
    with pytest.raises(ValueError, match="half_life"):
        ewma_final([1.0], half_life=0.0)
    assert math.isnan(ewma_final([], half_life=1.0))
    assert math.isnan(last_n_delta([], n=3))


def test_ridge_empty_and_bad_lambda() -> None:
    empty = ridge_opponent_adjust(
        pd.DataFrame(columns=["y", "offense_id", "defense_id", "is_home"])
    )
    assert empty.n_obs == 0
    assert empty.off_ratings == {}
    with pytest.raises(ValueError, match="ridge_lambda"):
        ridge_opponent_adjust(
            pd.DataFrame([{"y": 0.1, "offense_id": "A", "defense_id": "B", "is_home": True}]),
            ridge_lambda=-1.0,
        )
    with pytest.raises(ValueError, match="missing columns"):
        ridge_opponent_adjust(pd.DataFrame({"y": [1.0]}))


def test_builder_ewma_and_l3d_forms() -> None:
    history = _history_frame()
    as_of = datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC)
    cfg = EfficiencyConfig(ridge_lambda=1.0, shrinkage_k=0.0, ewma_half_life_efficiency=2.0)
    ewma_out = EfficiencyFeatureBuilder(_eff_spec("adj_off_epa_ewma"), history, config=cfg).build(
        ["A"], as_of
    )
    l3d_out = EfficiencyFeatureBuilder(_eff_spec("adj_off_epa_l3d"), history, config=cfg).build(
        ["A"], as_of
    )
    assert not math.isnan(float(ewma_out["value"].iloc[0]))
    assert not math.isnan(float(l3d_out["value"].iloc[0]))


def test_builder_indicator_null_policy_and_bad_name() -> None:
    history = _history_frame()
    as_of = datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC)
    spec = _eff_spec("adj_off_epa_std", null_policy="indicator")
    out = EfficiencyFeatureBuilder(spec, history.iloc[0:0], config=EfficiencyConfig()).build(
        ["Z"], as_of
    )
    assert bool(out["is_missing"].iloc[0]) is True
    with pytest.raises(FeatureBuildError, match="unsupported"):
        EfficiencyFeatureBuilder(_eff_spec("not_a_real_feature"), history)


def test_observations_with_drives_and_config_from_data() -> None:
    plays = pd.DataFrame(
        [
            {
                "game_id": 1,
                "offense_id": 10,
                "defense_id": 20,
                "offense_team": "10",
                "defense_team": "20",
                "epa": 0.5,
                "is_rush": True,
                "is_pass": False,
                "is_special_teams": False,
                "is_penalty": False,
                "is_havoc": True,
                "is_success": True,
                "garbage_time": False,
            },
            {
                "game_id": 1,
                "offense_id": 10,
                "defense_id": 20,
                "offense_team": "10",
                "defense_team": "20",
                "epa": 0.2,
                "is_rush": False,
                "is_pass": True,
                "is_special_teams": False,
                "is_penalty": False,
                "is_havoc": False,
                "is_success": False,
                "garbage_time": False,
            },
        ]
    )
    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "home_team_id": 10,
                "neutral_site": False,
                "start_date": pd.Timestamp("2023-09-02T17:00:00Z"),
                "season": 2023,
                "week": 1,
            }
        ]
    )
    teams = pd.DataFrame(
        [
            {"team_id": 10, "school": "Home", "classification": "fbs"},
            {"team_id": 20, "school": "Away", "classification": "fcs"},
        ]
    )
    drives = pd.DataFrame(
        [
            {
                "game_id": 1,
                "offense_id": 10,
                "start_yards_to_goal": 30,
                "points": 7,
            },
            {
                "game_id": 1,
                "offense_id": 10,
                "start_yards_to_goal": 75,
                "points": 0,
            },
        ]
    )
    obs = build_play_game_observations(plays, games, teams, drives=drives, drop_garbage=False)
    assert float(obs["finishing_drives"].iloc[0]) == pytest.approx(7.0)
    assert float(obs["field_position"].iloc[0]) == pytest.approx((70.0 + 25.0) / 2.0)
    assert obs["defense_id"].iloc[0] == FCS_TIER_ENTITY

    class _Data:
        ridge_lambda_efficiency = 3.0
        shrinkage_k_efficiency = 7.0
        ewma_half_life_efficiency = 5.0
        ewma_half_life_explosiveness = 9.0

    cfg = efficiency_config_from_data(_Data())
    assert cfg.ridge_lambda == 3.0
    assert cfg.half_life_for("explosiveness") == 9.0
    assert cfg.half_life_for("epa") == 5.0

    priors = resolve_priors(["A", "B"], league_mean=0.0, prior_lookup={"A": 1.5})
    assert priors["A"] == 1.5
    assert priors["B"] == 0.0
