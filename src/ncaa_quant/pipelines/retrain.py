"""Retrain gate flow: gated Weeks ~5 and ~10 (DESIGN §9.7, §9.8)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from prefect import flow, task
from tenacity import retry, stop_after_attempt, wait_exponential

from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.pipelines.common import PartitionKey, run_idempotent
from ncaa_quant.pipelines.gates import PromotionGateDecision, evaluate_promotion_gate
from ncaa_quant.pipelines.notifications import AlertKind, notify
from ncaa_quant.utils.logging import configure_logging, get_logger

log = get_logger(__name__)

RetrainFn = Callable[..., dict[str, Any]]


def is_retrain_gate_week(week: int, config: AppConfig | None = None) -> bool:
    cfg = config or load_config()
    return week in cfg.pipeline.retrain_gate_weeks


def _default_retrain(*, season: int, week: int) -> dict[str, Any]:
    return {
        "season": season,
        "week": week,
        "candidate_version": f"candidate-{season}-w{week}",
        "gate_passed": False,
        "comparison_report_path": None,
    }


def execute_retrain_gate(
    *,
    season: int,
    week: int,
    manual_approve: bool = False,
    force: bool = False,
    retrain_fn: RetrainFn | None = None,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    if not is_retrain_gate_week(week, cfg):
        return {
            "skipped": True,
            "reason": f"week {week} not in retrain_gate_weeks",
            "season": season,
            "week": week,
        }
    body = (retrain_fn or _default_retrain)(season=season, week=week)
    decision: PromotionGateDecision = evaluate_promotion_gate(
        candidate_version=str(body.get("candidate_version", "unknown")),
        gate_passed=bool(body.get("gate_passed", False)),
        manual_approve=manual_approve,
        force=force,
        comparison_report_path=body.get("comparison_report_path"),
    )
    return {
        "skipped": False,
        "season": season,
        "week": week,
        "retrain": body,
        "promotion": decision.to_dict(),
    }


def run_retrain_gate(
    *,
    season: int,
    week: int,
    manual_approve: bool = False,
    force: bool = False,
    retrain_fn: RetrainFn | None = None,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    key = PartitionKey(source="retrain_gate", partition=f"{season}-w{week}")
    return run_idempotent(
        key,
        lambda: execute_retrain_gate(
            season=season,
            week=week,
            manual_approve=manual_approve,
            force=force,
            retrain_fn=retrain_fn,
            config=cfg,
        ),
        config=cfg,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    reraise=True,
)
@task(name="retrain_gate_partition")
def retrain_gate_task(
    *,
    season: int,
    week: int,
    manual_approve: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    return run_retrain_gate(
        season=season,
        week=week,
        manual_approve=manual_approve,
        force=force,
    )


def notify_retrain_failure(flow_obj: Any, flow_run: Any, state: Any) -> None:
    configure_logging()
    notify(
        AlertKind.FLOW_FAILURE,
        "retrain_gate failed",
        f"state={getattr(state, 'name', '')} msg={getattr(state, 'message', '')}",
    )


@flow(name="retrain_gate", on_failure=[notify_retrain_failure])  # type: ignore[list-item]
def retrain_gate_flow(
    *,
    season: int | None = None,
    week: int | None = None,
    manual_approve: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Mon 06:00 (gate weeks) — candidate retrain + manual promotion gate."""
    configure_logging()
    now = datetime.now(tz=UTC)
    resolved_season = season if season is not None else now.year
    resolved_week = week if week is not None else 1
    return retrain_gate_task(
        season=resolved_season,
        week=resolved_week,
        manual_approve=manual_approve,
        force=force,
    )
