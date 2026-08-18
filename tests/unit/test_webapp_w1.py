"""Tests for Ridge webapp artifact export, grade export, and R2 push (W1)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pandas as pd
import pytest

from ncaa_quant.config import AppConfig, PathsConfig, WebappConfig
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
    tier_distribution,
)
from ncaa_quant.webapp.grade import (
    GradeExportError,
    assert_live_season,
    build_results_season,
    grade_export,
)
from ncaa_quant.webapp.push import META_FILENAME, push_artifacts_to_r2

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "src" / "ncaa_quant" / "webapp" / "schemas"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "webapp" / "fixtures"


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


def test_hysteresis_multi_boundary_exit_then_reassign() -> None:
    """Prior strong_lean exits hold band; tier reassigns to raw tier, not intermediate."""
    p_favored = 0.68
    raw = raw_tier_from_p_favored(p_favored)
    assert raw == "lean"
    tier, applied = apply_hysteresis(
        p_favored=p_favored,
        raw_tier=raw,
        previous_tier="strong_lean",
    )
    assert tier == "lean"
    assert tier != "clear_lean"
    assert applied is False


def test_w1a_old_boundaries_no_longer_apply() -> None:
    """Lock W1A amendment: pre-amendment thresholds must not classify tiers."""
    assert raw_tier_from_p_favored(0.70) == "clear_lean"
    assert raw_tier_from_p_favored(0.70) != "strong_lean"
    assert raw_tier_from_p_favored(0.65) == "lean"
    assert raw_tier_from_p_favored(0.65) != "strong_lean"


COMMITTED_FIXTURE_FILES = (
    "week_predictions.json",
    "track_record.json",
    "meta.json",
    "results_2024.json",
    "team_ratings_2024.json",
)


def _write_synthetic_fixture_sources(tmp_path: Path) -> tuple[Path, AppConfig]:
    """Walkforward parquet + staged schedule under tmp — no gitignored data/."""
    staged = tmp_path / "staged"
    games_dir = staged / "games" / "season=2024" / "week=5"
    teams_dir = staged / "teams" / "season=2024"
    games_dir.mkdir(parents=True)
    teams_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    hist_dir = data_dir / "artifacts" / "state_space"
    hist_dir.mkdir(parents=True)
    kickoff = pd.Timestamp("2024-09-28T19:30:00Z")
    pd.DataFrame(
        [
            {
                "game_id": 401628373,
                "season": 2024,
                "week": 5,
                "home_team_id": 245,
                "away_team_id": 8,
                "start_date": kickoff,
                "event_time": kickoff,
                "neutral_site": False,
                "conference_game": True,
                "home_points": 21,
                "away_points": 17,
                "completed": True,
            }
        ]
    ).to_parquet(games_dir / "part.parquet", index=False)
    pd.DataFrame(
        [
            {"team_id": 245, "school": "Texas A&M"},
            {"team_id": 8, "school": "Arkansas"},
        ]
    ).to_parquet(teams_dir / "part.parquet", index=False)
    pd.DataFrame(
        [
            {
                "team_id": 245,
                "season": 2024,
                "week": 1,
                "event_time": pd.Timestamp("2024-08-31T19:00:00Z"),
                "off_epa": 0.1,
                "def_epa": -0.05,
                "pace": 70.0,
                "sd_off_epa": 0.02,
                "sd_def_epa": 0.02,
            }
        ]
    ).to_parquet(hist_dir / "filter_history.parquet", index=False)
    wf_path = tmp_path / "week_predictions.parquet"
    pd.DataFrame(
        [
            {
                "game_id": 401628373,
                "pred_margin": 3.0,
                "sigma_m": 14.0,
                "sigma_m_is_missing": False,
                "p_ml_home": 0.62,
                "p_ml_home_is_missing": False,
                "model_version": "production-v0_reduced_v3",
                "run_id": "synthetic_ci",
                "home_points": 21,
                "away_points": 17,
            }
        ]
    ).to_parquet(wf_path, index=False)
    cfg = AppConfig(
        paths=PathsConfig(data_dir=str(data_dir), staged_dir=str(staged)),
        webapp=WebappConfig(
            fixture_artifacts_dir=str(tmp_path / "fx"),
            tier_state_path=str(tmp_path / "tier.json"),
        ),
    )
    return wf_path, cfg


def test_odds_denylist_on_fixture_artifacts() -> None:
    hits: list[str] = []
    for name in COMMITTED_FIXTURE_FILES:
        payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
        hits.extend(assert_no_denylisted_fields(payload))
    assert hits == [], f"denylist violations: {hits}"


def test_generate_fixture_week_artifacts_from_synthetic_sources(tmp_path: Path) -> None:
    wf_path, cfg = _write_synthetic_fixture_sources(tmp_path)
    out = generate_fixture_week_artifacts(
        config=cfg,
        output_dir=tmp_path / "fx",
        walkforward_path=wf_path,
    )
    hits: list[str] = []
    for _name, payload in out["artifacts"].items():
        hits.extend(assert_no_denylisted_fields(payload))
    assert hits == []
    week = out["artifacts"]["week_predictions.json"]
    assert week["games"][0]["game_id"] == "401628373"
    assert "results_2024.json" in out["artifacts"]
    ratings = out["artifacts"]["team_ratings_2024.json"]
    assert "245" in ratings["teams"]


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
        "week_predictions.json": (FIXTURE_DIR / "week_predictions.json").read_text(
            encoding="utf-8"
        ),
        "track_record.json": (FIXTURE_DIR / "track_record.json").read_text(encoding="utf-8"),
        META_FILENAME: (FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"),
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
    body = (FIXTURE_DIR / "meta.json").read_text(encoding="utf-8")
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


def test_schema_validation_sample_records() -> None:
    _validate(
        json.loads((FIXTURE_DIR / "week_predictions.json").read_text(encoding="utf-8")),
        "week_predictions.schema.json",
    )
    _validate(
        json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8")),
        "meta.schema.json",
    )
    _validate(
        json.loads((FIXTURE_DIR / "track_record.json").read_text(encoding="utf-8")),
        "track_record.schema.json",
    )
    _validate(
        json.loads((FIXTURE_DIR / "results_2024.json").read_text(encoding="utf-8")),
        "results_season.schema.json",
    )
    _validate(
        json.loads((FIXTURE_DIR / "team_ratings_2024.json").read_text(encoding="utf-8")),
        "team_ratings.schema.json",
    )


def test_tier_distribution_report() -> None:
    week = json.loads((FIXTURE_DIR / "week_predictions.json").read_text(encoding="utf-8"))
    dist = tier_distribution(week["games"])
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


def test_export_publish_artifacts_with_injected_schedule(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    teams_dir = staged / "teams" / "season=2026"
    teams_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {"team_id": 1, "school": "Home"},
            {"team_id": 2, "school": "Away"},
        ]
    ).to_parquet(teams_dir / "part.parquet", index=False)
    cfg = AppConfig(
        paths=PathsConfig(staged_dir=str(staged), data_dir=str(tmp_path / "data")),
        webapp=WebappConfig(
            export_enabled=False,
            tier_state_path=str(tmp_path / "tier.json"),
            tier_changes_path=str(tmp_path / "tier_changes.jsonl"),
        ),
    )
    published_at = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    publish = {
        "season": 2026,
        "week": 1,
        "refresh_kind": RefreshKind.TUESDAY_PRIMARY,
        "predictions": [
            {"game_id": "401000001", "mu_margin": 3.0, "sigma_margin": 14.0, "is_stale": False}
        ],
        "prediction_rows": [
            {
                "game_id": "401000001",
                "pred_margin": 3.0,
                "sigma_m": 14.0,
                "sigma_m_is_missing": False,
                "p_ml_home": 0.62,
                "p_ml_home_is_missing": False,
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
    hist = pd.DataFrame(
        [
            {
                "team_id": 1,
                "season": 2026,
                "week": 1,
                "event_time": pd.Timestamp("2026-08-30T19:00:00Z"),
                "off_epa": 0.1,
                "def_epa": -0.05,
                "pace": 70.0,
                "sd_off_epa": 0.02,
                "sd_def_epa": 0.02,
            }
        ]
    )
    out = export_publish_artifacts(
        publish,
        config=cfg,
        published_at=published_at,
        schedule_by_game=schedule,
        filter_history=hist,
        push=False,
    )
    week = json.loads(out["artifacts"]["week_predictions.json"])
    assert week["games"][0]["game_id"] == "401000001"
    assert out["push"] is None
    assert assert_no_denylisted_fields(week) == []
    ratings = json.loads(out["artifacts"]["team_ratings_2026.json"])
    assert "1" in ratings["teams"]


def test_build_results_season_honest_absence_statuses() -> None:
    published_at = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    kickoff = datetime(2026, 9, 5, 16, 0, tzinfo=UTC)
    publish_history = [
        {
            "refresh_kind": "tuesday_primary",
            "published_at": "2026-09-01T10:00:00Z",
            "games": [
                {
                    "game_id": "1",
                    "published_at": "2026-09-01T10:00:00Z",
                    "mu_margin": 3.0,
                    "sigma_margin": 14.0,
                    "margin_interval_lo": -10.0,
                    "margin_interval_hi": 16.0,
                    "margin_interval_nominal": 0.8,
                    "mu_total": 50.0,
                    "total_interval_lo": 30.0,
                    "total_interval_hi": 70.0,
                    "total_interval_nominal": 0.8,
                    "p_win_home": 0.62,
                    "conviction_tier": "lean",
                    "conviction_team": "Home",
                    "conviction_label": "Lean",
                }
            ],
        }
    ]
    schedule = {
        "1": {
            "game_id": "1",
            "week": 1,
            "home_team": "Home",
            "away_team": "Away",
            "kickoff_utc": kickoff,
            "completed": True,
            "home_points": 24,
            "away_points": 17,
        },
        "2": {
            "game_id": "2",
            "week": 1,
            "home_team": "A",
            "away_team": "B",
            "kickoff_utc": kickoff,
            "completed": False,
        },
        "3": {
            "game_id": "3",
            "week": 1,
            "home_team": "C",
            "away_team": "D",
            "kickoff_utc": kickoff,
            "completed": True,
            "home_points": None,
            "away_points": None,
        },
        "4": {
            "game_id": "4",
            "week": 1,
            "home_team": "E",
            "away_team": "F",
            "completed": True,
            "home_points": 10,
            "away_points": 7,
        },
        "5": {
            "game_id": "5",
            "week": 1,
            "home_team": "G",
            "away_team": "H",
            "kickoff_utc": kickoff,
            "completed": True,
            "home_points": 14,
            "away_points": 10,
        },
    }
    out = build_results_season(
        season=2026,
        published_at=published_at,
        completed_games=None,
        schedule_by_game=schedule,
        publish_history=publish_history,
    )
    by_id = {str(g["game_id"]): g["grade_status"] for g in out["games"]}
    assert by_id["1"] == "graded"
    assert by_id["2"] == "game_not_final"
    assert by_id["3"] == "postgame_missing"
    assert by_id["4"] == "postgame_missing"
    assert by_id["5"] == "no_pre_kickoff_publish"


def test_grade_export_with_tmp_staged(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    games_dir = staged / "games" / "season=2026" / "week=1"
    teams_dir = staged / "teams" / "season=2026"
    games_dir.mkdir(parents=True)
    teams_dir.mkdir(parents=True)
    kickoff = pd.Timestamp("2026-09-05T16:00:00Z")
    pd.DataFrame(
        [
            {
                "game_id": 401000001,
                "season": 2026,
                "week": 1,
                "home_team_id": 1,
                "away_team_id": 2,
                "start_date": kickoff,
                "event_time": kickoff,
                "neutral_site": False,
                "conference_game": False,
                "home_points": 21,
                "away_points": 17,
                "completed": True,
            }
        ]
    ).to_parquet(games_dir / "part.parquet", index=False)
    pd.DataFrame(
        [
            {"team_id": 1, "school": "Home"},
            {"team_id": 2, "school": "Away"},
        ]
    ).to_parquet(teams_dir / "part.parquet", index=False)
    empty_week = staged / "games" / "season=2026" / "week=2"
    empty_week.mkdir(parents=True)
    pd.DataFrame(
        {
            "game_id": pd.Series(dtype="int64"),
            "season": pd.Series(dtype="int64"),
            "week": pd.Series(dtype="int64"),
            "home_team_id": pd.Series(dtype="int64"),
            "away_team_id": pd.Series(dtype="int64"),
            "start_date": pd.Series(dtype="datetime64[ns, UTC]"),
            "event_time": pd.Series(dtype="datetime64[ns, UTC]"),
            "neutral_site": pd.Series(dtype="bool"),
            "conference_game": pd.Series(dtype="bool"),
            "home_points": pd.Series(dtype="float64"),
            "away_points": pd.Series(dtype="float64"),
            "completed": pd.Series(dtype="bool"),
        }
    ).to_parquet(empty_week / "part.parquet", index=False)
    cfg = AppConfig(paths=PathsConfig(staged_dir=str(staged), data_dir=str(tmp_path / "data")))
    published_at = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    history = [
        {
            "refresh_kind": "tuesday_primary",
            "published_at": "2026-09-01T10:00:00Z",
            "games": [
                {
                    "game_id": "401000001",
                    "published_at": "2026-09-01T10:00:00Z",
                    "mu_margin": 3.0,
                    "sigma_margin": 14.0,
                    "p_win_home": 0.6,
                }
            ],
        }
    ]
    out = grade_export(
        season=2026,
        published_at=published_at,
        publish_history=history,
        config=cfg,
    )
    assert out["season"] == 2026
    assert out["games"][0]["grade_status"] == "graded"


def test_export_publish_artifacts_stale_stamp(tmp_path: Path) -> None:
    cfg = AppConfig(
        webapp=WebappConfig(
            export_enabled=False,
            tier_state_path=str(tmp_path / "tier.json"),
            tier_changes_path=str(tmp_path / "tier_changes.jsonl"),
        )
    )
    publish = {
        "season": 2026,
        "week": 1,
        "refresh_kind": RefreshKind.TUESDAY_PRIMARY,
        "predictions": [{"game_id": "401000002", "mu_margin": 1.0, "sigma_margin": 14.0}],
        "prediction_rows": [
            {
                "game_id": "401000002",
                "pred_margin": 1.0,
                "sigma_m": 14.0,
                "p_ml_home": 0.55,
            }
        ],
        "stale": {
            "is_stale": True,
            "combined_stamp": "STALE(odds, 7.0h)",
            "sources": [
                {"source": "odds", "age_hours": 7.0, "last_good_at": "2026-09-01T03:00:00Z"}
            ],
        },
    }
    schedule = {
        "401000002": {
            "game_id": "401000002",
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
        published_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        schedule_by_game=schedule,
        push=False,
    )
    week = json.loads(out["artifacts"]["week_predictions.json"])
    assert week["games"][0]["is_stale"] is True
