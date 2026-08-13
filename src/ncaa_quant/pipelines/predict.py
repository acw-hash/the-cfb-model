"""Prediction publish flow with STALE mode (DESIGN §9.8, §10)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

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


def _default_predict(_stale_ctx: StaleContext) -> list[dict[str, Any]]:
    return []


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

    predict = predict_fn or _default_predict
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

            export_out = export_publish_artifacts(result, config=cfg, push=True)
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

    return run_predict_publish(
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

    return run_predict_publish(
        season=season,
        week=week,
        refresh_kind=RefreshKind.DAILY_REFRESH,
        predict_fn=_predict,
        simulate_ingest_failure=True,
        config=config,
        notifier=notifier,
    )
