"""Tests for Ridge webapp artifact export, grade export, and R2 push (W1)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from ncaa_quant.config import AppConfig, WebappConfig
from ncaa_quant.pipelines.notifications import AlertKind, RecordingNotifier
from ncaa_quant.pipelines.predict import RefreshKind, execute_predict_publish
from ncaa_quant.webapp.export import (
    ODDS_FIELD_DENYLIST,
    TierStateStore,
    apply_hysteresis,
    assert_no_denylisted_fields,
    build_game_prediction,
    build_week_predictions,
    compute_conviction,
    export_publish_artifacts,
    generate_fixture_week_artifacts,
    raw_tier_from_p_favored,
)
from ncaa_quant.webapp.grade import GradeExportError, assert_live_season
from ncaa_quant.webapp.push import META_FILENAME, push_artifacts_to_r2

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "src" / "ncaa_quant" / "webapp" / "schemas"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _validate(instance: dict[str, Any], schema_name: str) -> None:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    jsonschema.validate(instance=instance, schema=schema)


@pytest.fixture
def schedule_row() -> dict[str, Any]:
    return {
        "game_id": "401628373",
        "home_team": "Michigan",
        "away_team": "Minnesota",
        "home_team_id": 130,
        "away_team_id": 135,
        "kickoff_utc": "2024-10-05T19:30:00Z",
        "neutral_site": False,
        "conference_game": True,
    }


@pytest.fixture
def production_row() -> dict[str, Any]:
    return {
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


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        del Bucket, ContentType
        self.objects[Key] = Body
        self.put_calls.append(Key)

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        del Bucket
        if Key not in self.objects:
            raise KeyError(Key)
        return {"ContentLength": len(self.objects[Key])}


def test_null_sigma_preserves_nulls_and_suppresses_tier(schedule_row: dict[str, Any]) -> None:
    row = {
        "game_id": "401628373",
        "pred_margin": 4.0,
        "sigma_m": None,
        "sigma_m_is_missing": True,
        "null_reason": "cold_start_insufficient",
        "p_ml_home": None,
        "p_ml_home_is_missing": True,
    }
    game = build_game_prediction(
        row,
        schedule_row,
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
    assert game["sigma_margin"] is None
    assert game["p_win_home"] is None
    assert game["conviction_tier"] is None
    assert game["sigma_margin_credible"] is False


def test_hysteresis_holds_inside_band() -> None:
    raw = raw_tier_from_p_favored(0.56)
    assert raw == "toss_up"
    tier, applied = apply_hysteresis(p_favored=0.56, raw_tier=raw, previous_tier="lean")
    assert tier == "lean"
    assert applied is True


def test_hysteresis_flips_outside_band() -> None:
    raw = raw_tier_from_p_favored(0.52)
    tier, applied = apply_hysteresis(p_favored=0.52, raw_tier=raw, previous_tier="lean")
    assert tier == "toss_up"


def test_w1a_old_boundaries_no_longer_apply() -> None:
    """Lock W1A amendment: pre-amendment thresholds must not classify tiers."""
    assert raw_tier_from_p_favored(0.70) == "clear_lean"
    assert raw_tier_from_p_favored(0.70) != "strong_lean"
    assert raw_tier_from_p_favored(0.65) == "lean"
    assert raw_tier_from_p_favored(0.65) != "strong_lean"


def test_odds_denylist_on_fixture_artifacts(tmp_path: Path) -> None:
    cfg = AppConfig(webapp=WebappConfig(fixture_artifacts_dir=str(tmp_path / "fixtures")))
    out = generate_fixture_week_artifacts(config=cfg, output_dir=tmp_path / "fixtures")
    hits: list[str] = []
    for _name, payload in out["artifacts"].items():
        hits.extend(assert_no_denylisted_fields(payload))
    assert hits == [], f"denylist violations: {hits}"


def test_denylist_grep_evidence() -> None:
    """Denylist includes walkforward odds columns and bet-candidate keys."""
    assert "spread_close" in ODDS_FIELD_DENYLIST
    assert "edge" in ODDS_FIELD_DENYLIST
    assert "n_candidates" in ODDS_FIELD_DENYLIST
    assert "spread_asof" in ODDS_FIELD_DENYLIST


def test_lockbox_refuses_season_2025() -> None:
    with pytest.raises(GradeExportError, match="2025"):
        assert_live_season(2025)


def test_push_meta_uploads_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    cfg = AppConfig(
        webapp=WebappConfig(
            export_enabled=True,
            r2_bucket="ridge-test",
            r2_endpoint_url="https://example.r2.cloudflarestorage.com",
        )
    )
    client = FakeS3()
    artifacts = {
        "week_predictions.json": '{"games":[]}\n',
        "track_record.json": '{"metrics":[]}\n',
        META_FILENAME: '{"schema_version":"1.0.0"}\n',
    }
    result = push_artifacts_to_r2(
        artifacts,
        season=2024,
        week=5,
        refresh_kind="tuesday_primary",
        config=cfg,
        client=client,
    )
    assert result["meta_last"] is True
    assert result["upload_order"][-1] == META_FILENAME
    meta_indices = [i for i, key in enumerate(client.put_calls) if key.endswith(META_FILENAME)]
    data_indices = [i for i, key in enumerate(client.put_calls) if not key.endswith(META_FILENAME)]
    assert meta_indices and data_indices
    assert min(meta_indices) > max(data_indices)


def test_idempotent_repush_same_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    cfg = AppConfig(
        webapp=WebappConfig(
            r2_bucket="ridge-test",
            r2_endpoint_url="https://example.r2.cloudflarestorage.com",
        )
    )
    client = FakeS3()
    body = '{"schema_version":"1.0.0"}\n'
    artifacts = {META_FILENAME: body}
    first = push_artifacts_to_r2(
        artifacts,
        season=2024,
        week=5,
        refresh_kind="tuesday_primary",
        config=cfg,
        client=client,
    )
    second = push_artifacts_to_r2(
        artifacts,
        season=2024,
        week=5,
        refresh_kind="tuesday_primary",
        config=cfg,
        client=client,
    )
    assert first["content_hashes"] == second["content_hashes"]
    assert client.objects["latest/meta.json"] == body.encode()


def test_no_credentials_in_repo() -> None:
    """Assert no R2/AWS credential literals appear under tracked source trees."""
    credential_patterns = [
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"R2_ACCESS_KEY_ID\s*=\s*['\"][^'\"]{8,}['\"]"),
        re.compile(r"R2_SECRET_ACCESS_KEY\s*=\s*['\"][^'\"]{8,}['\"]"),
    ]
    scan_roots = [
        REPO_ROOT / "src",
        REPO_ROOT / "tests",
        REPO_ROOT / "webapp",
        REPO_ROOT / "configs",
    ]
    hits: list[str] = []
    for root in scan_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {
                ".py",
                ".json",
                ".yaml",
                ".yml",
                ".md",
                ".env",
                ".toml",
            }:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in credential_patterns:
                if pattern.search(text):
                    hits.append(f"{path}: {pattern.pattern}")
    assert hits == [], f"credential-like strings found: {hits}"


def test_export_failure_does_not_fail_predict_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = AppConfig(
        webapp=WebappConfig(export_enabled=True, tier_state_path=str(tmp_path / "tier.json")),
    )
    notifier = RecordingNotifier()

    def _predict(_ctx: Any) -> list[dict[str, Any]]:
        return [{"game_id": "999", "mu_margin": 1.0, "sigma_margin": 14.0}]

    monkeypatch.setattr(
        "ncaa_quant.webapp.export.export_publish_artifacts",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated export failure")),
    )

    result = execute_predict_publish(
        season=2024,
        week=5,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        predict_fn=_predict,
        config=cfg,
        notifier=notifier,
    )
    assert result["predictions"]
    assert result["webapp_export"]["ok"] is False
    assert AlertKind.WEBAPP_EXPORT_FAILURE in {a.kind for a in notifier.sent}


def test_bet_candidate_fields_not_in_export(tmp_path: Path) -> None:
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
        "n_candidates": 3,
        "n_accepted": 1,
        "n_rejected": 2,
    }
    cfg = AppConfig(webapp=WebappConfig(tier_state_path=str(tmp_path / "tier.json")))
    try:
        out = export_publish_artifacts(publish, config=cfg)
    except FileNotFoundError:
        pytest.skip("staged schedule unavailable")
    hits = assert_no_denylisted_fields(out["week_predictions"])
    assert "n_candidates" not in json.dumps(out["week_predictions"])
    assert hits == []


def test_schema_validation_sample_records(tmp_path: Path) -> None:
    cfg = AppConfig(webapp=WebappConfig(fixture_artifacts_dir=str(tmp_path / "fx")))
    out = generate_fixture_week_artifacts(config=cfg, output_dir=tmp_path / "fx")
    artifacts = out["artifacts"]
    _validate(artifacts["week_predictions.json"], "week_predictions.schema.json")
    _validate(artifacts["meta.json"], "meta.schema.json")
    _validate(artifacts["track_record.json"], "track_record.schema.json")
    _validate(artifacts["results_2024.json"], "results_season.schema.json")
    _validate(artifacts["team_ratings_2024.json"], "team_ratings.schema.json")


def test_tier_distribution_report(tmp_path: Path) -> None:
    cfg = AppConfig(webapp=WebappConfig(fixture_artifacts_dir=str(tmp_path / "fx")))
    out = generate_fixture_week_artifacts(config=cfg, output_dir=tmp_path / "fx")
    dist = out["tier_distribution"]
    assert dist["total"] > 0
    assert set(dist["counts"]) == {"strong_lean", "clear_lean", "lean", "toss_up", "suppressed"}


def test_tier_state_store_roundtrip(tmp_path: Path) -> None:
    store = TierStateStore(tmp_path / "tier.json")
    store.save(
        tiers={"2024:1": "lean"},
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
    )
    assert store.load()["2024:1"] == "lean"
    assert store.load_tier_primary()["2024:1"] == "lean"


def test_week_predictions_hysteresis_end_to_end(
    tmp_path: Path, schedule_row: dict[str, Any]
) -> None:
    store = TierStateStore(tmp_path / "tier.json")
    row = {
        "game_id": "401628373",
        "pred_margin": 1.0,
        "sigma_m": 14.0,
        "sigma_m_is_missing": False,
        "p_ml_home": 0.60,
        "p_ml_home_is_missing": False,
    }
    first = build_week_predictions(
        season=2024,
        week=5,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        published_at=datetime(2024, 10, 1, 10, 0, tzinfo=UTC),
        prediction_rows=[row],
        schedule_by_game={"401628373": schedule_row},
        tier_store=store,
    )
    assert first["games"][0]["conviction_tier"] == "lean"

    row_hold = {**row, "p_ml_home": 0.56}
    second = build_week_predictions(
        season=2024,
        week=5,
        refresh_kind=RefreshKind.DAILY_REFRESH,
        published_at=datetime(2024, 10, 3, 10, 0, tzinfo=UTC),
        prediction_rows=[row_hold],
        schedule_by_game={"401628373": schedule_row},
        tier_store=store,
    )
    assert second["games"][0]["conviction_tier"] == "lean"
    assert second["games"][0]["conviction_basis"]["hysteresis_applied"] is True

    row_flip = {**row, "p_ml_home": 0.52}
    third = build_week_predictions(
        season=2024,
        week=5,
        refresh_kind=RefreshKind.DAILY_REFRESH,
        published_at=datetime(2024, 10, 4, 10, 0, tzinfo=UTC),
        prediction_rows=[row_flip],
        schedule_by_game={"401628373": schedule_row},
        tier_store=store,
    )
    assert third["games"][0]["conviction_tier"] == "toss_up"


def test_compute_conviction_suppression_on_stale_age() -> None:
    row = {
        "pred_margin": 5.0,
        "sigma_m": 14.0,
        "sigma_m_is_missing": False,
        "p_ml_home": 0.7,
        "p_ml_home_is_missing": False,
        "is_stale": True,
        "stale_sources": [
            {"source": "odds", "age_hours": 7.0, "last_good_at": "2024-10-01T04:00:00Z"}
        ],
    }
    out = compute_conviction(row, home_team="Home", away_team="Away", previous_tier=None)
    assert out["conviction_tier"] is None
