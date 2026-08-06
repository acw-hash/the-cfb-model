"""Tempo + situational builders (Task 11)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.features.builders.situational import (
    BYE_DAYS,
    SHORT_WEEK_DAYS,
    SituationalFeatureBuilder,
    build_situational_frame,
    haversine_km,
    is_bye,
    is_short_week,
    load_rivalry_pairs,
    rest_days_between,
    tz_crossed_signed,
)
from ncaa_quant.features.builders.tempo import (
    ExpectedPossessionsArtifact,
    ExpectedPossessionsFeatureBuilder,
    TempoConfig,
    TempoFeatureBuilder,
    annotate_tempo_exclusions,
    build_expected_possessions_training_frame,
    build_tempo_observations,
    expected_possessions_oos_mae,
    fit_expected_possessions,
    game_possessions,
    is_end_of_half,
    is_kneel_or_spike,
    save_expected_possessions_artifact,
)
from ncaa_quant.features.materialize import materialize_partition, read_partition
from ncaa_quant.features.pit_audit import assert_partition_pit_clean
from ncaa_quant.features.registry import FeatureSpec, load_registry


def _tempo_spec(name: str = "adj_plays_per_game_std", **overrides: Any) -> FeatureSpec:
    base: dict[str, Any] = {
        "name": name,
        "version": "1",
        "dtype": "float64",
        "builder": "ncaa_quant.features.builders.tempo:TempoFeatureBuilder",
        "dependencies": ("raw:plays",),
        "as_of_semantics": "strict_lt",
        "null_policy": "allow",
        "lookback_window": "season_to_date",
        "hypothesis": "Tempo predicts possessions and totals.",
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


def _sit_spec(name: str, **overrides: Any) -> FeatureSpec:
    base: dict[str, Any] = {
        "name": name,
        "version": "1",
        "dtype": "float64",
        "builder": "ncaa_quant.features.builders.situational:SituationalFeatureBuilder",
        "dependencies": ("raw:games",),
        "as_of_semantics": "strict_lt",
        "null_policy": "allow",
        "lookback_window": "schedule",
        "hypothesis": "Situational context predicts margins and totals.",
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
# Haversine / timezone / rest
# ---------------------------------------------------------------------------


def test_haversine_known_city_pairs() -> None:
    # NYC (JFK-ish) ↔ LAX great-circle ≈ 3970 km; allow ±2%.
    nyc_lax = haversine_km(40.6413, -73.7781, 33.9425, -118.4081)
    assert nyc_lax == pytest.approx(3974.0, rel=0.02)

    # ORD ↔ JFK great-circle ≈ 1188 km.
    chi_nyc = haversine_km(41.9786, -87.9048, 40.6413, -73.7781)
    assert chi_nyc == pytest.approx(1188.0, rel=0.02)

    assert haversine_km(0.0, 0.0, 0.0, 0.0) == pytest.approx(0.0)


def test_timezone_crossing_direction_sign() -> None:
    # November (standard time): Pacific → Eastern is eastward → positive.
    at = datetime(2023, 11, 4, 20, 0, 0, tzinfo=UTC)
    eastward = tz_crossed_signed("America/Los_Angeles", "America/New_York", at=at)
    assert eastward == pytest.approx(3.0)
    westward = tz_crossed_signed("America/New_York", "America/Los_Angeles", at=at)
    assert westward == pytest.approx(-3.0)
    assert eastward > 0 > westward


def test_rest_days_bye_and_midweek() -> None:
    sat = datetime(2023, 9, 2, 19, 0, 0, tzinfo=UTC)
    next_sat = datetime(2023, 9, 9, 19, 0, 0, tzinfo=UTC)
    thu = datetime(2023, 9, 7, 23, 30, 0, tzinfo=UTC)
    after_bye = datetime(2023, 9, 16, 19, 0, 0, tzinfo=UTC)

    normal = rest_days_between(sat, next_sat)
    assert normal == pytest.approx(7.0)
    assert not is_short_week(normal)
    assert not is_bye(normal)

    short = rest_days_between(sat, thu)
    assert short == pytest.approx(5.0 + 4.5 / 24.0, abs=0.05)
    assert short < SHORT_WEEK_DAYS
    assert is_short_week(short)

    bye = rest_days_between(sat, after_bye)
    assert bye >= BYE_DAYS
    assert is_bye(bye)


# ---------------------------------------------------------------------------
# Tempo exclusions / observations
# ---------------------------------------------------------------------------


def test_kneel_spike_and_end_of_half_rules() -> None:
    assert is_kneel_or_spike("Kneel")
    assert is_kneel_or_spike("Spike")
    assert is_kneel_or_spike("QB Kneel")
    assert not is_kneel_or_spike("Rush")
    assert is_end_of_half(2, 90)
    assert is_end_of_half(4, 120)
    assert not is_end_of_half(1, 90)
    assert not is_end_of_half(2, 200)


def test_annotate_excludes_hurry_up_and_kneel() -> None:
    plays = pd.DataFrame(
        [
            {
                "game_id": 1,
                "drive_id": 1,
                "play_id": 1,
                "offense_id": 10,
                "period": 1,
                "clock": 800,
                "play_type": "Rush",
                "is_rush": True,
                "is_pass": False,
                "garbage_time": False,
            },
            {
                "game_id": 1,
                "drive_id": 1,
                "play_id": 2,
                "offense_id": 10,
                "period": 1,
                "clock": 795,  # 5s elapsed → hurry-up
                "play_type": "Pass Reception",
                "is_rush": False,
                "is_pass": True,
                "garbage_time": False,
            },
            {
                "game_id": 1,
                "drive_id": 1,
                "play_id": 3,
                "offense_id": 10,
                "period": 1,
                "clock": 760,
                "play_type": "Kneel",
                "is_rush": True,
                "is_pass": False,
                "garbage_time": False,
            },
        ]
    )
    out = annotate_tempo_exclusions(plays)
    assert bool(out.loc[out["play_id"] == 2, "is_hurry_up"].iloc[0]) is True
    assert bool(out.loc[out["play_id"] == 3, "is_kneel_spike"].iloc[0]) is True
    assert bool(out.loc[out["play_id"] == 2, "neutral_eligible"].iloc[0]) is False


def test_expected_possessions_sane_range_and_oos_mae() -> None:
    """Synthetic seasons: predictions in ~20–30; OOS MAE reported."""
    rng = np.random.default_rng(0)
    rows: list[dict[str, Any]] = []
    for season in (2021, 2022, 2023):
        for _i in range(80):
            home_pace = float(rng.uniform(55, 80))
            away_pace = float(rng.uniform(55, 80))
            home_pass = float(rng.uniform(0.35, 0.60))
            away_pass = float(rng.uniform(0.35, 0.60))
            # Planted: possessions ≈ 12 + 0.12*(pace sum) + noise → ~25–30.
            poss = 12.0 + 0.12 * (home_pace + away_pace) + float(rng.normal(0, 0.8))
            rows.append(
                {
                    "season": season,
                    "home_pace": home_pace,
                    "away_pace": away_pace,
                    "home_pass_rate": home_pass,
                    "away_pass_rate": away_pass,
                    "possessions": poss,
                }
            )
    frame = pd.DataFrame(rows)
    train = frame["season"].isin([2021, 2022])
    test = frame["season"] == 2023
    artifact, mae = expected_possessions_oos_mae(frame, train_mask=train, test_mask=test)
    assert artifact.oos_mae == pytest.approx(mae)
    preds = artifact.predict_frame(frame.loc[test])
    assert float(np.min(preds)) >= 18.0
    assert float(np.max(preds)) <= 35.0
    assert 0.0 < mae < 3.0
    # Expose for notes / acceptance dump.
    assert mae == pytest.approx(artifact.oos_mae)


def test_expected_possessions_artifact_roundtrip(tmp_path: Path) -> None:
    art = ExpectedPossessionsArtifact(
        intercept=10.0,
        coefficients=(0.1, 0.1, 2.0, 2.0),
        feature_names=("home_pace", "away_pace", "home_pass_rate", "away_pass_rate"),
        train_seasons=(2021, 2022),
        oos_mae=1.2,
        n_train=100,
        target_mean=25.0,
    )
    path = save_expected_possessions_artifact(art, tmp_path / "exp_pos.json")
    from ncaa_quant.features.builders.tempo import load_expected_possessions_artifact

    loaded = load_expected_possessions_artifact(path)
    assert loaded.intercept == pytest.approx(10.0)
    assert loaded.predict_row(
        {
            "home_pace": 70.0,
            "away_pace": 70.0,
            "home_pass_rate": 0.5,
            "away_pass_rate": 0.5,
        }
    ) == pytest.approx(10.0 + 14.0 + 2.0)


def test_tempo_builder_and_pit_audit(tmp_path: Path) -> None:
    history = pd.DataFrame(
        [
            {
                "game_id": 1,
                "offense_id": "A",
                "defense_id": "B",
                "is_home": True,
                "plays_per_game": 70.0,
                "event_time": pd.Timestamp("2023-09-02T17:00:00Z"),
            },
            {
                "game_id": 1,
                "offense_id": "B",
                "defense_id": "A",
                "is_home": False,
                "plays_per_game": 60.0,
                "event_time": pd.Timestamp("2023-09-02T17:00:00Z"),
            },
            {
                "game_id": 2,
                "offense_id": "A",
                "defense_id": "B",
                "is_home": False,
                "plays_per_game": 72.0,
                "event_time": pd.Timestamp("2023-09-09T17:00:00Z"),
            },
            {
                "game_id": 2,
                "offense_id": "B",
                "defense_id": "A",
                "is_home": True,
                "plays_per_game": 58.0,
                "event_time": pd.Timestamp("2023-09-09T17:00:00Z"),
            },
            # Future game — must not leak into as_of before it.
            {
                "game_id": 3,
                "offense_id": "A",
                "defense_id": "B",
                "is_home": True,
                "plays_per_game": 99.0,
                "event_time": pd.Timestamp("2023-09-16T17:00:00Z"),
            },
        ]
    )
    as_of = datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC)
    builder = TempoFeatureBuilder(
        _tempo_spec(),
        history,
        config=TempoConfig(ridge_lambda=1.0, shrinkage_k=0.0),
    )
    out = builder.build(["A"], as_of)
    assert not math.isnan(float(out["value"].iloc[0]))

    result = materialize_partition(
        builder,
        entity_ids=["A", "B"],
        as_of=as_of,
        season=2023,
        week=2,
        output_root=tmp_path,
    )
    stored = read_partition(tmp_path, result.partition)
    assert_partition_pit_clean(stored, builder, history, sample_size=2, seed=1)


def test_expected_possessions_builder_pit(tmp_path: Path) -> None:
    artifact = fit_expected_possessions(
        pd.DataFrame(
            [
                {
                    "home_pace": 70.0,
                    "away_pace": 65.0,
                    "home_pass_rate": 0.5,
                    "away_pass_rate": 0.45,
                    "possessions": 26.0,
                },
                {
                    "home_pace": 60.0,
                    "away_pace": 60.0,
                    "home_pass_rate": 0.4,
                    "away_pass_rate": 0.4,
                    "possessions": 24.0,
                },
                {
                    "home_pace": 75.0,
                    "away_pace": 70.0,
                    "home_pass_rate": 0.55,
                    "away_pass_rate": 0.5,
                    "possessions": 28.0,
                },
            ]
        )
    )
    history = pd.DataFrame(
        [
            {
                "game_id": 10,
                "event_time": pd.Timestamp("2023-09-01T12:00:00Z"),
                "home_pace": 70.0,
                "away_pace": 65.0,
                "home_pass_rate": 0.5,
                "away_pass_rate": 0.45,
            },
            {
                "game_id": 11,
                "event_time": pd.Timestamp("2023-09-20T12:00:00Z"),
                "home_pace": 99.0,
                "away_pace": 99.0,
                "home_pass_rate": 0.9,
                "away_pass_rate": 0.9,
            },
        ]
    )
    as_of = datetime(2023, 9, 5, 12, 0, 0, tzinfo=UTC)
    spec = _tempo_spec(
        "expected_possessions",
        builder="ncaa_quant.features.builders.tempo:ExpectedPossessionsFeatureBuilder",
    )
    builder = ExpectedPossessionsFeatureBuilder(spec, history, artifact=artifact)
    out = builder.build([10, 11], as_of)
    by_id = dict(zip(out["entity_id"], out["value"], strict=True))
    assert not math.isnan(float(by_id[10]))
    assert 20.0 <= float(by_id[10]) <= 30.0
    assert math.isnan(float(by_id[11]))  # future row filtered

    result = materialize_partition(
        builder,
        entity_ids=[10],
        as_of=as_of,
        season=2023,
        week=1,
        output_root=tmp_path,
    )
    stored = read_partition(tmp_path, result.partition)
    assert_partition_pit_clean(stored, builder, history, sample_size=1, seed=0)


# ---------------------------------------------------------------------------
# Situational frame + rivalry
# ---------------------------------------------------------------------------


def test_neutral_site_averages_travel_and_tz() -> None:
    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2023,
                "week": 1,
                "home_team_id": 1,
                "away_team_id": 2,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": 100,
                "start_date": pd.Timestamp("2023-09-02T19:00:00Z"),
                "event_time": pd.Timestamp("2023-09-02T19:00:00Z"),
            },
            {
                "game_id": 2,
                "season": 2023,
                "week": 1,
                "home_team_id": 2,
                "away_team_id": 1,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": 200,
                "start_date": pd.Timestamp("2023-09-02T20:00:00Z"),
                "event_time": pd.Timestamp("2023-09-02T20:00:00Z"),
            },
            {
                "game_id": 3,
                "season": 2023,
                "week": 5,
                "home_team_id": 1,
                "away_team_id": 2,
                "neutral_site": True,
                "conference_game": False,
                "venue_id": 300,
                "start_date": pd.Timestamp("2023-10-01T19:00:00Z"),
                "event_time": pd.Timestamp("2023-10-01T19:00:00Z"),
            },
        ]
    )
    venues = pd.DataFrame(
        [
            {
                "venue_id": 100,
                "latitude": 42.0,
                "longitude": -83.0,
                "elevation_m": 200.0,
                "timezone": "America/Detroit",
                "surface": "turf",
            },
            {
                "venue_id": 200,
                "latitude": 34.0,
                "longitude": -118.0,
                "elevation_m": 50.0,
                "timezone": "America/Los_Angeles",
                "surface": "grass",
            },
            {
                "venue_id": 300,
                "latitude": 33.75,
                "longitude": -84.40,
                "elevation_m": 300.0,
                "timezone": "America/New_York",
                "surface": "turf",
            },
        ]
    )
    teams = pd.DataFrame(
        [
            {"team_id": 1, "school": "Michigan"},
            {"team_id": 2, "school": "USC"},
        ]
    )
    frame = build_situational_frame(games, venues, teams, rivalry_pairs=frozenset())
    g3 = frame.loc[frame["game_id"] == 3].iloc[0]
    assert g3["neutral_site"] == pytest.approx(1.0)
    assert g3["travel_km"] > 1000.0
    assert not math.isnan(float(g3["tz_crossed"]))
    assert not math.isnan(float(g3["altitude_delta_m"]))


def test_rivalry_config_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError):
        load_rivalry_pairs(missing)
    bad = tmp_path / "bad.yaml"
    bad.write_text("rivalries: notalist\n", encoding="utf-8")
    with pytest.raises(ValueError, match="list"):
        load_rivalry_pairs(bad)
    bad2 = tmp_path / "bad2.yaml"
    bad2.write_text("rivalries:\n  - [OnlyOne]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="2-item"):
        load_rivalry_pairs(bad2)


def test_load_rivalry_pairs_from_config() -> None:
    pairs = load_rivalry_pairs(Path("configs/rivalries.yaml"))
    assert frozenset({"Michigan", "Ohio State"}) in pairs
    assert len(pairs) >= 60


def test_tempo_config_from_data_and_period_advance_elapsed() -> None:
    from ncaa_quant.features.builders.tempo import (
        inter_snap_elapsed_seconds,
        tempo_config_from_data,
    )

    class _Data:
        ewma_half_life_tempo = 12.0
        ridge_lambda_efficiency = 4.0
        shrinkage_k_efficiency = 9.0

    cfg = tempo_config_from_data(_Data())
    assert cfg.ewma_half_life_tempo == pytest.approx(12.0)
    assert cfg.ridge_lambda == pytest.approx(4.0)
    # Period advance: end of Q1 (clock 5) → early Q2 (clock 890) ≈ 5 + (900-890) = 15.
    assert inter_snap_elapsed_seconds(1, 5, 2, 890) == pytest.approx(15.0)
    assert math.isnan(inter_snap_elapsed_seconds(1, 100, 3, 100))


def test_expected_possessions_builder_rejects_wrong_name() -> None:
    from ncaa_quant.features.builder import FeatureBuildError

    art = ExpectedPossessionsArtifact(
        intercept=20.0,
        coefficients=(0.0, 0.0, 0.0, 0.0),
        feature_names=("home_pace", "away_pace", "home_pass_rate", "away_pass_rate"),
        train_seasons=(),
    )
    with pytest.raises(FeatureBuildError, match="expected_possessions"):
        ExpectedPossessionsFeatureBuilder(
            _tempo_spec("adj_plays_per_game_std"), pd.DataFrame(), artifact=art
        )
    pairs = load_rivalry_pairs(Path("configs/rivalries.yaml"))
    assert frozenset({"Michigan", "Ohio State"}) in pairs
    assert len(pairs) >= 60


def test_situational_rest_travel_rivalry() -> None:
    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2023,
                "week": 1,
                "home_team_id": 1,
                "away_team_id": 2,
                "neutral_site": False,
                "conference_game": True,
                "venue_id": 100,
                "start_date": pd.Timestamp("2023-09-02T19:00:00Z"),
                "event_time": pd.Timestamp("2023-09-02T19:00:00Z"),
            },
            {
                "game_id": 2,
                "season": 2023,
                "week": 2,
                "home_team_id": 1,
                "away_team_id": 3,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": 100,
                # Thursday after Saturday → short week for home.
                "start_date": pd.Timestamp("2023-09-07T23:30:00Z"),
                "event_time": pd.Timestamp("2023-09-07T23:30:00Z"),
            },
            {
                "game_id": 3,
                "season": 2023,
                "week": 4,
                "home_team_id": 1,
                "away_team_id": 2,
                "neutral_site": False,
                "conference_game": True,
                "venue_id": 100,
                # Bye for home after week-2 Thursday.
                "start_date": pd.Timestamp("2023-09-23T19:00:00Z"),
                "event_time": pd.Timestamp("2023-09-23T19:00:00Z"),
            },
        ]
    )
    venues = pd.DataFrame(
        [
            {
                "venue_id": 100,
                "latitude": 42.0,
                "longitude": -83.0,
                "elevation_m": 200.0,
                "timezone": "America/Detroit",
                "surface": "turf",
            },
            {
                "venue_id": 200,
                "latitude": 34.0,
                "longitude": -118.0,
                "elevation_m": 50.0,
                "timezone": "America/Los_Angeles",
                "surface": "grass",
            },
        ]
    )
    # Team 2's home venue on the west coast for travel/tz.
    games_home2 = games.copy()
    # Add a prior home game for team 2 at venue 200 so modal home venue resolves.
    extra = pd.DataFrame(
        [
            {
                "game_id": 0,
                "season": 2023,
                "week": 0,
                "home_team_id": 2,
                "away_team_id": 9,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": 200,
                "start_date": pd.Timestamp("2023-08-26T19:00:00Z"),
                "event_time": pd.Timestamp("2023-08-26T19:00:00Z"),
            }
        ]
    )
    games_all = pd.concat([extra, games_home2], ignore_index=True)
    teams = pd.DataFrame(
        [
            {"team_id": 1, "school": "Michigan"},
            {"team_id": 2, "school": "Ohio State"},
            {"team_id": 3, "school": "Akron"},
            {"team_id": 9, "school": "Dummy"},
        ]
    )
    pairs = frozenset({frozenset({"Michigan", "Ohio State"})})
    frame = build_situational_frame(games_all, venues, teams, rivalry_pairs=pairs)

    g2 = frame.loc[frame["game_id"] == 2].iloc[0]
    assert g2["short_week_flag"] == pytest.approx(1.0)
    assert g2["rest_days_home"] < SHORT_WEEK_DAYS

    g3 = frame.loc[frame["game_id"] == 3].iloc[0]
    assert g3["bye_flag"] == pytest.approx(1.0)
    assert g3["rivalry_flag"] == pytest.approx(1.0)
    assert g3["travel_km"] > 2000.0
    assert g3["tz_crossed"] > 0.0  # west → east for OSU visitor?
    # Away is Ohio State whose home is LA in this fixture → traveling to Detroit (east) → +.
    assert g3["tz_crossed"] == pytest.approx(3.0)
    assert g3["surface_change_flag"] == pytest.approx(1.0)
    # Away (Ohio State) previously played Michigan in game 1 → post-rivalry.
    assert g3["post_rivalry_flag"] == pytest.approx(1.0)

    g1 = frame.loc[frame["game_id"] == 1].iloc[0]
    assert g1["rivalry_flag"] == pytest.approx(1.0)
    # Away team's next scheduled game is the rematch (game 3) → lookahead.
    assert g1["rivalry_lookahead_flag"] == pytest.approx(1.0)


def test_situational_pit_audit(tmp_path: Path) -> None:
    history = pd.DataFrame(
        [
            {
                "game_id": 1,
                "event_time": pd.Timestamp("2023-08-20T12:00:00Z"),
                "rest_days_diff": 0.0,
                "travel_km": 500.0,
                "neutral_site": 0.0,
                "week_number": 1.0,
                "month": 9.0,
                "conference_game": 1.0,
                "rivalry_flag": 0.0,
                "short_week_flag": 0.0,
                "bye_flag": 0.0,
                "tz_crossed": 0.0,
                "altitude_delta_m": 0.0,
                "surface_turf": 1.0,
                "surface_change_flag": 0.0,
                "rest_days_home": 7.0,
                "rest_days_away": 7.0,
                "post_rivalry_flag": 0.0,
                "rivalry_lookahead_flag": 0.0,
            },
            {
                "game_id": 2,
                "event_time": pd.Timestamp("2023-09-20T12:00:00Z"),
                "rest_days_diff": 2.0,
                "travel_km": 9999.0,
                "neutral_site": 0.0,
                "week_number": 4.0,
                "month": 9.0,
                "conference_game": 0.0,
                "rivalry_flag": 1.0,
                "short_week_flag": 0.0,
                "bye_flag": 0.0,
                "tz_crossed": 1.0,
                "altitude_delta_m": 100.0,
                "surface_turf": 0.0,
                "surface_change_flag": 1.0,
                "rest_days_home": 8.0,
                "rest_days_away": 6.0,
                "post_rivalry_flag": 0.0,
                "rivalry_lookahead_flag": 0.0,
            },
        ]
    )
    as_of = datetime(2023, 9, 1, 12, 0, 0, tzinfo=UTC)
    builder = SituationalFeatureBuilder(_sit_spec("travel_km"), history)
    out = builder.build([1, 2], as_of)
    by_id = dict(zip(out["entity_id"], out["value"], strict=True))
    assert by_id[1] == pytest.approx(500.0)
    assert math.isnan(float(by_id[2]))

    result = materialize_partition(
        builder,
        entity_ids=[1],
        as_of=as_of,
        season=2023,
        week=1,
        output_root=tmp_path,
    )
    stored = read_partition(tmp_path, result.partition)
    assert_partition_pit_clean(stored, builder, history, sample_size=1, seed=0)


def test_registry_includes_tempo_and_situational() -> None:
    registry = load_registry()
    assert "adj_plays_per_game_std" in registry.specs
    assert "expected_possessions" in registry.specs
    assert "travel_km" in registry.specs
    assert "rivalry_flag" in registry.specs
    assert all(registry.get(n).hypothesis.strip() for n in registry.names())


def test_build_tempo_observations_smoke() -> None:
    plays = pd.DataFrame(
        [
            {
                "game_id": 1,
                "drive_id": 1,
                "play_id": i,
                "offense_id": 10,
                "defense_id": 20,
                "period": 1,
                "clock": 800 - 30 * i,
                "down": 1,
                "distance": 10,
                "score_margin": 0,
                "play_type": "Rush" if i % 2 == 0 else "Pass Reception",
                "is_rush": i % 2 == 0,
                "is_pass": i % 2 == 1,
                "garbage_time": False,
            }
            for i in range(6)
        ]
    )
    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "home_team_id": 10,
                "away_team_id": 20,
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
    obs = build_tempo_observations(plays, games, teams, drop_garbage=False)
    assert not obs.empty
    assert "plays_per_game" in obs.columns
    assert "sec_per_play" in obs.columns
    assert "pass_rate_oe" in obs.columns


def test_game_possessions_and_training_frame() -> None:
    drives = pd.DataFrame(
        {
            "game_id": [1, 1, 1, 2, 2],
            "offense_id": [10, 20, 10, 10, 20],
        }
    )
    poss = game_possessions(drives)
    assert float(poss.loc[poss["game_id"] == 1, "possessions"].iloc[0]) == 3.0

    tempo_obs = pd.DataFrame(
        [
            {
                "game_id": 1,
                "offense_id": "10",
                "defense_id": "20",
                "plays_per_game": 70.0,
                "pass_rate": 0.5,
                "event_time": pd.Timestamp("2023-09-02T17:00:00Z"),
                "is_home": True,
            },
            {
                "game_id": 1,
                "offense_id": "20",
                "defense_id": "10",
                "plays_per_game": 65.0,
                "pass_rate": 0.45,
                "event_time": pd.Timestamp("2023-09-02T17:00:00Z"),
                "is_home": False,
            },
            {
                "game_id": 2,
                "offense_id": "10",
                "defense_id": "20",
                "plays_per_game": 72.0,
                "pass_rate": 0.52,
                "event_time": pd.Timestamp("2023-09-09T17:00:00Z"),
                "is_home": True,
            },
            {
                "game_id": 2,
                "offense_id": "20",
                "defense_id": "10",
                "plays_per_game": 66.0,
                "pass_rate": 0.44,
                "event_time": pd.Timestamp("2023-09-09T17:00:00Z"),
                "is_home": False,
            },
        ]
    )
    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "home_team_id": 10,
                "away_team_id": 20,
                "neutral_site": False,
                "event_time": pd.Timestamp("2023-09-02T17:00:00Z"),
                "season": 2023,
            },
            {
                "game_id": 2,
                "home_team_id": 10,
                "away_team_id": 20,
                "neutral_site": False,
                "event_time": pd.Timestamp("2023-09-09T17:00:00Z"),
                "season": 2023,
            },
        ]
    )
    train = build_expected_possessions_training_frame(tempo_obs, games, drives)
    # Game 2 has prior games for both sides.
    assert set(train["game_id"].tolist()) == {2}
    assert float(train["home_pace"].iloc[0]) == pytest.approx(70.0)
    assert float(train["possessions"].iloc[0]) == pytest.approx(2.0)
    # Single-row fit is underdetermined; multi-row fit covered in OOS MAE test.
    multi = pd.concat([train, train.assign(home_pace=68.0, possessions=2.2)], ignore_index=True)
    art = fit_expected_possessions(multi)
    pred = art.predict_row(
        {
            "home_pace": 70.0,
            "away_pace": 65.0,
            "home_pass_rate": 0.5,
            "away_pass_rate": 0.45,
        }
    )
    assert math.isfinite(pred)
