"""W9-A: full current-code walk-forward, serialize champion, week-5 causal check.

Writes the fundamental run under ``data/backtests/task23_fundamental_reduced_v3/``
and registers a new champion. Never writes into
``data/backtests/task23_fundamental_reduced_v2/``. Does not call R2, Prefect,
or live webapp export.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.evaluation.backtest_runner import (
    load_backtest_config,
    load_staged_games,
    run_backtest,
    walkforward_config_from_mapping,
)
from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON
from ncaa_quant.evaluation.production_stack import (
    MAX_CREDIBLE_MARGIN_PRED,
    MEMBER_DEGENERACY_SD_EPS,
    ProductionFeatureProvider,
    ProductionStack,
)
from ncaa_quant.evaluation.walkforward import assert_prediction_quality_gate
from ncaa_quant.models.heads.elasticnet import NULL_SHARE_DROP_THRESHOLD
from ncaa_quant.pipelines.gates import evaluate_promotion_gate
from ncaa_quant.registry.bundle import (
    ENSEMBLE_FILENAME,
    FEATURES_FILENAME,
    FIT_PROCESS_FILENAME,
    INVENTORY_FILENAME,
    POSSESSIONS_FILENAME,
    PROMOTION_FILENAME,
    RATING_SNAPSHOT_FILENAME,
    WEEK_PREDICTIONS_FILENAME,
    save_production_ensemble,
)
from ncaa_quant.registry.champion_serialize import (
    _CaptureFeatureProvider,
    _load_stack_inputs,
    _team_ids_from_snapshot,
    hash_isolation_paths,
    isolation_state_paths,
    sha256_file,
)
from ncaa_quant.registry.manifest import build_manifest, resolve_git_dirty, resolve_git_sha
from ncaa_quant.registry.stages import ModelStage
from ncaa_quant.registry.store import ModelRegistry
from ncaa_quant.utils.logging import configure_logging, get_logger

log = get_logger(__name__)

REPO_ROOT: Path = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY_ROOT: Path = REPO_ROOT / "data" / "registry"
W9M_REGISTRY_BACKUP: Path = REPO_ROOT / "data" / "registry_w9m_truncated"
CHAMPION3_ROOT: Path = REPO_ROOT / "data" / "backtests" / "task23_fundamental_reduced_v2"
FORBIDDEN_RUN_ID: str = "task23_fundamental_reduced_v2"
FUND_CONFIG_NAME: str = "task23_fundamental_full_reduced_v3"
A2_CONFIG_NAME: str = "task23_A2_rating_updates_frozen_reduced_v2"
FUND_RUN_ID: str = "task23_fundamental_reduced_v3"
FUND_MODEL_VERSION: str = "production-v0_reduced_v3"
ORACLE_SEASON: int = 2024
ORACLE_WEEK: int = 5
FUND_LABEL: str = (
    "W9A-PATH-A;FEATURE_TIME=TUESDAY_DECISION;ensemble_scope=REDUCED_PER_ADR_0013;ADR_0014"
)
A2_LABEL: str = (
    "W9A-PATH-A;A2;FEATURE_TIME=TUESDAY_DECISION;ensemble_scope=REDUCED_PER_ADR_0013;ADR_0014"
)
W9M_WEEK5: Path = REPO_ROOT / "data" / "registry" / "artifacts" / "v1" / WEEK_PREDICTIONS_FILENAME
ARTIFACT_DIR: Path = REPO_ROOT / "docs" / "notes" / "_artifacts" / "webapp-w9a"
ADR_0014_OOD_BLOCKS: frozenset[tuple[int, int]] = frozenset({(2019, 2), (2019, 3), (2019, 4)})
WEEK5_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mu_margin", ("mu_margin", "pred_margin")),
    ("sigma_margin", ("sigma_margin", "sigma_m")),
    ("p_ml_home", ("p_ml_home",)),
)


class W9AStop(RuntimeError):
    """Hard stop required by the W9-A task contract."""


def hash_tree(root: Path) -> str:
    """Stable SHA-256 over ``relpath + file digest`` for every file under ``root``."""
    if not root.exists():
        return "ABSENT"
    files = sorted(p for p in root.rglob("*") if p.is_file())
    hasher = hashlib.sha256()
    for path in files:
        rel = path.relative_to(root).as_posix().encode("utf-8")
        hasher.update(rel)
        hasher.update(b"\0")
        hasher.update(hashlib.sha256(path.read_bytes()).digest())
    return hasher.hexdigest()


def yaml_season_lists(payload: Mapping[str, Any]) -> dict[str, list[int]]:
    """Return walk-forward season lists from a backtest YAML mapping."""
    wf = payload.get("walkforward", payload)
    if not isinstance(wf, Mapping):
        raise W9AStop("walkforward block missing")
    out: dict[str, list[int]] = {}
    for key in ("test_seasons", "continuity_seasons", "warmup_seasons"):
        raw = wf.get(key) or []
        out[key] = [int(s) for s in raw]
    return out


def assert_lockbox_absent_from_yaml(payload: Mapping[str, Any], *, name: str) -> None:
    """Refuse any YAML that lists the lockbox season in any replay role."""
    seasons = yaml_season_lists(payload)
    for key, vals in seasons.items():
        if LOCKBOX_SEASON in vals:
            raise W9AStop(f"{name} {key} contains lockbox {LOCKBOX_SEASON}: {vals}")


def _series_by_alias(frame: pd.DataFrame, aliases: tuple[str, ...], *, field: str) -> pd.Series:
    for name in aliases:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce")
    raise W9AStop(f"week-5 frame missing {field} (tried {aliases})")


def week5_crosscheck(new: pd.DataFrame, old: pd.DataFrame) -> dict[str, Any]:
    """Compare 2024 week-5 μ/σ/p_ml. Any nonzero max |Δ| is a STOP."""
    left = new.copy()
    right = old.copy()
    left["game_id"] = pd.to_numeric(left["game_id"], errors="coerce").astype("int64")
    right["game_id"] = pd.to_numeric(right["game_id"], errors="coerce").astype("int64")
    merged = left.merge(right, on="game_id", suffixes=("_new", "_old"), how="outer")
    n = int(len(merged))
    ids_new = set(left["game_id"].tolist())
    ids_old = set(right["game_id"].tolist())
    report: dict[str, Any] = {
        "n": n,
        "n_new": int(len(left)),
        "n_old": int(len(right)),
        "game_id_sets_identical": ids_new == ids_old,
        "fields": {},
        "all_zero": True,
    }
    if ids_new != ids_old:
        report["all_zero"] = False
        report["only_new"] = sorted(ids_new - ids_old)
        report["only_old"] = sorted(ids_old - ids_new)
        return report
    left_i = left.set_index("game_id")
    right_i = right.set_index("game_id")
    for field, aliases in WEEK5_FIELDS:
        a = _series_by_alias(left_i.loc[merged["game_id"]], aliases, field=field).to_numpy(
            dtype=float
        )
        b = _series_by_alias(right_i.loc[merged["game_id"]], aliases, field=field).to_numpy(
            dtype=float
        )
        delta = a - b
        finite = pd.Series(delta).abs()
        max_abs = (
            float(finite.max()) if len(finite) and bool(finite.notna().all()) else float("inf")
        )
        report["fields"][field] = {
            "n": int(len(delta)),
            "max_abs": max_abs,
            "zero": bool(max_abs == 0.0),
        }
        if max_abs != 0.0:
            report["all_zero"] = False
    return report


def ungradable_blocks(predictions: pd.DataFrame) -> set[tuple[int, int]]:
    """(season, week) blocks with a non-empty ``null_reason``."""
    if predictions.empty or "null_reason" not in predictions.columns:
        return set()
    reason = predictions["null_reason"].astype(str)
    mask = (
        predictions["null_reason"].notna()
        & reason.str.len().gt(0)
        & ~reason.isin({"nan", "None", "<NA>"})
    )
    if not mask.any():
        return set()
    sub = predictions.loc[mask, ["season", "week"]]
    return {(int(s), int(w)) for s, w in zip(sub["season"], sub["week"], strict=True)}


def assert_quality_gate_attributable(quality: Mapping[str, Any], predictions: pd.DataFrame) -> None:
    """STOP unless null-μ / ungradable counts are the known ADR 0014 2019 w2–4 rows."""
    n_null = int(quality.get("n_null_mu") or 0)
    if n_null != 0:
        raise W9AStop(f"quality gate n_null_mu={n_null} (expected 0)")
    n_ungradable = int(quality.get("n_ungradable") or 0)
    blocks = ungradable_blocks(predictions)
    extra = blocks - ADR_0014_OOD_BLOCKS
    if extra:
        raise W9AStop(
            f"ungradable blocks outside ADR 0014 2019 w2–4: {sorted(extra)} "
            f"(n_ungradable={n_ungradable})"
        )
    if n_ungradable and not blocks <= ADR_0014_OOD_BLOCKS:
        raise W9AStop(f"n_ungradable={n_ungradable} but blocks={sorted(blocks)}")


def n_season(predictions: pd.DataFrame, season: int) -> int:
    """Count rows with ``season == season`` (0 when column absent)."""
    if predictions.empty or "season" not in predictions.columns:
        return 0
    return int((pd.to_numeric(predictions["season"], errors="coerce") == season).sum())


def preserve_w9m_registry(*, src: Path | None = None, dest: Path | None = None) -> Path:
    """Copy the truncated W9-M registry to a distinct path. Does not delete source."""
    source = src if src is not None else DEFAULT_REGISTRY_ROOT
    target = dest if dest is not None else W9M_REGISTRY_BACKUP
    if target.exists():
        print(f"W9-A registry_backup_already_present={target}")
        return target
    if not source.exists():
        raise W9AStop(f"registry source missing: {source}")
    shutil.copytree(source, target)
    print(f"W9-A registry_backup={target}")
    return target


def _inventory(
    *,
    predictor: Any,
    snapshot: Mapping[str, Any] | None,
    features: pd.DataFrame | None,
    artifact_sizes: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "oracle_season": ORACLE_SEASON,
        "oracle_week": ORACLE_WEEK,
        "run_id": FUND_RUN_ID,
        "model_version": FUND_MODEL_VERSION,
        "walk_forward": "full_2019_2024",
        "mapping_state": "end_of_run_after_last_2024_retrain",
        "seeds": {
            "walkforward_config_seed": int(predictor.config.seed),
            "predictor_seed": int(predictor.seed),
        },
        "adr_0014_thresholds": {
            "MAX_CREDIBLE_MARGIN_PRED": MAX_CREDIBLE_MARGIN_PRED,
            "MEMBER_DEGENERACY_SD_EPS": MEMBER_DEGENERACY_SD_EPS,
            "NULL_SHARE_DROP_THRESHOLD": NULL_SHARE_DROP_THRESHOLD,
            "location": "code constants (not fitted); production_stack.py / elasticnet.py",
        },
        "state": {
            "rating_engine_snapshot": {
                "lives": "rating_snapshot.json (Tuesday as-of 2024 week 5, pre-reveal)",
                "n_keys": 0 if snapshot is None else len(snapshot),
                "n_team_ids": 0 if snapshot is None else len(_team_ids_from_snapshot(snapshot)),
            },
            "mapping_layer": {
                "lives": ENSEMBLE_FILENAME,
                "contents": "full-run ProductionEnsemblePredictor after 2024 week-10 retrain",
            },
            "predict_features": {
                "lives": FEATURES_FILENAME,
                "n_rows": 0 if features is None else int(len(features)),
                "time_semantics": "as_of Tuesday 2024 week 5, computed before reveal",
            },
        },
        "artifacts": dict(artifact_sizes),
    }


def _refuse_champion3_write(output_dir: Path) -> None:
    resolved = output_dir.resolve()
    forbidden = CHAMPION3_ROOT.resolve()
    if resolved == forbidden or forbidden in resolved.parents:
        raise W9AStop(f"refusing to write into champion 3 root: {resolved}")


def run_fundamental_fit(
    *,
    registry_root: Path | None = None,
    staged_dir: Path | str = "data/staged",
    output_root: Path | str = "data/backtests",
    manual_approve: bool = True,
) -> dict[str, Any]:
    """Full 2019–2024 walk-forward → week-5 check → serialize → promote."""
    os.environ["NCAA_QUANT_WEBAPP__EXPORT_ENABLED"] = "false"
    configure_logging()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    root = registry_root if registry_root is not None else DEFAULT_REGISTRY_ROOT

    payload = load_backtest_config(FUND_CONFIG_NAME)
    assert_lockbox_absent_from_yaml(payload, name=FUND_CONFIG_NAME)
    cfg = walkforward_config_from_mapping(payload)
    if cfg.run_id == FORBIDDEN_RUN_ID or payload.get("run_id") == FORBIDDEN_RUN_ID:
        raise W9AStop("refusing forbidden run_id task23_fundamental_reduced_v2")

    iso_paths = isolation_state_paths(REPO_ROOT)
    hashes_before = hash_isolation_paths(iso_paths)
    c3_before = hash_tree(CHAMPION3_ROOT)
    print(f"W9-A champion3_hash_before={c3_before}")
    (ARTIFACT_DIR / "champion3_hash_before.txt").write_text(c3_before + "\n", encoding="utf-8")
    (ARTIFACT_DIR / "isolation_before.json").write_text(
        json.dumps(hashes_before, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    preserve_w9m_registry()

    git_sha = resolve_git_sha(repo_root=REPO_ROOT)
    git_dirty = resolve_git_dirty(repo_root=REPO_ROOT)
    print(f"W9-A git_sha={git_sha} git_dirty={git_dirty}")
    if git_dirty:
        raise W9AStop("working tree is dirty; ADR 0005 requires git_dirty=false")

    staged_path = Path(staged_dir)
    stack = _load_stack_inputs(
        staged_path=staged_path,
        replay_seasons=cfg.all_replay_seasons(),
        cfg=cfg,
        stack_kind="fundamental",
    )
    capture = _CaptureFeatureProvider(
        stack.feature_provider, season=ORACLE_SEASON, week=ORACLE_WEEK
    )
    wrapped = ProductionStack(
        kind=stack.kind,
        config=stack.config,
        feature_provider=cast(ProductionFeatureProvider, capture),
        rating_engine=stack.rating_engine,
        predictor=stack.predictor,
    )
    games = load_staged_games(staged_path, cfg.all_replay_seasons())
    if LOCKBOX_SEASON in {int(s) for s in games["season"].astype(int).unique()}:
        raise W9AStop("lockbox 2025 present in loaded games")
    print(f"W9-A games_full={len(games)} replay_seasons={list(cfg.all_replay_seasons())}")
    print(f"W9-A 2025_rows_in_fit_games={n_season(games, LOCKBOX_SEASON)}")

    output_dir = Path(output_root) / cfg.run_id / cfg.ablation_id
    _refuse_champion3_write(output_dir)
    if (output_dir / "predictions.parquet").is_file():
        raise W9AStop(f"output already present (refusing reuse): {output_dir}")

    pid = os.getpid()
    fit_started = datetime.now(tz=UTC)
    start_line = (
        f"W9-A FIT START pid={pid} start={fit_started.strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"run_id={cfg.run_id} label={FUND_LABEL}"
    )
    print(start_line, flush=True)

    t0 = time.perf_counter()
    elapsed = 0.0
    fit_ended = datetime.now(tz=UTC)
    try:
        result = run_backtest(
            FUND_CONFIG_NAME,
            games=games,
            stack=wrapped,
            snapshots=wrapped.feature_provider.snapshots,
            cfbd_lines=wrapped.predictor.cfbd_lines,
            output_root=Path(output_root),
            force=False,
            tracking_uri=str(payload.get("tracking_uri", "file:./mlruns")),
            label=FUND_LABEL,
            config_payload=payload,
            stack_kind="fundamental",
        )
    finally:
        elapsed = time.perf_counter() - t0
        fit_ended = datetime.now(tz=UTC)
        print(
            f"W9-A FIT EXIT pid={pid} end={fit_ended.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"wall_clock_sec={elapsed:.3f}",
            flush=True,
        )
    if elapsed > 4 * 3600:
        raise W9AStop(f"fundamental wall clock {elapsed:.3f}s exceeded 4 hours")

    preds = result.predictions
    n_2025 = n_season(preds, LOCKBOX_SEASON)
    print(f"W9-A N_2025={n_2025} predictions_n={len(preds)}")
    if n_2025 != 0:
        raise W9AStop(f"N_2025={n_2025} in predictions.parquet")

    quality = assert_prediction_quality_gate(
        preds,
        max_zero_mu_rate=wrapped.config.max_zero_mu_rate,
        min_train_games=wrapped.config.min_train_games,
        raise_on_fail=False,
    )
    print(f"W9-A quality_gate={json.dumps(quality.as_dict(), sort_keys=True, default=str)}")
    if not quality.passed:
        raise W9AStop(f"quality gate failed: {quality.failures}")
    assert_quality_gate_attributable(quality.as_dict(), preds)

    week5 = preds.loc[
        (pd.to_numeric(preds["season"], errors="coerce") == ORACLE_SEASON)
        & (pd.to_numeric(preds["week"], errors="coerce") == ORACLE_WEEK)
    ].copy()
    w9m_path = W9M_WEEK5
    backup_week = W9M_REGISTRY_BACKUP / "artifacts" / "v1" / WEEK_PREDICTIONS_FILENAME
    if not w9m_path.is_file() and backup_week.is_file():
        w9m_path = backup_week
    if not w9m_path.is_file():
        raise W9AStop(f"W9-M week-5 parquet missing: {w9m_path}")
    w9m = pd.read_parquet(w9m_path)
    check = week5_crosscheck(week5, w9m)
    print(f"W9-A week5_crosscheck={json.dumps(check, sort_keys=True)}")
    (ARTIFACT_DIR / "week5_crosscheck.json").write_text(
        json.dumps(check, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if not check["all_zero"] or int(check["n"]) != 56:
        raise W9AStop(f"week-5 cross-check failed: {check}")

    if capture.features is None or capture.rating_snapshot is None:
        raise W9AStop("did not capture 2024 week 5 features/rating snapshot")

    tmp = root / "_staging_w9a"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    ensemble_path = tmp / ENSEMBLE_FILENAME
    save_production_ensemble(wrapped.predictor, ensemble_path)
    feat_path = tmp / FEATURES_FILENAME
    capture.features.to_parquet(feat_path, index=False)
    snap_path = tmp / RATING_SNAPSHOT_FILENAME
    snap_path.write_text(
        json.dumps(capture.rating_snapshot, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    poss_path = tmp / POSSESSIONS_FILENAME
    with poss_path.open("wb") as fh:
        pickle.dump(
            dict(wrapped.feature_provider.possessions_artifacts),
            fh,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    week_path = tmp / WEEK_PREDICTIONS_FILENAME
    week5.to_parquet(week_path, index=False)

    artifacts_meta: dict[str, dict[str, Any]] = {}
    for path in (ensemble_path, feat_path, snap_path, poss_path, week_path):
        artifacts_meta[path.name] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    inv = _inventory(
        predictor=wrapped.predictor,
        snapshot=capture.rating_snapshot,
        features=capture.features,
        artifact_sizes=artifacts_meta,
    )
    inv_path = tmp / INVENTORY_FILENAME
    inv_path.write_text(json.dumps(inv, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    registry = ModelRegistry(root, tracking_uri=None)
    seed_manifest = result.manifest.seed_manifest
    manifest = build_manifest(
        config={"config_name": FUND_CONFIG_NAME, "walk_forward": "full_2019_2024"},
        seed_manifest=seed_manifest,
        extra={
            "run_id": FUND_RUN_ID,
            "model_version": FUND_MODEL_VERSION,
            "oracle": f"{ORACLE_SEASON}w{ORACLE_WEEK}",
            "wall_clock_sec": f"{elapsed:.3f}",
            "quality_gate_passed": str(bool(quality.passed)),
            "label": FUND_LABEL,
        },
        repo_root=REPO_ROOT,
    )
    record = registry.register_candidate(
        run_id=FUND_RUN_ID,
        manifest=manifest,
        predictions=week_path.read_bytes(),
        metrics={"n_week5": float(len(week5)), "wall_clock_sec": float(elapsed)},
        feature_signature={
            "columns": list(capture.features.columns),
            "n_rows": int(len(capture.features)),
        },
        extra_artifacts={
            ENSEMBLE_FILENAME: ensemble_path,
            FEATURES_FILENAME: feat_path,
            RATING_SNAPSHOT_FILENAME: snap_path,
            POSSESSIONS_FILENAME: poss_path,
            WEEK_PREDICTIONS_FILENAME: week_path,
            INVENTORY_FILENAME: inv_path,
        },
        notes=(
            "W9-A serialized champion: full 2019–2024 walk-forward on current code; "
            f"run_id={FUND_RUN_ID} / {FUND_MODEL_VERSION}"
        ),
    )
    registry.set_stage(record.version, ModelStage.CHALLENGER)
    gate = evaluate_promotion_gate(
        candidate_version=str(record.version),
        gate_passed=bool(quality.passed),
        manual_approve=manual_approve,
        force=False,
    )
    print(f"W9-A promotion_gate={json.dumps(gate.to_dict(), sort_keys=True)}")
    (ARTIFACT_DIR / "promotion_gate.json").write_text(
        json.dumps(gate.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if not gate.approved:
        raise W9AStop(f"promotion gate failed: {gate.reason}")
    if gate.force:
        raise W9AStop("promotion force=True is forbidden")
    prior = registry.resolve_champion()
    registry.set_stage(record.version, ModelStage.CHAMPION, prior_champion_version=prior.version)
    champ = registry.resolve_champion()
    art = Path(champ.artifact_dir)
    (art / PROMOTION_FILENAME).write_text(
        json.dumps(gate.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    process_rec = {
        "pid": pid,
        "started_at": fit_started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_at": fit_ended.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "wall_clock_sec": round(elapsed, 3),
        "seeds": result.manifest.seed_manifest,
        "champion_version_registry": champ.version,
        "replay_seasons": list(cfg.all_replay_seasons()),
        "read_from_2025": False,
        "run_id": FUND_RUN_ID,
        "label": FUND_LABEL,
        "n_2025": n_2025,
        "output_dir": str(result.output_dir),
        "export_enabled_env": os.environ.get("NCAA_QUANT_WEBAPP__EXPORT_ENABLED"),
    }
    (root / FIT_PROCESS_FILENAME).write_text(
        json.dumps(process_rec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (ARTIFACT_DIR / "fundamental_process.json").write_text(
        json.dumps(process_rec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    hashes_after = hash_isolation_paths(iso_paths)
    c3_after = hash_tree(CHAMPION3_ROOT)
    changed = [k for k in hashes_before if hashes_before[k] != hashes_after.get(k)]
    print(f"W9-A isolation_changed={changed}")
    print(f"W9-A champion3_hash_after={c3_after}")
    (ARTIFACT_DIR / "champion3_hash_after.txt").write_text(c3_after + "\n", encoding="utf-8")
    (ARTIFACT_DIR / "isolation_after.json").write_text(
        json.dumps(hashes_after, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if changed:
        raise W9AStop(f"isolation violated; hashes changed: {changed}")
    if c3_after != c3_before:
        raise W9AStop("champion 3 directory hash changed")
    print("W9-A fit exiting")
    return {
        "elapsed_sec": elapsed,
        "champion_version": champ.version,
        "artifact_dir": str(art),
        "promotion": gate.to_dict(),
        "quality_gate": quality.as_dict(),
        "week5": check,
        "n_2025": n_2025,
        "output_dir": str(result.output_dir),
        "process": process_rec,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in {"fit-fundamental"}:
        print("usage: python -m ncaa_quant.registry.w9a_revalidate fit-fundamental")
        return 2
    try:
        run_fundamental_fit()
    except W9AStop as exc:
        print(f"W9-A STOP: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
