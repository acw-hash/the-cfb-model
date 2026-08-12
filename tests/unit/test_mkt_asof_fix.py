"""MKT-ASOF-FIX — per-game feature as-of + kickoff hard constraint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    resolve_lines_for_games,
    week_decision_as_of,
)
from ncaa_quant.features.market_lines import feature_as_of_for_game, slot_close_instant

STOP = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "notes"
    / "_artifacts"
    / "v2_baseline"
    / "STOP.md"
)

# Ten STOP leak rows (feature, game_id) with flagged feature_et / feature_row.
STOP_LEAKS: tuple[tuple[str, int, str, str], ...] = (
    ("mkt_spread", 401282809, "2021-09-11T19:55:00+00:00", "d4f417860685715403d2026699ffd7b1"),
    ("mkt_total", 401282809, "2021-09-11T19:55:00+00:00", "d4f417860685715403d2026699ffd7b1"),
    ("mkt_n_books", 401282809, "2021-09-11T19:55:00+00:00", "d4f417860685715403d2026699ffd7b1"),
    ("mkt_is_missing", 401282809, "2021-09-11T19:55:00+00:00", "d4f417860685715403d2026699ffd7b1"),
    ("mkt_spread", 401282066, "2021-09-11T19:55:00+00:00", "6a4e27efe65f0e67b01af4ceab9df336"),
    ("mkt_total", 401282066, "2021-09-11T19:55:00+00:00", "6a4e27efe65f0e67b01af4ceab9df336"),
    ("mkt_n_books", 401282066, "2021-09-11T19:55:00+00:00", "6a4e27efe65f0e67b01af4ceab9df336"),
    ("mkt_is_missing", 401282066, "2021-09-11T19:55:00+00:00", "6a4e27efe65f0e67b01af4ceab9df336"),
    ("mkt_spread", 401282189, "2021-09-12T02:05:00+00:00", "1cfc3c1bd2a352460f298a9aed2f6bd9"),
    ("mkt_total", 401282189, "2021-09-12T02:05:00+00:00", "1cfc3c1bd2a352460f298a9aed2f6bd9"),
)


def test_stop_md_lists_ten_leaks() -> None:
    text = STOP.read_text(encoding="utf-8")
    for _feat, gid, et, row_id in STOP_LEAKS:
        assert str(gid) in text
        assert et.replace("+00:00", "+00:00") in text or et[:19] in text
        assert row_id[:8] in text


def test_feature_as_of_prefers_week_decision_when_before_kickoff() -> None:
    cfg = WalkForwardConfig()
    week_ao = week_decision_as_of(2023, 5, cfg)
    kick = week_ao + timedelta(days=4)
    got = feature_as_of_for_game(kick, week_ao, season=2023, week=5)
    assert got == week_ao


def test_feature_as_of_falls_back_when_week_decision_after_kickoff() -> None:
    cfg = WalkForwardConfig()
    week_ao = week_decision_as_of(2021, 2, cfg)
    kick = week_ao - timedelta(days=2)
    assert kick < week_ao
    got = feature_as_of_for_game(kick, week_ao, season=2021, week=2)
    assert got is not None
    assert got < kick
    assert got == slot_close_instant(kick)


def test_post_kickoff_snap_excluded_when_week_as_of_after_kickoff() -> None:
    """Week-0 exception (kick before week Tuesday): post-kickoff snap must not resolve."""
    cfg = WalkForwardConfig()
    # Friday Week-0 kick; modal week Tuesday is the following Tuesday (Sat slate).
    kick = datetime(2021, 8, 28, 20, 50, tzinfo=UTC)
    sat_slate = datetime(2021, 9, 4, 19, 0, tzinfo=UTC)
    week_ao = datetime(2021, 8, 31, 10, 0, tzinfo=UTC)  # Tue 06:00 EDT
    assert kick < week_ao
    flagged_et = kick + timedelta(minutes=25)
    pre_kick = kick - timedelta(minutes=5)
    games = pd.DataFrame(
        [
            {
                "game_id": 401282714,
                "game_key": "k401282714",
                "season": 2021,
                "week": 1,
                "event_time": kick,
                "home_team": "Home U",
                "away_team": "Away U",
            },
            # Dominate the modal Monday so week Tuesday falls after the Week-0 kick.
            {
                "game_id": 401282715,
                "game_key": "k401282715",
                "season": 2021,
                "week": 1,
                "event_time": sat_slate,
                "home_team": "Home U",
                "away_team": "Away U",
            },
            {
                "game_id": 401282716,
                "game_key": "k401282716",
                "season": 2021,
                "week": 1,
                "event_time": sat_slate + timedelta(hours=3),
                "home_team": "Home U",
                "away_team": "Away U",
            },
        ]
    )
    snaps = pd.DataFrame(
        [
            {
                "game_id": 401282714,
                "book": "dk",
                "market": "spread",
                "side": "Home U",
                "line": -7.0,
                "event_time": pre_kick,
                "snapshot_id": "pre-ok",
            },
            {
                "game_id": 401282714,
                "book": "dk",
                "market": "total",
                "side": "over",
                "line": 50.0,
                "event_time": pre_kick,
                "snapshot_id": "pre-ok-t",
            },
            {
                "game_id": 401282714,
                "book": "dk",
                "market": "spread",
                "side": "Home U",
                "line": -3.0,
                "event_time": flagged_et,
                "snapshot_id": "post-kick-bad",
            },
            {
                "game_id": 401282714,
                "book": "dk",
                "market": "total",
                "side": "over",
                "line": 60.0,
                "event_time": flagged_et,
                "snapshot_id": "post-kick-bad",
            },
        ]
    )
    resolved = resolve_lines_for_games(
        games,
        week_ao,
        snapshots=snaps,
        cfbd_lines=None,
        config=cfg,
        closing=False,
    )
    row = resolved.loc[resolved["game_id"] == 401282714].iloc[0]
    assert row["source_row_id"] != "post-kick-bad"
    assert row["source_row_id"] == "pre-ok"
    assert row["spread"] == pytest.approx(-7.0)


def test_stop_ten_leaked_rows_never_resolve_to_flagged_snapshots() -> None:
    """Acceptance: STOP's ten leaks resolve to null or an earlier point, never flagged rows."""
    cfg = WalkForwardConfig()
    # Labor-Day week_as_of is after kickoff; calendar alignment + kickoff guard
    # must still refuse the flagged post-kickoff snapshot_ids.
    week_ao = week_decision_as_of(2021, 2, cfg)
    # Three STOP games; kickoffs before flagged feature_et (and before week as_of).
    game_meta = {
        401282809: datetime(2021, 9, 11, 19, 30, tzinfo=UTC),
        401282066: datetime(2021, 9, 11, 19, 30, tzinfo=UTC),
        401282189: datetime(2021, 9, 12, 2, 0, tzinfo=UTC),
    }
    flagged_by_game = {
        401282809: ("2021-09-11T19:55:00+00:00", "d4f417860685715403d2026699ffd7b1"),
        401282066: ("2021-09-11T19:55:00+00:00", "6a4e27efe65f0e67b01af4ceab9df336"),
        401282189: ("2021-09-12T02:05:00+00:00", "1cfc3c1bd2a352460f298a9aed2f6bd9"),
    }
    games_rows = []
    snap_rows = []
    for gid, kick in game_meta.items():
        assert kick < week_ao
        et_s, row_id = flagged_by_game[gid]
        flagged_et = pd.Timestamp(et_s).to_pydatetime()
        assert flagged_et >= kick
        games_rows.append(
            {
                "game_id": gid,
                "game_key": f"k{gid}",
                "season": 2021,
                "week": 2,
                "event_time": kick,
                "home_team": "Home U",
                "away_team": "Away U",
            }
        )
        # Flagged post-kickoff snap plus a Tuesday-era earlier snap.
        tue_era = datetime(2021, 9, 7, 10, 0, tzinfo=UTC)
        snap_rows.extend(
            [
                {
                    "game_id": gid,
                    "book": "dk",
                    "market": "spread",
                    "side": "Home U",
                    "line": -6.5,
                    "event_time": tue_era,
                    "snapshot_id": f"earlier-{gid}",
                },
                {
                    "game_id": gid,
                    "book": "dk",
                    "market": "total",
                    "side": "over",
                    "line": 48.0,
                    "event_time": tue_era,
                    "snapshot_id": f"earlier-{gid}-t",
                },
                {
                    "game_id": gid,
                    "book": "dk",
                    "market": "spread",
                    "side": "Home U",
                    "line": -1.0,
                    "event_time": flagged_et,
                    "snapshot_id": row_id,
                },
                {
                    "game_id": gid,
                    "book": "dk",
                    "market": "total",
                    "side": "over",
                    "line": 99.0,
                    "event_time": flagged_et,
                    "snapshot_id": row_id,
                },
            ]
        )
    games = pd.DataFrame(games_rows)
    snaps = pd.DataFrame(snap_rows)
    resolved = resolve_lines_for_games(
        games,
        week_ao,
        snapshots=snaps,
        cfbd_lines=None,
        config=cfg,
        closing=False,
    )
    by_id = resolved.set_index("game_id")
    flagged_ids = {row_id for _et, row_id in flagged_by_game.values()}
    for _feat, gid, _et, row_id in STOP_LEAKS:
        src = by_id.loc[gid, "source_row_id"]
        assert src not in flagged_ids
        assert src != row_id
        # Null or earlier decision-point snap.
        assert (
            src is None
            or (isinstance(src, float) and pd.isna(src))
            or str(src).startswith("earlier-")
        )


