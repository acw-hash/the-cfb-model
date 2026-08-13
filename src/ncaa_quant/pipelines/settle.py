"""CLV settlement flow: Sun (DESIGN §9.8, §12)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from prefect import flow, task
from tenacity import retry, stop_after_attempt, wait_exponential

from ncaa_quant.betting.clv import RecommendationRecord, WeeklyClvReport, settle_week
from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.pipelines.common import PartitionKey, run_idempotent
from ncaa_quant.pipelines.notifications import AlertKind, notify
from ncaa_quant.utils.logging import configure_logging, get_logger

log = get_logger(__name__)

SettleFn = Callable[..., tuple[list[Any], WeeklyClvReport]]


def _default_settle(
    recommendations: Sequence[RecommendationRecord],
    closes: Mapping[str, Any],
    *,
    season: int,
    week: int,
) -> tuple[list[Any], WeeklyClvReport]:
    return settle_week(recommendations, closes, season=season, week=week)


def execute_settle_clv(
    *,
    season: int,
    week: int,
    recommendations: Sequence[RecommendationRecord],
    closes: Mapping[str, Any],
    settle_fn: SettleFn | None = None,
) -> dict[str, Any]:
    fn = settle_fn or _default_settle
    settled, report = fn(recommendations, closes, season=season, week=week)
    return {
        "season": season,
        "week": week,
        "n_settled": len(settled),
        "mean_clv": report.mean_clv,
        "n_bets": report.n_bets,
    }


def run_settle_clv(
    *,
    season: int,
    week: int,
    recommendations: Sequence[RecommendationRecord],
    closes: Mapping[str, Any],
    settle_fn: SettleFn | None = None,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    key = PartitionKey(source="settle_clv", partition=f"{season}-w{week}")
    return run_idempotent(
        key,
        lambda: execute_settle_clv(
            season=season,
            week=week,
            recommendations=recommendations,
            closes=closes,
            settle_fn=settle_fn,
        ),
        config=cfg,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    reraise=True,
)
@task(name="settle_clv_partition")
def settle_clv_task(*, season: int, week: int) -> dict[str, Any]:
    return run_settle_clv(season=season, week=week, recommendations=(), closes={})


def notify_settle_failure(flow_obj: Any, flow_run: Any, state: Any) -> None:
    configure_logging()
    notify(
        AlertKind.FLOW_FAILURE,
        "settle_clv failed",
        f"state={getattr(state, 'name', '')} msg={getattr(state, 'message', '')}",
    )


@flow(name="settle_clv", on_failure=[notify_settle_failure])  # type: ignore[list-item]
def settle_clv_flow(
    *,
    season: int | None = None,
    week: int | None = None,
) -> dict[str, Any]:
    """Sun — settle the week's bets and emit CLV summary."""
    configure_logging()
    now = datetime.now(tz=UTC)
    resolved_season = season if season is not None else now.year
    resolved_week = week if week is not None else 1
    result = settle_clv_task(season=resolved_season, week=resolved_week)
    notify(
        AlertKind.CLV_WEEKLY_SUMMARY,
        f"CLV summary {resolved_season} w{resolved_week}",
        f"n_bets={result.get('n_bets')} mean_clv={result.get('mean_clv')}",
    )
    return result
