"""W9-D Amendment 1: artifact vintage matches the producing run."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from ncaa_quant.config import AppConfig, PathsConfig, WebappConfig
from ncaa_quant.pipelines.predict import RefreshKind
from ncaa_quant.webapp.export import (
    IncoherentMarginIntervalError,
    UnknownRunProvenanceError,
    assert_no_incoherent_margin_interval,
    build_game_prediction,
    export_publish_artifacts,
    margin_quantile_heads_coherent,
    vintage_label_for_run,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_WEEK = REPO_ROOT / "webapp" / "fixtures" / "week_predictions.json"
FIXTURE_META = REPO_ROOT / "webapp" / "fixtures" / "meta.json"
SANDBOX_WEEK = (
    REPO_ROOT
    / "docs"
    / "notes"
    / "_artifacts"
    / "webapp-w9d"
    / "sandbox_roundtrip"
    / "week_predictions.json"
)
SANDBOX_META = (
    REPO_ROOT / "docs" / "notes" / "_artifacts" / "webapp-w9d" / "sandbox_roundtrip" / "meta.json"
)

V3_RUN_ID = "task23_fundamental_reduced_v3"
V2_RUN_ID = "task23_fundamental_reduced_v2"
V3_VINTAGE = "W9A_REVAL"
V2_VINTAGE = "REGRADED_V2"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_vintage_matches_run(artifact: dict[str, object], *, meta: dict[str, object]) -> None:
    identity = artifact["model_identity"]
    assert isinstance(identity, dict)
    run_id = str(identity["run_id"])
    expected = vintage_label_for_run(run_id)
    assert artifact["vintage_label"] == expected
    assert meta["vintage_label"] == expected
    games = artifact["games"]
    assert isinstance(games, list)
    assert games
    for game in games:
        assert isinstance(game, dict)
        assert game["vintage_label"] == expected
    if run_id == V3_RUN_ID:
        assert expected == V3_VINTAGE
        assert expected != V2_VINTAGE


def test_vintage_label_for_run_maps_known_walkforwards() -> None:
    assert vintage_label_for_run(V3_RUN_ID) == V3_VINTAGE
    assert vintage_label_for_run(V2_RUN_ID) == V2_VINTAGE
    with pytest.raises(UnknownRunProvenanceError, match="future_run"):
        vintage_label_for_run("task23_fundamental_reduced_future_run")


def test_export_stamps_vintage_from_producing_run(tmp_path: Path) -> None:
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
    published_at = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)

    def _export(run_id: str, model_version: str) -> tuple[dict[str, object], dict[str, object]]:
        publish = {
            "season": 2026,
            "week": 1,
            "refresh_kind": RefreshKind.TUESDAY_PRIMARY,
            "predictions": [
                {
                    "game_id": "401000001",
                    "mu_margin": 3.0,
                    "sigma_margin": 14.0,
                    "is_stale": False,
                }
            ],
            "prediction_rows": [
                {
                    "game_id": "401000001",
                    "pred_margin": 3.0,
                    "sigma_m": 14.0,
                    "sigma_m_is_missing": False,
                    "p_ml_home": 0.62,
                    "p_ml_home_is_missing": False,
                    "run_id": run_id,
                    "model_version": model_version,
                    "champion_version": 2,
                }
            ],
            "stale": {"is_stale": False, "combined_stamp": None, "sources": []},
        }
        out = export_publish_artifacts(
            publish,
            config=cfg,
            published_at=published_at,
            schedule_by_game=schedule,
            push=False,
        )
        week = json.loads(out["artifacts"]["week_predictions.json"])
        meta = json.loads(out["artifacts"]["meta.json"])
        return week, meta

    week_v3, meta_v3 = _export(V3_RUN_ID, "production-v0_reduced_v3")
    assert week_v3["vintage_label"] == V3_VINTAGE
    assert week_v3["games"][0]["vintage_label"] == V3_VINTAGE
    assert meta_v3["vintage_label"] == V3_VINTAGE
    assert week_v3["model_identity"]["run_id"] == V3_RUN_ID
    assert week_v3["model_identity"]["model_version"] == "production-v0_reduced_v3"
    assert week_v3["model_identity"]["champion_version"] == 2
    assert meta_v3["champion_model"]["model_version"] == "production-v0_reduced_v3"
    assert V2_VINTAGE not in json.dumps(week_v3)

    week_v2, meta_v2 = _export(V2_RUN_ID, "production-v0_reduced_v2")
    assert week_v2["vintage_label"] == V2_VINTAGE
    assert meta_v2["vintage_label"] == V2_VINTAGE

    with pytest.raises(UnknownRunProvenanceError, match="future_run"):
        _export("task23_fundamental_reduced_future_run", "production-v0_reduced_future")


def test_2024_fixture_vintage_matches_producing_run() -> None:
    week = _load(FIXTURE_WEEK)
    meta = _load(FIXTURE_META)
    _assert_vintage_matches_run(week, meta=meta)
    assert week["model_identity"]["run_id"] == V3_RUN_ID
    assert len(week["games"]) == 56  # type: ignore[arg-type]


def test_2026_sandbox_vintage_matches_producing_run() -> None:
    if not SANDBOX_WEEK.is_file() or not SANDBOX_META.is_file():
        pytest.skip("W9-D 2026 sandbox artifact not on disk")
    week = _load(SANDBOX_WEEK)
    meta = _load(SANDBOX_META)
    _assert_vintage_matches_run(week, meta=meta)
    assert week["model_identity"]["run_id"] == V3_RUN_ID
    assert week["model_identity"]["model_version"] == "production-v0_reduced_v3"
    assert len(week["games"]) == 91  # type: ignore[arg-type]
    assert week.get("fixture") is not True


# Champion CQR 80% add (W9-D Amendment 1). Used only to reconstruct q10/q90
# from already-published lo/hi on artifacts that do not carry quantile columns.
CQR_THR_80 = 6.8371215750064245
V3_PREDICTIONS = (
    REPO_ROOT
    / "data"
    / "backtests"
    / "task23_fundamental_reduced_v3"
    / "full"
    / "predictions.parquet"
)
AMENDMENT1_INTERVAL = (
    REPO_ROOT / "docs" / "notes" / "_artifacts" / "webapp-w9d" / "amendment1_interval.json"
)


def _schedule() -> dict[str, object]:
    return {
        "game_id": "401000001",
        "home_team": "Home",
        "away_team": "Away",
        "home_team_id": 1,
        "away_team_id": 2,
        "kickoff_utc": "2026-09-05T16:00:00Z",
        "neutral_site": False,
        "conference_game": False,
    }


def _build_game(row: dict[str, object]) -> dict[str, object]:
    return build_game_prediction(
        row,
        _schedule(),
        season=2026,
        week=1,
        published_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        vintage_label=V3_VINTAGE,
        ensemble_scope_label="REDUCED_PER_ADR_0013",
        feature_time_label="FEATURE_TIME=TUESDAY_DECISION",
        previous_tier=None,
        tier_primary=None,
    )


def _reconstruct_heads(lo: float, hi: float) -> tuple[float, float]:
    q10 = lo + CQR_THR_80
    q90 = hi - CQR_THR_80
    if q10 <= q90:
        return q10, q90
    return q90, q10


def test_coherence_gate_nulls_incoherent_margin_interval() -> None:
    game = _build_game(
        {
            "game_id": "401000001",
            "pred_margin": 47.6,
            "sigma_m": 20.0,
            "sigma_m_is_missing": False,
            "pred_margin_q10": -9.8,
            "pred_margin_q90": 39.3,
            "cqr_lo": -16.6,
            "cqr_hi": 46.1,
            "cqr_nominal": 0.8,
            "p_ml_home": 0.99,
            "p_ml_home_is_missing": False,
            "null_reason": None,
        }
    )
    assert game["mu_margin"] == pytest.approx(47.6)
    assert game["margin_interval_lo"] is None
    assert game["margin_interval_hi"] is None
    assert game["margin_interval_nominal"] is None


def test_coherence_gate_does_not_suppress_skewed_but_coherent_row() -> None:
    game = _build_game(
        {
            "game_id": "401000001",
            "pred_margin": 43.0,
            "sigma_m": 18.0,
            "sigma_m_is_missing": False,
            "pred_margin_q10": -20.0,
            "pred_margin_q90": 48.0,
            "cqr_lo": -26.837,
            "cqr_hi": 54.837,
            "cqr_nominal": 0.8,
            "p_ml_home": 0.97,
            "p_ml_home_is_missing": False,
            "null_reason": None,
        }
    )
    assert game["margin_interval_lo"] == pytest.approx(-26.837)
    assert game["margin_interval_hi"] == pytest.approx(54.837)
    assert game["margin_interval_nominal"] == pytest.approx(0.8)


def test_incoherent_band_assertion_bite() -> None:
    with pytest.raises(IncoherentMarginIntervalError, match="q10 < mu < q90"):
        assert_no_incoherent_margin_interval(
            mu=47.6,
            q10=-9.8,
            q90=39.3,
            lo=-16.6,
            hi=46.1,
        )


def test_2024_fixture_margin_intervals_are_coherent() -> None:
    week = _load(FIXTURE_WEEK)
    games = week["games"]
    assert isinstance(games, list)
    suppressed = 0
    for game in games:
        assert isinstance(game, dict)
        mu = float(game["mu_margin"])  # type: ignore[arg-type]
        lo = game["margin_interval_lo"]
        hi = game["margin_interval_hi"]
        assert lo is not None and hi is not None
        q10, q90 = _reconstruct_heads(float(lo), float(hi))
        if not margin_quantile_heads_coherent(mu, q10, q90):
            suppressed += 1
    assert suppressed == 0
    assert len(games) == 56


def test_2026_week1_suppression_count_is_nineteen() -> None:
    if not AMENDMENT1_INTERVAL.is_file():
        pytest.skip("Amendment 1 interval diagnostic not on disk")
    report = _load(AMENDMENT1_INTERVAL)
    slates = report["slates"]
    assert isinstance(slates, dict)
    week1 = slates["2026_w1"]
    assert isinstance(week1, dict)
    assert week1["n"] == 91
    assert week1["n_q90_below_mu"] == 19
    assert week1["n_q10_above_mu"] == 0
    if SANDBOX_WEEK.is_file():
        games = _load(SANDBOX_WEEK)["games"]
        assert isinstance(games, list)
        n_null = 0
        n_kept_incoherent = 0
        for game in games:
            assert isinstance(game, dict)
            lo = game["margin_interval_lo"]
            hi = game["margin_interval_hi"]
            if lo is None or hi is None:
                n_null += 1
                continue
            mu = float(game["mu_margin"])  # type: ignore[arg-type]
            q10, q90 = _reconstruct_heads(float(lo), float(hi))
            if not margin_quantile_heads_coherent(mu, q10, q90):
                n_kept_incoherent += 1
        assert n_kept_incoherent == 0
        assert n_null == 19


def test_v3_backtest_coherence_gate_is_a_no_op() -> None:
    if not V3_PREDICTIONS.is_file():
        pytest.skip("v3 predictions parquet not on disk")
    frame = pd.read_parquet(V3_PREDICTIONS)
    mu = pd.to_numeric(frame["pred_margin"], errors="coerce")
    q10 = pd.to_numeric(frame["pred_margin_q10"], errors="coerce")
    q90 = pd.to_numeric(frame["pred_margin_q90"], errors="coerce")
    realized = pd.to_numeric(frame["realized_margin"], errors="coerce")
    eligible = mu.notna() & q10.notna() & q90.notna() & realized.notna()
    sub = frame.loc[eligible]
    assert len(sub) == 4743
    lo = sub[["pred_margin_q10", "pred_margin_q90"]].min(axis=1)
    hi = sub[["pred_margin_q10", "pred_margin_q90"]].max(axis=1)
    incoherent = ~((lo < sub["pred_margin"]) & (sub["pred_margin"] < hi))
    assert int(incoherent.sum()) == 0
