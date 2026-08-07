"""Roster / prior builders (Task 12)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from typer.testing import CliRunner

from ncaa_quant.cli import app
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.features.builders.roster import (
    DEFAULT_RECRUITING_WEIGHTS,
    PORTAL_ERA_START,
    RosterConfig,
    RosterFeatureBuilder,
    assert_no_zero_fill,
    build_qb_status_frame,
    build_roster_frame,
    coordinator_tenure_and_change,
    encode_qb_status,
    hc_tenure_years,
    is_portal_era,
    load_coordinators,
    portal_net_rating,
    preseason_event_time,
    qb_entity_id,
    scrape_depth_chart_qb_status,
    set_qb_status,
    weighted_recruiting_composite,
)
from ncaa_quant.features.materialize import materialize_partition, read_partition
from ncaa_quant.features.pit_audit import assert_partition_pit_clean
from ncaa_quant.features.registry import FeatureSpec, load_registry


def _roster_spec(name: str, **overrides: Any) -> FeatureSpec:
    base: dict[str, Any] = {
        "name": name,
        "version": "1",
        "dtype": "float64",
        "builder": "ncaa_quant.features.builders.roster:RosterFeatureBuilder",
        "dependencies": ("raw:teams",),
        "as_of_semantics": "strict_lt",
        "null_policy": "indicator" if name != "portal_era" else "forbid",
        "lookback_window": "preseason",
        "hypothesis": "Roster priors predict early-season margins.",
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


def _ts(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Recruiting weights / portal era
# ---------------------------------------------------------------------------


def test_recruiting_weight_math() -> None:
    points = {2020: 100.0, 2021: 200.0, 2022: 300.0, 2023: 400.0}
    got = weighted_recruiting_composite(points, 2023, weights=(0.1, 0.2, 0.3, 0.4))
    expected = 0.1 * 100 + 0.2 * 200 + 0.3 * 300 + 0.4 * 400
    assert got == pytest.approx(expected)

    # Missing oldest year → renormalize remaining weights.
    partial = {2021: 200.0, 2022: 300.0, 2023: 400.0}
    got2 = weighted_recruiting_composite(partial, 2023, weights=DEFAULT_RECRUITING_WEIGHTS)
    den = 0.2 + 0.3 + 0.4
    expected2 = (0.2 * 200 + 0.3 * 300 + 0.4 * 400) / den
    assert got2 == pytest.approx(expected2)

    assert math.isnan(weighted_recruiting_composite({}, 2023))


def test_portal_era_boundary_2020_2021() -> None:
    assert not is_portal_era(2020)
    assert is_portal_era(2021)
    assert PORTAL_ERA_START == 2021

    portal = pd.DataFrame(
        [
            {
                "season": 2020,
                "athlete_id": 1,
                "origin_team_id": 10,
                "dest_team_id": 20,
                "rating": 0.9,
                "event_time": _ts(2020, 1, 1),
            }
        ]
    )
    # Pre-2021: always null even if rows exist.
    assert math.isnan(portal_net_rating(portal, team_id=20, season=2020, as_of=_ts(2020, 8, 2)))


def test_portal_null_not_zero_pre_2021() -> None:
    teams = pd.DataFrame(
        [
            {"team_id": 1, "season": 2020, "school": "A"},
            {"team_id": 1, "season": 2021, "school": "A"},
        ]
    )
    empty = pd.DataFrame()
    history = build_roster_frame(
        teams=teams,
        returning=empty,
        talent=empty,
        recruiting=empty,
        portal=empty,
        coaches=empty,
        seasons=[2020, 2021],
        coordinators=(),
    )
    row_2020 = history.loc[history["season"] == 2020].iloc[0]
    row_2021 = history.loc[history["season"] == 2021].iloc[0]
    assert row_2020["portal_era"] == 0.0
    assert row_2021["portal_era"] == 1.0
    assert math.isnan(row_2020["portal_net_rating"])
    assert row_2020["portal_net_rating"] != 0.0
    assert math.isnan(row_2021["portal_net_rating"])  # no rated transfers → null, not 0


def test_no_builder_zero_fills_missing_values() -> None:
    """Acceptance: missing sources stay null — never coerced to 0.0."""
    teams = pd.DataFrame([{"team_id": 7, "season": 2022, "school": "Zed"}])
    empty = pd.DataFrame()
    history = build_roster_frame(
        teams=teams,
        returning=empty,
        talent=empty,
        recruiting=empty,
        portal=empty,
        coaches=empty,
        seasons=[2022],
        coordinators=(),
    )
    row = history.iloc[0]
    missing_cols = [
        "returning_offense_pct",
        "returning_defense_pct",
        "talent_composite",
        "blue_chip_ratio",
        "recruiting_4yr_weighted",
        "portal_net_rating",
        "hc_tenure_years",
        "new_hc_flag",
        "oc_tenure_years",
        "dc_tenure_years",
        "oc_change_flag",
        "dc_change_flag",
    ]
    for col in missing_cols:
        val = float(row[col])
        assert math.isnan(val), f"{col} should be NaN, got {val}"
        assert_no_zero_fill(val, is_missing_source=True)

    # Builder output also preserves nulls with indicator.
    as_of = _ts(2022, 9, 1)
    for name in missing_cols:
        builder = RosterFeatureBuilder(_roster_spec(name), history)
        out = builder.build([7], as_of)
        assert math.isnan(float(out.iloc[0]["value"]))
        assert bool(out.iloc[0]["is_missing"]) is True
        assert float(out.iloc[0]["value"]) != 0.0


def test_portal_net_rating_sums_inbound_minus_outbound() -> None:
    portal = pd.DataFrame(
        [
            {
                "season": 2023,
                "athlete_id": 1,
                "origin_team_id": 99,
                "dest_team_id": 10,
                "rating": 0.8,
                "event_time": _ts(2023, 1, 15),
            },
            {
                "season": 2023,
                "athlete_id": 2,
                "origin_team_id": 10,
                "dest_team_id": 11,
                "rating": 0.3,
                "event_time": _ts(2023, 2, 1),
            },
            {
                "season": 2023,
                "athlete_id": 3,
                "origin_team_id": 10,
                "dest_team_id": 12,
                "rating": None,
                "event_time": _ts(2023, 3, 1),
            },
        ]
    )
    net = portal_net_rating(portal, team_id=10, season=2023, as_of=_ts(2023, 8, 2))
    assert net == pytest.approx(0.8 - 0.3)


# ---------------------------------------------------------------------------
# Coaches / coordinators
# ---------------------------------------------------------------------------


def test_hc_tenure_and_new_hc() -> None:
    coaches = pd.DataFrame(
        [
            {
                "team_id": 1,
                "season": 2021,
                "first_name": "Pat",
                "last_name": "Fitz",
            },
            {
                "team_id": 1,
                "season": 2022,
                "first_name": "Pat",
                "last_name": "Fitz",
            },
            {
                "team_id": 1,
                "season": 2023,
                "first_name": "New",
                "last_name": "Coach",
            },
        ]
    )
    assert hc_tenure_years(coaches, team_id=1, season=2022) == 2.0
    assert hc_tenure_years(coaches, team_id=1, season=2023) == 1.0
    assert math.isnan(hc_tenure_years(coaches, team_id=1, season=2020))


def test_coordinator_tenure_change_and_missing_indicator() -> None:
    from ncaa_quant.features.builders.roster import CoordinatorSeason

    rows = (
        CoordinatorSeason("Alabama", 2023, "OC A", "DC A"),
        CoordinatorSeason("Alabama", 2024, "OC B", "DC A"),
        CoordinatorSeason("Alabama", 2025, "OC B", "DC A"),
    )
    ten, chg = coordinator_tenure_and_change(rows, school="Alabama", season=2025, role="oc")
    assert ten == 2.0
    assert chg == 0.0
    ten2, chg2 = coordinator_tenure_and_change(rows, school="Alabama", season=2024, role="oc")
    assert ten2 == 1.0
    assert chg2 == 1.0
    # Non-P5 / missing school → nulls (never zero).
    ten_m, chg_m = coordinator_tenure_and_change(rows, school="Boise State", season=2024, role="oc")
    assert math.isnan(ten_m) and math.isnan(chg_m)


def test_load_coordinators_seed() -> None:
    rows = load_coordinators()
    assert len(rows) >= 68 * 3
    alabama = [r for r in rows if r.school == "Alabama" and r.season == 2024]
    assert len(alabama) == 1
    assert alabama[0].oc is not None


# ---------------------------------------------------------------------------
# QB status CLI + scrape stub
# ---------------------------------------------------------------------------


def test_encode_qb_status() -> None:
    assert encode_qb_status("starter") == 1.0
    assert encode_qb_status("backup") == 0.0
    assert math.isnan(encode_qb_status("unknown"))


def test_scrape_stub_raises() -> None:
    with pytest.raises(NotImplementedError, match="depth-chart"):
        scrape_depth_chart_qb_status(season=2025, week=1)


def test_qb_status_cli_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staged = tmp_path / "staged"
    kick = _ts(2023, 9, 2)
    now = datetime(2023, 9, 2, 12, 0, tzinfo=UTC)
    entry = datetime(2023, 9, 1, 12, 0, tzinfo=UTC)

    games = pd.DataFrame(
        [
            {
                "game_id": 1001,
                "season": 2023,
                "week": 1,
                "season_type": "regular",
                "start_date": kick,
                "home_team_id": 10,
                "away_team_id": 20,
                "home_points": None,
                "away_points": None,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": 1,
                "completed": False,
                "event_time_estimated": True,
                "source_version": "test",
                "event_time": kick,
                "ingested_at": now,
            }
        ]
    )
    teams = pd.DataFrame(
        [
            {
                "team_id": 10,
                "season": 2023,
                "school": "Home U",
                "abbreviation": "HOM",
                "conference": "SEC",
                "classification": "fbs",
                "source_version": "test",
                "event_time": preseason_event_time(2023),
                "ingested_at": now,
            },
            {
                "team_id": 20,
                "season": 2023,
                "school": "Away U",
                "abbreviation": "AWY",
                "conference": "SEC",
                "classification": "fbs",
                "source_version": "test",
                "event_time": preseason_event_time(2023),
                "ingested_at": now,
            },
        ]
    )
    with ParquetStore(staged) as store:
        store.write_partition("games", games, {"season": 2023, "week": 1})
        store.write_partition("teams", teams, {"season": 2023})

    class _Cfg:
        class paths:
            staged_dir = str(staged)

    monkeypatch.setattr("ncaa_quant.config.load_config", lambda: _Cfg())

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["roster", "set-qb", "--game", "1001", "--team", "Home U", "--status", "starter"],
    )
    assert result.exit_code == 0, result.output
    assert "status=starter" in result.output

    with ParquetStore(staged) as store:
        qb = store.read("qb_status")
        assert len(qb) == 1
        assert qb.iloc[0]["status"] == "starter"
        assert int(qb.iloc[0]["team_id"]) == 10
        assert int(qb.iloc[0]["game_id"]) == 1001

        hist = build_qb_status_frame(qb)
        hist = hist.copy()
        hist["event_time"] = entry
        builder = RosterFeatureBuilder(_roster_spec("qb_status"), hist)
        out = builder.build([qb_entity_id(1001, 10)], now)
        assert float(out.iloc[0]["value"]) == 1.0
        assert bool(out.iloc[0]["is_missing"]) is False


def test_set_qb_status_direct(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    kick = _ts(2023, 10, 1)
    ingested = datetime(2023, 10, 1, 6, 0, tzinfo=UTC)
    t0 = datetime(2023, 9, 30, tzinfo=UTC)
    games = pd.DataFrame(
        [
            {
                "game_id": 55,
                "season": 2023,
                "week": 5,
                "season_type": "regular",
                "start_date": kick,
                "home_team_id": 1,
                "away_team_id": 2,
                "home_points": None,
                "away_points": None,
                "neutral_site": False,
                "conference_game": True,
                "venue_id": 1,
                "completed": False,
                "event_time_estimated": True,
                "source_version": "test",
                "event_time": kick,
                "ingested_at": ingested,
            }
        ]
    )
    with ParquetStore(staged) as store:
        store.write_partition("games", games, {"season": 2023, "week": 5})
        set_qb_status(
            store,
            game_id=55,
            team_id=2,
            status="backup",
            event_time=t0,
            ingested_at=t0,
        )
        set_qb_status(
            store,
            game_id=55,
            team_id=2,
            status="starter",
            event_time=t0 + timedelta(hours=2),
            ingested_at=t0 + timedelta(hours=2),
        )
        qb = store.read("qb_status")
        assert len(qb) == 2
        hist = build_qb_status_frame(qb)
        builder = RosterFeatureBuilder(_roster_spec("qb_status"), hist)
        mid = t0 + timedelta(hours=1)
        out_mid = builder.build([qb_entity_id(55, 2)], mid)
        assert float(out_mid.iloc[0]["value"]) == 0.0
        out_late = builder.build([qb_entity_id(55, 2)], t0 + timedelta(hours=3))
        assert float(out_late.iloc[0]["value"]) == 1.0


# ---------------------------------------------------------------------------
# Frame + pit_audit
# ---------------------------------------------------------------------------


def test_build_roster_frame_returning_and_talent() -> None:
    teams = pd.DataFrame(
        [
            {"team_id": 1, "season": 2023, "school": "Alabama"},
            {"team_id": 2, "season": 2023, "school": "Boise State"},
        ]
    )
    returning = pd.DataFrame(
        [
            {
                "team_id": 1,
                "season": 2023,
                "offense_pct": 0.7,
                "defense_pct": 0.55,
                "event_time": preseason_event_time(2023),
            }
        ]
    )
    talent = pd.DataFrame(
        [
            {
                "team_id": 1,
                "season": 2023,
                "talent": 980.5,
                "event_time": preseason_event_time(2023),
            }
        ]
    )
    recruiting = pd.DataFrame(
        [
            {
                "team_id": 1,
                "season": y,
                "points": float(100 * (y - 2019)),
                "blue_chip_ratio": 0.4 if y == 2023 else None,
                "event_time": preseason_event_time(y),
            }
            for y in range(2020, 2024)
        ]
    )
    from ncaa_quant.features.builders.roster import CoordinatorSeason

    coords = (
        CoordinatorSeason("Alabama", 2023, "OC1", "DC1"),
        CoordinatorSeason("Alabama", 2022, "OC0", "DC1"),
    )
    history = build_roster_frame(
        teams=teams,
        returning=returning,
        talent=talent,
        recruiting=recruiting,
        portal=pd.DataFrame(),
        coaches=pd.DataFrame(
            [
                {
                    "team_id": 1,
                    "season": 2023,
                    "first_name": "Kalen",
                    "last_name": "DeBoer",
                }
            ]
        ),
        seasons=[2023],
        coordinators=coords,
        config=RosterConfig(recruiting_weights=(0.1, 0.2, 0.3, 0.4)),
    )
    ala = history.loc[history["team_id"] == 1].iloc[0]
    assert ala["returning_offense_pct"] == pytest.approx(0.7)
    assert ala["talent_composite"] == pytest.approx(980.5)
    assert ala["blue_chip_ratio"] == pytest.approx(0.4)
    assert ala["recruiting_4yr_weighted"] == pytest.approx(
        weighted_recruiting_composite({2020: 100.0, 2021: 200.0, 2022: 300.0, 2023: 400.0}, 2023)
    )
    assert ala["hc_tenure_years"] == 1.0
    assert ala["new_hc_flag"] == 1.0
    assert ala["oc_change_flag"] == 1.0
    # Boise State not in coordinators → nulls.
    boise = history.loc[history["team_id"] == 2].iloc[0]
    assert math.isnan(boise["oc_tenure_years"])
    assert math.isnan(boise["returning_offense_pct"])


def test_roster_pit_audit(tmp_path: Path) -> None:
    teams = pd.DataFrame([{"team_id": 3, "season": 2023, "school": "Test"}])
    returning = pd.DataFrame(
        [
            {
                "team_id": 3,
                "season": 2023,
                "offense_pct": 0.6,
                "defense_pct": None,
                "event_time": preseason_event_time(2023),
            }
        ]
    )
    history = build_roster_frame(
        teams=teams,
        returning=returning,
        talent=pd.DataFrame(),
        recruiting=pd.DataFrame(),
        portal=pd.DataFrame(),
        coaches=pd.DataFrame(),
        seasons=[2023],
        coordinators=(),
    )
    as_of = _ts(2023, 9, 10)
    builder = RosterFeatureBuilder(_roster_spec("returning_offense_pct"), history)
    result = materialize_partition(
        builder,
        entity_ids=[3],
        as_of=as_of,
        season=2023,
        week=1,
        output_root=tmp_path / "features",
    )
    stored = read_partition(tmp_path / "features", result.partition)
    assert_partition_pit_clean(stored, builder, history, sample_size=5, seed=0)


def test_registry_includes_roster_features() -> None:
    reg = load_registry()
    for name in (
        "returning_offense_pct",
        "portal_net_rating",
        "portal_era",
        "qb_status",
        "oc_change_flag",
    ):
        spec = reg.get(name)
        assert spec.hypothesis.strip()
        assert "roster" in spec.builder
