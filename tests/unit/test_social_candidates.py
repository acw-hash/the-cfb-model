"""S1 — local social candidates sidecar."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ncaa_quant.betting.filters import BetCandidate, FilterReason
from ncaa_quant.config import AppConfig, BettingConfig, SocialConfig, WebappConfig, load_config
from ncaa_quant.pipelines.notifications import AlertKind, RecordingNotifier
from ncaa_quant.pipelines.predict import (
    RefreshKind,
    execute_predict_publish,
    run_fixture_week_publish,
)
from ncaa_quant.social.candidates import (
    build_candidate_record,
    candidates_sidecar_path,
    export_social_candidates,
    orient_bet_lines,
    records_from_filter_result,
)
from ncaa_quant.webapp.export import ODDS_FIELD_DENYLIST, assert_no_denylisted_fields, export_publish_artifacts


def _social_cfg(tmp_path: Path, *, enabled: bool = True) -> AppConfig:
    return AppConfig(
        social=SocialConfig(enabled=enabled, output_dir=str(tmp_path / "social")),
        webapp=WebappConfig(
            export_enabled=False,
            tier_state_path=str(tmp_path / "tier.json"),
            publish_history_path=str(tmp_path / "publish_history"),
        ),
        pipeline=load_config().pipeline.model_copy(
            update={
                "idempotency_dir": str(tmp_path / "pipeline_state"),
                "dead_letter_dir": str(tmp_path / "pipeline_state" / "dead_letter"),
            }
        ),
    )


def _orientation_detail(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "home_team": "Ohio State",
        "away_team": "Michigan",
        "bet_on": "home",
        "market_line_home": -7.5,
        "model_line_home": -10.0,
        "american_odds": -110.0,
        "p_win": 0.57,
    }
    base.update(overrides)
    return base


def test_social_disabled_by_default() -> None:
    cfg = load_config()
    assert cfg.social.enabled is False
    assert cfg.social.public_min_edge_sides == 0.045
    assert cfg.social.public_min_edge_totals == 0.055
    assert cfg.social.unit_fraction == 0.005


def test_orient_bet_lines_home_away_and_totals() -> None:
    side_team, mkt, model = orient_bet_lines(
        market="side",
        home_team="Ohio State",
        away_team="Michigan",
        bet_on="home",
        market_line_home=-7.5,
        model_line_home=-10.0,
    )
    assert side_team == "Ohio State"
    assert mkt == pytest.approx(-7.5)
    assert model == pytest.approx(-10.0)

    side_team, mkt, model = orient_bet_lines(
        market="side",
        home_team="Ohio State",
        away_team="Michigan",
        bet_on="away",
        market_line_home=-7.5,
        model_line_home=-10.0,
    )
    assert side_team == "Michigan"
    assert mkt == pytest.approx(7.5)
    assert model == pytest.approx(10.0)

    side_team, mkt, model = orient_bet_lines(
        market="total",
        home_team="Ohio State",
        away_team="Michigan",
        bet_on="over",
        market_line_home=48.5,
        model_line_home=52.0,
    )
    assert side_team == "Over"
    assert mkt == pytest.approx(48.5)
    assert model == pytest.approx(52.0)


def test_build_candidate_record_uses_recommended_stake() -> None:
    rec = build_candidate_record(
        game_id="401628373",
        market="side",
        edge=0.052,
        expected_value=0.041,
        model_market_residual_points=2.5,
        american_odds=-110.0,
        p_win=0.57,
        home_team="Ohio State",
        away_team="Michigan",
        bet_on="home",
        market_line_home=-7.5,
        model_line_home=-10.0,
        betting=BettingConfig(),
    )
    assert rec.side_team == "Ohio State"
    assert rec.market_line == pytest.approx(-7.5)
    assert rec.model_line == pytest.approx(-10.0)
    assert rec.stake_fraction > 0.0
    assert rec.stake_fraction <= 0.015


def test_export_social_candidates_writes_sidecar(tmp_path: Path) -> None:
    cfg = _social_cfg(tmp_path, enabled=True)
    published = datetime(2024, 9, 24, 6, 0, 0, tzinfo=UTC)
    draft = {
        "game_id": "401628373",
        "market": "side",
        "edge": 0.052,
        "expected_value": 0.041,
        "model_market_residual_points": 2.5,
        **_orientation_detail(),
    }
    rejected_draft = {
        **draft,
        "game_id": "401628374",
        "edge": 0.01,
        "bet_on": "away",
        "reasons": [FilterReason.EDGE_TOO_SMALL],
    }
    publish = {
        "season": 2024,
        "week": 5,
        "refresh_kind": RefreshKind.TUESDAY_PRIMARY,
        "published_at": published,
        "fixture": True,
        "accepted": [draft],
        "rejected": [rejected_draft],
    }
    path = export_social_candidates(publish, cfg)
    assert path is not None
    assert path.is_file()
    expected = candidates_sidecar_path(
        cfg.social, season=2024, week=5, refresh_kind=RefreshKind.TUESDAY_PRIMARY
    )
    assert path == expected

    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["season"] == 2024
    assert body["week"] == 5
    assert body["refresh_kind"] == RefreshKind.TUESDAY_PRIMARY
    assert body["fixture"] is True
    assert body["published_at"].startswith("2024-09-24T06:00:00")
    assert len(body["accepted"]) == 1
    assert body["accepted"][0]["side_team"] == "Ohio State"
    assert body["accepted"][0]["market_line"] == pytest.approx(-7.5)
    assert "stake_fraction" in body["accepted"][0]
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["reasons"] == ["edge_too_small"]
    assert body["rejected"][0]["side_team"] == "Michigan"


def test_export_disabled_writes_nothing(tmp_path: Path) -> None:
    cfg = _social_cfg(tmp_path, enabled=False)
    publish = {
        "season": 2024,
        "week": 5,
        "refresh_kind": RefreshKind.TUESDAY_PRIMARY,
        "fixture": False,
        "accepted": [],
        "rejected": [],
    }
    assert export_social_candidates(publish, cfg) is None
    social_root = tmp_path / "social"
    assert not social_root.exists() or not any(social_root.rglob("candidates.json"))


def test_fixture_week_publish_writes_sidecar_with_fixture_flag(tmp_path: Path) -> None:
    cfg = _social_cfg(tmp_path, enabled=True)
    result = run_fixture_week_publish(config=cfg)
    assert result["fixture"] is True
    assert result["social_export"]["ok"] is True
    path = Path(result["social_export"]["path"])
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["fixture"] is True
    assert body["accepted"] == []
    assert body["rejected"] == []


def test_social_disabled_on_publish_writes_no_file(tmp_path: Path) -> None:
    cfg = _social_cfg(tmp_path, enabled=False)

    def _predict(_ctx: Any) -> list[dict[str, Any]]:
        return [{"game_id": "g1", "mu_margin": 1.0, "sigma_margin": 14.0}]

    result = execute_predict_publish(
        season=2024,
        week=5,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        predict_fn=_predict,
        config=cfg,
    )
    assert "social_export" not in result
    social_root = tmp_path / "social"
    assert not social_root.exists() or not any(social_root.rglob("candidates.json"))


def test_social_export_failure_does_not_fail_publish(tmp_path: Path) -> None:
    cfg = _social_cfg(tmp_path, enabled=True)
    notifier = RecordingNotifier()

    def _predict(_ctx: Any) -> list[dict[str, Any]]:
        return [{"game_id": "g-stub", "mu_margin": 2.0, "sigma_margin": 14.0}]

    def _build(_preds: Any) -> list[BetCandidate]:
        return [
            BetCandidate(
                game_id="g-stub",
                market="side",
                edge=0.05,
                expected_value=0.02,
                is_stale=False,
                qb_status_known=True,
                is_bowl=False,
                model_market_residual_points=2.0,
            )
        ]

    result = execute_predict_publish(
        season=2024,
        week=5,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        predict_fn=_predict,
        build_candidates_fn=_build,
        config=cfg,
        notifier=notifier,
        # details omitted → records_from_filter_result raises
    )
    assert result["predictions"]
    assert result["n_accepted"] == 1
    assert result["social_export"]["ok"] is False
    assert AlertKind.SOCIAL_EXPORT_FAILURE in {a.kind for a in notifier.sent}


def test_rejected_reasons_are_filter_reason_values() -> None:
    cand = BetCandidate(
        game_id="g1",
        market="side",
        edge=0.01,
        expected_value=0.02,
        is_stale=False,
        qb_status_known=True,
        is_bowl=False,
        model_market_residual_points=2.0,
    )
    _acc, rej = records_from_filter_result(
        [],
        [(cand, (FilterReason.EDGE_TOO_SMALL, FilterReason.NON_POSITIVE_EV))],
        details={"g1:side": _orientation_detail()},
        betting=BettingConfig(),
    )
    assert rej[0]["reasons"] == ["edge_too_small", "non_positive_ev"]


def test_social_fields_not_in_webapp_export(tmp_path: Path) -> None:
    """Bet/social payload on publish_result must not appear in R2-bound artifacts."""
    publish = {
        "season": 2024,
        "week": 5,
        "refresh_kind": RefreshKind.TUESDAY_PRIMARY,
        "predictions": [
            {
                "game_id": "401628373",
                "mu_margin": 1.0,
                "sigma_margin": 14.0,
                "is_stale": False,
                "stale_stamp": None,
            }
        ],
        "prediction_rows": [],
        "stale": {"is_stale": False, "combined_stamp": None, "sources": []},
        "accepted": [
            {
                "game_id": "401628373",
                "market": "side",
                "side_team": "Texas A&M",
                "market_line": -3.5,
                "model_line": -7.0,
                "edge": 0.05,
                "expected_value": 0.04,
                "american_odds": -110.0,
                "p_win": 0.55,
                "stake_fraction": 0.008,
                "model_market_residual_points": 3.5,
            }
        ],
        "rejected": [],
        "n_candidates": 1,
        "n_accepted": 1,
        "n_rejected": 0,
    }
    cfg = AppConfig(
        webapp=WebappConfig(
            tier_state_path=str(tmp_path / "tier.json"),
            publish_history_path=str(tmp_path / "publish_history"),
        )
    )
    try:
        out = export_publish_artifacts(publish, config=cfg)
    except FileNotFoundError:
        pytest.skip("staged schedule unavailable")
    hits = assert_no_denylisted_fields(out["week_predictions"])
    dumped = json.dumps(out["week_predictions"])
    assert "stake_fraction" not in dumped
    assert "american_odds" not in dumped
    assert "side_team" not in dumped
    assert "accepted" in ODDS_FIELD_DENYLIST
    assert hits == []
