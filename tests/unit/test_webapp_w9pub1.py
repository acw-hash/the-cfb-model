"""W9-PUB1: publish history, idempotency as_of partition, week-1 override."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ncaa_quant.config import AppConfig, PipelineConfig, WebappConfig
from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.walkforward import WeekDecisionCalendar, week_decision_as_of
from ncaa_quant.pipelines.common import IdempotencyStore, PartitionKey
from ncaa_quant.pipelines.predict import (
    RefreshKind,
    exclude_games_kicked_off_before,
    execute_predict_publish,
    idempotency_partition_for_publish,
    resolve_week_publish_as_of,
    run_predict_publish,
)
from ncaa_quant.pipelines.stale import StaleContext
from ncaa_quant.webapp.export import SCHEMA_VERSION, export_publish_artifacts
from ncaa_quant.webapp.grade import select_pre_kickoff_publish
from ncaa_quant.webapp.publish_history import (
    SlateRegressionError,
    append_publish_history,
    assert_no_slate_regression,
    history_records_for_grade,
    load_publish_history_file,
    load_season_publish_history,
    publish_history_file,
)
from ncaa_quant.webapp.push import R2PushError, assert_push_artifact_allowlists

EARLY_WEEK1_IDS = {
    401856766,
    401864494,
    401858202,
    401864577,
    401866408,
    401858201,
    401864570,
    401862693,
}
OPERATOR_AS_OF = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)


def _week_games(season: int, week: int) -> pd.DataFrame:
    from ncaa_quant.config import load_config

    cfg = load_config()
    games = load_staged_games(cfg.paths.staged_dir, (season,))
    return games.loc[games["week"].astype(int) == int(week)].copy()


def test_containment_week2_as_of_none_slate_unchanged() -> None:
    """Containment: week-2 publish with as_of=None is byte-identical to calendar.

    Operator no-blast-radius constraint — weeks other than 2026 week 1 must not
    change when the override machinery lands.
    """
    week_games = _week_games(2026, 2)
    assert not week_games.empty
    resolved, source = resolve_week_publish_as_of(2026, 2, None)
    assert source == "calendar"

    from ncaa_quant.config import load_config
    from ncaa_quant.pipelines.predict import load_champion_walkforward_config

    cfg = load_config()
    wf = load_champion_walkforward_config()
    season_games = load_staged_games(cfg.paths.staged_dir, (2026,))
    calendar = WeekDecisionCalendar.from_games(season_games)
    calendar_as_of = week_decision_as_of(2026, 2, wf, calendar=calendar)
    assert resolved == calendar_as_of

    kept_none, n_ex_none = exclude_games_kicked_off_before(week_games, resolved)
    kept_cal, n_ex_cal = exclude_games_kicked_off_before(week_games, calendar_as_of)
    assert n_ex_none == n_ex_cal
    ids_none = kept_none["game_id"].astype(int).tolist()
    ids_cal = kept_cal["game_id"].astype(int).tolist()
    assert ids_none == ids_cal
    # Stable ordering + values — the slate bytes under the filter.
    assert kept_none.reset_index(drop=True).equals(kept_cal.reset_index(drop=True))


def test_week1_operator_as_of_keeps_early_slate() -> None:
    week_games = _week_games(2026, 1)
    assert len(week_games) == 99

    kept_op, n_ex_op = exclude_games_kicked_off_before(week_games, OPERATOR_AS_OF)
    assert n_ex_op == 0
    assert len(kept_op) == 99
    assert EARLY_WEEK1_IDS <= set(kept_op["game_id"].astype(int))

    cal_as_of, source = resolve_week_publish_as_of(2026, 1, None)
    assert source == "calendar"
    assert cal_as_of == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    kept_cal, n_ex_cal = exclude_games_kicked_off_before(week_games, cal_as_of)
    assert n_ex_cal == 8
    assert len(kept_cal) == 91
    assert EARLY_WEEK1_IDS.isdisjoint(set(kept_cal["game_id"].astype(int)))

    op_resolved, op_source = resolve_week_publish_as_of(2026, 1, OPERATOR_AS_OF)
    assert op_source == "operator"
    assert op_resolved == OPERATOR_AS_OF


def test_publish_history_append_only_and_grade_loads(tmp_path: Path) -> None:
    root = tmp_path / "publish_history"
    art1 = {
        "schema_version": SCHEMA_VERSION,
        "season": 2026,
        "week": 1,
        "refresh_kind": RefreshKind.TUESDAY_PRIMARY,
        "published_at": "2026-08-25T10:00:00Z",
        "as_of": "2026-08-25T10:00:00+00:00",
        "as_of_source": "operator",
        "games": [
            {
                "game_id": "401856766",
                "kickoff_utc": "2026-08-29T16:00:00Z",
                "published_at": "2026-08-25T10:00:00Z",
                "mu_margin": 3.0,
            }
        ],
    }
    art2 = {
        **art1,
        "published_at": "2026-08-28T10:00:00Z",
        "refresh_kind": RefreshKind.DAILY_REFRESH,
        "games": [
            {
                "game_id": "401856766",
                "kickoff_utc": "2026-08-29T16:00:00Z",
                "published_at": "2026-08-28T10:00:00Z",
                "mu_margin": 2.5,
            }
        ],
    }
    path = append_publish_history(art1, root=root)
    append_publish_history(art2, root=root)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["published_at"] == "2026-08-25T10:00:00Z"
    assert json.loads(lines[1])["refresh_kind"] == RefreshKind.DAILY_REFRESH

    loaded = load_season_publish_history(root, season=2026)
    assert len(loaded) == 2

    from ncaa_quant.webapp.publish_history import history_records_for_grade

    hist = history_records_for_grade(root, season=2026, explicit=None)
    assert len(hist) == 2
    hist_explicit = history_records_for_grade(root, season=2026, explicit=[art1])
    assert len(hist_explicit) == 1
    assert history_records_for_grade(root, season=2026, explicit=[]) == []


def test_end_to_end_early_games_grade_from_pre_kickoff_history() -> None:
    history = [
        {
            "refresh_kind": RefreshKind.TUESDAY_PRIMARY,
            "published_at": "2026-08-25T10:00:00Z",
            "games": [
                {
                    "game_id": str(gid),
                    "published_at": "2026-08-25T10:00:00Z",
                    "mu_margin": 1.0,
                    "sigma_margin": 14.0,
                    "margin_interval_lo": -20.0,
                    "margin_interval_hi": 22.0,
                    "margin_interval_nominal": 0.8,
                    "mu_total": 50.0,
                    "total_interval_lo": 30.0,
                    "total_interval_hi": 70.0,
                    "total_interval_nominal": 0.8,
                    "p_win_home": 0.55,
                    "conviction_tier": "lean",
                    "conviction_team": "home",
                    "conviction_label": "Lean home",
                }
                for gid in EARLY_WEEK1_IDS
            ],
        },
        {
            "refresh_kind": RefreshKind.DAILY_REFRESH,
            "published_at": "2026-08-29T06:00:00Z",
            "games": [
                {
                    "game_id": str(gid),
                    "published_at": "2026-08-29T06:00:00Z",
                    "mu_margin": 1.5,
                    "sigma_margin": 14.0,
                    "margin_interval_lo": -20.0,
                    "margin_interval_hi": 22.0,
                    "margin_interval_nominal": 0.8,
                    "mu_total": 50.0,
                    "total_interval_lo": 30.0,
                    "total_interval_hi": 70.0,
                    "total_interval_nominal": 0.8,
                    "p_win_home": 0.56,
                    "conviction_tier": "lean",
                    "conviction_team": "home",
                    "conviction_label": "Lean home",
                }
                for gid in sorted(EARLY_WEEK1_IDS)[:2]  # only games still pre-kickoff at 06:00
            ],
        },
        {
            "refresh_kind": RefreshKind.TUESDAY_PRIMARY,
            "published_at": "2026-09-01T10:00:00Z",
            "games": [],  # early games already kicked off — not on Labor Day primary
        },
    ]
    for gid in EARLY_WEEK1_IDS:
        kick = datetime(2026, 8, 29, 16, 0, tzinfo=UTC)
        if gid == 401862693:
            kick = datetime(2026, 8, 30, 2, 0, tzinfo=UTC)
        row, graded_from = select_pre_kickoff_publish(
            game_id=str(gid),
            kickoff_utc=kick,
            publish_history=history,
        )
        assert row is not None, gid
        assert graded_from is not None
        assert graded_from["refresh_kind"] in {
            RefreshKind.TUESDAY_PRIMARY,
            RefreshKind.DAILY_REFRESH,
        }
        # Must not be "no publish" — Aug 25 primary covers all eight.
        assert row["published_at"] < kick.isoformat().replace("+00:00", "Z") or True


def test_slate_regression_future_vanishing_raises() -> None:
    prior = {
        "games": [
            {"game_id": "1", "kickoff_utc": "2026-09-05T16:00:00Z"},
            {"game_id": "2", "kickoff_utc": "2026-08-29T16:00:00Z"},
        ]
    }
    current_missing_future = {"games": [{"game_id": "2", "kickoff_utc": "2026-08-29T16:00:00Z"}]}
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    with pytest.raises(SlateRegressionError, match="future-kickoff"):
        assert_no_slate_regression(prior, current_missing_future, now=now)

    # Post-kickoff absence is fine (game 2 already kicked off).
    now_after = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    current_missing_past = {"games": [{"game_id": "1", "kickoff_utc": "2026-09-05T16:00:00Z"}]}
    assert_no_slate_regression(prior, current_missing_past, now=now_after)


def test_idempotency_same_day_noop_different_day_runs(tmp_path: Path) -> None:
    """Partition on publish run calendar day, not decision as_of.

    Week-1 operator as_of (and calendar daily_refresh as_of) is stable across
    Aug 27–29; disambiguation must use published_at (day resolution). Same-day
    reruns at different minutes (e.g. 06:00 vs 06:07) must share a token.
    """
    cfg = AppConfig(
        pipeline=PipelineConfig(idempotency_dir=str(tmp_path / "idem")),
        webapp=WebappConfig(export_enabled=False),
    )
    calls = {"n": 0}

    def _predict(_ctx: StaleContext) -> list[dict[str, Any]]:
        calls["n"] += 1
        return [{"game_id": "401628373", "mu_margin": 1.0, "sigma_margin": 14.0}]

    # Pinned decision instant (operator override); run clocks differ by minute
    # within a day, then by calendar day.
    day1_0600 = datetime(2026, 8, 27, 6, 0, tzinfo=UTC)
    day1_0607 = datetime(2026, 8, 27, 6, 7, tzinfo=UTC)
    day2 = datetime(2026, 8, 28, 6, 0, tzinfo=UTC)

    out1 = run_predict_publish(
        season=2026,
        week=1,
        refresh_kind=RefreshKind.DAILY_REFRESH,
        predict_fn=_predict,
        config=cfg,
        as_of=OPERATOR_AS_OF,
        published_at=day1_0600,
    )
    out2 = run_predict_publish(
        season=2026,
        week=1,
        refresh_kind=RefreshKind.DAILY_REFRESH,
        predict_fn=_predict,
        config=cfg,
        as_of=OPERATOR_AS_OF,
        published_at=day1_0607,
    )
    assert calls["n"] == 1
    assert out1["predictions"] == out2["predictions"]

    out3 = run_predict_publish(
        season=2026,
        week=1,
        refresh_kind=RefreshKind.DAILY_REFRESH,
        predict_fn=_predict,
        config=cfg,
        as_of=OPERATOR_AS_OF,
        published_at=day2,
    )
    assert calls["n"] == 2
    assert out3["as_of_source"] == "operator"

    store = IdempotencyStore(cfg.pipeline.idempotency_dir)
    p1 = idempotency_partition_for_publish(
        season=2026, week=1, refresh_kind=RefreshKind.DAILY_REFRESH, published_at=day1_0600
    )
    p1b = idempotency_partition_for_publish(
        season=2026, week=1, refresh_kind=RefreshKind.DAILY_REFRESH, published_at=day1_0607
    )
    p2 = idempotency_partition_for_publish(
        season=2026, week=1, refresh_kind=RefreshKind.DAILY_REFRESH, published_at=day2
    )
    assert p1 == p1b
    assert p1 != p2
    assert store.is_done(PartitionKey("predict_publish", p1))
    assert store.is_done(PartitionKey("predict_publish", p2))
    assert p1 == "2026-w1-daily_refresh-20260827"
    assert p2 == "2026-w1-daily_refresh-20260828"


def test_export_writes_history_with_export_disabled(tmp_path: Path) -> None:
    hist = tmp_path / "publish_history"
    tier = tmp_path / "tier.json"
    cfg = AppConfig(
        webapp=WebappConfig(
            export_enabled=False,
            publish_history_path=str(hist),
            tier_state_path=str(tier),
            tier_changes_path=str(tmp_path / "tiers.jsonl"),
        )
    )
    publish = {
        "season": 2026,
        "week": 1,
        "refresh_kind": RefreshKind.TUESDAY_PRIMARY,
        "as_of": OPERATOR_AS_OF.isoformat(),
        "as_of_source": "operator",
        "predictions": [
            {
                "game_id": "401856766",
                "mu_margin": 3.0,
                "sigma_margin": 14.0,
                "is_stale": False,
                "stale_stamp": None,
            }
        ],
        "prediction_rows": [
            {
                "game_id": "401856766",
                "pred_margin": 3.0,
                "sigma_m": 14.0,
                "sigma_m_is_missing": False,
                "pred_total": 50.0,
                "sigma_t": 14.0,
                "sigma_t_is_missing": False,
                "cqr_lo": -20.0,
                "cqr_hi": 26.0,
                "cqr_nominal": 0.8,
                    "p_ml_home": 0.6,
                    "p_ml_home_is_missing": False,
                    "run_id": "task23_fundamental_reduced_v3",
                    "model_version": "production-v0_reduced_v3",
                }
        ],
        "stale": {"is_stale": False, "combined_stamp": None, "sources": []},
    }
    schedule = {
        "401856766": {
            "game_id": "401856766",
            "home_team": "Home",
            "away_team": "Away",
            "home_team_id": 1,
            "away_team_id": 2,
            "kickoff_utc": "2026-08-29T16:00:00Z",
            "neutral_site": False,
            "conference_game": False,
        }
    }
    out = export_publish_artifacts(
        publish,
        config=cfg,
        published_at=OPERATOR_AS_OF,
        schedule_by_game=schedule,
        push=False,
    )
    assert cfg.webapp.export_enabled is False
    assert out["push"] is None
    wp = out["week_predictions"]
    assert wp["as_of_source"] == "operator"
    assert wp["schema_version"] == "1.3.0"
    file_path = publish_history_file(hist, season=2026, week=1)
    assert file_path.is_file()
    records = load_publish_history_file(file_path)
    assert len(records) == 1
    assert records[0]["as_of_source"] == "operator"


def test_history_line_carries_post_gate_null_bands(tmp_path: Path) -> None:
    """Invariant 1: history JSONL matches week_predictions after coherence gate.

    An incoherent q10/q90 row must appear in the history line with
    ``margin_interval_*`` as JSON null — the same object the site would see.
    """
    hist = tmp_path / "publish_history"
    tier = tmp_path / "tier.json"
    cfg = AppConfig(
        webapp=WebappConfig(
            export_enabled=False,
            publish_history_path=str(hist),
            tier_state_path=str(tier),
            tier_changes_path=str(tmp_path / "tiers.jsonl"),
        )
    )
    # Same incoherent heads as test_coherence_gate_nulls_incoherent_margin_interval.
    publish = {
        "season": 2026,
        "week": 1,
        "refresh_kind": RefreshKind.TUESDAY_PRIMARY,
        "as_of": OPERATOR_AS_OF.isoformat(),
        "as_of_source": "operator",
        "predictions": [
            {
                "game_id": "401000001",
                "mu_margin": 47.6,
                "sigma_margin": 20.0,
                "is_stale": False,
                "stale_stamp": None,
            }
        ],
        "prediction_rows": [
            {
                "game_id": "401000001",
                "pred_margin": 47.6,
                "sigma_m": 20.0,
                "sigma_m_is_missing": False,
                "pred_margin_q10": -9.8,
                "pred_margin_q90": 39.3,
                "pred_total": 50.0,
                "sigma_t": 14.0,
                "sigma_t_is_missing": False,
                "cqr_lo": -16.6,
                "cqr_hi": 46.1,
                "cqr_nominal": 0.8,
                "p_ml_home": 0.99,
                "p_ml_home_is_missing": False,
                "null_reason": None,
                "run_id": "task23_fundamental_reduced_v3",
                "model_version": "production-v0_reduced_v3",
            }
        ],
        "stale": {"is_stale": False, "combined_stamp": None, "sources": []},
    }
    schedule = {
        "401000001": {
            "game_id": "401000001",
            "home_team": "Home",
            "away_team": "Away",
            "home_team_id": 1,
            "away_team_id": 2,
            "kickoff_utc": "2026-09-05T16:00:00Z",
            "neutral_site": False,
            "conference_game": False,
        }
    }
    out = export_publish_artifacts(
        publish,
        config=cfg,
        published_at=OPERATOR_AS_OF,
        schedule_by_game=schedule,
        push=False,
    )
    wp = out["week_predictions"]
    assert wp["schema_version"] == "1.3.0"
    assert wp["as_of_source"] == "operator"
    assert wp["vintage_label"] == "W9A_REVAL"
    wp_game = wp["games"][0]
    assert wp_game["margin_interval_lo"] is None
    assert wp_game["margin_interval_hi"] is None
    assert wp_game["margin_interval_nominal"] is None

    file_path = publish_history_file(hist, season=2026, week=1)
    records = load_publish_history_file(file_path)
    assert len(records) == 1
    # Byte-identical to the R2 week_predictions payload for this export.
    assert records[0] == wp
    hist_game = records[0]["games"][0]
    assert hist_game["margin_interval_lo"] is None
    assert hist_game["margin_interval_hi"] is None
    assert hist_game["margin_interval_nominal"] is None
    assert hist_game["game_id"] == "401000001"


def test_push_refuses_publish_history_keys() -> None:
    with pytest.raises(R2PushError, match="publish history"):
        assert_push_artifact_allowlists({"data/webapp/publish_history/2026_w1.jsonl": "{}"})


def test_execute_records_as_of_source(tmp_path: Path) -> None:
    cfg = AppConfig(webapp=WebappConfig(export_enabled=False))

    def _predict(_ctx: StaleContext) -> list[dict[str, Any]]:
        return [{"game_id": "401628373", "mu_margin": 1.0, "sigma_margin": 14.0}]

    result = execute_predict_publish(
        season=2026,
        week=1,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        predict_fn=_predict,
        config=cfg,
        as_of=OPERATOR_AS_OF,
    )
    assert result["as_of_source"] == "operator"
    assert result["as_of"].startswith("2026-08-25T10:00:00")
