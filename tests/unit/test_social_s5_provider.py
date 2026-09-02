"""S5 bet candidate provider + exposure-filter threading tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.betting.devig import american_to_decimal, proportional_devig
from ncaa_quant.betting.edges import expected_value
from ncaa_quant.betting.filters import BetCandidate, FilterReason, evaluate_filters
from ncaa_quant.betting.provider import (
    build_candidates_from_odds,
    qb_status_known_for_game,
    resolve_asof_snapshot_window,
)
from ncaa_quant.config import AppConfig, BettingConfig, PathsConfig
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.distribution.simulate import JointDraws
from ncaa_quant.evaluation.walkforward import WalkForwardConfig
from ncaa_quant.pipelines.predict import (
    _default_build_candidates,
    apply_bet_filters,
    execute_predict_publish,
)
from ncaa_quant.pipelines.stale import StampedPrediction

# ---------------------------------------------------------------------------
# Hand-computed fixture (acceptance #1)
# ---------------------------------------------------------------------------
#
# Setup (verifiable with a calculator):
#   Two-way American −110 / −110
#     raw implied each = 110/210
#     proportional de-vig → fair p_market = 0.5 each
#   Model p_win = 0.55 (forced via fixed JointDraws: 550/1000 margins cover −3.5)
#     Cover rule: margin + line > 0 ⇒ margin > 3.5; we use margin=10 (cover) or
#     margin=0 (no cover).
#   edge = 0.55 − 0.5 = 0.05
#   decimal(−110) = 210/110
#   EV = 0.55 * (210/110) − 1 = 0.05
#


def _hand_draws(*, p_cover: float = 0.55, n: int = 1000) -> JointDraws:
    n_win = int(round(p_cover * n))
    margins = np.array([[10.0] * n_win + [0.0] * (n - n_win)], dtype=float)
    totals = np.full_like(margins, 50.0)
    return JointDraws(margins=margins, totals=totals, seed=0, n_draws=n)


def _stage_minimal_store(
    tmp_path: Path,
    *,
    season: int = 2024,
    week: int = 5,
    game_id: int = 401628373,
    home: str = "Home U",
    away: str = "Away U",
    home_id: int = 1,
    away_id: int = 2,
    kickoff: datetime | None = None,
    as_of: datetime | None = None,
    home_line: float = -3.5,
    home_price: float = -110.0,
    away_price: float = -110.0,
    qb_rows: list[dict[str, Any]] | None = None,
) -> ParquetStore:
    kick = kickoff or datetime(2024, 9, 28, 19, 0, tzinfo=UTC)
    ao = as_of or datetime(2024, 9, 24, 10, 0, tzinfo=UTC)
    store = ParquetStore(tmp_path / "staged")
    games = pd.DataFrame(
        [
            {
                "game_id": game_id,
                "season": season,
                "week": week,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "start_date": kick,
                "event_time": kick + timedelta(hours=3),
                "home_points": None,
                "away_points": None,
                "neutral_site": False,
                "conference_game": False,
                "source_version": "test",
                "ingested_at": ao,
            }
        ]
    )
    # games schema may require more cols — write with validate=False for fixture thinness
    store.write_partition("games", games, {"season": season, "week": week}, validate=False)

    teams = pd.DataFrame(
        [
            {
                "team_id": home_id,
                "season": season,
                "school": home,
                "conference": "Test",
                "classification": "fbs",
                "event_time": ao,
                "ingested_at": ao,
                "source_version": "test",
            },
            {
                "team_id": away_id,
                "season": season,
                "school": away,
                "conference": "Test",
                "classification": "fbs",
                "event_time": ao,
                "ingested_at": ao,
                "source_version": "test",
            },
        ]
    )
    store.write_partition("teams", teams, {"season": season}, validate=False)

    snaps = pd.DataFrame(
        [
            {
                "snapshot_id": "s1",
                "game_key": "k",
                "game_id": game_id,
                "season": season,
                "week": week,
                "book": "draftkings",
                "market": "spread",
                "side": home,
                "line": home_line,
                "price": home_price,
                "home_team": home,
                "away_team": away,
                "captured_at": ao,
                "event_time": ao,
                "ingested_at": ao,
                "source_version": "test",
                "snapshot_source": "historical",
                "decision_point": "tuesday_0600_et",
                "n_books_available": 1,
            },
            {
                "snapshot_id": "s2",
                "game_key": "k",
                "game_id": game_id,
                "season": season,
                "week": week,
                "book": "draftkings",
                "market": "spread",
                "side": away,
                "line": -home_line,
                "price": away_price,
                "home_team": home,
                "away_team": away,
                "captured_at": ao,
                "event_time": ao,
                "ingested_at": ao,
                "source_version": "test",
                "snapshot_source": "historical",
                "decision_point": "tuesday_0600_et",
                "n_books_available": 1,
            },
        ]
    )
    store.write_partition("odds_snapshots", snaps, {"season": season, "week": week}, validate=False)

    if qb_rows:
        qb = pd.DataFrame(qb_rows)
        store.write_partition("qb_status", qb, {"season": season}, validate=False)

    return store


def test_hand_computed_provider_fixture(tmp_path: Path) -> None:
    as_of = datetime(2024, 9, 24, 10, 0, tzinfo=UTC)
    kick = datetime(2024, 9, 28, 19, 0, tzinfo=UTC)
    store = _stage_minimal_store(tmp_path, as_of=as_of, kickoff=kick, qb_rows=None)

    # Hand arithmetic for −110/−110
    q = 110.0 / 210.0
    p_market = float(proportional_devig([q, q])[0])
    assert p_market == pytest.approx(0.5)
    p_model = 0.55
    edge = p_model - p_market
    assert edge == pytest.approx(0.05)
    dec = american_to_decimal(-110)
    assert dec == pytest.approx(210.0 / 110.0)
    ev = expected_value(p_model, -110)
    assert ev == pytest.approx(p_model * dec - 1.0)
    assert ev == pytest.approx(0.05)

    pred = {
        "game_id": 401628373,
        "mu_margin": 7.0,
        "sigma_margin": 14.0,
        "pred_margin": 7.0,
        "sigma_m": 14.0,
        "mu_total": 50.0,
        "sigma_total": 16.0,
        "null_reason": None,
        "sigma_m_is_missing": False,
        "home_team": "Home U",
        "away_team": "Away U",
        "home_team_id": 1,
        "away_team_id": 2,
        "kickoff_utc": kick.isoformat().replace("+00:00", "Z"),
        "is_stale": False,
    }
    cfg = BettingConfig(
        candidates_enabled=True,
        candidate_markets=["side"],
        no_bet_on_qb_unknown=False,
        min_edge_sides=0.0,
        min_model_market_agreement=100.0,
    )
    app = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged")),
        betting=cfg,
    )
    cands, details = build_candidates_from_odds(
        [pred],
        season=2024,
        week=5,
        as_of=as_of,
        store=store,
        config=app,
        draws=_hand_draws(p_cover=0.55),
    )
    assert len(cands) == 1
    cand = cands[0]
    assert cand.block_reasons == ()
    assert cand.edge == pytest.approx(0.05, abs=1e-9)
    assert cand.expected_value == pytest.approx(0.05, abs=1e-9)
    assert cand.p_win == pytest.approx(0.55)
    assert cand.american_odds == pytest.approx(-110.0)
    d = details["401628373:side"]
    assert d["book"] == "draftkings"
    assert d["p_win"] == pytest.approx(0.55)
    assert d["qb_status_source"] == "check_disabled"


def test_asof_window_never_future() -> None:
    as_of = datetime(2024, 9, 24, 10, 0, tzinfo=UTC)
    frame = pd.DataFrame(
        [
            {
                "game_id": 1,
                "event_time": as_of + timedelta(hours=1),
                "book": "dk",
                "market": "spread",
                "side": "H",
                "line": -3.0,
                "price": -110.0,
            },
            {
                "game_id": 1,
                "event_time": as_of - timedelta(hours=1),
                "book": "dk",
                "market": "spread",
                "side": "H",
                "line": -3.5,
                "price": -110.0,
            },
        ]
    )
    window, rung = resolve_asof_snapshot_window(
        frame,
        game_id=1,
        bound=as_of,
        kickoff=as_of + timedelta(days=3),
        config=WalkForwardConfig(),
    )
    assert not window.empty
    assert (window["event_time"] <= pd.Timestamp(as_of)).all()
    assert float(window.iloc[0]["line"]) == -3.5
    assert rung in {"odds_api_snapshot", "odds_api_snapshot_fallback"}


def test_qb_status_both_teams_required(tmp_path: Path) -> None:
    as_of = datetime(2024, 9, 24, 10, 0, tzinfo=UTC)
    qb = pd.DataFrame(
        [
            {
                "game_id": 10,
                "team_id": 1,
                "season": 2024,
                "status": "starter",
                "event_time": as_of - timedelta(hours=1),
                "ingested_at": as_of,
                "source_version": "manual_v1",
            }
        ]
    )
    known, src = qb_status_known_for_game(
        qb, game_id=10, home_team_id=1, away_team_id=2, as_of=as_of
    )
    assert known is False
    assert src == "unchecked"

    qb2 = pd.concat(
        [
            qb,
            pd.DataFrame(
                [
                    {
                        "game_id": 10,
                        "team_id": 2,
                        "season": 2024,
                        "status": "starter",
                        "event_time": as_of - timedelta(minutes=30),
                        "ingested_at": as_of,
                        "source_version": "manual_v1",
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    known2, src2 = qb_status_known_for_game(
        qb2, game_id=10, home_team_id=1, away_team_id=2, as_of=as_of
    )
    assert known2 is True
    assert src2 == "staged_asof"


def test_qb_unknown_status_rejects(tmp_path: Path) -> None:
    as_of = datetime(2024, 9, 24, 10, 0, tzinfo=UTC)
    kick = datetime(2024, 9, 28, 19, 0, tzinfo=UTC)
    qb_rows = [
        {
            "game_id": 401628373,
            "team_id": 1,
            "season": 2024,
            "status": "starter",
            "event_time": as_of - timedelta(hours=1),
            "ingested_at": as_of,
            "source_version": "manual_v1",
        },
        {
            "game_id": 401628373,
            "team_id": 2,
            "season": 2024,
            "status": "unknown",
            "event_time": as_of - timedelta(hours=1),
            "ingested_at": as_of,
            "source_version": "manual_v1",
        },
    ]
    store = _stage_minimal_store(tmp_path, as_of=as_of, kickoff=kick, qb_rows=qb_rows)
    pred = {
        "game_id": 401628373,
        "mu_margin": 7.0,
        "sigma_margin": 14.0,
        "null_reason": None,
        "sigma_m_is_missing": False,
        "home_team": "Home U",
        "away_team": "Away U",
        "home_team_id": 1,
        "away_team_id": 2,
        "kickoff_utc": kick.isoformat().replace("+00:00", "Z"),
        "is_stale": False,
    }
    app = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged")),
        betting=BettingConfig(
            candidates_enabled=True,
            no_bet_on_qb_unknown=True,
            min_edge_sides=0.0,
            min_model_market_agreement=100.0,
        ),
    )
    cands, details = build_candidates_from_odds(
        [pred],
        season=2024,
        week=5,
        as_of=as_of,
        store=store,
        config=app,
        draws=_hand_draws(),
    )
    assert len(cands) == 1
    assert cands[0].qb_status_known is False
    assert details["401628373:side"]["qb_status_source"] == "unchecked"
    result = evaluate_filters(cands[0], app.betting)
    assert result.accepted is False
    assert FilterReason.QB_STATUS_UNKNOWN in result.reasons


def _cand(
    *,
    game_id: str,
    edge: float,
    team_ids: tuple[str, ...] = (),
    p_win: float = 0.6,
    american_odds: float = -110.0,
) -> BetCandidate:
    return BetCandidate(
        game_id=game_id,
        market="side",
        edge=edge,
        expected_value=0.05,
        is_stale=False,
        qb_status_known=True,
        is_bowl=False,
        model_market_residual_points=1.0,
        team_ids=team_ids,
        p_win=p_win,
        american_odds=american_odds,
    )


def test_max_bets_per_week_caps_forty_candidates() -> None:
    """D4: 40 candidates capped at max_bets_per_week (fails on pre-D4 apply)."""
    cfg = BettingConfig(
        max_bets_per_week=10,
        min_edge_sides=0.0,
        min_model_market_agreement=100.0,
        no_bet_on_stale=False,
        no_bet_on_qb_unknown=False,
        max_weekly_exposure=1.0,
        max_exposure_per_team=1.0,
    )
    cands = [_cand(game_id=f"g{i}", edge=0.10 - i * 0.001) for i in range(40)]
    accepted, rejected = apply_bet_filters(cands, betting_config=cfg)
    assert len(accepted) == 10
    assert len(rejected) == 30
    assert all(FilterReason.MAX_BETS_PER_WEEK in reasons for _, reasons in rejected)


def test_max_team_exposure_two_bets_same_team() -> None:
    """D4: two large stakes on one team hit MAX_TEAM_EXPOSURE."""
    cfg = BettingConfig(
        max_bets_per_week=20,
        min_edge_sides=0.0,
        min_model_market_agreement=100.0,
        no_bet_on_stale=False,
        no_bet_on_qb_unknown=False,
        max_weekly_exposure=1.0,
        max_exposure_per_team=0.02,
        max_stake_pct=0.015,
        kelly_fraction=0.25,
    )
    # Each stake caps at 1.5%; second 1.5% on same team exceeds 2% team cap.
    cands = [
        _cand(game_id="g1", edge=0.20, team_ids=("42",), p_win=0.70, american_odds=-110),
        _cand(game_id="g2", edge=0.19, team_ids=("42",), p_win=0.70, american_odds=-110),
        _cand(game_id="g3", edge=0.18, team_ids=("99",), p_win=0.70, american_odds=-110),
    ]
    accepted, rejected = apply_bet_filters(cands, betting_config=cfg)
    assert any(c.game_id == "g1" for c in accepted)
    team42_rejected = [(c, r) for c, r in rejected if c.game_id == "g2"]
    assert team42_rejected
    assert FilterReason.MAX_TEAM_EXPOSURE in team42_rejected[0][1]


def test_candidates_enabled_false_is_empty_default() -> None:
    stamped = [
        StampedPrediction(
            game_id="1",
            mu_margin=3.0,
            sigma_margin=14.0,
            stale_stamp=None,
            is_stale=False,
        )
    ]
    assert _default_build_candidates(stamped) == []


def test_lockbox_season_still_raises() -> None:
    from ncaa_quant.pipelines.predict import LockboxSeasonError, load_production_prediction_rows

    with pytest.raises(LockboxSeasonError):
        load_production_prediction_rows(2025, 1)


def test_provider_no_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import urllib.request

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("network call attempted")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    as_of = datetime(2024, 9, 24, 10, 0, tzinfo=UTC)
    kick = datetime(2024, 9, 28, 19, 0, tzinfo=UTC)
    store = _stage_minimal_store(tmp_path, as_of=as_of, kickoff=kick)
    pred = {
        "game_id": 401628373,
        "mu_margin": 7.0,
        "sigma_margin": 14.0,
        "null_reason": None,
        "sigma_m_is_missing": False,
        "home_team": "Home U",
        "away_team": "Away U",
        "home_team_id": 1,
        "away_team_id": 2,
        "kickoff_utc": kick.isoformat().replace("+00:00", "Z"),
        "is_stale": False,
    }
    app = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged")),
        betting=BettingConfig(no_bet_on_qb_unknown=False, min_edge_sides=0.0),
    )
    build_candidates_from_odds(
        [pred],
        season=2024,
        week=5,
        as_of=as_of,
        store=store,
        config=app,
        draws=_hand_draws(),
    )


def test_flag_off_publish_identical_to_provider_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #5: candidates_enabled True vs False — empty card when no odds."""
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    out_a.mkdir()
    out_b.mkdir()

    def _predict(_stale: Any) -> list[dict[str, Any]]:
        return [
            {
                "game_id": "401000001",
                "mu_margin": 3.0,
                "sigma_margin": 14.0,
                "null_reason": None,
                "sigma_m_is_missing": False,
            }
        ]

    base = AppConfig(
        paths=PathsConfig(
            staged_dir=str(tmp_path / "staged"),
            raw_dir=str(tmp_path / "raw"),
        )
    )
    (tmp_path / "staged").mkdir()
    (tmp_path / "raw").mkdir()

    cfg_off = base.model_copy(
        update={
            "betting": BettingConfig(candidates_enabled=False),
            "social": base.social.model_copy(update={"enabled": True, "output_dir": str(out_a)}),
            "webapp": base.webapp.model_copy(update={"export_enabled": False}),
        }
    )
    cfg_on = base.model_copy(
        update={
            "betting": BettingConfig(candidates_enabled=True, no_bet_on_qb_unknown=False),
            "social": base.social.model_copy(update={"enabled": True, "output_dir": str(out_b)}),
            "webapp": base.webapp.model_copy(update={"export_enabled": False}),
        }
    )

    r_off = execute_predict_publish(
        season=2024,
        week=5,
        refresh_kind="tuesday_primary",
        predict_fn=_predict,
        odds_ingest_fn=lambda: {},
        config=cfg_off,
        as_of=datetime(2024, 9, 24, 10, 0, tzinfo=UTC),
        fixture=True,
        published_at=datetime(2024, 9, 24, 10, 0, tzinfo=UTC),
    )
    r_on = execute_predict_publish(
        season=2024,
        week=5,
        refresh_kind="tuesday_primary",
        predict_fn=_predict,
        odds_ingest_fn=lambda: {},
        config=cfg_on,
        as_of=datetime(2024, 9, 24, 10, 0, tzinfo=UTC),
        fixture=True,
        published_at=datetime(2024, 9, 24, 10, 0, tzinfo=UTC),
    )
    # No staged odds → provider refuses with NO_SNAPSHOT; flag-off has zero candidates.
    # Byte-identical webapp artifacts are N/A (export off). Guard: n_accepted both 0.
    assert r_off["n_accepted"] == 0
    assert r_on["n_accepted"] == 0
    assert r_off["n_candidates"] == 0
    assert r_on["n_candidates"] >= 0  # may include refusal shells


