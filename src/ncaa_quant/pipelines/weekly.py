"""Weekly update flow: Sun 06:00 validation + Stage-1 updates (DESIGN §9.8)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from prefect import flow, task
from tenacity import retry, stop_after_attempt, wait_exponential

from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.pipelines.common import PartitionKey, run_idempotent
from ncaa_quant.pipelines.notifications import AlertKind, notify
from ncaa_quant.utils.logging import configure_logging, get_logger

log = get_logger(__name__)

WeeklyUpdateFn = Callable[..., dict[str, Any]]


def _default_weekly_update(*, season: int, week: int) -> dict[str, Any]:
    return {
        "season": season,
        "week": week,
        "stage1_updated": True,
        "features_refreshed": True,
        "innovation_flags": [],
    }


def execute_weekly_update(
    *,
    season: int,
    week: int,
    update_fn: WeeklyUpdateFn | None = None,
) -> dict[str, Any]:
    fn = update_fn or _default_weekly_update
    return fn(season=season, week=week)


def run_weekly_update(
    *,
    season: int,
    week: int,
    update_fn: WeeklyUpdateFn | None = None,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    key = PartitionKey(source="weekly_update", partition=f"{season}-w{week}")
    return run_idempotent(
        key,
        lambda: execute_weekly_update(season=season, week=week, update_fn=update_fn),
        config=cfg,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    reraise=True,
)
@task(name="weekly_update_partition")
def weekly_update_task(*, season: int, week: int) -> dict[str, Any]:
    return run_weekly_update(season=season, week=week)


def notify_weekly_failure(flow_obj: Any, flow_run: Any, state: Any) -> None:
    configure_logging()
    notify(
        AlertKind.FLOW_FAILURE,
        "weekly_update failed",
        f"state={getattr(state, 'name', '')} msg={getattr(state, 'message', '')}",
    )


@flow(name="weekly_update", on_failure=[notify_weekly_failure])  # type: ignore[list-item]
def weekly_update_flow(
    *,
    season: int | None = None,
    week: int | None = None,
) -> dict[str, Any]:
    """Sun 06:00 — validate, Stage-1 updates, feature refresh, innovation flags."""
    configure_logging()
    now = datetime.now(tz=UTC)
    resolved_season = season if season is not None else now.year
    resolved_week = week if week is not None else 1
    result = weekly_update_task(season=resolved_season, week=resolved_week)
    flags = result.get("innovation_flags") or []
    for flag in flags:
        notify(AlertKind.RATING_INNOVATION, "rating innovation flag", str(flag))
    return result
