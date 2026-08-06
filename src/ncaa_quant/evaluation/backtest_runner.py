"""Resumable walk-forward backtest runner + plan (Task 22B / 22B-FIX).

``backtest run --config <name>`` executes one named walk-forward end to end.
``backtest plan --config <name>`` prints seasons, weeks, retrain points, and
estimated wall clock without spending compute. A config may name a single run
or a **run set** (``runs:`` list) — Task 23's eight-run bill.

Resumability is keyed by ``(run_id, season, week)``. Completed units are skipped
unless ``--force``. A crash mid-run never duplicates or half-writes a week
(atomic parquet write per unit).

Every run writes a :class:`~ncaa_quant.registry.manifest.RunManifest` per §8
item 8, including ablation settings and the season list actually executed.
Prediction rows carry ``run_id`` and ``ablation_id``.

Wall-clock model (Task 22B-FIX)
-------------------------------
Constants below are **measured**, not guessed. Basis (2026-08-05, Windows
workstation for this repo):

* **Wired week-unit** — full ``WalkForwardHarness.run`` on staged 2023
  (910 games, 15 weeks, production_stack fundamental): **0.1413 s/week**.
  Members active on that path: Stage-1 filter updates, rating features,
  LightGBM μ + ElasticNet μ (A4 ensemble). **Absent / short-circuited:**
  CatBoost, NGBoost, 100k-draw Monte Carlo, 50 epistemic posterior draws —
  none are invoked by ``production_stack`` / the harness predict loop.
  The Task 22B report of 367.5 s for one pass used hardcoded 2.5 s/week
  without those members; that figure was not a measured full §5.2 bill.

* **§5.2 / §2.6 add-ons** (microbenchmarked separately, then added to the
  week-unit so the plan does not pretend the cheap wired path is the full
  system): CatBoost predict, NGBoost predict, ``sample_bivariate_normal``
  at ``DEFAULT_N_DRAWS=100_000`` for ~65 games, and 50 LightGBM predict
  passes as the epistemic-draw floor. Retrain add-on = wired LGBM+ENet fit
  + CatBoost fit + NGBoost fit on ~400 training rows.

Extrapolation: ``n_week_units * SEC_PER_WEEK_FULL + n_retrain_points *
SEC_PER_RETRAIN_FULL`` per run; run-set totals sum the runs. This is still a
lower bound on a future Optuna-sized / full-feature-store backtest — stated
in the plan text — but it is honest about what is measured today.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]

from ncaa_quant.evaluation.production_stack import (
    ProductionStack,
    StackKind,
    build_production_stack,
)
from ncaa_quant.evaluation.walkforward import (
    MappingLayerMode,
    MarketFeatureSource,
    PreseasonPriorsMode,
    RatingUpdatesMode,
    RunKind,
    WalkForwardConfig,
    WalkForwardError,
    WalkForwardHarness,
    WalkForwardResult,
    assert_prediction_quality_gate,
)
from ncaa_quant.registry.manifest import (
    ManifestError,
    RunManifest,
    build_manifest,
    write_manifest,
)
from ncaa_quant.registry.tracking import log_evaluation_run
from ncaa_quant.utils.logging import get_logger
from ncaa_quant.utils.seeding import set_global_seed

log = get_logger(__name__)

# Measured week-unit / retrain costs — see module docstring for basis.
SEC_PER_WEEK_WIRED: float = 0.1413
SEC_PER_WEEK_FULL: float = 0.7528  # wired + CatBoost/NGBoost pred + MC + epistemic
SEC_PER_RETRAIN_FULL: float = 1.1404  # LGBM+ENet+CatBoost+NGBoost fits

# Backward-compatible aliases (plan path uses FULL).
SEC_PER_WEEK_ESTIMATE: float = SEC_PER_WEEK_FULL
SEC_PER_RETRAIN_ESTIMATE: float = SEC_PER_RETRAIN_FULL

COST_MEASUREMENT_BASIS: str = (
    "week_unit=WalkForwardHarness.run staged 2023 (910 games/15 weeks) "
    f"wired={SEC_PER_WEEK_WIRED:.4f}s/week; "
    "add-ons microbench CatBoost+NGBoost predict + 100k-draw MC (~65 games) "
    "+ 50 epistemic LGBM predicts -> "
    f"full={SEC_PER_WEEK_FULL:.4f}s/week; "
    f"retrain_full={SEC_PER_RETRAIN_FULL:.4f}s "
    "(LGBM+ENet+CatBoost+NGBoost fit, ~400 rows); "
    "hardware=local Windows workstation; "
    "WIRING FINDING: CatBoost/NGBoost/MC/epistemic are NOT in production_stack "
    "predict loop - prior 367.5s plan used hardcoded 2.5s/week on the cheap path"
)

DEFAULT_OUTPUT_ROOT = Path("data/backtests")


class BacktestRunnerError(WalkForwardError):
    """Invalid backtest config or resume state."""


@dataclass(frozen=True)
class BacktestPlan:
    """Dry-run bill for one named backtest config."""

    config_name: str
    run_id: str
    ablation_id: str
    seasons: tuple[int, ...]
    weeks_by_season: dict[int, list[int]]
    retrain_points: list[dict[str, int]]
    n_week_units: int
    n_retrain_points: int
    estimated_wall_clock_sec: float
    ablation_settings: dict[str, Any]
    measurement_basis: str = COST_MEASUREMENT_BASIS
    stack: str = "fundamental"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format_text(self) -> str:
        lines = [
            f"BACKTEST PLAN  config={self.config_name}",
            f"  run_id={self.run_id}  ablation_id={self.ablation_id}  stack={self.stack}",
            f"  seasons={list(self.seasons)}",
            f"  week_units={self.n_week_units}  retrain_points={self.n_retrain_points}",
            f"  estimated_wall_clock_sec={self.estimated_wall_clock_sec:.1f}  "
            f"(~{self.estimated_wall_clock_sec / 60.0:.1f} min)",
            f"  measurement_basis={self.measurement_basis}",
            "  ablations:",
        ]
        for k, v in self.ablation_settings.items():
            lines.append(f"    {k}: {v}")
        lines.append("  weeks:")
        for season in self.seasons:
            weeks = self.weeks_by_season.get(season, [])
            lines.append(f"    {season}: {weeks}")
        lines.append("  retrain_points:")
        for rp in self.retrain_points:
            lines.append(f"    season={rp['season']} week={rp['week']}")
        return "\n".join(lines)


@dataclass(frozen=True)
class BacktestRunSetPlan:
    """Dry-run bill for a named multi-run set (Task 23)."""

    run_set_name: str
    plans: tuple[BacktestPlan, ...]
    estimated_wall_clock_sec: float
    measurement_basis: str = COST_MEASUREMENT_BASIS

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_set_name": self.run_set_name,
            "estimated_wall_clock_sec": self.estimated_wall_clock_sec,
            "measurement_basis": self.measurement_basis,
            "runs": [p.to_dict() for p in self.plans],
        }

    def format_text(self) -> str:
        lines = [
            f"BACKTEST RUN-SET PLAN  run_set={self.run_set_name}",
            f"  n_runs={len(self.plans)}",
            f"  total_estimated_wall_clock_sec={self.estimated_wall_clock_sec:.1f}  "
            f"(~{self.estimated_wall_clock_sec / 60.0:.1f} min, "
            f"~{self.estimated_wall_clock_sec / 3600.0:.2f} h)",
            f"  measurement_basis={self.measurement_basis}",
            "  per-run:",
        ]
        for p in self.plans:
            lines.append(
                f"    {p.config_name}: week_units={p.n_week_units} "
                f"retrain_points={p.n_retrain_points} "
                f"est_sec={p.estimated_wall_clock_sec:.1f} "
                f"(~{p.estimated_wall_clock_sec / 60.0:.1f} min) "
                f"seasons={list(p.seasons)} ablation_id={p.ablation_id}"
            )
        lines.append("  ---")
        for p in self.plans:
            lines.append(p.format_text())
            lines.append("")
        return "\n".join(lines)


@dataclass
class BacktestRunResult:
    """Outcome of ``backtest run``."""

    run_id: str
    ablation_id: str
    predictions: pd.DataFrame
    manifest: RunManifest
    output_dir: Path
    mlflow_run_id: str | None = None
    resumed_units: int = 0
    completed_units: int = 0
    label: str = ""


def load_backtest_config(path: Path | str) -> dict[str, Any]:
    """Load a YAML backtest / ablation / eval run config."""
    target = Path(path)
    if not target.is_file():
        for root in (Path("configs/ablations"), Path("configs/eval")):
            candidate = root / f"{path}.yaml"
            if candidate.is_file():
                target = candidate
                break
        else:
            msg = f"backtest config not found: {path}"
            raise BacktestRunnerError(msg)
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise BacktestRunnerError(f"config root must be a mapping: {target}")
    return dict(payload)


def is_run_set_config(payload: Mapping[str, Any]) -> bool:
    """True when the config defines a Task 23-style ``runs:`` list."""
    runs = payload.get("runs")
    return isinstance(runs, list) and len(runs) > 0


def walkforward_config_from_mapping(payload: Mapping[str, Any]) -> WalkForwardConfig:
    """Build :class:`WalkForwardConfig` from a config mapping.

    Run-set configs (``runs:``) yield a synthetic config whose season list is
    the union of all member runs — used by the CLI to load staged games before
    ``plan_backtest`` expands per-run bills.
    """
    if is_run_set_config(payload):
        return _union_walkforward_from_run_set(payload)

    wf = payload.get("walkforward", payload)
    if not isinstance(wf, Mapping):
        raise BacktestRunnerError("config.walkforward must be a mapping")

    def _tuple_int(key: str, default: tuple[int, ...]) -> tuple[int, ...]:
        raw = wf.get(key, default)
        return tuple(int(x) for x in raw)

    run_kind_raw = str(wf.get("run_kind", payload.get("run_kind", "backtest")))
    return WalkForwardConfig(
        test_seasons=_tuple_int("test_seasons", WalkForwardConfig.test_seasons),
        continuity_seasons=_tuple_int("continuity_seasons", WalkForwardConfig.continuity_seasons),
        warmup_seasons=_tuple_int("warmup_seasons", ()),
        retrain_weeks=_tuple_int("retrain_weeks", (5, 10)),
        as_of_weekday=int(wf.get("as_of_weekday", 1)),
        as_of_hour=int(wf.get("as_of_hour", 6)),
        as_of_minute=int(wf.get("as_of_minute", 0)),
        as_of_tz=str(wf.get("as_of_tz", "America/New_York")),
        market_features_available=bool(wf.get("market_features_available", True)),
        preseason_priors=cast(PreseasonPriorsMode, str(wf.get("preseason_priors", "fitted"))),
        rating_updates=cast(RatingUpdatesMode, str(wf.get("rating_updates", "continual"))),
        mapping_layer=cast(MappingLayerMode, str(wf.get("mapping_layer", "ensemble"))),
        garbage_time_filter=bool(wf.get("garbage_time_filter", True)),
        market_feature_source=cast(
            MarketFeatureSource, str(wf.get("market_feature_source", "snapshots"))
        ),
        run_id=str(wf.get("run_id", payload.get("run_id", "default"))),
        ablation_id=str(wf.get("ablation_id", payload.get("ablation_id", "full"))),
        run_kind=cast(RunKind, run_kind_raw),
        include_continuity_in_headline=bool(wf.get("include_continuity_in_headline", False)),
        seed=int(wf.get("seed", 42)),
        model_version=str(wf.get("model_version", "production-v0")),
        min_train_games=int(wf.get("min_train_games", WalkForwardConfig.min_train_games)),
        max_zero_mu_rate=float(wf.get("max_zero_mu_rate", WalkForwardConfig.max_zero_mu_rate)),
        nnls_equal_weight_fallback=bool(wf.get("nnls_equal_weight_fallback", False)),
        enforce_prediction_quality_gate=bool(
            wf.get("enforce_prediction_quality_gate", run_kind_raw != "smoke")
        ),
    )


def _union_walkforward_from_run_set(payload: Mapping[str, Any]) -> WalkForwardConfig:
    runs = payload["runs"]
    assert isinstance(runs, list)
    test: set[int] = set()
    cont: set[int] = set()
    for raw in runs:
        if not isinstance(raw, Mapping):
            raise BacktestRunnerError("run_set entry must be a mapping")
        member = walkforward_config_from_mapping(dict(raw))
        test.update(member.test_seasons)
        cont.update(member.continuity_seasons)
    return WalkForwardConfig(
        test_seasons=tuple(sorted(test)),
        continuity_seasons=tuple(sorted(cont)),
        retrain_weeks=(5, 10),
        run_id=str(payload.get("run_set", "run_set")),
        ablation_id="run_set",
        market_features_available=False,
    )


def _weeks_by_season_for(
    cfg: WalkForwardConfig,
    games: pd.DataFrame | None,
) -> dict[int, list[int]]:
    weeks_by_season: dict[int, list[int]] = {}
    if games is not None and not games.empty:
        for season in cfg.all_replay_seasons():
            sub = games.loc[games["season"] == season]
            found = sorted(int(w) for w in sub["week"].unique())
            # Missing staged seasons still appear with the default week grid so
            # the wall-clock estimate is the full bill, not the materialized subset.
            weeks_by_season[season] = found if found else list(range(1, 16))
    else:
        for season in cfg.all_replay_seasons():
            weeks_by_season[season] = list(range(1, 16))
    return weeks_by_season


def _retrain_points_for(
    cfg: WalkForwardConfig,
    weeks_by_season: Mapping[int, list[int]],
) -> list[dict[str, int]]:
    retrain_points: list[dict[str, int]] = []
    for season in cfg.all_replay_seasons():
        retrain_points.append({"season": season, "week": 0})
        for week in cfg.retrain_weeks:
            if week in weeks_by_season.get(season, []):
                retrain_points.append({"season": season, "week": int(week)})
    return retrain_points


def _estimate_wall_clock_sec(n_weeks: int, n_retrain: int) -> float:
    return float(n_weeks * SEC_PER_WEEK_FULL + n_retrain * SEC_PER_RETRAIN_FULL)


def plan_backtest(
    config_name: str,
    *,
    games: pd.DataFrame | None = None,
    config_payload: Mapping[str, Any] | None = None,
) -> BacktestPlan | BacktestRunSetPlan:
    """Printable run plan — seasons, weeks, retrain points, estimated wall clock.

    When ``config_payload`` (or the loaded config) contains a ``runs:`` list,
    returns a :class:`BacktestRunSetPlan` with per-run and total estimates.
    """
    payload = (
        dict(config_payload) if config_payload is not None else load_backtest_config(config_name)
    )
    if is_run_set_config(payload):
        return plan_backtest_run_set(config_name, games=games, config_payload=payload)

    cfg = walkforward_config_from_mapping(payload)
    cfg.validate_ablations()

    weeks_by_season = _weeks_by_season_for(cfg, games)
    retrain_points = _retrain_points_for(cfg, weeks_by_season)
    n_weeks = sum(len(v) for v in weeks_by_season.values())
    n_retrain = len(retrain_points)
    est = _estimate_wall_clock_sec(n_weeks, n_retrain)

    return BacktestPlan(
        config_name=str(config_name),
        run_id=cfg.run_id,
        ablation_id=cfg.ablation_id,
        seasons=cfg.all_replay_seasons(),
        weeks_by_season=weeks_by_season,
        retrain_points=retrain_points,
        n_week_units=n_weeks,
        n_retrain_points=n_retrain,
        estimated_wall_clock_sec=float(est),
        ablation_settings=cfg.ablation_settings(),
        stack=str(payload.get("stack", "fundamental")),
    )


def plan_backtest_run_set(
    config_name: str,
    *,
    games: pd.DataFrame | None = None,
    config_payload: Mapping[str, Any] | None = None,
) -> BacktestRunSetPlan:
    """Plan each named run in a run-set config; report per-run and total."""
    payload = (
        dict(config_payload) if config_payload is not None else load_backtest_config(config_name)
    )
    if not is_run_set_config(payload):
        raise BacktestRunnerError(f"not a run-set config: {config_name}")
    runs_raw = payload["runs"]
    assert isinstance(runs_raw, list)
    plans: list[BacktestPlan] = []
    for raw in runs_raw:
        if not isinstance(raw, Mapping):
            raise BacktestRunnerError("run_set entry must be a mapping")
        entry = dict(raw)
        name = str(entry.get("name", entry.get("run_id", f"run_{len(plans)}")))
        member_plan = plan_backtest(name, games=games, config_payload=entry)
        if isinstance(member_plan, BacktestRunSetPlan):
            raise BacktestRunnerError("nested run sets are not supported")
        plans.append(member_plan)
    total = float(sum(p.estimated_wall_clock_sec for p in plans))
    return BacktestRunSetPlan(
        run_set_name=str(payload.get("run_set", config_name)),
        plans=tuple(plans),
        estimated_wall_clock_sec=total,
    )


def _unit_key(run_id: str, season: int, week: int) -> str:
    return f"{run_id}|{season}|{week}"


def _checkpoint_path(output_dir: Path) -> Path:
    return output_dir / "checkpoint.json"


def _load_checkpoint(output_dir: Path) -> set[str]:
    path = _checkpoint_path(output_dir)
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return set(str(x) for x in payload.get("completed_units", []))


def _save_checkpoint(output_dir: Path, completed: set[str]) -> None:
    path = _checkpoint_path(output_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"completed_units": sorted(completed)}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)


def require_complete_manifest(manifest: RunManifest) -> None:
    """Fail rather than write if any of the four hashes / seeds are missing."""
    required = {
        "git_sha": manifest.git_sha,
        "dvc_hash": manifest.dvc_hash,
        "config_hash": manifest.config_hash,
        "environment_lockfile_hash": manifest.environment_lockfile_hash,
    }
    empty = [k for k, v in required.items() if v is None or str(v).strip() == ""]
    if empty:
        raise ManifestError(f"manifest incomplete, refusing to write: missing {empty}")
    if not manifest.seed_manifest:
        raise ManifestError("manifest incomplete: seed_manifest empty")
    if "ablation_settings" not in manifest.extra and "ablations" not in manifest.extra:
        raise ManifestError("manifest incomplete: ablation settings missing from extra")


def run_backtest(
    config_name: str,
    *,
    games: pd.DataFrame,
    stack: ProductionStack | None = None,
    snapshots: pd.DataFrame | None = None,
    cfbd_lines: pd.DataFrame | None = None,
    observations: pd.DataFrame | None = None,
    priors_frame: pd.DataFrame | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    force: bool = False,
    tracking_uri: str | None = None,
    label: str = "",
    config_payload: Mapping[str, Any] | None = None,
    stack_kind: StackKind = "fundamental",
) -> BacktestRunResult:
    """Execute one named walk-forward run end to end (resumable by week)."""
    payload = (
        dict(config_payload) if config_payload is not None else load_backtest_config(config_name)
    )
    if is_run_set_config(payload):
        raise BacktestRunnerError(
            "run-set configs are plan-only; pass a single-run config to backtest run"
        )
    cfg = walkforward_config_from_mapping(payload)
    cfg.validate_ablations()
    seed_manifest = set_global_seed(cfg.seed)

    if stack is None:
        wf_payload = payload.get("walkforward", payload)
        n_mc = None
        n_ep = None
        if isinstance(wf_payload, Mapping):
            if "n_mc_draws" in wf_payload:
                n_mc = int(wf_payload["n_mc_draws"])
            if "n_epistemic_draws" in wf_payload:
                n_ep = int(wf_payload["n_epistemic_draws"])
        if "n_mc_draws" in payload:
            n_mc = int(payload["n_mc_draws"])
        if "n_epistemic_draws" in payload:
            n_ep = int(payload["n_epistemic_draws"])
        enforce = bool(payload.get("enforce_ablation_preconditions", False))
        stack = build_production_stack(
            cfg,
            kind=stack_kind,
            observations=observations,
            priors_frame=priors_frame,
            snapshots=snapshots,
            cfbd_lines=cfbd_lines,
            n_mc_draws=n_mc,
            n_epistemic_draws=n_ep,
            enforce_ablation_preconditions=enforce,
        )
        cfg = stack.config

    output_dir = Path(output_root) / cfg.run_id / cfg.ablation_id
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = set() if force else _load_checkpoint(output_dir)

    harness = WalkForwardHarness(
        config=cfg,
        predictor=stack.predictor,
        feature_provider=stack.feature_provider,
        rating_engine=stack.rating_engine,
    )

    # Full harness run is the atomic unit for correctness; week-level resume
    # stores per-week prediction shards and concatenates at the end.
    week_dir = output_dir / "weeks"
    week_dir.mkdir(parents=True, exist_ok=True)

    # If all units already complete and not forcing, load and return.
    planned = plan_backtest(config_name, games=games, config_payload=payload)
    if isinstance(planned, BacktestRunSetPlan):
        raise BacktestRunnerError("internal error: run-set plan in single-run path")
    plan = planned
    all_units = {
        _unit_key(cfg.run_id, season, week)
        for season, weeks in plan.weeks_by_season.items()
        for week in weeks
    }
    resumed = 0
    if not force and all_units and all_units <= completed:
        preds_path = output_dir / "predictions.parquet"
        if preds_path.is_file():
            predictions = pd.read_parquet(preds_path)
            manifest = _read_or_build_manifest(output_dir, cfg, seed_manifest, plan)
            return BacktestRunResult(
                run_id=cfg.run_id,
                ablation_id=cfg.ablation_id,
                predictions=predictions,
                manifest=manifest,
                output_dir=output_dir,
                resumed_units=len(completed),
                completed_units=len(completed),
                label=label,
            )

    t0 = time.perf_counter()
    result: WalkForwardResult = harness.run(
        games,
        snapshots=snapshots,
        cfbd_lines=cfbd_lines,
    )
    elapsed = time.perf_counter() - t0

    # Persist per-week shards atomically + checkpoint.
    predictions = result.predictions
    if not predictions.empty:
        from ncaa_quant.evaluation.production_stack import validate_prediction_distribution

        # Market baselines for metric suite (de-vigged −110/−110 ⇒ fair 0.5).
        if "p_mkt_ats_home" not in predictions.columns:
            predictions = predictions.copy()
            predictions["p_mkt_ats_home"] = 0.5
            predictions["p_mkt_ou_over"] = 0.5
            if "spread_close" in predictions.columns:
                spread = pd.to_numeric(predictions["spread_close"], errors="coerce")
                predictions["p_mkt_ml_home"] = 1.0 / (1.0 + np.exp(spread / 10.0))

        validate_prediction_distribution(predictions)
        if cfg.enforce_prediction_quality_gate and result.quality_gate is None:
            assert_prediction_quality_gate(
                predictions,
                max_zero_mu_rate=cfg.max_zero_mu_rate,
                min_train_games=cfg.min_train_games,
                raise_on_fail=True,
            )
        for (season, week), chunk in predictions.groupby(["season", "week"], sort=True):
            key = _unit_key(cfg.run_id, int(season), int(week))
            shard = week_dir / f"season={int(season)}_week={int(week)}.parquet"
            if key in completed and not force:
                resumed += 1
                continue
            _atomic_write_parquet(chunk.reset_index(drop=True), shard)
            completed.add(key)
            _save_checkpoint(output_dir, completed)

    preds_path = output_dir / "predictions.parquet"
    _atomic_write_parquet(predictions, preds_path)

    seasons_executed = (
        sorted({int(s) for s in predictions["season"].unique()})
        if not predictions.empty
        else list(cfg.test_seasons)
    )
    nnls_reports = getattr(stack.predictor, "nnls_fold_reports", []) or []
    gate_payload = result.quality_gate.as_dict() if result.quality_gate is not None else {}
    extra = {
        "ablation_settings": json.dumps(cfg.ablation_settings(), sort_keys=True),
        "ablations": json.dumps(cfg.ablation_settings(), sort_keys=True),
        "seasons_executed": json.dumps(seasons_executed),
        "label": label or "",
        "wall_clock_sec": f"{elapsed:.3f}",
        "stack_kind": stack.kind,
        "run_kind": cfg.run_kind,
        "n_train_games_max": (
            str(int(predictions["n_train_games"].max()))
            if not predictions.empty and "n_train_games" in predictions.columns
            else "0"
        ),
        "quality_gate": json.dumps(gate_payload, sort_keys=True),
        "nnls_fold_reports": json.dumps(nnls_reports, sort_keys=True),
    }
    manifest = build_manifest(
        config={
            "config_name": config_name,
            "walkforward": {k: getattr(cfg, k) for k in cfg.__dataclass_fields__},
        },
        seed_manifest=seed_manifest,
        extra={k: str(v) for k, v in extra.items()},
    )
    require_complete_manifest(manifest)
    write_manifest(output_dir / "manifest.json", manifest)

    mlflow_run_id: str | None = None
    uri = tracking_uri or str(payload.get("tracking_uri", "file:./mlruns"))
    try:
        mlflow_run_id = log_evaluation_run(
            tracking_uri=uri,
            manifest=manifest,
            metrics={
                "n_predictions": float(len(predictions)),
                "wall_clock_sec": float(elapsed),
            },
            metrics_by_season={
                int(s): {
                    "n_predictions": float((predictions["season"] == s).sum()),
                }
                for s in seasons_executed
            }
            if not predictions.empty
            else None,
            report_paths=[preds_path, output_dir / "manifest.json"],
            experiment_name=str(payload.get("experiment_name", "ncaa-quant-backtest")),
            run_name=f"{cfg.run_id}:{cfg.ablation_id}",
            tags={
                "run_id": cfg.run_id,
                "ablation_id": cfg.ablation_id,
                "label": label or "backtest",
            },
        )
    except Exception as exc:  # noqa: BLE001 — tracking must not kill the run artifact
        log.warning("mlflow_log_failed", error=str(exc))

    log.info(
        "backtest_complete",
        run_id=cfg.run_id,
        ablation_id=cfg.ablation_id,
        n_predictions=len(predictions),
        label=label,
        mlflow_run_id=mlflow_run_id,
    )
    return BacktestRunResult(
        run_id=cfg.run_id,
        ablation_id=cfg.ablation_id,
        predictions=predictions,
        manifest=manifest,
        output_dir=output_dir,
        mlflow_run_id=mlflow_run_id,
        resumed_units=resumed,
        completed_units=len(completed),
        label=label,
    )


def _read_or_build_manifest(
    output_dir: Path,
    cfg: WalkForwardConfig,
    seed_manifest: Any,
    plan: BacktestPlan,
) -> RunManifest:
    path = output_dir / "manifest.json"
    if path.is_file():
        from ncaa_quant.registry.manifest import read_manifest

        return read_manifest(path)
    return build_manifest(
        config={"walkforward": {k: getattr(cfg, k) for k in cfg.__dataclass_fields__}},
        seed_manifest=seed_manifest,
        extra={
            "ablation_settings": json.dumps(cfg.ablation_settings(), sort_keys=True),
            "ablations": json.dumps(cfg.ablation_settings(), sort_keys=True),
            "seasons_executed": json.dumps(list(plan.seasons)),
        },
    )


def load_staged_games(staged_root: Path | str, seasons: Sequence[int]) -> pd.DataFrame:
    """Load staged games partitions for ``seasons``."""
    from ncaa_quant.data.storage import ParquetStore

    store = ParquetStore(staged_root)
    frames: list[pd.DataFrame] = []
    for season in seasons:
        paths = store._matching_paths("games", {"season": int(season)})  # noqa: SLF001
        for path in paths:
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame()
    games = pd.concat(frames, ignore_index=True)
    if "event_time" not in games.columns and "start_date" in games.columns:
        games["event_time"] = pd.to_datetime(games["start_date"], utc=True)
    return games