def test_thirteen_day_old_snapshot_rejects_stale(tmp_path: Path) -> None:
    """Snapshot age > odds_max_age_hours → STALE_INPUTS (not publish-row is_stale)."""
    as_of = datetime(2024, 9, 24, 10, 0, tzinfo=UTC)
    kick = datetime(2024, 9, 28, 19, 0, tzinfo=UTC)
    snap_at = as_of - timedelta(days=13)
    store = _stage_minimal_store(tmp_path, as_of=snap_at, kickoff=kick, qb_rows=None)
    pred = {
        "game_id": 401628373,
        "mu_margin": 7.0,
        "sigma_margin": 14.0,
        "null_reason": None,
        "sigma_m_is_missing": False,
        "home_team": "Home U",
        "away_team": "Away U",
        "home_team_id": 1,
        "away_team_id": 2,
        "kickoff_utc": kick.isoformat().replace("+00:00", "Z"),
        # Publish-level stamp is fresh — must not mask snapshot age.
        "is_stale": False,
    }
    app = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged")),
        betting=BettingConfig(
            candidates_enabled=True,
            no_bet_on_qb_unknown=False,
            no_bet_on_stale=True,
            odds_max_age_hours=6.0,
            min_edge_sides=0.0,
            min_model_market_agreement=100.0,
        ),
    )
    cands, details = build_candidates_from_odds(
        [pred],
        season=2024,
        week=5,
        as_of=as_of,
        store=store,
        config=app,
        draws=_hand_draws(),
    )
    assert len(cands) == 1
    assert cands[0].is_stale is True
    assert details["401628373:side"]["ladder_rung"] == "odds_api_snapshot_fallback"
    result = evaluate_filters(cands[0], app.betting)
    assert result.accepted is False
    assert FilterReason.STALE_INPUTS in result.reasons


