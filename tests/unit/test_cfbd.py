"""Tests for CFBD ingestion."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest
from typer.testing import CliRunner

from ncaa_quant.cli import app
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.cfbd import (
    GAME_DURATION,
    GAME_DURATION_OT,
    CFBDClient,
    RateLimitBudgetError,
    archive_raw_cfbd,
    game_event_time,
    game_went_overtime,
    is_partition_complete,
    normalize_advanced_payload,
    normalize_coaches_payload,
    normalize_drives_payload,
    normalize_games_payload,
    normalize_lines_payload,
    normalize_plays_payload,
    normalize_portal_payload,
    normalize_recruiting_payload,
    normalize_returning_payload,
    normalize_roster_payload,
    normalize_talent_payload,
    normalize_teams_payload,
    normalize_venues_payload,
    parse_seasons_arg,
    preseason_event_time,
    resolve_game_event_time,
    run_cfbd_backfill,
)
from ncaa_quant.utils.logging import configure_logging

INGESTED = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
KICKOFF = datetime(2023, 9, 2, 19, 0, 0, tzinfo=UTC)

GAMES_PAYLOAD = [
    {
        "id": 401520182,
        "season": 2023,
        "week": 1,
        "season_type": "regular",
        "start_date": "2023-09-02T19:00:00.000Z",
        "completed": True,
        "neutral_site": False,
        "conference_game": True,
        "home_id": 333,
        "home_team": "Alabama",
        "home_points": 56,
        "away_id": 99,
        "away_team": "Texas",
        "away_points": 19,
        "venue_id": 3853,
    }
]

TEAMS_PAYLOAD = [
    {
        "id": 333,
        "school": "Alabama",
        "conference": "SEC",
        "abbreviation": "ALA",
        "classification": "fbs",
    },
    {
        "id": 99,
        "school": "Texas",
        "conference": "Big 12",
        "abbreviation": "TEX",
        "classification": "fbs",
    },
]

PLAYS_PAYLOAD = [
    {
        "id": 1001,
        "drive_id": 200,
        "game_id": 401520182,
        "offense": "Alabama",
        "defense": "Texas",
        "period": 1,
        "down": 1,
        "distance": 10,
        "yards_to_goal": 75,
        "play_type": "Rush",
        "yards_gained": 5,
        "ppa": 0.12,
        "offenseScore": 7,
        "defenseScore": 0,
        "clock": {"minutes": 12, "seconds": 30},
        "success": True,
        "scoring": False,
    }
]

LINES_PAYLOAD = [
    {
        "id": 401520182,
        "season": 2023,
        "week": 1,
        "lines": [
            {
                "provider": "consensus",
                "spread": -13.5,
                "spreadOpen": -14.0,
                "overUnder": 55.5,
                "overUnderOpen": 54.5,
                "homeMoneyline": -500,
                "awayMoneyline": 380,
            }
        ],
    }
]

TALENT_PAYLOAD = [{"year": 2023, "school": "Alabama", "talent": 980.4}]

PORTAL_PAYLOAD = [
    {
        "season": 2023,
        "firstName": "Jane",
        "lastName": "Doe",
        "origin": "Texas",
        "destination": "Alabama",
        "transferDate": "2023-01-10T00:00:00.000Z",
        "rating": 0.92,
        "id": 55,
    }
]


@pytest.fixture(autouse=True)
def _logging() -> None:
    configure_logging(level="INFO")


@pytest.fixture
def team_map() -> dict[str, str]:
    return {}


def test_parse_seasons_arg() -> None:
    assert parse_seasons_arg("2023") == (2023,)
    assert parse_seasons_arg("2022-2024") == (2022, 2023, 2024)


def test_event_time_game_and_preseason() -> None:
    assert game_event_time(KICKOFF) == KICKOFF + GAME_DURATION
    assert preseason_event_time(2023) == datetime(2023, 8, 1, tzinfo=UTC)


def test_resolve_game_event_time_regulation_ot_and_completion() -> None:
    reg = resolve_game_event_time(KICKOFF)
    assert reg.event_time == KICKOFF + GAME_DURATION
    assert reg.estimated is True

    ot = resolve_game_event_time(KICKOFF, overtime=True)
    assert ot.event_time == KICKOFF + GAME_DURATION_OT
    assert ot.estimated is True

    done = datetime(2023, 9, 2, 22, 15, tzinfo=UTC)
    actual = resolve_game_event_time(KICKOFF, completion=done, overtime=True)
    assert actual.event_time == done
    assert actual.estimated is False


def test_game_went_overtime_line_scores_and_notes() -> None:
    assert game_went_overtime({"homeLineScores": [7, 7, 7, 7, 3]}) is True
    assert game_went_overtime({"away_line_scores": [0, 0, 0, 0]}) is False
    assert game_went_overtime({"notes": "2OT"}) is True
    assert game_went_overtime({"notes": "overtime thriller"}) is True


def test_normalize_games_event_time() -> None:
    df = normalize_games_payload(GAMES_PAYLOAD, ingested_at=INGESTED)
    assert len(df) == 1
    assert df.iloc[0]["game_id"] == 401520182
    assert df.iloc[0]["event_time"] == KICKOFF + GAME_DURATION
    assert bool(df.iloc[0]["event_time_estimated"]) is True


def test_normalize_games_ot_uses_longer_duration() -> None:
    payload = [
        {
            **GAMES_PAYLOAD[0],
            "home_line_scores": [7, 14, 7, 7, 3],
            "away_line_scores": [0, 7, 7, 14, 0],
        }
    ]
    df = normalize_games_payload(payload, ingested_at=INGESTED)
    assert df.iloc[0]["event_time"] == KICKOFF + GAME_DURATION_OT
    assert bool(df.iloc[0]["event_time_estimated"]) is True


def test_normalize_games_completion_timestamp_not_estimated() -> None:
    done = "2023-09-02T22:45:00.000Z"
    payload = [{**GAMES_PAYLOAD[0], "end_date": done}]
    df = normalize_games_payload(payload, ingested_at=INGESTED)
    assert df.iloc[0]["event_time"] == datetime(2023, 9, 2, 22, 45, tzinfo=UTC)
    assert bool(df.iloc[0]["event_time_estimated"]) is False


def test_normalize_unplayed_future_game_event_time_not_after_ingest() -> None:
    """Live schedule rows must satisfy DESIGN §8; kickoff stays on start_date."""
    from ncaa_quant.data.schemas import GamesSchema

    kickoff = datetime(2026, 9, 5, 16, 0, 0, tzinfo=UTC)
    ingested = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
    payload = [
        {
            **GAMES_PAYLOAD[0],
            "id": 401856766,
            "season": 2026,
            "start_date": "2026-09-05T16:00:00.000Z",
            "completed": False,
            "home_points": None,
            "away_points": None,
        }
    ]
    df = normalize_games_payload(payload, ingested_at=ingested)
    assert len(df) == 1
    assert bool(df.iloc[0]["completed"]) is False
    assert df.iloc[0]["start_date"] == kickoff
    assert df.iloc[0]["event_time"] == ingested
    assert df.iloc[0]["event_time"] <= df.iloc[0]["ingested_at"]
    assert bool(df.iloc[0]["event_time_estimated"]) is True
    GamesSchema.validate(df, lazy=True)


def test_normalize_teams_and_talent(team_map: dict[str, str]) -> None:
    teams = normalize_teams_payload(
        TEAMS_PAYLOAD, season=2023, ingested_at=INGESTED, team_map=team_map
    )
    school_to_id = {str(r["school"]): int(r["team_id"]) for _, r in teams.iterrows()}
    talent = normalize_talent_payload(
        TALENT_PAYLOAD,
        season=2023,
        ingested_at=INGESTED,
        school_to_id=school_to_id,
        team_map=team_map,
    )
    assert len(talent) == 1
    assert talent.iloc[0]["team_id"] == 333
    assert talent.iloc[0]["event_time"] == preseason_event_time(2023)


def test_normalize_plays(team_map: dict[str, str]) -> None:
    school_to_id = {"Alabama": 333, "Texas": 99}
    df = normalize_plays_payload(
        PLAYS_PAYLOAD,
        season=2023,
        week=1,
        ingested_at=INGESTED,
        school_to_id=school_to_id,
        team_map=team_map,
        game_start_by_id={401520182: KICKOFF},
    )
    assert len(df) == 1
    assert df.iloc[0]["offense_id"] == 333
    assert df.iloc[0]["epa"] == pytest.approx(0.12)
    assert df.iloc[0]["event_time"] == KICKOFF + GAME_DURATION
    assert int(df.iloc[0]["offense_score"]) == 7
    assert int(df.iloc[0]["defense_score"]) == 0
    assert int(df.iloc[0]["score_margin"]) == 7
    assert int(df.iloc[0]["clock"]) == 12 * 60 + 30
    assert pd.isna(df.iloc[0]["wp"])


def test_normalize_lines_open_close() -> None:
    df = normalize_lines_payload(
        LINES_PAYLOAD,
        season=2023,
        week=1,
        ingested_at=INGESTED,
        game_start_by_id={401520182: KICKOFF},
    )
    assert set(df["line_type"]) == {"open", "close"}
    assert df.iloc[0]["event_time"] == KICKOFF


def test_normalize_portal_uses_transfer_date(team_map: dict[str, str]) -> None:
    df = normalize_portal_payload(
        PORTAL_PAYLOAD,
        season=2023,
        ingested_at=INGESTED,
        school_to_id={"Alabama": 333, "Texas": 99},
        team_map=team_map,
    )
    assert len(df) == 1
    assert df.iloc[0]["event_time"] == datetime(2023, 1, 10, tzinfo=UTC)


def test_normalize_portal_fallback_preseason(team_map: dict[str, str]) -> None:
    payload = [{**PORTAL_PAYLOAD[0], "transferDate": None}]
    df = normalize_portal_payload(
        payload,
        season=2023,
        ingested_at=INGESTED,
        school_to_id={"Alabama": 333, "Texas": 99},
        team_map=team_map,
    )
    assert df.iloc[0]["event_time"] == preseason_event_time(2023)


def test_retry_on_500_then_success() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(
            200,
            json=TEAMS_PAYLOAD,
            headers={"x-ratelimit-remaining": "100"},
        )

    transport = httpx.MockTransport(handler)
    with CFBDClient("test-key", transport=transport, requests_per_second=1000.0) as client:
        body = client.fetch_teams(2023)
    assert attempts["n"] == 3
    assert json.loads(body)[0]["school"] == "Alabama"


def test_retry_exhausted_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    transport = httpx.MockTransport(handler)
    with (
        CFBDClient("test-key", transport=transport, requests_per_second=1000.0) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        client.fetch_teams(2023)


def test_rate_limit_budget_guard() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[],
            headers={"x-ratelimit-remaining": "2"},
        )

    transport = httpx.MockTransport(handler)
    with CFBDClient(
        "test-key",
        transport=transport,
        requests_per_second=1000.0,
        rate_limit_reserve=5,
    ) as client:
        client.fetch_teams(2023)
        with pytest.raises(RateLimitBudgetError):
            client.fetch_teams(2023)


def test_raw_archival_before_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_root = tmp_path / "raw"
    staged = tmp_path / "staged"
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        path = request.url.path
        if path.endswith("/teams"):
            return httpx.Response(200, json=TEAMS_PAYLOAD)
        if path.endswith("/games"):
            return httpx.Response(200, json=GAMES_PAYLOAD)
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    client = CFBDClient("test-key", transport=transport, requests_per_second=1000.0)

    def boom(*_args: object, **_kwargs: object) -> pd.DataFrame:
        # Ensure raw exists before normalize is invoked — archive happens in
        # orchestration before this call; raise to simulate parser failure.
        raw_files = list(raw_root.rglob("*.json"))
        assert raw_files, "raw archive missing before parse"
        raise RuntimeError("parse failed")

    monkeypatch.setattr(
        "ncaa_quant.ingestion.cfbd.normalize_teams_payload",
        boom,
    )

    with pytest.raises(RuntimeError, match="parse failed"):
        run_cfbd_backfill(
            seasons=[2023],
            endpoints=["teams"],
            force=True,
            api_key="test-key",
            raw_root=raw_root,
            staged_root=staged,
            client=client,
            team_map={},
        )
    assert list(raw_root.rglob("*.json"))
    client.close()


def test_resumability_skips_completed_partition(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    staged = tmp_path / "staged"
    fetch_counts = {"teams": 0, "games": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/teams"):
            fetch_counts["teams"] += 1
            return httpx.Response(200, json=TEAMS_PAYLOAD)
        if path.endswith("/games"):
            fetch_counts["games"] += 1
            return httpx.Response(200, json=GAMES_PAYLOAD)
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    client = CFBDClient("test-key", transport=transport, requests_per_second=1000.0)

    run_cfbd_backfill(
        seasons=[2023],
        endpoints=["teams", "games"],
        force=False,
        api_key="test-key",
        raw_root=raw_root,
        staged_root=staged,
        client=client,
        team_map={},
    )
    first_teams = fetch_counts["teams"]
    first_games = fetch_counts["games"]
    assert first_teams >= 1
    assert first_games >= 1

    run_cfbd_backfill(
        seasons=[2023],
        endpoints=["teams", "games"],
        force=False,
        api_key="test-key",
        raw_root=raw_root,
        staged_root=staged,
        client=client,
        team_map={},
    )
    assert fetch_counts["teams"] == first_teams
    assert fetch_counts["games"] == first_games

    with ParquetStore(staged) as store:
        assert is_partition_complete(store, "teams", {"season": 2023})
        assert is_partition_complete(store, "games", {"season": 2023, "week": 1})
        games = store.read("games", filters={"season": 2023})
        assert len(games) == 1
    client.close()


def test_kill_mid_backfill_leaves_no_partial_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_root = tmp_path / "raw"
    staged = tmp_path / "staged"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/teams"):
            return httpx.Response(200, json=TEAMS_PAYLOAD)
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    client = CFBDClient("test-key", transport=transport, requests_per_second=1000.0)

    original_write = ParquetStore.write_partition

    def exploding_write(
        self: ParquetStore,
        table: str,
        df: pd.DataFrame,
        partition: dict[str, int],
        mode: str = "overwrite",
        *,
        validate: bool = True,
    ) -> Path:
        if table == "teams":
            raise RuntimeError("killed during write")
        return original_write(self, table, df, partition, mode, validate=validate)

    monkeypatch.setattr(ParquetStore, "write_partition", exploding_write)

    with pytest.raises(RuntimeError, match="killed"):
        run_cfbd_backfill(
            seasons=[2023],
            endpoints=["teams"],
            force=True,
            api_key="test-key",
            raw_root=raw_root,
            staged_root=staged,
            client=client,
            team_map={},
        )

    part = staged / "teams" / "season=2023" / "part.parquet"
    assert not part.exists()
    # Raw payload survived.
    assert list(raw_root.rglob("*.json"))
    client.close()


def test_archive_raw_cfbd_filename(tmp_path: Path) -> None:
    path = archive_raw_cfbd(
        tmp_path,
        datetime(2023, 9, 1, 12, 0, 0, tzinfo=UTC),
        b"[]",
        endpoint="plays",
        season=2023,
        week=1,
        season_type="regular",
    )
    assert path.exists()
    assert "plays_s2023_w1_regular_" in path.name


def test_cli_cfbd_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    from ncaa_quant.ingestion.cfbd import CfbdIngestResult

    def fake_backfill(**_kwargs: object) -> CfbdIngestResult:
        return CfbdIngestResult(
            seasons=(2023,),
            partitions_written=2,
            partitions_skipped=0,
            rows_written=10,
            raw_paths=(),
        )

    monkeypatch.setattr("ncaa_quant.ingestion.cfbd.run_cfbd_backfill", fake_backfill)
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "cfbd", "--seasons", "2023"])
    assert result.exit_code == 0
    assert "partitions_written=2" in result.stdout


def test_game_duration_constant() -> None:
    assert timedelta(hours=5) == GAME_DURATION
    assert timedelta(hours=7) == GAME_DURATION_OT


def test_normalize_remaining_endpoints(team_map: dict[str, str]) -> None:
    school_to_id = {"Alabama": 333, "Texas": 99}
    drives = normalize_drives_payload(
        [
            {
                "id": 10,
                "game_id": 401520182,
                "offense": "Alabama",
                "defense": "Texas",
                "start_period": 1,
                "end_period": 1,
                "plays": 5,
                "yards": 40,
                "scoring": True,
                "start_yards_to_goal": 75,
                "end_yards_to_goal": 0,
                "start_offense_score": 0,
                "end_offense_score": 7,
            }
        ],
        season=2023,
        week=1,
        ingested_at=INGESTED,
        school_to_id=school_to_id,
        team_map=team_map,
        game_start_by_id={401520182: KICKOFF},
    )
    assert len(drives) == 1
    assert drives.iloc[0]["points"] == 7

    advanced = normalize_advanced_payload(
        [
            {
                "gameId": 401520182,
                "season": 2023,
                "week": 1,
                "team": "Alabama",
                "offense": {
                    "ppa": 0.3,
                    "successRate": 0.5,
                    "explosiveness": 1.2,
                    "pointsPerOpportunity": 2.1,
                    "fieldPosition": {"averageStart": 72.0},
                },
                "defense": {"ppa": -0.1, "havoc": {"total": 0.2}},
            }
        ],
        season=2023,
        week=1,
        ingested_at=INGESTED,
        school_to_id=school_to_id,
        team_map=team_map,
        game_start_by_id={401520182: KICKOFF},
    )
    assert len(advanced) == 1
    assert advanced.iloc[0]["success_rate"] == pytest.approx(0.5)

    venues = normalize_venues_payload(
        [
            {
                "id": 1,
                "name": "Bryant-Denny",
                "city": "Tuscaloosa",
                "state": "AL",
                "latitude": 33.2,
                "longitude": -87.5,
                "elevation": 70.0,
                "capacity": 100000,
                "grass": True,
                "dome": False,
            }
        ],
        season=2023,
        ingested_at=INGESTED,
    )
    assert venues.iloc[0]["venue_id"] == 1
    assert venues.iloc[0]["surface"] == "grass"
    assert venues.iloc[0]["timezone"] is None or pd.isna(venues.iloc[0]["timezone"])

    coaches = normalize_coaches_payload(
        [
            {
                "first_name": "Nick",
                "last_name": "Saban",
                "seasons": [
                    {"school": "Alabama", "year": 2023, "games": 14, "wins": 12, "losses": 2}
                ],
            }
        ],
        season=2023,
        ingested_at=INGESTED,
        school_to_id=school_to_id,
        team_map=team_map,
    )
    assert len(coaches) == 1

    roster = normalize_roster_payload(
        [
            {
                "id": 7,
                "first_name": "A",
                "last_name": "Player",
                "team": "Alabama",
                "position": "QB",
                "year": 3,
            }
        ],
        season=2023,
        ingested_at=INGESTED,
        school_to_id=school_to_id,
        team_map=team_map,
    )
    assert roster.iloc[0]["athlete_id"] == 7

    returning = normalize_returning_payload(
        [
            {
                "season": 2023,
                "team": "Alabama",
                "percentPPA": {"total": 0.7, "offense": 0.65},
            }
        ],
        season=2023,
        ingested_at=INGESTED,
        school_to_id=school_to_id,
        team_map=team_map,
    )
    assert returning.iloc[0]["overall_pct"] == pytest.approx(0.7)

    recruiting = normalize_recruiting_payload(
        [
            {
                "year": 2023,
                "team": "Alabama",
                "rank": 1,
                "points": 300.0,
                "averageRating": 92.0,
                "blueChips": 10,
                "totalCommits": 20,
            }
        ],
        season=2023,
        ingested_at=INGESTED,
        school_to_id=school_to_id,
        team_map=team_map,
    )
    assert recruiting.iloc[0]["blue_chip_ratio"] == pytest.approx(0.5)


def test_full_week_backfill_mocked(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    staged = tmp_path / "staged"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/teams"):
            return httpx.Response(200, json=TEAMS_PAYLOAD)
        if path.endswith("/games"):
            return httpx.Response(200, json=GAMES_PAYLOAD)
        if path.endswith("/plays"):
            return httpx.Response(200, json=PLAYS_PAYLOAD)
        if path.endswith("/drives"):
            return httpx.Response(200, json=[])
        if path.endswith("/advanced"):
            return httpx.Response(200, json=[])
        if path.endswith("/lines"):
            return httpx.Response(200, json=LINES_PAYLOAD)
        if path.endswith("/talent"):
            return httpx.Response(200, json=TALENT_PAYLOAD)
        if path.endswith("/venues"):
            return httpx.Response(200, json=[{"id": 1, "name": "Stadium"}])
        if path.endswith("/games/teams"):
            return httpx.Response(200, json=[])
        if "/player/returning" in path:
            return httpx.Response(200, json=[])
        if "/recruiting/teams" in path:
            return httpx.Response(200, json=[])
        if "/player/portal" in path:
            return httpx.Response(200, json=PORTAL_PAYLOAD)
        if path.endswith("/coaches"):
            return httpx.Response(200, json=[])
        if path.endswith("/roster"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    client = CFBDClient("test-key", transport=transport, requests_per_second=1000.0)
    result = run_cfbd_backfill(
        seasons=[2023],
        endpoints=[
            "teams",
            "venues",
            "talent",
            "returning",
            "recruiting",
            "portal",
            "coaches",
            "roster",
            "games",
            "plays",
            "drives",
            "advanced",
            "lines",
            "games_teams",
        ],
        force=True,
        api_key="test-key",
        raw_root=raw_root,
        staged_root=staged,
        client=client,
        team_map={},
    )
    assert result.partitions_written > 0
    with ParquetStore(staged) as store:
        games = store.read("games", filters={"season": 2023})
        plays = store.read("plays", filters={"season": 2023, "week": 1})
        assert len(games) == 1
        assert len(plays) == 1
        assert is_partition_complete(store, "talent", {"season": 2023})
    # Second run skips fetches for completed partitions.
    result2 = run_cfbd_backfill(
        seasons=[2023],
        endpoints=["teams", "games", "plays", "games_teams"],
        force=False,
        api_key="test-key",
        raw_root=raw_root,
        staged_root=staged,
        client=client,
        team_map={},
    )
    assert result2.partitions_skipped > 0
    assert result2.raw_paths == ()
    client.close()


def test_cli_cfbd_requires_seasons() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "cfbd"])
    assert result.exit_code == 2


def test_cli_cfbd_incremental(monkeypatch: pytest.MonkeyPatch) -> None:
    from ncaa_quant.ingestion.cfbd import CfbdIngestResult

    def fake_inc(**_kwargs: object) -> CfbdIngestResult:
        return CfbdIngestResult(
            seasons=(2025,),
            partitions_written=1,
            partitions_skipped=0,
            rows_written=3,
            raw_paths=(),
        )

    monkeypatch.setattr("ncaa_quant.ingestion.cfbd.run_cfbd_incremental", fake_inc)
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "cfbd", "--incremental"])
    assert result.exit_code == 0
    assert "seasons=[2025]" in result.stdout
