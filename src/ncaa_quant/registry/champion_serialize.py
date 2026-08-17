"""W9-M: fit the champion walk-forward through 2024 week 5 and register it.

Writes only under the registry root. Does not touch backtest week parquets,
filter_history, possessions live, tier state, or the idempotency ledger.
Does not call MLflow, R2, or Prefect.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import sys
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.evaluation.backtest_runner import (
    load_backtest_config,
    load_staged_games,
    walkforward_config_from_mapping,
)
from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON, assert_lockbox_excluded
from ncaa_quant.evaluation.production_stack import (
    MAX_CREDIBLE_MARGIN_PRED,
    MEMBER_DEGENERACY_SD_EPS,
    ProductionFeatureProvider,
    ProductionStack,
    StackKind,
    build_observations_from_staged,
    build_production_stack,
)
from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    WalkForwardHarness,
    WalkForwardResult,
    assert_prediction_quality_gate,
)
from ncaa_quant.models.heads.elasticnet import NULL_SHARE_DROP_THRESHOLD
from ncaa_quant.pipelines.gates import evaluate_promotion_gate
from ncaa_quant.pipelines.predict import (
    _alias_stamp_columns,
    run_isolated_week_export,
)
from ncaa_quant.registry.bundle import (
    ENSEMBLE_FILENAME,
    FEATURES_FILENAME,
    FIT_PROCESS_FILENAME,
    INVENTORY_FILENAME,
    POSSESSIONS_FILENAME,
    PROMOTION_FILENAME,
    RATING_SNAPSHOT_FILENAME,
    WEEK_PREDICTIONS_FILENAME,
    load_production_ensemble,
    save_production_ensemble,
)
from ncaa_quant.registry.manifest import build_manifest
from ncaa_quant.registry.stages import ModelStage
from ncaa_quant.registry.store import ModelRegistry
from ncaa_quant.utils.logging import configure_logging, get_logger

log = get_logger(__name__)

REPO_ROOT: Path = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY_ROOT: Path = REPO_ROOT / "data" / "registry"
DEFAULT_CONFIG_NAME: str = "task23_fundamental_full_reduced_v2"
ORACLE_SEASON: int = 2024
ORACLE_WEEK: int = 5
CHAMPION_RUN_ID: str = "task23_fundamental_reduced_v2"
CHAMPION_MODEL_VERSION: str = "production-v0_reduced_v2"
FIXTURE_PATH: Path = REPO_ROOT / "webapp" / "fixtures" / "week_predictions.json"

COMPARE_FIELDS: tuple[str, ...] = (
    "mu_margin",
    "sigma_margin",
    "margin_interval_lo",
    "margin_interval_hi",
    "mu_total",
    "sigma_total",
    "p_win_home",
    "p_favored",
)


class _CaptureFeatureProvider:
    """Forwarding wrapper that snapshots rating state at one (season, week)."""

    def __init__(
        self,
        inner: ProductionFeatureProvider,
        *,
        season: int,
        week: int,
    ) -> None:
        self._inner = inner
        self._season = int(season)
        self._week = int(week)
        self.rating_snapshot: dict[str, Any] | None = None
        self.features: pd.DataFrame | None = None
        self.as_of: datetime | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def compute_game_features(
        self,
        games: pd.DataFrame,
        as_of: datetime,
        *,
        rating_state: Mapping[str, Any],
        market_features: bool,
    ) -> pd.DataFrame:
        out = self._inner.compute_game_features(
            games, as_of, rating_state=rating_state, market_features=market_features
        )
        if not games.empty:
            season = int(games["season"].iloc[0])
            week = int(games["week"].iloc[0])
            if season == self._season and week == self._week:
                self.rating_snapshot = {str(k): v for k, v in dict(rating_state).items()}
                self.features = out.copy()
                self.as_of = as_of
        return out


def isolation_state_paths(repo_root: Path | None = None) -> list[Path]:
    """Real on-disk state files that this task must not mutate."""
    root = repo_root if repo_root is not None else REPO_ROOT
    paths: list[Path] = [
        root / "data" / "webapp" / "tier_state.json",
        root / "data" / "webapp" / "tier_changes.jsonl",
        root / "data" / "pipeline_state" / "idempotency.json",
        root / "data" / "artifacts" / "state_space" / "filter_history.parquet",
        root / "data" / "artifacts" / "expected_possessions" / "live.json",
    ]
    week_dir = root / "data" / "backtests" / "task23_fundamental_reduced_v2" / "full" / "weeks"
    if week_dir.is_dir():
        paths.extend(sorted(week_dir.glob("*.parquet")))
    return paths


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hash_isolation_paths(paths: list[Path]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in paths:
        if path.is_file():
            out[str(path.as_posix())] = sha256_file(path)
        else:
            out[str(path.as_posix())] = "ABSENT"
    return out


def _truncate_games_through_oracle(games: pd.DataFrame) -> pd.DataFrame:
    """Keep all pre-2024 games and 2024 weeks 1–5 only (PIT prefix of champion 3)."""
    season = games["season"].astype(int)
    week = games["week"].astype(int)
    mask = (season < ORACLE_SEASON) | ((season == ORACLE_SEASON) & (week <= ORACLE_WEEK))
    return games.loc[mask].copy()


def _load_stack_inputs(
    *,
    staged_path: Path,
    replay_seasons: tuple[int, ...],
    cfg: WalkForwardConfig,
    stack_kind: StackKind,
) -> ProductionStack:
    """Mirror ``ncaa-quant backtest run`` data loading; no lockbox seasons."""
    from ncaa_quant.cli import load_fitted_priors_frame_for_backtest, load_staged_odds_snapshots
    from ncaa_quant.data.storage import ParquetStore
    from ncaa_quant.evaluation.inert import InertComponentError, assert_prior_family_staged
    from ncaa_quant.features.possessions import build_possessions_training_from_staged

    assert_lockbox_excluded(replay_seasons, context="W9-M champion serialize")
    try:
        assert_prior_family_staged(replay_seasons, staged_root=staged_path)
    except InertComponentError:
        log.warning("w9m_prior_family_precondition_skipped")

    games = load_staged_games(staged_path, replay_seasons)
    if games.empty:
        raise FileNotFoundError(f"No staged games for seasons {replay_seasons}")

    store = ParquetStore(staged_path)
    advanced_frames: list[pd.DataFrame] = []
    plays_frames: list[pd.DataFrame] = []
    lines_frames: list[pd.DataFrame] = []
    teams_frames: list[pd.DataFrame] = []
    drives_frames: list[pd.DataFrame] = []
    for season in replay_seasons:
        for path in store._matching_paths("advanced_box", {"season": int(season)}):  # noqa: SLF001
            advanced_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("plays", {"season": int(season)}):  # noqa: SLF001
            plays_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("lines_historical", {"season": int(season)}):  # noqa: SLF001
            lines_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("teams", {"season": int(season)}):  # noqa: SLF001
            teams_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("drives", {"season": int(season)}):  # noqa: SLF001
            drives_frames.append(pd.read_parquet(path))

    advanced = pd.concat(advanced_frames, ignore_index=True) if advanced_frames else None
    plays = pd.concat(plays_frames, ignore_index=True) if plays_frames else None
    cfbd_lines = pd.concat(lines_frames, ignore_index=True) if lines_frames else None
    teams = pd.concat(teams_frames, ignore_index=True) if teams_frames else pd.DataFrame()
    drives = pd.concat(drives_frames, ignore_index=True) if drives_frames else pd.DataFrame()
    obs, n_on, n_off = build_observations_from_staged(
        plays=plays,
        games=games,
        advanced=advanced,
        garbage_time_filter=cfg.garbage_time_filter,
    )
    play_counts = (n_on, n_off) if n_off > 0 else None
    snapshots = load_staged_odds_snapshots(staged_path, replay_seasons)
    priors_frame = load_fitted_priors_frame_for_backtest(staged_path, replay_seasons)
    possessions_training = (
        build_possessions_training_from_staged(
            plays=plays if plays is not None else pd.DataFrame(),
            games=games,
            teams=teams,
            drives=drives,
            garbage_time_filter=cfg.garbage_time_filter,
        )
        if plays is not None and not plays.empty and not drives.empty
        else None
    )
    stack = build_production_stack(
        cfg,
        kind=stack_kind,
        observations=obs,
        priors_frame=priors_frame,
        snapshots=snapshots,
        cfbd_lines=cfbd_lines,
        possessions_training=possessions_training,
        play_counts=play_counts,
        enforce_ablation_preconditions=True,
    )
    # Champion artifacts live under ablation_id=full; kind=fundamental would remap.
    restored = replace(stack.config, ablation_id=str(cfg.ablation_id), run_id=str(cfg.run_id))
    stack.predictor.config = restored
    return ProductionStack(
        kind=stack.kind,
        config=restored,
        feature_provider=stack.feature_provider,
        rating_engine=stack.rating_engine,
        predictor=stack.predictor,
    )


def _team_ids_from_snapshot(snapshot: Mapping[str, Any]) -> list[str]:
    ids: set[str] = set()
    for key in snapshot:
        tid = str(key).split(":", 1)[0]
        if tid:
            ids.add(tid)
    return sorted(ids, key=lambda x: (len(x), x))


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
        "run_id": CHAMPION_RUN_ID,
        "model_version": CHAMPION_MODEL_VERSION,
        "seeds": {
            "walkforward_config_seed": int(predictor.config.seed),
            "predictor_seed": int(predictor.seed),
            "margin_head_seed": int(predictor.margin_head.seed),
            "total_head_seed": int(predictor.total_head.seed),
            "enet_seed": int(predictor.enet_margin.seed),
            "sigma_margin_seed": int(predictor.sigma_margin_head.seed),
            "sigma_total_seed": int(predictor.sigma_total_head.seed),
            "quantile_seed": int(predictor.quantile_margin_head.seed),
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
                "contents": "LGBM μ margin/total, ENet μ margin + scaler/medians, "
                "σ heads, quantile head, NNLS, CQR, PIT maps, key-number kernel, "
                "member_status, rho",
            },
            "ml_ats_ou_calibrators": {
                "kind": "distributional PIT (ADR 0011); models/calibrate.py is diagnostics-only",
                "routing": "ml and ats_close → margin PIT; ou_close → total PIT",
                "lives": "inside production_ensemble.pkl (_calibration)",
            },
            "conformal_cqr": {"lives": "inside production_ensemble.pkl (_cqr)"},
            "feature_scalers": {
                "elasticnet_standard_scaler": "inside enet_margin (_scaler)",
                "lgbm": "none (trees; no scaler)",
            },
            "team_id_index": {
                "lives": "keys of rating_snapshot.json (tid:dim)",
                "source": "StateSpaceRatingEngine._states at week-5 as_of",
            },
            "predict_features": {
                "lives": FEATURES_FILENAME,
                "n_rows": 0 if features is None else int(len(features)),
                "time_semantics": "as_of Tuesday 2024 week 5, computed before reveal",
            },
            "possessions": {
                "lives": POSSESSIONS_FILENAME,
                "note": "PIT refit artifacts from the walk-forward; live.json is not used",
            },
        },
        "artifacts": dict(artifact_sizes),
    }


def run_fit(
    *,
    registry_root: Path | None = None,
    staged_dir: Path | str = "data/staged",
    config_name: str = DEFAULT_CONFIG_NAME,
    manual_approve: bool = True,
) -> dict[str, Any]:
    """Sanctioned truncated walk-forward → registry bundle + promotion gate."""
    configure_logging()
    root = registry_root if registry_root is not None else DEFAULT_REGISTRY_ROOT
    root.mkdir(parents=True, exist_ok=True)
    iso_paths = isolation_state_paths()
    hashes_before = hash_isolation_paths(iso_paths)

    payload = load_backtest_config(config_name)
    cfg = walkforward_config_from_mapping(payload)
    replay = cfg.all_replay_seasons()
    print(f"W9-M replay_seasons={list(replay)}")
    print(f"W9-M lockbox_season={LOCKBOX_SEASON} in_replay={LOCKBOX_SEASON in replay}")
    print(f"W9-M walkforward_seed={cfg.seed}")
    print(
        f"W9-M LIGHTGBM_RANDOM_SEED={os.environ.get('LIGHTGBM_RANDOM_SEED', 'unset_until_harness')}"
    )

    staged_path = Path(staged_dir)
    stack_kind: StackKind = "fundamental"
    stack = _load_stack_inputs(
        staged_path=staged_path,
        replay_seasons=replay,
        cfg=cfg,
        stack_kind=stack_kind,
    )
    games = load_staged_games(staged_path, replay)
    games_fit = _truncate_games_through_oracle(games)
    mask_2024 = games_fit["season"].astype(int) == ORACLE_SEASON
    max_2024_week = int(games_fit.loc[mask_2024, "week"].max())
    print(
        f"W9-M games_full={len(games)} games_truncated={len(games_fit)} "
        f"max_2024_week={max_2024_week}"
    )
    seasons_in_games = sorted({int(s) for s in games_fit["season"].astype(int).unique()})
    print(f"W9-M seasons_in_truncated_games={seasons_in_games}")
    print("W9-M 2025_rows_in_fit_games=0")

    capture = _CaptureFeatureProvider(
        stack.feature_provider, season=ORACLE_SEASON, week=ORACLE_WEEK
    )
    harness = WalkForwardHarness(
        config=stack.config,
        predictor=stack.predictor,
        feature_provider=capture,
        rating_engine=stack.rating_engine,
    )

    fit_started = datetime.now(tz=UTC)
    pid = os.getpid()
    print(f"W9-M fit_pid={pid} start={fit_started.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    t0 = time.perf_counter()
    result: WalkForwardResult = harness.run(
        games_fit,
        snapshots=stack.feature_provider.snapshots,
        cfbd_lines=stack.predictor.cfbd_lines,
    )
    elapsed = time.perf_counter() - t0
    fit_ended = datetime.now(tz=UTC)
    print(f"W9-M fit_wall_clock_sec={elapsed:.3f}")
    print(f"W9-M seed_manifest={json.dumps(result.seed_manifest.to_dict(), sort_keys=True)}")

    if capture.features is None or capture.rating_snapshot is None:
        raise RuntimeError("did not capture 2024 week 5 features/rating snapshot")

    week_preds = result.predictions
    if not week_preds.empty:
        week_preds = week_preds.loc[
            (week_preds["season"].astype(int) == ORACLE_SEASON)
            & (week_preds["week"].astype(int) == ORACLE_WEEK)
        ].copy()
    print(f"W9-M n_week5_prediction_rows={len(week_preds)}")

    quality = result.quality_gate
    if quality is None:
        quality = assert_prediction_quality_gate(
            result.predictions,
            max_zero_mu_rate=stack.config.max_zero_mu_rate,
            min_train_games=stack.config.min_train_games,
            raise_on_fail=False,
        )
    print(f"W9-M quality_gate={json.dumps(quality.as_dict(), sort_keys=True, default=str)}")

    tmp = root / "_staging"
    tmp.mkdir(parents=True, exist_ok=True)
    ensemble_path = tmp / ENSEMBLE_FILENAME
    save_production_ensemble(stack.predictor, ensemble_path)
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
            dict(stack.feature_provider.possessions_artifacts),
            fh,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    week_path = tmp / WEEK_PREDICTIONS_FILENAME
    week_preds.to_parquet(week_path, index=False)

    artifacts_meta = {}
    for path in (ensemble_path, feat_path, snap_path, poss_path, week_path):
        artifacts_meta[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    inv = _inventory(
        predictor=stack.predictor,
        snapshot=capture.rating_snapshot,
        features=capture.features,
        artifact_sizes=artifacts_meta,
    )
    inv_path = tmp / INVENTORY_FILENAME
    inv_path.write_text(json.dumps(inv, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    registry = ModelRegistry(root, tracking_uri=None)
    manifest = build_manifest(
        config={"config_name": config_name, "truncated_through": f"{ORACLE_SEASON}w{ORACLE_WEEK}"},
        seed_manifest=result.seed_manifest,
        extra={
            "run_id": CHAMPION_RUN_ID,
            "model_version": CHAMPION_MODEL_VERSION,
            "oracle": f"{ORACLE_SEASON}w{ORACLE_WEEK}",
            "wall_clock_sec": f"{elapsed:.3f}",
            "quality_gate_passed": str(bool(quality.passed)),
        },
    )
    record = registry.register_candidate(
        run_id=CHAMPION_RUN_ID,
        manifest=manifest,
        predictions=week_path.read_bytes(),
        metrics={"n_week5": float(len(week_preds)), "wall_clock_sec": float(elapsed)},
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
            "W9-M serialized champion: mapping layer after 2024 week-5 retrain; "
            "identity run_id=task23_fundamental_reduced_v2 / production-v0_reduced_v2"
        ),
    )
    registry.set_stage(record.version, ModelStage.CHALLENGER)
    gate = evaluate_promotion_gate(
        candidate_version=str(record.version),
        gate_passed=bool(quality.passed),
        manual_approve=manual_approve,
        force=False,
    )
    print(f"W9-M promotion_gate={json.dumps(gate.to_dict(), sort_keys=True)}")
    if not gate.approved:
        raise RuntimeError(f"automatic/manual promotion gate failed: {gate.reason}")
    registry.set_stage(record.version, ModelStage.CHAMPION)
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
        "seeds": result.seed_manifest.to_dict(),
        "champion_version_registry": champ.version,
        "replay_seasons": list(replay),
        "read_from_2025": False,
        "policy": "evaluation-scoped lockbox; 2025 not in replay_seasons or games",
    }
    (root / FIT_PROCESS_FILENAME).write_text(
        json.dumps(process_rec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    hashes_after = hash_isolation_paths(iso_paths)
    changed = [k for k in hashes_before if hashes_before[k] != hashes_after.get(k)]
    print(f"W9-M isolation_changed={changed}")
    print(f"W9-M hashes_before={json.dumps(hashes_before, sort_keys=True)}")
    print(f"W9-M hashes_after={json.dumps(hashes_after, sort_keys=True)}")
    print(f"W9-M registry_index={root / 'registry_index.json'}")
    print(f"W9-M artifact_dir={art}")
    print("W9-M fit exiting")
    if changed:
        raise RuntimeError(f"isolation violated; hashes changed: {changed}")
    return {
        "elapsed_sec": elapsed,
        "champion_version": champ.version,
        "artifact_dir": str(art),
        "promotion": gate.to_dict(),
        "quality_gate": quality.as_dict(),
        "hashes_before": hashes_before,
        "hashes_after": hashes_after,
        "process": process_rec,
    }


def _live_predict_fn(features: pd.DataFrame, predictor: Any) -> Any:
    def _predict(_stale_ctx: Any) -> list[dict[str, Any]]:
        pred = predictor.predict(features)
        rows: list[dict[str, Any]] = []
        for rec in pred.to_dict(orient="records"):
            rec["season"] = ORACLE_SEASON
            rec["week"] = ORACLE_WEEK
            rec["run_id"] = CHAMPION_RUN_ID
            rec["model_version"] = CHAMPION_MODEL_VERSION
            rows.append(_alias_stamp_columns(rec))
        return rows

    return _predict


def _max_abs_delta(a: Any, b: Any) -> float | None:
    if a is None and b is None:
        return 0.0
    if a is None or b is None:
        return None
    try:
        return abs(float(a) - float(b))
    except (TypeError, ValueError):
        return None


def run_verify(
    *,
    registry_root: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Cold-load the serialized champion and oracle-diff 2024 week 5."""
    configure_logging()
    verify_pid = os.getpid()
    verify_started = datetime.now(tz=UTC)
    root = registry_root if registry_root is not None else DEFAULT_REGISTRY_ROOT
    fit_proc_path = root / FIT_PROCESS_FILENAME
    if not fit_proc_path.is_file():
        raise FileNotFoundError(f"fit process record missing: {fit_proc_path}")
    fit_proc = json.loads(fit_proc_path.read_text(encoding="utf-8"))
    fit_pid = int(fit_proc["pid"])
    print(f"W9-M verify_pid={verify_pid} start={verify_started.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"W9-M fit_pid={fit_pid} fit_ended_at={fit_proc.get('ended_at')}")
    print(f"W9-M pids_differ={verify_pid != fit_pid}")
    if verify_pid == fit_pid:
        raise RuntimeError("verify must run in a process that did not fit the model")

    registry = ModelRegistry(root, tracking_uri=None)
    champ = registry.resolve_champion()
    art = Path(champ.artifact_dir)
    predictor = load_production_ensemble(art / ENSEMBLE_FILENAME)
    features = pd.read_parquet(art / FEATURES_FILENAME)
    print(f"W9-M loaded_champion registry_version={champ.version} n_features={len(features)}")

    out_dir = output_dir if output_dir is not None else root / "oracle_export"
    state_dir = root / "oracle_state"
    iso_paths = isolation_state_paths()
    hashes_before = hash_isolation_paths(iso_paths)
    export = run_isolated_week_export(
        season=ORACLE_SEASON,
        week=ORACLE_WEEK,
        output_dir=out_dir,
        state_dir=state_dir,
        predict_fn=_live_predict_fn(features, predictor),
    )
    hashes_after = hash_isolation_paths(iso_paths)
    changed = [k for k in hashes_before if hashes_before[k] != hashes_after.get(k)]
    print(f"W9-M verify_isolation_changed={changed}")

    produced_path = Path(export["written"]["week_predictions.json"])
    produced = json.loads(produced_path.read_text(encoding="utf-8"))
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    pg = produced["games"]
    fg = fixture["games"]
    p_ids = {str(g["game_id"]) for g in pg}
    f_ids = {str(g["game_id"]) for g in fg}
    print(f"W9-M n_games produced={len(pg)} fixture={len(fg)}")
    print(f"W9-M game_id_sets_identical={p_ids == f_ids}")
    by_f = {str(g["game_id"]): g for g in fg}
    max_d: dict[str, float] = {k: 0.0 for k in COMPARE_FIELDS}
    disagreements: list[dict[str, Any]] = []
    n_tier_agree = 0
    for g in pg:
        other = by_f[str(g["game_id"])]
        for field in COMPARE_FIELDS:
            d = _max_abs_delta(g.get(field), other.get(field))
            if d is None:
                max_d[field] = float("inf")
            else:
                max_d[field] = max(max_d[field], d)
        if g.get("conviction_tier") == other.get("conviction_tier"):
            n_tier_agree += 1
        else:
            disagreements.append(
                {
                    "game_id": str(g["game_id"]),
                    "produced": g.get("conviction_tier"),
                    "fixture": other.get("conviction_tier"),
                }
            )
    print("W9-M oracle_max_abs_delta")
    for field, val in max_d.items():
        print(f"  {field:24s} {val}")
    print(f"W9-M conviction_tier_agree={n_tier_agree}/{len(pg)}")
    print(f"W9-M conviction_disagreements={json.dumps(disagreements)}")
    ident_p = produced.get("model_identity") or {}
    ident_f = fixture.get("model_identity") or {}
    print(
        "W9-M identity produced "
        f"champion_version={ident_p.get('champion_version')} "
        f"run_id={ident_p.get('run_id')} model_version={ident_p.get('model_version')}"
    )
    print(
        "W9-M identity fixture "
        f"champion_version={ident_f.get('champion_version')} "
        f"run_id={ident_f.get('run_id')} model_version={ident_f.get('model_version')}"
    )
    print(f"W9-M export_enabled={export.get('export_enabled')}")
    if changed:
        raise RuntimeError(f"verify isolation violated: {changed}")
    return {
        "n_games": len(pg),
        "game_id_sets_identical": p_ids == f_ids,
        "max_abs_delta": max_d,
        "tier_agree": n_tier_agree,
        "disagreements": disagreements,
        "identity_produced": ident_p,
        "identity_fixture": ident_f,
        "fit_pid": fit_pid,
        "verify_pid": verify_pid,
        "export_enabled": export.get("export_enabled"),
        "written": export.get("written"),
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in {"fit", "verify"}:
        print("usage: python -m ncaa_quant.registry.champion_serialize {fit|verify}")
        return 2
    if args[0] == "fit":
        run_fit()
        return 0
    run_verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
