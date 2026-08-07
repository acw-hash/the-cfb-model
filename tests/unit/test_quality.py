"""Tests for the data quality layer (Task 7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.quality.pit_audit import check_temporal_sanity
from ncaa_quant.quality.quarantine import is_quarantined, load_validation_results
from ncaa_quant.quality.runner import run_quality
from ncaa_quant.quality.validators import (
    check_duplicates,
    check_line_open_close_move,
    check_pbp_drive_points_reconcile,
    check_play_sequence_monotone,
    check_referential_plays_in_games,
    check_score_consistency_box,
)

INGESTED = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
EVENT = datetime(2023, 9, 2, 19, 0, tzinfo=UTC)


def _games_frame(**overrides: object) -> pd.DataFrame:
    row = {
        "game_id": 1,
        "season": 2023,
        "week": 1,
        "season_type": "regular",
        "start_date": EVENT,
        "home_team_id": 10,
        "away_team_id": 20,
        "home_points": 28,
        "away_points": 14,
        "neutral_site": False,
        "conference_game": True,
        "venue_id": 100,
        "completed": True,
        "event_time_estimated": True,
        "source_version": "test",
        "event_time": EVENT,
        "ingested_at": INGESTED,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _venues_frame(**overrides: object) -> pd.DataFrame:
    row = {
        "venue_id": 100,
        "season": 2023,
        "name": "Test Stadium",
        "city": "Town",
        "state": "TX",
        "latitude": 30.0,
        "longitude": -97.0,
        "elevation_m": 100.0,
        "capacity": 50000,
        "grass": True,
        "dome": False,
        "surface": "grass",
        "timezone": "America/Chicago",
        "source_version": "test",
        "event_time": EVENT,
        "ingested_at": INGESTED,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _plays_frame(rows: list[dict[str, object]] | None = None) -> pd.DataFrame:
    base = {
        "play_id": 1,
        "game_id": 1,
        "drive_id": 1000,
        "season": 2023,
        "week": 1,
        "offense_id": 10,
        "defense_id": 20,
        "period": 1,
        "down": 1,
        "distance": 10,
        "yards_to_goal": 75,
        "play_type": "Rush",
        "yards_gained": 4,
        "epa": 0.1,
        "wp": 0.5,
        "success": True,
        "scoring": False,
        "source_version": "test",
        "event_time": EVENT,
        "ingested_at": INGESTED,
    }
    if rows is None:
        return pd.DataFrame([{**base}])
    return pd.DataFrame([{**base, **r} for r in rows])


def _drives_frame(rows: list[dict[str, object]] | None = None) -> pd.DataFrame:
    base = {
        "drive_id": 1000,
        "game_id": 1,
        "season": 2023,
        "week": 1,
        "offense_id": 10,
        "defense_id": 20,
        "start_period": 1,
        "end_period": 1,
        "plays": 5,
        "yards": 40,
        "scoring": True,
        "start_yards_to_goal": 75,
        "end_yards_to_goal": 0,
        "points": 7,
        "source_version": "test",
        "event_time": EVENT,
        "ingested_at": INGESTED,
    }
    if rows is None:
        return pd.DataFrame([{**base}])
    return pd.DataFrame([{**base, **r} for r in rows])


def _box_frame(rows: list[dict[str, object]] | None = None) -> pd.DataFrame:
    base = {
        "game_id": 1,
        "team_id": 10,
        "season": 2023,
        "week": 1,
        "offense_epa": 0.2,
        "defense_epa": -0.1,
        "success_rate": 0.45,
        "explosiveness": 1.2,
        "havoc_rate": 0.15,
        "finishing_drives": 0.4,
        "field_position": 28.0,
        "points": 28,
        "source_version": "test",
        "event_time": EVENT,
        "ingested_at": INGESTED,
    }
    if rows is None:
        return pd.DataFrame(
            [
                {**base},
                {**base, "team_id": 20, "points": 14, "offense_epa": -0.1},
            ]
        )
    return pd.DataFrame([{**base, **r} for r in rows])


def _lines_frame(rows: list[dict[str, object]] | None = None) -> pd.DataFrame:
    base = {
        "game_id": 1,
        "season": 2023,
        "week": 1,
        "book": "consensus",
        "line_type": "open",
        "spread": -7.0,
        "total": 55.0,
        "home_ml": -280.0,
        "away_ml": 230.0,
        "source_version": "test",
        "event_time": EVENT,
        "ingested_at": INGESTED,
    }
    if rows is None:
        return pd.DataFrame(
            [
                {**base},
                {**base, "line_type": "close", "spread": -7.5, "total": 54.5},
            ]
        )
    return pd.DataFrame([{**base, **r} for r in rows])


def _write_partition(
    root: Path, table: str, df: pd.DataFrame, *, season: int, week: int | None
) -> None:
    path = root / table / f"season={season}"
    if week is not None:
        path = path / f"week={week}"
    path.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path / "part.parquet", index=False)


def _seed_clean_season(root: Path) -> None:
    games = _games_frame()
    venues = _venues_frame()
    plays = _plays_frame(
        [
            {"play_id": 1, "period": 1},
            {"play_id": 2, "period": 1, "down": 2},
            {"play_id": 3, "period": 2, "drive_id": 1001},
        ]
    )
    drives = _drives_frame(
        [
            {"drive_id": 1000, "offense_id": 10, "points": 21},
            {"drive_id": 1001, "offense_id": 10, "points": 7},
            {"drive_id": 1002, "offense_id": 20, "defense_id": 10, "points": 14},
        ]
    )
    box = _box_frame()
    lines = _lines_frame()
    _write_partition(root, "games", games, season=2023, week=1)
    _write_partition(root, "venues", venues, season=2023, week=None)
    _write_partition(root, "plays", plays, season=2023, week=1)
    _write_partition(root, "drives", drives, season=2023, week=1)
    _write_partition(root, "advanced_box", box, season=2023, week=1)
    _write_partition(root, "lines_historical", lines, season=2023, week=1)


def test_negative_score_caught_by_ge_and_custom(tmp_path: Path) -> None:
    root = tmp_path / "staged"
    _seed_clean_season(root)
    games = _games_frame(home_points=-3)
    _write_partition(root, "games", games, season=2023, week=1)

    result = run_quality(
        (2023,),
        staged_dir=root,
        report_dir=tmp_path / "reports",
        tables=("games", "venues"),
    )
    expectations = {f.expectation for f in result.findings}
    assert "range_non_negative_home_points" in expectations or any(
        "between" in f.expectation for f in result.findings
    )
    assert is_quarantined(root, "games", season=2023, week=1)


def test_orphan_pbp_game_caught(tmp_path: Path) -> None:
    games = _games_frame()
    plays = _plays_frame([{"game_id": 999}])
    findings = check_referential_plays_in_games(plays, games)
    assert len(findings) == 1
    assert findings[0].expectation == "referential_plays_game_id"


def test_duplicated_rows_caught() -> None:
    games = pd.concat([_games_frame(), _games_frame()], ignore_index=True)
    findings = check_duplicates(games, key_columns=["game_id"])
    assert len(findings) == 1
    assert findings[0].n_failures == 2


def test_future_event_time_caught() -> None:
    games = _games_frame(event_time=INGESTED + timedelta(hours=1))
    findings = check_temporal_sanity(games)
    assert len(findings) == 1
    assert findings[0].expectation == "temporal_sanity_event_time_le_ingested_at"


def test_mismatched_box_final_caught() -> None:
    games = _games_frame(home_points=28, away_points=14)
    box = _box_frame(
        [
            {"team_id": 10, "points": 21},
            {"team_id": 20, "points": 14},
        ]
    )
    findings = check_score_consistency_box(games, box)
    assert len(findings) == 1
    assert findings[0].expectation == "score_consistency_box_vs_final"


def test_play_sequence_non_monotone_caught() -> None:
    plays = _plays_frame(
        [
            {"play_id": 1, "period": 2},
            {"play_id": 2, "period": 1},  # period regresses as play_id increases
        ]
    )
    findings = check_play_sequence_monotone(plays)
    assert len(findings) == 1


def test_line_move_flag_not_fail() -> None:
    lines = _lines_frame(
        [
            {"line_type": "open", "spread": -3.0, "total": 45.0},
            {"line_type": "close", "spread": -25.0, "total": 45.0},
        ]
    )
    findings = check_line_open_close_move(lines)
    assert len(findings) == 1
    assert findings[0].severity == "flag"


def test_drive_points_reconcile_caught() -> None:
    games = _games_frame(home_points=28, away_points=14)
    drives = _drives_frame(
        [
            {"drive_id": 1, "offense_id": 10, "points": 7},
            {"drive_id": 2, "offense_id": 20, "defense_id": 10, "points": 0},
        ]
    )
    findings = check_pbp_drive_points_reconcile(games, drives, tolerance=8)
    assert len(findings) == 1


def test_clean_season_passes_with_zero_failures(tmp_path: Path) -> None:
    root = tmp_path / "staged"
    _seed_clean_season(root)
    result = run_quality(
        (2023,),
        staged_dir=root,
        report_dir=tmp_path / "reports",
        tables=(
            "games",
            "plays",
            "drives",
            "advanced_box",
            "lines_historical",
            "venues",
        ),
    )
    hard = [f for f in result.findings if f.severity == "fail"]
    assert hard == [], [f.message for f in hard]
    assert result.partitions_quarantined == 0
    assert result.report_md is not None and result.report_md.exists()
    assert result.report_html is not None and result.report_html.exists()
    assert not is_quarantined(root, "games", season=2023, week=1)
    vr = load_validation_results(root, season=2023)
    assert not vr.empty
    assert (vr["status"] == "PASSED").all() or (vr[vr["severity"] == "fail"].empty)


def test_quarantine_soft_continues_other_partitions(tmp_path: Path) -> None:
    root = tmp_path / "staged"
    _seed_clean_season(root)
    # Corrupt week 1 games; add a clean week 2.
    bad_games = _games_frame(home_points=-1)
    _write_partition(root, "games", bad_games, season=2023, week=1)

    games2 = _games_frame(game_id=2, week=2, home_points=17, away_points=10)
    plays2 = _plays_frame([{"game_id": 2, "week": 2, "play_id": 10}])
    drives2 = _drives_frame(
        [
            {"game_id": 2, "week": 2, "drive_id": 2000, "offense_id": 10, "points": 17},
            {
                "game_id": 2,
                "week": 2,
                "drive_id": 2001,
                "offense_id": 20,
                "defense_id": 10,
                "points": 10,
            },
        ]
    )
    box2 = _box_frame(
        [
            {"game_id": 2, "week": 2, "team_id": 10, "points": 17},
            {"game_id": 2, "week": 2, "team_id": 20, "points": 10},
        ]
    )
    lines2 = _lines_frame(
        [
            {"game_id": 2, "week": 2, "line_type": "open"},
            {"game_id": 2, "week": 2, "line_type": "close", "spread": -3.5},
        ]
    )
    _write_partition(root, "games", games2, season=2023, week=2)
    _write_partition(root, "plays", plays2, season=2023, week=2)
    _write_partition(root, "drives", drives2, season=2023, week=2)
    _write_partition(root, "advanced_box", box2, season=2023, week=2)
    _write_partition(root, "lines_historical", lines2, season=2023, week=2)

    result = run_quality(
        (2023,),
        staged_dir=root,
        report_dir=tmp_path / "reports",
        tables=("games", "venues"),
    )
    assert is_quarantined(root, "games", season=2023, week=1)
    assert not is_quarantined(root, "games", season=2023, week=2)
    assert result.partitions_checked >= 2
