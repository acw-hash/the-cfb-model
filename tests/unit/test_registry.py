"""Task 22 — MLflow registry, promotion gate, rollback."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from ncaa_quant.registry.manifest import (
    RunManifest,
    build_manifest,
    read_manifest,
    write_manifest,
)
from ncaa_quant.registry.promote import (
    MetricComparisonInput,
    PromotionError,
    evaluate_gate,
    promote,
    rollback,
)
from ncaa_quant.registry.resolve import (
    load_champion_predictions,
    resolve_champion,
)
from ncaa_quant.registry.stages import ModelStage
from ncaa_quant.registry.store import ModelRegistry, NoChampionError
from ncaa_quant.registry.tracking import TrackingSession, log_training_run
from ncaa_quant.utils.seeding import SeedManifest, set_global_seed


def _manifest(**overrides: object) -> RunManifest:
    seeds = set_global_seed(7)
    base = build_manifest(
        config={"seed": 7, "model": "test"},
        seed_manifest=seeds,
        git_sha="abc123",
        dvc_hash="dvcdeadbeef",
        environment_lockfile_hash="lockcafe",
        repo_root=Path.cwd(),
    )
    if not overrides:
        return base
    payload = base.to_dict()
    payload.update(overrides)
    return RunManifest.from_dict(payload)


def _registry(tmp_path: Path, tracking: Path | None = None) -> ModelRegistry:
    uri = tracking.as_uri() if tracking is not None else None
    return ModelRegistry(tmp_path / "registry", tracking_uri=uri)


def _metric_bundle(
    *,
    n: int = 60,
    weeks: int = 10,
    candidate_better: bool,
    noise: float = 0.05,
    seed: int = 0,
) -> list[MetricComparisonInput]:
    """Build paired CRPS / log-loss / CLV series.

    When ``candidate_better``, candidate has clearly lower losses and higher CLV.
    """
    rng = np.random.default_rng(seed)
    blocks = np.repeat(np.arange(weeks), n // weeks)[:n]
    if n != len(blocks):
        blocks = np.resize(blocks, n)

    # Base champion quality
    champ_crps = 8.0 + rng.normal(0, noise, size=n)
    champ_ll = 0.65 + rng.normal(0, noise * 0.1, size=n)
    champ_clv = 0.00 + rng.normal(0, noise * 0.05, size=n)

    if candidate_better:
        cand_crps = champ_crps - 1.5 + rng.normal(0, noise * 0.2, size=n)
        cand_ll = champ_ll - 0.08 + rng.normal(0, noise * 0.02, size=n)
        cand_clv = champ_clv + 0.04 + rng.normal(0, noise * 0.01, size=n)
    else:
        cand_crps = champ_crps + 1.5 + rng.normal(0, noise * 0.2, size=n)
        cand_ll = champ_ll + 0.08 + rng.normal(0, noise * 0.02, size=n)
        cand_clv = champ_clv - 0.04 + rng.normal(0, noise * 0.01, size=n)

    return [
        MetricComparisonInput(
            name="crps",
            champion=champ_crps.tolist(),
            candidate=cand_crps.tolist(),
            blocks=blocks.tolist(),
            direction="lower",
        ),
        MetricComparisonInput(
            name="log_loss",
            champion=champ_ll.tolist(),
            candidate=cand_ll.tolist(),
            blocks=blocks.tolist(),
            direction="lower",
        ),
        MetricComparisonInput(
            name="clv",
            champion=champ_clv.tolist(),
            candidate=cand_clv.tolist(),
            blocks=blocks.tolist(),
            direction="higher",
        ),
    ]


def _register(
    registry: ModelRegistry,
    *,
    predictions: bytes,
    run_id: str = "run-1",
    stage: ModelStage = ModelStage.CANDIDATE,
) -> int:
    rec = registry.register_candidate(
        run_id=run_id,
        manifest=_manifest(),
        predictions=predictions,
        metrics={"crps": 1.0},
        feature_signature={"names": ["x0"], "dtypes": ["float64"]},
        stage=stage,
    )
    return rec.version


# ---------------------------------------------------------------------------
# Manifest / tracking
# ---------------------------------------------------------------------------
def test_manifest_roundtrip(tmp_path: Path) -> None:
    m = _manifest()
    path = write_manifest(tmp_path / "manifest.json", m)
    loaded = read_manifest(path)
    assert loaded.git_sha == m.git_sha
    assert loaded.config_hash == m.config_hash
    assert loaded.seed_manifest["global_seed"] == 7


def test_tracking_session_logs_manifest(tmp_path: Path) -> None:
    tracking = tmp_path / "mlruns"
    manifest = _manifest()
    with TrackingSession(
        tracking_uri=tracking.as_uri(),
        experiment_name="task22-test",
        run_name="unit",
        manifest=manifest,
        tags={"suite": "unit"},
    ) as session:
        session.log_params({"lr": 0.01, "n_estimators": 100})
        session.log_metrics_per_season({2023: {"crps": 7.1, "log_loss": 0.61}})
        session.log_dict_artifact({"ok": True}, "meta.json")
        run_id = session.run_id
    assert run_id
    # Training helper
    rid2 = log_training_run(
        tracking_uri=tracking.as_uri(),
        manifest=manifest,
        params={"head": "margin"},
        metrics_by_season={2022: {"crps": 8.0}},
        experiment_name="task22-test",
    )
    assert rid2 != run_id


# ---------------------------------------------------------------------------
# No champion / resolve
# ---------------------------------------------------------------------------
def test_inference_fails_loudly_without_champion(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    with pytest.raises(NoChampionError, match="no champion"):
        resolve_champion(registry)
    with pytest.raises(NoChampionError):
        load_champion_predictions(registry)


# ---------------------------------------------------------------------------
# Promotion: worse blocked, better succeeds
# ---------------------------------------------------------------------------
def test_promotion_blocked_on_worse_candidate(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    champ_preds = b"GOLDEN-CHAMPION-v1"
    cand_preds = b"WORSE-CANDIDATE"
    v_champ = _register(registry, predictions=champ_preds, run_id="champ")
    # Cold-start promote champion (cal + leakage only).
    metrics_neutral = _metric_bundle(candidate_better=True, seed=1)
    # For cold start we need a first champion — use promote with no incumbent.
    result0 = promote(
        registry,
        v_champ,
        metrics_neutral,
        seasons=[2022, 2023],
        calibration_slope=1.0,
        leakage_gate_passed=True,
        n_boot=499,
        seed=0,
    )
    assert result0.promoted
    assert registry.resolve_champion().version == v_champ

    v_worse = _register(registry, predictions=cand_preds, run_id="worse")
    worse_metrics = _metric_bundle(candidate_better=False, seed=2)
    result = promote(
        registry,
        v_worse,
        worse_metrics,
        seasons=[2022, 2023],
        calibration_slope=1.0,
        leakage_gate_passed=True,
        n_boot=499,
        seed=0,
    )
    assert result.promoted is False
    assert result.archived_version == v_worse
    assert registry.get_version(v_worse).stage_enum is ModelStage.ARCHIVED
    assert registry.resolve_champion().version == v_champ

    artifact = Path(registry.get_version(v_worse).artifact_dir)
    report_html = artifact / "comparison_report.html"
    report_json = artifact / "comparison_report.json"
    assert report_html.is_file()
    assert report_json.is_file()
    html = report_html.read_text(encoding="utf-8")
    assert "BLOCK" in html or "FAIL" in html or "metrics failed" in html.lower()
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert any(not m["beats_incumbent"] for m in payload["metric_results"])


def test_promotion_succeeds_on_better_candidate(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    v_champ = _register(registry, predictions=b"champ-v1", run_id="c1")
    promote(
        registry,
        v_champ,
        _metric_bundle(candidate_better=True, seed=3),
        seasons=[2023],
        calibration_slope=0.98,
        leakage_gate_passed=True,
        n_boot=499,
        seed=1,
    )
    v_better = _register(registry, predictions=b"better-v2", run_id="c2")
    result = promote(
        registry,
        v_better,
        _metric_bundle(candidate_better=True, seed=4),
        seasons=[2022, 2023],
        calibration_slope=1.02,
        leakage_gate_passed=True,
        n_boot=499,
        seed=2,
    )
    assert result.promoted is True
    assert result.report.passed is True
    assert result.report.force_override is False
    assert registry.resolve_champion().version == v_better
    assert registry.get_version(v_champ).stage_enum is ModelStage.ARCHIVED


def test_promotion_blocked_on_calibration_or_leakage(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    v1 = _register(registry, predictions=b"a", run_id="a")
    promote(
        registry,
        v1,
        _metric_bundle(candidate_better=True, seed=5),
        seasons=[2023],
        calibration_slope=1.0,
        leakage_gate_passed=True,
        n_boot=299,
        seed=0,
    )
    v2 = _register(registry, predictions=b"b", run_id="b")
    bad_cal = promote(
        registry,
        v2,
        _metric_bundle(candidate_better=True, seed=6),
        seasons=[2023],
        calibration_slope=1.5,  # outside [0.85, 1.15]
        leakage_gate_passed=True,
        n_boot=299,
        seed=0,
    )
    assert bad_cal.promoted is False
    assert bad_cal.report.calibration_passed is False

    v3 = _register(registry, predictions=b"c", run_id="c")
    bad_leak = promote(
        registry,
        v3,
        _metric_bundle(candidate_better=True, seed=7),
        seasons=[2023],
        calibration_slope=1.0,
        leakage_gate_passed=False,
        n_boot=299,
        seed=0,
    )
    assert bad_leak.promoted is False
    assert bad_leak.report.leakage_passed is False


# ---------------------------------------------------------------------------
# --force override
# ---------------------------------------------------------------------------
def test_force_writes_override_record(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    v1 = _register(registry, predictions=b"champ", run_id="c1")
    promote(
        registry,
        v1,
        _metric_bundle(candidate_better=True, seed=8),
        seasons=[2023],
        calibration_slope=1.0,
        leakage_gate_passed=True,
        n_boot=299,
        seed=0,
    )
    v_worse = _register(registry, predictions=b"forced-worse", run_id="w")
    with pytest.raises(PromotionError, match="force_reason"):
        promote(
            registry,
            v_worse,
            _metric_bundle(candidate_better=False, seed=9),
            seasons=[2023],
            calibration_slope=1.0,
            leakage_gate_passed=True,
            force=True,
            force_reason="",
            force_actor="alice",
            n_boot=299,
        )

    result = promote(
        registry,
        v_worse,
        _metric_bundle(candidate_better=False, seed=9),
        seasons=[2023],
        calibration_slope=1.0,
        leakage_gate_passed=True,
        force=True,
        force_reason="operator judgment after injury news",
        force_actor="alice",
        n_boot=299,
        seed=0,
    )
    assert result.promoted is True
    assert result.report.force_override is True
    assert result.report.override_record is not None
    assert result.report.override_record["actor"] == "alice"
    overrides = list((tmp_path / "registry" / "overrides").glob("override_*.json"))
    assert len(overrides) == 1
    disk = json.loads(overrides[0].read_text(encoding="utf-8"))
    assert disk["reason"] == "operator judgment after injury news"
    assert registry.resolve_champion().version == v_worse


# ---------------------------------------------------------------------------
# Rollback restores golden predictions byte-identically
# ---------------------------------------------------------------------------
def test_rollback_restores_golden_predictions_byte_identically(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    golden = b"\x00GOLDEN-PREDICTIONS-v1\xff\x01\x02"
    v1 = _register(registry, predictions=golden, run_id="v1")
    promote(
        registry,
        v1,
        _metric_bundle(candidate_better=True, seed=10),
        seasons=[2023],
        calibration_slope=1.0,
        leakage_gate_passed=True,
        n_boot=299,
        seed=0,
    )
    assert load_champion_predictions(registry) == golden

    v2_preds = b"NEWER-CHAMPION-BYTES"
    v2 = _register(registry, predictions=v2_preds, run_id="v2")
    promote(
        registry,
        v2,
        _metric_bundle(candidate_better=True, seed=11),
        seasons=[2023],
        calibration_slope=1.0,
        leakage_gate_passed=True,
        n_boot=299,
        seed=1,
    )
    assert load_champion_predictions(registry) == v2_preds

    rolled = rollback(registry, target_version=v1)
    assert rolled.version == v1
    assert registry.resolve_champion().version == v1
    assert load_champion_predictions(registry) == golden
    # Byte-identical to the original artifact on disk
    assert registry.load_predictions(v1) == golden


def test_rollback_default_previous(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    versions = []
    for i in range(3):
        v = _register(registry, predictions=f"p{i}".encode(), run_id=f"r{i}")
        promote(
            registry,
            v,
            _metric_bundle(candidate_better=True, seed=20 + i),
            seasons=[2023],
            calibration_slope=1.0,
            leakage_gate_passed=True,
            n_boot=199,
            seed=i,
        )
        versions.append(v)
    assert registry.resolve_champion().version == versions[-1]
    rollback(registry)  # previous
    assert registry.resolve_champion().version == versions[-2]


# ---------------------------------------------------------------------------
# Gate evaluation unit
# ---------------------------------------------------------------------------
def test_evaluate_gate_requires_all_three_metrics() -> None:
    with pytest.raises(PromotionError, match="missing required metrics"):
        evaluate_gate(
            [
                MetricComparisonInput(
                    name="crps",
                    champion=[1.0],
                    candidate=[0.5],
                    blocks=[1],
                    direction="lower",
                )
            ],
            seasons=[2023],
            calibration_slope=1.0,
            leakage_gate_passed=True,
            candidate_version=2,
            champion_version=1,
            n_boot=50,
        )


def test_seed_manifest_type_accepted() -> None:
    seeds = SeedManifest(
        global_seed=1,
        python_hash_seed="1",
        lightgbm_seed=1,
        xgboost_seed=1,
        numpy_seed=1,
    )
    m = build_manifest(
        config={"a": 1},
        seed_manifest=seeds,
        git_sha="x",
        dvc_hash="y",
        environment_lockfile_hash="z",
    )
    assert m.seed_manifest["global_seed"] == 1


def test_cli_resolve_and_rollback(tmp_path: Path) -> None:
    """Smoke-test ``python -m ncaa_quant.registry`` resolve + rollback commands."""
    from typer.testing import CliRunner

    from ncaa_quant.registry.cli import app

    registry = _registry(tmp_path)
    v1 = _register(registry, predictions=b"cli-golden", run_id="cli1")
    promote(
        registry,
        v1,
        _metric_bundle(candidate_better=True, seed=30),
        seasons=[2023],
        calibration_slope=1.0,
        leakage_gate_passed=True,
        n_boot=199,
        seed=0,
    )
    v2 = _register(registry, predictions=b"cli-v2", run_id="cli2")
    promote(
        registry,
        v2,
        _metric_bundle(candidate_better=True, seed=31),
        seasons=[2023],
        calibration_slope=1.0,
        leakage_gate_passed=True,
        n_boot=199,
        seed=1,
    )

    runner = CliRunner()
    root = str(tmp_path / "registry")
    resolved = runner.invoke(app, ["resolve-champion", "--root", root])
    assert resolved.exit_code == 0
    assert f"champion=v{v2}" in resolved.stdout

    rolled = runner.invoke(app, ["rollback", "--root", root, "--to", str(v1)])
    assert rolled.exit_code == 0
    assert f"champion=v{v1}" in rolled.stdout
    assert load_champion_predictions(registry) == b"cli-golden"

    empty = _registry(tmp_path / "empty")
    missing = runner.invoke(app, ["resolve-champion", "--root", str(tmp_path / "empty")])
    assert missing.exit_code == 2
    assert "no champion" in missing.stdout.lower()
    del empty


def test_illegal_stage_transition() -> None:
    from ncaa_quant.registry.stages import assert_transition_allowed

    with pytest.raises(ValueError, match="illegal stage transition"):
        assert_transition_allowed(ModelStage.ARCHIVED, ModelStage.CHAMPION)