def test_closing_true_rejects_at_or_after_kickoff() -> None:
    cfg = WalkForwardConfig()
    kick = datetime(2022, 9, 10, 19, 0, tzinfo=UTC)
    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "game_key": "k1",
                "season": 2022,
                "week": 2,
                "event_time": kick,
                "home_team": "Home U",
                "away_team": "Away U",
            }
        ]
    )
    snaps = pd.DataFrame(
        [
            {
                "game_id": 1,
                "book": "dk",
                "market": "spread",
                "side": "Home U",
                "line": -3.0,
                "event_time": kick,  # at kickoff — forbidden
                "snapshot_id": "at-kick",
            },
            {
                "game_id": 1,
                "book": "dk",
                "market": "spread",
                "side": "Home U",
                "line": -4.0,
                "event_time": kick + timedelta(minutes=1),
                "snapshot_id": "after-kick",
            },
            {
                "game_id": 1,
                "book": "dk",
                "market": "total",
                "side": "over",
                "line": 50.0,
                "event_time": kick - timedelta(minutes=10),
                "snapshot_id": "pre",
            },
            {
                "game_id": 1,
                "book": "dk",
                "market": "spread",
                "side": "Home U",
                "line": -7.0,
                "event_time": kick - timedelta(minutes=10),
                "snapshot_id": "pre-s",
            },
        ]
    )
    closed = resolve_lines_for_games(
        games,
        kick - timedelta(days=1),
        snapshots=snaps,
        cfbd_lines=None,
        config=cfg,
        closing=True,
    )
    assert closed.iloc[0]["source_row_id"] == "pre-s"
    assert closed.iloc[0]["spread"] == pytest.approx(-7.0)


def test_no_cfbd_fallback_on_feature_path_snapshot_season() -> None:
    cfg = WalkForwardConfig()
    week_ao = week_decision_as_of(2021, 2, cfg)
    kick = week_ao - timedelta(days=1)
    games = pd.DataFrame(
        [
            {
                "game_id": 99,
                "game_key": "k99",
                "season": 2021,
                "week": 2,
                "event_time": kick,
                "home_team": "Home U",
                "away_team": "Away U",
            }
        ]
    )
    cfbd = pd.DataFrame(
        [{"game_id": 99, "book": "consensus", "line_type": "close", "spread": -10.0, "total": 55.0}]
    )
    resolved = resolve_lines_for_games(
        games,
        week_ao,
        snapshots=pd.DataFrame(),
        cfbd_lines=cfbd,
        config=cfg,
        closing=False,
    )
    assert resolved.iloc[0]["line_source"] == "null"
    assert pd.isna(resolved.iloc[0]["spread"])
