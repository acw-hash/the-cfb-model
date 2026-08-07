"""Tests for pandera schemas, ParquetStore, and as_of_join."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from ncaa_quant.data.asof import AsOfJoinError, as_of_join
from ncaa_quant.data.schemas import (
    GamesSchema,
    LinesHistoricalSchema,
    TeamsSchema,
    validate_table,
)
from ncaa_quant.data.storage import ParquetStore, PartitionError
from ncaa_quant.utils.timeutils import NaiveDatetimeError


def _ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def _game_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "game_id": 1001,
        "season": 2024,
        "week": 1,
        "season_type": "regular",
        "start_date": _ts("2024-09-01T19:00:00"),
        "home_team_id": 10,
        "away_team_id": 20,
        "home_points": 31,
        "away_points": 24,
        "neutral_site": False,
        "conference_game": True,
        "venue_id": 5,
        "completed": True,
        "event_time_estimated": True,
        "source_version": "test",
        "event_time": _ts("2024-09-01T23:00:00"),
        "ingested_at": _ts("2024-09-02T02:00:00"),
    }
    base.update(overrides)
    return base


def _games_df(**overrides: object) -> pd.DataFrame:
    return pd.DataFrame([_game_row(**overrides)])


def _team_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "team_id": 10,
        "season": 2024,
        "school": "Alabama",
        "conference": "SEC",
        "abbreviation": "ALA",
        "classification": "fbs",
        "source_version": "test",
        "event_time": _ts("2024-07-01"),
        "ingested_at": _ts("2024-07-02"),
    }
    base.update(overrides)
    return base


def test_games_schema_round_trip() -> None:
    df = _games_df()
    validated = GamesSchema.validate(df)
    assert list(validated.columns) == list(df.columns)
    assert validated.iloc[0]["home_points"] == 31


def test_schema_points_out_of_range_raises() -> None:
    df = _games_df(home_points=101)
    with pytest.raises(SchemaErrors) as exc_info:
        GamesSchema.validate(df, lazy=True)
    message = str(exc_info.value).lower()
    assert "home_points" in message or "less_than_or_equal_to" in message


def test_schema_spread_out_of_range_raises() -> None:
    df = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2024,
                "week": 1,
                "book": "consensus",
                "line_type": "close",
                "spread": 70.0,
                "total": 55.0,
                "home_ml": -110.0,
                "away_ml": -110.0,
                "source_version": "test",
                "event_time": _ts("2024-09-01"),
                "ingested_at": _ts("2024-09-02"),
            }
        ]
    )
    with pytest.raises(SchemaErrors):
        LinesHistoricalSchema.validate(df, lazy=True)


def test_schema_event_after_ingest_raises() -> None:
    df = _games_df(
        event_time=_ts("2024-09-03"),
        ingested_at=_ts("2024-09-02"),
    )
    with pytest.raises(SchemaErrors) as exc_info:
        GamesSchema.validate(df, lazy=True)
    assert "event_time_le_ingested_at" in str(exc_info.value)


def test_validate_table_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown table"):
        validate_table("not_a_table", _games_df())


def test_partition_write_idempotent_byte_identical(tmp_path: Path) -> None:
    """Acceptance: rewriting a partition twice produces identical file hashes."""
    store = ParquetStore(tmp_path / "staged")
    df = _games_df()
    partition = {"season": 2024, "week": 1}
    path1 = store.write_partition("games", df, partition)
    hash1 = hashlib.sha256(path1.read_bytes()).hexdigest()
    path2 = store.write_partition("games", df, partition)
    hash2 = hashlib.sha256(path2.read_bytes()).hexdigest()
    assert path1 == path2
    assert hash1 == hash2
    # Visible under `pytest -s` for AUDIT-9 acceptance evidence.
    print(f"partition_path={path1}")
    print(f"hash_write_1={hash1}")
    print(f"hash_write_2={hash2}")


def test_partition_write_idempotent_shuffled_rows(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "staged")
    row_a = _game_row(game_id=1, home_points=10)
    row_b = _game_row(game_id=2, home_points=20, away_team_id=30)
    df1 = pd.DataFrame([row_a, row_b])
    df2 = pd.DataFrame([row_b, row_a])
    partition = {"season": 2024, "week": 1}
    p1 = store.write_partition("games", df1, partition)
    h1 = hashlib.sha256(p1.read_bytes()).hexdigest()
    p2 = store.write_partition("games", df2, partition)
    h2 = hashlib.sha256(p2.read_bytes()).hexdigest()
    assert h1 == h2


def test_read_and_query(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "staged")
    store.write_partition("games", _games_df(), {"season": 2024, "week": 1})
    store.write_partition(
        "games",
        _games_df(game_id=1002, week=2, home_points=14, away_points=7),
        {"season": 2024, "week": 2},
    )
    store.write_partition(
        "teams",
        pd.DataFrame([_team_row()]),
        {"season": 2024},
    )

    week1 = store.read("games", filters={"season": 2024, "week": 1})
    assert len(week1) == 1
    assert int(week1.iloc[0]["game_id"]) == 1001

    both = store.read("games", filters={"season": 2024})
    assert len(both) == 2

    teams = store.read("teams", filters={"season": 2024})
    assert len(teams) == 1

    result = store.query("SELECT game_id, week FROM games WHERE week = 2")
    assert len(result) == 1
    assert int(result.iloc[0]["game_id"]) == 1002


def test_partition_keys_enforced(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "staged")
    with pytest.raises(PartitionError, match="week"):
        store.write_partition("games", _games_df(), {"season": 2024})
    with pytest.raises(PartitionError, match="season only"):
        store.write_partition(
            "teams",
            pd.DataFrame([_team_row()]),
            {"season": 2024, "week": 1},
        )


def test_as_of_join_picks_most_recent_strict() -> None:
    left = pd.DataFrame(
        {
            "team_id": [10],
            "kickoff": [_ts("2024-09-15T17:00:00")],
        }
    )
    right = pd.DataFrame(
        {
            "team_id": [10, 10, 10],
            "event_time": [
                _ts("2024-09-01"),
                _ts("2024-09-10"),
                _ts("2024-09-20"),
            ],
            "rating": [1.0, 2.0, 99.0],
        }
    )
    out = as_of_join(left, right, on="team_id", ts_col="event_time", as_of="kickoff")
    assert len(out) == 1
    assert out.iloc[0]["rating"] == 2.0


def test_as_of_join_excludes_event_time_equal_as_of() -> None:
    """Acceptance / leakage guard: event_time == as_of must be excluded."""
    as_of = _ts("2024-09-10T12:00:00")
    left = pd.DataFrame({"team_id": [10], "label": ["g1"]})
    right = pd.DataFrame(
        {
            "team_id": [10, 10],
            "event_time": [as_of, _ts("2024-09-09T12:00:00")],
            "rating": [99.0, 3.0],
        }
    )
    out = as_of_join(left, right, on="team_id", ts_col="event_time", as_of=as_of.to_pydatetime())
    assert out.iloc[0]["rating"] == 3.0
    # Equal-bound row (rating=99) must not win; only strict '<' is eligible.
    assert 99.0 not in set(out["rating"].dropna())
    print(
        "boundary: right.event_time == as_of excluded; "
        f"selected_rating={out.iloc[0]['rating']} (expected 3.0, not 99.0)"
    )


def _crosswalk_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "odds_event_id": "odds-evt-1",
        "game_id": 401520123,
        "game_key": "2024:Alabama:Auburn:2024-11-30",
        "season": 2024,
        "home_team": "Alabama",
        "away_team": "Auburn",
        "kickoff": _ts("2024-11-30T17:00:00"),
        "kickoff_delta_hours": 0.5,
        "match_status": "matched",
        "source_version": "test",
        "event_time": _ts("2024-11-28T12:00:00"),
        "ingested_at": _ts("2024-11-28T12:01:00"),
    }
    base.update(overrides)
    return base


def test_odds_cfbd_crosswalk_schema_round_trip() -> None:
    """AUDIT-6: crosswalk schema exists even though population is Task 4/5."""
    from ncaa_quant.data.schemas import OddsCfbdGameCrosswalkSchema

    df = pd.DataFrame([_crosswalk_row()])
    validated = OddsCfbdGameCrosswalkSchema.validate(df, lazy=True)
    assert int(validated.iloc[0]["game_id"]) == 401520123
    assert validated.iloc[0]["match_status"] == "matched"


def test_odds_cfbd_crosswalk_quarantine_allows_null_game_id() -> None:
    from ncaa_quant.data.schemas import OddsCfbdGameCrosswalkSchema

    ok = pd.DataFrame([_crosswalk_row(game_id=None, match_status="quarantined")])
    OddsCfbdGameCrosswalkSchema.validate(ok, lazy=True)


def test_odds_cfbd_crosswalk_matched_requires_game_id() -> None:
    from ncaa_quant.data.schemas import OddsCfbdGameCrosswalkSchema

    bad = pd.DataFrame([_crosswalk_row(game_id=None, match_status="matched")])
    with pytest.raises(SchemaErrors):
        OddsCfbdGameCrosswalkSchema.validate(bad, lazy=True)


def test_crosswalk_partition_by_season(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "staged")
    df = pd.DataFrame([_crosswalk_row()])
    path = store.write_partition("odds_cfbd_game_crosswalk", df, {"season": 2024})
    assert path.exists()
    out = store.read("odds_cfbd_game_crosswalk", filters={"season": 2024})
    assert len(out) == 1
    assert int(out.iloc[0]["game_id"]) == 401520123


def _odds_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "snapshot_id": "s1",
        "game_key": "2024:A:B:2024-09-01",
        "game_id": 1,
        "season": 2024,
        "week": 1,
        "book": "pinnacle",
        "market": "total",
        "side": "over",
        "line": 45.0,
        "price": -110.0,
        "home_team": "A",
        "away_team": "B",
        "captured_at": _ts("2024-09-01T12:00:00"),
        "source_version": "test",
        "snapshot_source": "live",
        "decision_point": None,
        "n_books_available": 1,
        "event_time": _ts("2024-09-01T12:00:00"),
        "ingested_at": _ts("2024-09-01T12:01:00"),
    }
    base.update(overrides)
    return base


def test_odds_snapshot_line_sanity_rejects_bad_total() -> None:
    from ncaa_quant.data.schemas import OddsSnapshotsSchema

    df = pd.DataFrame([_odds_row(line=15.0)])
    with pytest.raises(SchemaErrors):
        OddsSnapshotsSchema.validate(df, lazy=True)


def test_odds_snapshot_requires_game_key_and_captured_at() -> None:
    from ncaa_quant.data.schemas import OddsSnapshotsSchema

    ok = pd.DataFrame([_odds_row()])
    OddsSnapshotsSchema.validate(ok, lazy=True)

    missing_key = pd.DataFrame([_odds_row()])
    missing_key = missing_key.drop(columns=["game_key"])
    with pytest.raises(SchemaErrors):
        OddsSnapshotsSchema.validate(missing_key, lazy=True)

    missing_cap = pd.DataFrame([_odds_row()])
    missing_cap = missing_cap.drop(columns=["captured_at"])
    with pytest.raises(SchemaErrors):
        OddsSnapshotsSchema.validate(missing_cap, lazy=True)


def test_append_mode_and_context_manager(tmp_path: Path) -> None:
    with ParquetStore(tmp_path / "staged") as store:
        store.write_partition("games", _games_df(game_id=1), {"season": 2024, "week": 1})
        store.write_partition(
            "games",
            _games_df(game_id=2),
            {"season": 2024, "week": 1},
            mode="append",
        )
        out = store.read("games", filters={"season": 2024, "week": 1})
        assert set(out["game_id"].astype(int)) == {1, 2}


def test_read_unknown_filter_column(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "staged")
    store.write_partition("games", _games_df(), {"season": 2024, "week": 1})
    with pytest.raises(KeyError, match="filter column"):
        store.read("games", filters={"season": 2024, "not_a_col": 1})


def test_as_of_join_multi_key() -> None:
    left = pd.DataFrame(
        {
            "team_id": [10],
            "season": [2024],
            "kickoff": [_ts("2024-09-15")],
        }
    )
    right = pd.DataFrame(
        {
            "team_id": [10, 10],
            "season": [2023, 2024],
            "event_time": [_ts("2024-09-01"), _ts("2024-09-10")],
            "rating": [0.1, 4.0],
        }
    )
    out = as_of_join(left, right, on=["team_id", "season"], ts_col="event_time", as_of="kickoff")
    assert out.iloc[0]["rating"] == 4.0


def test_as_of_join_raises_on_tz_naive_right() -> None:
    left = pd.DataFrame(
        {
            "team_id": [10],
            "kickoff": [_ts("2024-09-15")],
        }
    )
    right = pd.DataFrame(
        {
            "team_id": [10],
            "event_time": [pd.Timestamp("2024-09-01")],  # naive
            "rating": [1.0],
        }
    )
    with pytest.raises(NaiveDatetimeError, match="NAIVE-DATETIME-FORBIDDEN"):
        as_of_join(left, right, on="team_id", ts_col="event_time", as_of="kickoff")


def test_as_of_join_raises_on_missing_ts_col() -> None:
    left = pd.DataFrame({"team_id": [10], "kickoff": [_ts("2024-09-15")]})
    right = pd.DataFrame({"team_id": [10], "rating": [1.0]})
    with pytest.raises(AsOfJoinError, match="ts_col"):
        as_of_join(left, right, on="team_id", ts_col="event_time", as_of="kickoff")


def test_as_of_join_scalar_bound() -> None:
    left = pd.DataFrame({"team_id": [10, 20]})
    right = pd.DataFrame(
        {
            "team_id": [10, 20],
            "event_time": [_ts("2024-09-01"), _ts("2024-09-01")],
            "rating": [1.5, 2.5],
        }
    )
    out = as_of_join(
        left,
        right,
        on="team_id",
        ts_col="event_time",
        as_of=datetime(2024, 9, 15, tzinfo=UTC),
    )
    assert list(out["rating"]) == [1.5, 2.5]


def test_teams_schema_round_trip() -> None:
    df = pd.DataFrame([_team_row()])
    validated = TeamsSchema.validate(df)
    assert validated.iloc[0]["school"] == "Alabama"