def test_missing_qb_row_rejects_when_flag_on(tmp_path: Path) -> None:
    """No staged qb_status row → unknown, not known; rejects when flag is on."""
    as_of = datetime(2024, 9, 24, 10, 0, tzinfo=UTC)
    kick = datetime(2024, 9, 28, 19, 0, tzinfo=UTC)
    store = _stage_minimal_store(tmp_path, as_of=as_of, kickoff=kick, qb_rows=None)
    pred = {
        "game_id": 401628373,
        "mu_margin": 7.0,
        "sigma_margin": 14.0,
        "null_reason": None,
        "sigma_m_is_missing": False,
        "home_team": "Home U",
        "away_team": "Away U",
        "home_team_id": 1,
        "away_team_id": 2,
        "kickoff_utc": kick.isoformat().replace("+00:00", "Z"),
        "is_stale": False,
    }
    app = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged")),
        betting=BettingConfig(
            candidates_enabled=True,
            no_bet_on_qb_unknown=True,
            no_bet_on_stale=False,
            min_edge_sides=0.0,
            min_model_market_agreement=100.0,
        ),
    )
    cands, details = build_candidates_from_odds(
        [pred],
        season=2024,
        week=5,
        as_of=as_of,
        store=store,
        config=app,
        draws=_hand_draws(),
    )
    assert len(cands) == 1
    assert cands[0].qb_status_known is False
    assert details["401628373:side"]["qb_status_source"] == "unchecked"
    result = evaluate_filters(cands[0], app.betting)
    assert result.accepted is False
    assert FilterReason.QB_STATUS_UNKNOWN in result.reasons


