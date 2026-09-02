"""Prefect flow for forward kickoff-aligned slot_close capture (V4-B1 / V4-B1a).

V4-B1a registers a fine-grained wall-clock cron that polls for due slots via
:func:`~ncaa_quant.pipelines.slot_close_schedule.execute_slot_close_poll`,
which delegates capture to
:func:`~ncaa_quant.ingestion.slot_close_capture.run_due_slot_close_captures`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from prefect import flow

from ncaa_quant.config import load_config
from ncaa_quant.pipelines.common import PartitionKey, run_idempotent
from ncaa_quant.pipelines.notifications import AlertKind, notify
from ncaa_quant.pipelines.slot_close_schedule import execute_slot_close_poll
from ncaa_quant.utils.logging import configure_logging, get_logger
from ncaa_quant.utils.timeutils import to_utc

log = get_logger(__name__)


def notify_slot_close_failure(
    flow_obj: Any,  # noqa: ARG001 — Prefect hook signature
    flow_run: Any,
    state: Any,
) -> None:
    configure_logging()
    notify(
        AlertKind.FLOW_FAILURE,
        "capture_slot_close failed",
        f"flow_run_id={getattr(flow_run, 'id', '')} "
        f"state={getattr(state, 'name', '')} msg={getattr(state, 'message', '')}",
    )


@flow(name="capture_slot_close", on_failure=[notify_slot_close_failure])  # type: ignore[list-item]
def capture_slot_close_flow(
    *,
    seasons: list[int] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Poll for due kickoff slots at ``as_of``; record missed past-kickoff slots."""
    configure_logging()
    cfg = load_config()
    now = as_of or datetime.now(tz=UTC)
    season_list = seasons if seasons is not None else [cfg.data.end_season]
    partition = to_utc(now).strftime("%Y%m%dT%H%M")
    key = PartitionKey(source="capture_slot_close", partition=partition)

    def _run() -> dict[str, Any]:
        poll = execute_slot_close_poll(
            seasons=season_list,
            as_of=now,
            config=cfg,
        )
        return {
            "run_id": poll.run_id,
            "as_of": poll.as_of.isoformat(),
            "seasons": season_list,
            "outcome": poll.outcome,
            "due_count": poll.due_count,
            "captured": poll.captured,
            "skipped": poll.skipped,
            "missed_recorded": poll.missed_recorded,
            "credits_spent": poll.credits_spent,
            "rows_written": poll.rows_written,
            "slot_outcomes": [
                {
                    "slot_id": s.slot_id,
                    "status": s.status,
                    "credits_charged": s.credits_charged,
                }
                for s in poll.slot_outcomes
            ],
        }

    out = run_idempotent(key, _run, config=cfg)
    log.info("capture_slot_close_complete", **out)
    return out


def serve_capture_slot_close() -> None:
    """Serve the poll flow on the configured fine-grained cron (V4-B1a)."""
    configure_logging()
    cfg = load_config()
    cron = cfg.pipeline.slot_close_poll_cron
    get_logger(__name__).info(
        "serving_capture_slot_close",
        mode="wall_clock_poll",
        cron=cron,
    )
    capture_slot_close_flow.serve(name="capture_slot_close", cron=cron)
