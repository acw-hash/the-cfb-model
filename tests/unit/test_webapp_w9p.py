"""W9-P: wire predict_fn to stored production predictor output; 2024 w5 oracle."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ncaa_quant.config import AppConfig, WebappConfig
from ncaa_quant.pipelines.notifications import RecordingNotifier
from ncaa_quant.pipelines.predict import (
    LOCKBOX_SEASON,
    LockboxSeasonError,
    RefreshKind,
    execute_predict_publish,
    load_production_prediction_rows,
    oracle_predict_fn,
    production_week_predictions_path,
    run_isolated_week_export,
)
from ncaa_quant.webapp.export import (
    PUBLISHED_GAME_PREDICTION_KEYS,
    SCHEMA_VERSION,
    WITHDRAWN_FIELDS,
    PublishedKeyAllowlistError,
    assert_game_prediction_allowlist,
    assert_no_denylisted_fields,
    build_game_prediction,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "webapp" / "fixtures" / "week_predictions.json"
GAME_ID_RE = re.compile(r"^[0-9]{6,12}$")

REAL_STATE_FILES: tuple[Path, ...] = (
    REPO_ROOT / "data" / "webapp" / "tier_state.json",
    REPO_ROOT / "data" / "webapp" / "tier_changes.jsonl",
    REPO_ROOT / "data" / "pipeline_state" / "idempotency.json",
    REPO_ROOT / "data" / "artifacts" / "state_space" / "filter_history.parquet",
    REPO_ROOT / "data" / "artifacts" / "expected_possessions" / "live.json",
    REPO_ROOT
    / "data"
    / "backtests"
    / "task23_fundamental_reduced_v2"
    / "full"
    / "weeks"
    / "season=2024_week=5.parquet",
)

COMPARE_FIELDS: tuple[str, ...] = (
    "mu_margin",
    "sigma_margin",
    "margin_interval_lo",
    "margin_interval_hi",
    "mu_total",
    "sigma_total",
    "p_win_home",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _optional_abs(a: Any, b: Any) -> float | None:
    if a is None and b is None:
        return 0.0
    if a is None or b is None:
        return None
    try:
        fa = float(a)
        fb = float(b)
    except (TypeError, ValueError):
        return None
    if math.isnan(fa) and math.isnan(fb):
        return 0.0
    if math.isnan(fa) or math.isnan(fb):
        return None
    return abs(fa - fb)


def test_lockbox_season_2025_refused() -> None:
    with pytest.raises(LockboxSeasonError, match="lockbox"):
        load_production_prediction_rows(LOCKBOX_SEASON, 1)


def test_wired_rows_have_production_and_stamp_aliases() -> None:
    rows = load_production_prediction_rows(2024, 5)
    assert len(rows) == 56
    row = rows[0]
    assert "pred_margin" in row
    assert "sigma_m" in row
    assert "p_ml_home" in row
    assert "mu_margin" in row
    assert "sigma_margin" in row
    assert math.isfinite(float(row["mu_margin"]))
    assert str(row["game_id"]) == str(int(row["game_id"]))
    seasons = {int(r["season"]) for r in rows}
    assert seasons == {2024}


def test_sigma_refused_aliases_preserve_null_probabilities() -> None:
    row = {
        "game_id": "401628373",
        "pred_margin": 4.2,
        "mu_margin": 4.2,
        "sigma_m": None,
        "sigma_margin": float("nan"),
        "sigma_m_is_missing": True,
        "p_ml_home": None,
        "p_ml_home_is_missing": True,
        "null_reason": "cold_start_insufficient",
    }
    game = build_game_prediction(
        row,
        {
            "game_id": "401628373",
            "home_team": "Texas A&M",
            "away_team": "Arkansas",
            "home_team_id": 245,
            "away_team_id": 8,
            "kickoff_utc": "2024-09-28T16:00:00Z",
            "neutral_site": False,
            "conference_game": True,
        },
        season=2024,
        week=5,
        published_at=datetime(2024, 9, 24, 6, 0, 0, tzinfo=UTC),
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        vintage_label="REGRADED_V2",
        ensemble_scope_label="REDUCED_PER_ADR_0013",
        feature_time_label="FEATURE_TIME=TUESDAY_DECISION",
        previous_tier=None,
        tier_primary=None,
    )
    assert game["sigma_margin"] is None
    assert game["p_win_home"] is None
    assert game["sigma_margin_credible"] is False
    assert game["conviction_tier"] is None
    assert game["p_win_home"] != 0


def test_allowlist_bite_on_wired_game() -> None:
    rows = load_production_prediction_rows(2024, 5)
    game = build_game_prediction(
        rows[0],
        {
            "game_id": str(rows[0]["game_id"]),
            "home_team": "Home",
            "away_team": "Away",
            "home_team_id": 1,
            "away_team_id": 2,
            "kickoff_utc": "2024-09-28T16:00:00Z",
            "neutral_site": False,
            "conference_game": False,
        },
        season=2024,
        week=5,
        published_at=datetime(2024, 9, 24, 6, 0, 0, tzinfo=UTC),
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        vintage_label="REGRADED_V2",
        ensemble_scope_label="REDUCED_PER_ADR_0013",
        feature_time_label="FEATURE_TIME=TUESDAY_DECISION",
        previous_tier=None,
        tier_primary=None,
    )
    assert_game_prediction_allowlist(game)
    poisoned = dict(game)
    poisoned["unsanctioned_edge"] = 0.03
    with pytest.raises(PublishedKeyAllowlistError, match="unsanctioned_edge"):
        assert_game_prediction_allowlist(poisoned)
    assert_game_prediction_allowlist(game)


def test_isolated_2024w5_oracle_against_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = {str(p): _sha256(p) for p in REAL_STATE_FILES if p.is_file()}
    assert len(before) == len(REAL_STATE_FILES)

    output_dir = tmp_path / "artifacts"
    state_dir = tmp_path / "isolated_state"
    cfg = AppConfig(
        webapp=WebappConfig(
            export_enabled=False,
            r2_bucket="",
            r2_endpoint_url="",
            revalidate_url="",
        )
    )
    notifier = RecordingNotifier()
    out = run_isolated_week_export(
        season=2024,
        week=5,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        output_dir=output_dir,
        state_dir=state_dir,
        config=cfg,
        notifier=notifier,
        predict_fn=oracle_predict_fn(2024, 5),
    )
    captured = capsys.readouterr()
    log_text = captured.out + captured.err
    print(log_text)

    after = {str(p): _sha256(p) for p in REAL_STATE_FILES if p.is_file()}
    print("W9-P real-state SHA-256 before/after")
    for path, digest in before.items():
        print(f"  before {digest}  {path}")
        print(f"  after  {after[path]}  {path}")
        assert after[path] == digest

    assert out["export_enabled"] is False
    assert "export_enabled=False" in log_text
    assert "PutObject" not in log_text
    assert "r2.cloudflarestorage.com" not in log_text
    assert "amazonaws.com" not in log_text
    assert out["export"]["push"] is None

    week_path = output_dir / "week_predictions.json"
    produced = json.loads(week_path.read_text(encoding="utf-8"))
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert produced["schema_version"] == SCHEMA_VERSION == "1.2.0"
    assert produced["season"] == 2024
    assert produced["week"] == 5
    assert produced["refresh_kind"] == "tuesday_primary"
    assert "fixture" not in produced

    games = produced["games"]
    fx_games = fixture["games"]
    assert len(games) == 56
    assert len(fx_games) == 56
    produced_ids = {str(g["game_id"]) for g in games}
    fixture_ids = {str(g["game_id"]) for g in fx_games}
    # Same 2024 week-5 CFBD slate; μ/identity now differ (fixture is v2 registry
    # champion / honest Tuesday; W9-P still reads champion-3 parquet).
    assert produced_ids == fixture_ids
    for gid in produced_ids:
        assert GAME_ID_RE.match(gid)

    for game in games:
        assert_game_prediction_allowlist(game)
        for key in WITHDRAWN_FIELDS:
            assert key not in game
        extra = set(game.keys()) - PUBLISHED_GAME_PREDICTION_KEYS
        assert not extra
        if game["sigma_margin_credible"] is False:
            assert game["p_win_home"] is None
            assert game["conviction_tier"] is None

    denylist_hits = assert_no_denylisted_fields(produced)
    assert denylist_hits == []

    print("W9-P jq keys (first game):", sorted(games[0].keys()))

    pr_by_id = {str(g["game_id"]): g for g in games}
    parquet_rows = {
        str(int(row["game_id"])): row for row in load_production_prediction_rows(2024, 5)
    }
    parquet_field = {
        "mu_margin": "mu_margin",
        "sigma_margin": "sigma_margin",
        "margin_interval_lo": "cqr_lo",
        "margin_interval_hi": "cqr_hi",
        "mu_total": "pred_total",
        "sigma_total": "sigma_t",
        "p_win_home": "p_ml_home",
    }

    print("W9-P comparison table vs champion-3 parquet (max |delta| over 56 games)")
    print(f"{'field':<24} {'max_abs_delta'}")
    max_deltas: dict[str, float | None] = {}
    for field in COMPARE_FIELDS:
        src = parquet_field[field]
        deltas = [
            _optional_abs(pr_by_id[gid].get(field), parquet_rows[gid].get(src))
            for gid in produced_ids
        ]
        if any(d is None for d in deltas):
            max_deltas[field] = None
            print(f"{field:<24} None (null mismatch)")
        else:
            peak = max(deltas)  # type: ignore[arg-type]
            max_deltas[field] = float(peak)
            print(f"{field:<24} {peak}")

    print("W9-P fixture model_identity", json.dumps(fixture["model_identity"], sort_keys=True))
    print("W9-P produced model_identity", json.dumps(produced["model_identity"], sort_keys=True))

    # Numeric oracle: W9-P still reads the champion-3 stored frame, empty hysteresis.
    for field, peak in max_deltas.items():
        assert peak is not None, field
        assert peak < 1e-12, (field, peak)
    assert produced["model_identity"]["champion_version"] == 3
    assert produced["model_identity"]["run_id"] == "task23_fundamental_reduced_v2"
    assert produced["model_identity"]["model_version"] == "production-v0_reduced_v2"
    assert fixture["model_identity"]["champion_version"] == 2
    assert fixture["model_identity"]["run_id"] == "task23_fundamental_reduced_v3"

    assert "results_2024.json" not in out["written"]
    assert "results_2025.json" not in out["written"]
    used_parquet = str(production_week_predictions_path(2024, 5))
    assert "2024_week=5" in used_parquet
    assert "2025" not in used_parquet
    assert "filter_history" not in log_text
    assert "grade_export" not in log_text
    assert not (state_dir / "pipeline_state" / "idempotency.json").is_file()

    # Oracle parquet entry point produced production columns.
    raw = out["result"]["prediction_rows"]
    assert len(raw) == 56
    assert "pred_margin" in raw[0]
    assert "p_ml_home" in raw[0]


def test_execute_predict_publish_uses_wired_default(tmp_path: Path) -> None:
    cfg = AppConfig(
        webapp=WebappConfig(
            export_enabled=False,
            tier_state_path=str(tmp_path / "tier_state.json"),
            tier_changes_path=str(tmp_path / "tier_changes.jsonl"),
        )
    )
    result = execute_predict_publish(
        season=2024,
        week=5,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        config=cfg,
        notifier=RecordingNotifier(),
        predict_fn=oracle_predict_fn(2024, 5),
    )
    assert len(result["prediction_rows"]) == 56
    assert result.get("webapp_export") is None
    assert Counter(int(r["season"]) for r in result["prediction_rows"]) == {2024: 56}
