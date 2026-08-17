"""Prediction publish flow with STALE mode (DESIGN §9.8, §10)."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
from prefect import flow, task
from tenacity import retry, stop_after_attempt, wait_exponential

from ncaa_quant.betting.filters import BetCandidate, FilterReason, evaluate_filters
from ncaa_quant.config import AppConfig, BettingConfig, load_config
from ncaa_quant.pipelines.common import PartitionKey, run_idempotent
from ncaa_quant.pipelines.notifications import AlertKind, Notifier, build_notifier, notify
from ncaa_quant.pipelines.stale import (
    IngestFailure,
    StaleContext,
    StampedPrediction,
    resolve_stale_context,
    stamp_predictions,
)
from ncaa_quant.utils.logging import configure_logging, get_logger

log = get_logger(__name__)

OddsIngestFn = Callable[[], dict[str, Any]]
PredictFn = Callable[[StaleContext], list[dict[str, Any]]]
BuildCandidatesFn = Callable[[list[StampedPrediction]], list[BetCandidate]]

LOCKBOX_SEASON = 2025
_REPO_ROOT = Path(__file__).resolve().parents[3]
#: Stored ProductionEnsemblePredictor.predict() frames from the champion walkforward
#: (task23 fundamental reduced v2). No pickled mapping layer exists on disk.
_CHAMPION_WEEK_DIR = (
    _REPO_ROOT / "data" / "backtests" / "task23_fundamental_reduced_v2" / "full" / "weeks"
)
_FIXTURE_WEEK5_PUBLISHED_AT = datetime(2024, 9, 24, 6, 0, 0, tzinfo=UTC)


class LockboxSeasonError(ValueError):
    """predict_fn refused a lockbox season (2025 is never evaluated)."""


class RefreshKind(StrEnum):
    """predict_publish schedule variants (§9.8)."""

    TUESDAY_PRIMARY = "tuesday_primary"
    DAILY_REFRESH = "daily_refresh"
    T_MINUS_6H = "t_minus_6h"
    T_MINUS_1H = "t_minus_1h"


def check_odds_cadence(
    *,
    raw_root: Path | str,
    expected_per_day: int,
    tolerance: int,
    window_hours: int = 24,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return cadence stats; caller decides whether to alert."""
    root = Path(raw_root)
    clock = now if now is not None else datetime.now(tz=UTC)
    cutoff = clock.timestamp() - window_hours * 3600
    count = 0
    if root.is_dir():
        for path in root.rglob("*.json"):
            if path.stat().st_mtime >= cutoff:
                count += 1
    minimum = max(0, expected_per_day - tolerance)
    shortfall = count < minimum
    return {
        "snapshots_24h": count,
        "expected_minimum": minimum,
        "shortfall": shortfall,
    }


def production_week_predictions_path(season: int, week: int) -> Path:
    """On-disk champion-week frame written by WalkForwardHarness after ``predict()``."""
    return _CHAMPION_WEEK_DIR / f"season={season}_week={week}.parquet"


def _as_stamp_float(value: Any) -> float:
    """Coerce None / NA to NaN so :func:`stamp_predictions` can ``float()`` it."""
    if value is None:
        return float("nan")
    try:
        if pd.isna(value):
            return float("nan")
    except (TypeError, ValueError):
        pass
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    if math.isnan(out) or math.isinf(out):
        return float("nan")
    return out


