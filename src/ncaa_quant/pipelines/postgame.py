"""Post-game ingestion flow: Sat 23:30 + hourly to 03:00 (DESIGN §9.8, §10)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from prefect import flow, task
from tenacity import retry, stop_after_attempt, wait_exponential

from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.pipelines.common import PartitionKey, run_idempotent
from ncaa_quant.pipelines.notifications import AlertKind, notify
from ncaa_quant.quality.runner import QualityRunResult, run_quality
from ncaa_quant.utils.logging import configure_logging, get_logger

log = get_logger(__name__)

CfbdIncrementalFn = Callable[..., dict[str, Any]]
QualityFn = Callable[..., QualityRunResult]


def _default_cfbd_incremental(*, season: int, week: int) -> dict[str, Any]:
    from ncaa_quant.ingestion.cfbd import run_cfbd_incremental

    result = run_cfbd_incremental()
    return {
        "season": season,
        "week": week,
        "partitions_written": result.partitions_written,
        "rows_written": result.rows_written,
    }


def _default_quality(*, season: int) -> QualityRunResult:
    return run_quality((season,))


def execute_postgame_ingest(
    *,
    season: int,
    week: int,
    slot: str,
    cfbd_fn: CfbdIncrementalFn | None = None,
    quality_fn: QualityFn | None = None,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    ingest = cfbd_fn or _default_cfbd_incremental
    ingest_result = ingest(season=season, week=week)
    quality = quality_fn or _default_quality
    q_result = quality(season=season)
    return {
        "season": season,
        "week": week,
        "slot": slot,
        "ingest": ingest_result,
        "quality_quarantined": q_result.partitions_quarantined,
        "quality_hard_failures": q_result.hard_failure_count,
    }


def run_postgame_ingest(
    *,
    season: int,
    week: int,
    slot: str,
    cfbd_fn: CfbdIncrementalFn | None = None,
    quality_fn: QualityFn | None = None,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    key = PartitionKey(source="postgame_ingest", partition=f"{season}-w{week}-{slot}")
    return run_idempotent(
        key,
        lambda: execute_postgame_ingest(
            season=season,
            week=week,
            slot=slot,
            cfbd_fn=cfbd_fn,
            quality_fn=quality_fn,
            config=cfg,
        ),
        config=cfg,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    reraise=True,
)
@task(name="postgame_ingest_partition")
def postgame_ingest_task(
    *,
    season: int,
    week: int,
    slot: str,
) -> dict[str, Any]:
    return run_postgame_ingest(season=season, week=week, slot=slot)


def notify_postgame_failure(flow_obj: Any, flow_run: Any, state: Any) -> None:
    configure_logging()
    notify(
        AlertKind.FLOW_FAILURE,
        "postgame_ingest failed",
        f"state={getattr(state, 'name', '')} msg={getattr(state, 'message', '')}",
    )


@flow(name="postgame_ingest", on_failure=[notify_postgame_failure])  # type: ignore[list-item]
def postgame_ingest_flow(
    *,
    season: int | None = None,
    week: int | None = None,
    slot: str = "sat2330",
) -> dict[str, Any]:
    """Ingest finals + PBP; run quality gates (§9.8 Sat 23:30 / hourly Sun 03:00)."""
    configure_logging()
    now = datetime.now(tz=UTC)
    resolved_season = season if season is not None else now.year
    resolved_week = week if week is not None else 1
    log.info(
        "postgame_ingest_start",
        season=resolved_season,
        week=resolved_week,
        slot=slot,
    )
    result = postgame_ingest_task(season=resolved_season, week=resolved_week, slot=slot)
    if result.get("quality_hard_failures", 0) > 0:
        notify(
            AlertKind.QUALITY_GATE_FAILURE,
            "postgame quality gate failure",
            f"season={resolved_season} week={resolved_week} "
            f"hard_failures={result['quality_hard_failures']}",
        )
    return result