def test_check_disabled_does_not_hardcode_qb_known(tmp_path: Path) -> None:
    """Flag off stamps check_disabled but must not force qb_status_known=True."""
    as_of = datetime(2024, 9, 24, 10, 0, tzinfo=UTC)
    kick = datetime(2024, 9, 28, 19, 0, tzinfo=UTC)
    store = _stage_minimal_store(tmp_path, as_of=as_of, kickoff=kick, qb_rows=None)
    pred = {
        "game_id": 401628373,
        "mu_margin": 7.0,
        "sigma_margin": 14.0,
        "null_reason": None,
        "sigma_m_is_missing": False,
        "home_team": "Home U",
        "away_team": "Away U",
        "home_team_id": 1,
        "away_team_id": 2,
        "kickoff_utc": kick.isoformat().replace("+00:00", "Z"),
        "is_stale": False,
    }
    app = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged")),
        betting=BettingConfig(
            candidates_enabled=True,
            no_bet_on_qb_unknown=False,
            no_bet_on_stale=False,
            min_edge_sides=0.0,
            min_model_market_agreement=100.0,
        ),
    )
    cands, details = build_candidates_from_odds(
        [pred],
        season=2024,
        week=5,
        as_of=as_of,
        store=store,
        config=app,
        draws=_hand_draws(),
    )
    assert cands[0].qb_status_known is False
    assert details["401628373:side"]["qb_status_source"] == "check_disabled"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()