def _alias_stamp_columns(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep production names; add stub aliases ``stamp_predictions`` requires.

    Rename itself lives in ``export.build_game_prediction`` (``_field`` first-present).
    """
    out = dict(row)
    gid = out.get("game_id")
    if gid is not None and str(gid) != "":
        try:
            out["game_id"] = str(int(gid))
        except (TypeError, ValueError):
            out["game_id"] = str(gid)
    if "mu_margin" not in out or out.get("mu_margin") is None:
        out["mu_margin"] = out.get("pred_margin")
    if "sigma_margin" not in out or out.get("sigma_margin") is None:
        out["sigma_margin"] = out.get("sigma_m")
    out["mu_margin"] = _as_stamp_float(out.get("mu_margin"))
    out["sigma_margin"] = _as_stamp_float(out.get("sigma_margin"))
    return out


def load_production_prediction_rows(season: int, week: int) -> list[dict[str, Any]]:
    """Return production walkforward rows for ``(season, week)`` plus stamp aliases.

    This is the stored ``ProductionEnsemblePredictor.predict()`` frame from the
    champion reduced-v2 walkforward. Mapping-layer heads are not pickled; calling
    ``predict()`` live would require a mapping-layer retrain (forbidden here).
    Season 2025 is refused. The parquet is 2024-only for the W9-P oracle.
    """
    if season == LOCKBOX_SEASON:
        msg = (
            f"season {LOCKBOX_SEASON} is lockbox; predict_fn refuses it "
            "(producing predictions for 2025 is not permitted)"
        )
        raise LockboxSeasonError(msg)
    path = production_week_predictions_path(season, week)
    if not path.is_file():
        msg = f"champion week predictions missing: {path}"
        raise FileNotFoundError(msg)
    frame = pd.read_parquet(path)
    if "season" in frame.columns:
        frame = frame.loc[frame["season"].astype(int) == int(season)]
    if "week" in frame.columns:
        frame = frame.loc[frame["week"].astype(int) == int(week)]
    if frame.empty:
        msg = f"champion week predictions empty after season/week filter: {path}"
        raise FileNotFoundError(msg)
    rows = [_alias_stamp_columns(rec) for rec in frame.to_dict(orient="records")]
    first = rows[0]
    log.info(
        "production_predict_loaded",
        season=season,
        week=week,
        path=str(path),
        n=len(rows),
        champion_version=3,
        model_version=first.get("model_version"),
        run_id=first.get("run_id"),
    )
    return rows


def _default_predict_for(season: int, week: int) -> PredictFn:
    def _predict(_stale_ctx: StaleContext) -> list[dict[str, Any]]:
        return load_production_prediction_rows(season, week)

    return _predict


def _default_build_candidates(predictions: list[StampedPrediction]) -> list[BetCandidate]:
    out: list[BetCandidate] = []
    for p in predictions:
        out.append(
            BetCandidate(
                game_id=p.game_id,
                market="side",
                edge=0.05,
                expected_value=0.02,
                is_stale=p.is_stale,
                qb_status_known=True,
                is_bowl=False,
                model_market_residual_points=2.0,
            )
        )
    return out


def apply_bet_filters(
    candidates: Sequence[BetCandidate],
    *,
    betting_config: BettingConfig | None = None,
) -> tuple[list[BetCandidate], list[tuple[BetCandidate, tuple[FilterReason, ...]]]]:
    """Run §12 filters; return accepted and rejected with reasons."""
    cfg = betting_config or load_config().betting
    accepted: list[BetCandidate] = []
    rejected: list[tuple[BetCandidate, tuple[FilterReason, ...]]] = []
    for cand in candidates:
        result = evaluate_filters(cand, cfg)
        if result.accepted:
            accepted.append(cand)
        else:
            rejected.append((cand, result.reasons))
    return accepted, rejected


def execute_predict_publish(
    *,
    season: int,
    week: int,
    refresh_kind: str,
    odds_ingest_fn: OddsIngestFn | None = None,
    predict_fn: PredictFn | None = None,
    build_candidates_fn: BuildCandidatesFn | None = None,
    simulate_ingest_failure: bool = False,
    config: AppConfig | None = None,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    """Core predict/publish body (testable without Prefect parameter schema)."""
    cfg = config or load_config()
    ingest_failed = False
    ingest_error: str | None = None
    raw_root = Path(cfg.paths.raw_dir) / "odds_api"

    if simulate_ingest_failure:
        ingest_failed = True
        ingest_error = "simulated ingest failure (chaos test)"
    elif odds_ingest_fn is not None:
        try:
            odds_ingest_fn()
        except Exception as exc:
            ingest_failed = True
            ingest_error = str(exc)
            log.warning("odds_ingest_failed_entering_stale_mode", error=ingest_error)

    stale_ctx = (
        resolve_stale_context(ingest_failed=ingest_failed, raw_root=raw_root, config=cfg)
        if ingest_failed
        else StaleContext(sources=(), use_last_good=False)
    )

    if ingest_failed and not stale_ctx.use_last_good:
        raise IngestFailure(ingest_error or "ingest failed with no fallback")

    predict = predict_fn or _default_predict_for(season, week)
    raw_preds = predict(stale_ctx)
    stamped = stamp_predictions(raw_preds, stale_ctx)

    build = build_candidates_fn or _default_build_candidates
    candidates = build(stamped)
    accepted, rejected = apply_bet_filters(candidates, betting_config=cfg.betting)

    stale_rejections = [
        (c.game_id, list(reasons))
        for c, reasons in rejected
        if FilterReason.STALE_INPUTS in reasons
    ]

    cadence = check_odds_cadence(
        raw_root=raw_root,
        expected_per_day=cfg.pipeline.odds_snapshots_per_day,
        tolerance=cfg.pipeline.odds_cadence_tolerance,
    )
    n = notifier or build_notifier(cfg)
    if cadence["shortfall"]:
        notify(
            AlertKind.CADENCE_SHORTFALL,
            "odds cadence shortfall",
            f"snapshots_24h={cadence['snapshots_24h']} expected_min={cadence['expected_minimum']}",
            config=cfg,
            notifier=n,
        )

    for cand in accepted:
        notify(
            AlertKind.NEW_BET_CANDIDATE,
            f"bet candidate {cand.game_id}",
            f"edge={cand.edge:.3f} market={cand.market}",
            config=cfg,
            notifier=n,
        )

    result: dict[str, Any] = {
        "season": season,
        "week": week,
        "refresh_kind": refresh_kind,
        "ingest_failed": ingest_failed,
        "ingest_error": ingest_error,
        "stale": stale_ctx.to_dict(),
        "predictions": [p.to_dict() for p in stamped],
        "prediction_rows": list(raw_preds),
        "n_candidates": len(candidates),
        "n_accepted": len(accepted),
        "n_rejected": len(rejected),
        "stale_rejections": stale_rejections,
        "cadence": cadence,
        "last_good_at": (
            stale_ctx.sources[0].last_good_at.isoformat() if stale_ctx.sources else None
        ),
    }

    if cfg.webapp.export_enabled:
        try:
            from ncaa_quant.webapp.export import export_publish_artifacts

            export_out = export_publish_artifacts(result, config=cfg, push=True, notifier=n)
            result["webapp_export"] = {"ok": True, "push": export_out.get("push")}
        except Exception as exc:
            log.warning("webapp_export_failed", error=str(exc))
            notify(
                AlertKind.WEBAPP_EXPORT_FAILURE,
                "Ridge artifact export/push failed",
                str(exc),
                config=cfg,
                notifier=n,
            )
            result["webapp_export"] = {"ok": False, "error": str(exc)}

    return result


def _run_helper_publish(
    *,
    season: int,
    week: int,
    refresh_kind: str,
    odds_ingest_fn: OddsIngestFn | None = None,
    predict_fn: PredictFn | None = None,
    simulate_ingest_failure: bool = False,
    config: AppConfig | None = None,
    notifier: Notifier | None = None,
    publish_scope: str = "sandbox",
) -> dict[str, Any]:
    """Run predict/publish for test helpers; default scope is non-live ``sandbox/``.

    Test and chaos helpers must not write ``latest/`` or ``v*/`` even when
    ``export_enabled`` is true on the workstation. The pipeline body runs with
    export suppressed; artifacts are pushed explicitly under ``publish_scope``.
    """
    cfg = config or load_config()
    export_wanted = cfg.webapp.export_enabled
    inner_cfg = cfg
    if export_wanted and publish_scope != "live":
        inner_cfg = cfg.model_copy(
            update={"webapp": cfg.webapp.model_copy(update={"export_enabled": False})}
        )

    result = run_predict_publish(
        season=season,
        week=week,
        refresh_kind=refresh_kind,
        odds_ingest_fn=odds_ingest_fn,
        predict_fn=predict_fn,
        simulate_ingest_failure=simulate_ingest_failure,
        config=inner_cfg,
        notifier=notifier,
    )

    if not export_wanted or publish_scope == "live":
        return result

    n = notifier or build_notifier(cfg)
    try:
        from ncaa_quant.webapp.export import SCHEMA_VERSION, export_publish_artifacts
        from ncaa_quant.webapp.push import push_artifacts_to_r2

        export_out = export_publish_artifacts(result, config=cfg, push=False, notifier=n)
        push_result = push_artifacts_to_r2(
            export_out["artifacts"],
            season=season,
            week=week,
            refresh_kind=refresh_kind,
            schema_version=SCHEMA_VERSION,
            publish_scope="sandbox",
            config=cfg,
            notifier=n,
            skip_revalidation=True,
        )
        result["webapp_export"] = {"ok": True, "push": push_result, "publish_scope": publish_scope}
    except Exception as exc:
        log.warning("webapp_export_failed", error=str(exc))
        notify(
            AlertKind.WEBAPP_EXPORT_FAILURE,
            "Ridge artifact export/push failed",
            str(exc),
            config=cfg,
            notifier=n,
        )
        result["webapp_export"] = {"ok": False, "error": str(exc)}

    return result


def run_predict_publish(
    *,
    season: int,
    week: int,
    refresh_kind: str,
    odds_ingest_fn: OddsIngestFn | None = None,
    predict_fn: PredictFn | None = None,
    build_candidates_fn: BuildCandidatesFn | None = None,
    simulate_ingest_failure: bool = False,
    config: AppConfig | None = None,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    """Idempotent wrapper around :func:`execute_predict_publish`."""
    cfg = config or load_config()
    key = PartitionKey(source="predict_publish", partition=f"{season}-w{week}-{refresh_kind}")

    def _run() -> dict[str, Any]:
        return execute_predict_publish(
            season=season,
            week=week,
            refresh_kind=refresh_kind,
            odds_ingest_fn=odds_ingest_fn,
            predict_fn=predict_fn,
            build_candidates_fn=build_candidates_fn,
            simulate_ingest_failure=simulate_ingest_failure,
            config=cfg,
            notifier=notifier,
        )

    return run_idempotent(key, _run, config=cfg)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    reraise=True,
)
@task(name="predict_publish_partition")
def predict_publish_task(
    *,
    season: int,
    week: int,
    refresh_kind: str,
    simulate_ingest_failure: bool = False,
) -> dict[str, Any]:
    """Generate predictions; STALE-stamp and suppress bets on ingest failure."""
    return run_predict_publish(
        season=season,
        week=week,
        refresh_kind=refresh_kind,
        simulate_ingest_failure=simulate_ingest_failure,
    )


def notify_predict_failure(flow_obj: Any, flow_run: Any, state: Any) -> None:
    configure_logging()
    notify(
        AlertKind.FLOW_FAILURE,
        "predict_publish failed",
        f"state={getattr(state, 'name', '')} msg={getattr(state, 'message', '')}",
    )


@flow(name="predict_publish", on_failure=[notify_predict_failure])  # type: ignore[list-item]
def predict_publish_flow(
    *,
    season: int | None = None,
    week: int | None = None,
    refresh_kind: str = RefreshKind.TUESDAY_PRIMARY,
    simulate_ingest_failure: bool = False,
) -> dict[str, Any]:
    """Tue 06:00 + Thu–Sat refresh — predictions, edges, internal report."""
    configure_logging()
    now = datetime.now(tz=UTC)
    resolved_season = season if season is not None else now.year
    resolved_week = week if week is not None else 1
    log.info(
        "predict_publish_start",
        season=resolved_season,
        week=resolved_week,
        refresh_kind=refresh_kind,
    )
    return predict_publish_task(
        season=resolved_season,
        week=resolved_week,
        refresh_kind=refresh_kind,
        simulate_ingest_failure=simulate_ingest_failure,
    )


def run_fixture_week_publish(
    *,
    season: int = 2024,
    week: int = 5,
    config: AppConfig | None = None,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    """End-to-end dry-run body for fixture week (test / acceptance helper)."""

    def _predict(_ctx: StaleContext) -> list[dict[str, Any]]:
        return [
            {"game_id": "g-fix-1", "mu_margin": 3.5, "sigma_margin": 14.0},
            {"game_id": "g-fix-2", "mu_margin": -1.0, "sigma_margin": 13.5},
        ]

    def _ingest() -> dict[str, Any]:
        return {"rows_written": 100}

    return _run_helper_publish(
        season=season,
        week=week,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        odds_ingest_fn=_ingest,
        predict_fn=_predict,
        config=config,
        notifier=notifier,
    )


def run_chaos_stale_publish(
    *,
    raw_root: Path,
    season: int = 2024,
    week: int = 5,
    last_good_at: datetime,
    config: AppConfig | None = None,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    """Chaos test helper: kill ingestion, publish STALE, suppress bets."""
    raw_root.mkdir(parents=True, exist_ok=True)
    day = last_good_at.date().isoformat()
    stamp = last_good_at.strftime("%Y%m%dT%H%M%S%fZ")
    (raw_root / day).mkdir(parents=True, exist_ok=True)
    (raw_root / day / f"{stamp}.json").write_text("[]\n", encoding="utf-8")

    def _predict(_ctx: StaleContext) -> list[dict[str, Any]]:
        return [{"game_id": "g-chaos-1", "mu_margin": 2.0, "sigma_margin": 14.0}]

    return _run_helper_publish(
        season=season,
        week=week,
        refresh_kind=RefreshKind.DAILY_REFRESH,
        predict_fn=_predict,
        simulate_ingest_failure=True,
        config=config,
        notifier=notifier,
    )


def _isolated_publish_config(config: AppConfig, state_dir: Path) -> AppConfig:
    """Redirect hysteresis / jsonl / ledger writes; force export off (no R2)."""
    return config.model_copy(
        update={
            "webapp": config.webapp.model_copy(
                update={
                    "export_enabled": False,
                    "tier_state_path": str(state_dir / "tier_state.json"),
                    "tier_changes_path": str(state_dir / "tier_changes.jsonl"),
                }
            ),
            "pipeline": config.pipeline.model_copy(
                update={
                    "idempotency_dir": str(state_dir / "pipeline_state"),
                    "dead_letter_dir": str(state_dir / "pipeline_state" / "dead_letter"),
                }
            ),
        }
    )


def run_isolated_week_export(
    *,
    season: int = 2024,
    week: int = 5,
    refresh_kind: str = RefreshKind.TUESDAY_PRIMARY,
    output_dir: Path | str,
    state_dir: Path | str,
    published_at: datetime | None = None,
    config: AppConfig | None = None,
    notifier: Notifier | None = None,
    predict_fn: PredictFn | None = None,
) -> dict[str, Any]:
    """Run wired ``predict_fn`` → local artifacts. No R2, no real tier files.

    Uses :func:`execute_predict_publish` (not the idempotent wrapper) so the
    real ``data/pipeline_state/idempotency.json`` is not touched. Export is
    disabled on the config; artifacts are written only under ``output_dir``.
    ``predict_fn`` defaults to the parquet loader; pass a live fitted predictor
    callable to verify the serialized champion without reading week parquet.
    """
    from ncaa_quant.webapp.export import export_publish_artifacts

    out_dir = Path(output_dir)
    iso_state = Path(state_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    iso_state.mkdir(parents=True, exist_ok=True)

    base = config or load_config()
    cfg = _isolated_publish_config(base, iso_state)
    clock = published_at
    if clock is None and season == 2024 and week == 5:
        clock = _FIXTURE_WEEK5_PUBLISHED_AT
    if clock is None:
        clock = datetime.now(tz=UTC)

    print(f"W9-P isolated export_enabled={cfg.webapp.export_enabled}")
    print(f"W9-P tier_state_path={cfg.webapp.tier_state_path}")
    print(f"W9-P tier_changes_path={cfg.webapp.tier_changes_path}")
    print(f"W9-P season={season} week={week} refresh_kind={refresh_kind}")

    result = execute_predict_publish(
        season=season,
        week=week,
        refresh_kind=refresh_kind,
        config=cfg,
        notifier=notifier,
        predict_fn=predict_fn,
    )
    first = (result.get("prediction_rows") or [{}])[0]
    print("W9-P champion_version=3")
    print(f"W9-P model_version={first.get('model_version')}")
    print(f"W9-P run_id={first.get('run_id')}")
    print(f"W9-P n_prediction_rows={len(result.get('prediction_rows') or [])}")

    export_out = export_publish_artifacts(
        result,
        config=cfg,
        published_at=clock,
        push=False,
        notifier=notifier,
    )
    written: dict[str, str] = {}
    for name, body in (export_out.get("artifacts") or {}).items():
        dest = out_dir / str(name)
        dest.write_text(str(body), encoding="utf-8")
        written[str(name)] = str(dest)
        print(f"W9-P wrote {dest}")

    identity = (export_out.get("week_predictions") or {}).get("model_identity") or {}
    print(f"W9-P artifact_model_identity={json.dumps(identity, sort_keys=True)}")
    print("W9-P push=False (R2 disabled; no upload)")

    return {
        "result": result,
        "export": export_out,
        "written": written,
        "export_enabled": cfg.webapp.export_enabled,
        "state_dir": str(iso_state),
        "output_dir": str(out_dir),
    }
