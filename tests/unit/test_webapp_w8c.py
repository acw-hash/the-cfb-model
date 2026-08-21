"""W8-C: publish-key allowlist and withdrawn cover/over fields."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ncaa_quant.pipelines.predict import RefreshKind
from ncaa_quant.webapp.export import (
    SCHEMA_VERSION,
    WITHDRAWN_FIELDS,
    PublishedKeyAllowlistError,
    assert_game_prediction_allowlist,
    build_game_prediction,
)

SCHEDULE = {
    "game_id": "401628373",
    "home_team": "Michigan",
    "away_team": "Minnesota",
    "home_team_id": 130,
    "away_team_id": 135,
    "kickoff_utc": "2024-10-05T19:30:00Z",
    "neutral_site": False,
    "conference_game": True,
}

PRODUCTION = {
    "game_id": "401628373",
    "pred_margin": 4.15,
    "sigma_m": 16.73,
    "sigma_m_is_missing": False,
    "pred_total": 49.7,
    "sigma_t": 16.85,
    "sigma_t_is_missing": False,
    "cqr_lo": -23.8,
    "cqr_hi": 33.4,
    "cqr_nominal": 0.8,
    "p_ml_home": 0.676,
    "p_ats_home": 0.425,
    "p_ou_over": 0.447,
    "p_ml_home_is_missing": False,
    "p_ats_home_is_missing": False,
    "p_ou_over_is_missing": False,
    "null_reason": None,
    "is_stale": False,
    "stale_stamp": None,
    "stale_sources": [],
}


def _build() -> dict[str, object]:
    return build_game_prediction(
        PRODUCTION,
        SCHEDULE,
        season=2024,
        week=5,
        published_at=datetime(2024, 10, 1, 10, 0, tzinfo=UTC),
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        vintage_label="REGRADED_V2",
        ensemble_scope_label="REDUCED_PER_ADR_0013",
        feature_time_label="FEATURE_TIME=TUESDAY_DECISION",
        previous_tier=None,
        tier_primary=None,
    )


def test_schema_version_is_1_2_0() -> None:
    assert SCHEMA_VERSION == "1.3.0"


def test_export_omits_withdrawn_cover_over_keys() -> None:
    game = _build()
    assert_game_prediction_allowlist(game)
    for key in WITHDRAWN_FIELDS:
        assert key not in game
    assert "p_win_home" in game


def test_allowlist_rejects_unknown_key_then_passes() -> None:
    game = _build()
    assert_game_prediction_allowlist(game)
    poisoned = dict(game)
    poisoned["unsanctioned_edge"] = 0.03
    with pytest.raises(PublishedKeyAllowlistError, match="unsanctioned_edge"):
        assert_game_prediction_allowlist(poisoned)
    assert_game_prediction_allowlist(game)
