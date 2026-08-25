"""S2 — public Best Bet selection (social.select)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from ncaa_quant.config import BettingConfig, SocialConfig
from ncaa_quant.social.select import A_TIER_EDGE, BestBet, select_best_bets

NOW = datetime(2024, 9, 24, 10, 0, 0, tzinfo=UTC)


def _game(
    game_id: str,
    *,
    is_stale: bool = False,
    sigma_ok: bool = True,
    kickoff_utc: str = "2024-09-28T19:30:00Z",
    away: str = "Away U",
    home: str = "Home U",
    mu_margin: float | None = 7.0,
) -> dict[str, Any]:
    return {
        "game_id": game_id,
        "away_team": away,
        "home_team": home,
        "is_stale": is_stale,
        "sigma_margin_credible": sigma_ok,
        "kickoff_utc": kickoff_utc,
        "mu_margin": mu_margin,
        "margin_interval_lo": -10.0,
        "margin_interval_hi": 20.0,
    }


def _cand(
    game_id: str,
    *,
    edge: float = 0.05,
    market: str = "side",
    stake_fraction: float = 0.01,
) -> dict[str, Any]:
    return {
        "game_id": game_id,
        "market": market,
        "side_team": "Home U",
        "market_line": -3.5,
        "model_line": -7.5,
        "edge": edge,
        "expected_value": 0.04,
        "american_odds": -110,
        "p_win": 0.57,
        "stake_fraction": stake_fraction,
        "model_market_residual_points": 4.0,
    }


def test_select_applies_public_edge_bars_and_sorts() -> None:
    games = {
        "1": _game("1", home="A"),
        "2": _game("2", home="B"),
        "3": _game("3", home="C"),
    }
    accepted = [
        _cand("1", edge=0.046),  # clears sides bar
        _cand("2", edge=0.044),  # below sides bar
        _cand("3", edge=0.070, market="total"),  # clears totals bar
    ]
    social = SocialConfig(public_min_edge_sides=0.045, public_min_edge_totals=0.055)
    bets, skipped = select_best_bets(games, accepted, NOW, social=social, betting=BettingConfig())
    assert [b.cand["game_id"] for b in bets] == ["3", "1"]
    assert bets[0].rating == "A"
    assert bets[1].rating == "B"
    assert any(c["game_id"] == "2" and why == "below public edge bar" for c, why in skipped)


def test_select_excludes_stale_and_sigma_refused() -> None:
    games = {
        "stale": _game("stale", is_stale=True),
        "sigma": _game("sigma", sigma_ok=False),
        "ok": _game("ok"),
    }
    accepted = [
        _cand("stale", edge=0.08),
        _cand("sigma", edge=0.08),
        _cand("ok", edge=0.08),
    ]
    bets, skipped = select_best_bets(
        games,
        accepted,
        NOW,
        social=SocialConfig(),
        betting=BettingConfig(),
    )
    assert [b.cand["game_id"] for b in bets] == ["ok"]
    reasons = {c["game_id"]: why for c, why in skipped}
    assert reasons["stale"] == "stale inputs at post time"
    assert reasons["sigma"] == "sigma refused (ADR 0014)"


def test_select_excludes_already_kicked_off() -> None:
    games = {"g": _game("g", kickoff_utc="2024-09-24T09:00:00Z")}
    bets, skipped = select_best_bets(
        games,
        [_cand("g", edge=0.08)],
        NOW,
        social=SocialConfig(),
        betting=BettingConfig(),
    )
    assert bets == []
    assert skipped[0][1] == "already kicked off"


def test_select_caps_at_max_bets_per_week_variable_count() -> None:
    games = {str(i): _game(str(i)) for i in range(12)}
    accepted = [_cand(str(i), edge=0.05 + i * 0.001) for i in range(12)]
    betting = BettingConfig(max_bets_per_week=4)
    bets, _ = select_best_bets(games, accepted, NOW, social=SocialConfig(), betting=betting)
    assert len(bets) == 4
    # Highest edges first — never padded to a round marketing number.
    assert [b.rank for b in bets] == [1, 2, 3, 4]
    edges = [float(b.cand["edge"]) for b in bets]
    assert edges == sorted(edges, reverse=True)


def test_select_n0_when_nothing_clears() -> None:
    games = {"1": _game("1")}
    bets, skipped = select_best_bets(
        games,
        [_cand("1", edge=0.01)],
        NOW,
        social=SocialConfig(),
        betting=BettingConfig(),
    )
    assert bets == []
    assert len(skipped) == 1


def test_select_reads_thresholds_from_social_config_override() -> None:
    """S3 overrides floors via SocialConfig copies — not script constants."""
    games = {"1": _game("1")}
    accepted = [_cand("1", edge=0.05)]
    strict = SocialConfig(public_min_edge_sides=0.06)
    bets, _ = select_best_bets(games, accepted, NOW, social=strict, betting=BettingConfig())
    assert bets == []

    loose = SocialConfig(public_min_edge_sides=0.04)
    bets2, _ = select_best_bets(games, accepted, NOW, social=loose, betting=BettingConfig())
    assert len(bets2) == 1


def test_best_bet_units_use_unit_fraction() -> None:
    bet = BestBet(
        rank=1,
        rating="A",
        game=_game("1"),
        cand=_cand("1", stake_fraction=0.015),
        unit_fraction=0.005,
    )
    assert bet.units == pytest.approx(3.0)
    assert float(bet.edge_pct) == pytest.approx(5.0)
    assert pytest.approx(0.060) == A_TIER_EDGE

    fallback = BestBet(
        rank=1,
        rating="B",
        game=_game("1"),
        cand=_cand("1", stake_fraction=0.0),
        unit_fraction=0.005,
    )
    assert fallback.units == pytest.approx(1.0)


def test_select_skips_missing_game_and_bad_kickoff() -> None:
    games = {"1": _game("1", kickoff_utc="not-a-date")}
    accepted = [_cand("missing", edge=0.08), _cand("1", edge=0.08)]
    bets, skipped = select_best_bets(
        games, accepted, NOW, social=SocialConfig(), betting=BettingConfig()
    )
    assert len(bets) == 1
    assert bets[0].cand["game_id"] == "1"
    assert any(c["game_id"] == "missing" for c, _ in skipped)
