"""Prefect flow for forward kickoff-aligned slot_close capture (V4-B1).

Not registered on a cron schedule — Prefect ``.serve(cron=...)`` cannot fire
per-kickoff triggers. Operator invokes this flow (or the CLI) on a short poll
loop until each week's slots are captured.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from prefect import flow

from ncaa_quant.config import load_config
from ncaa_quant.ingestion.slot_close_capture import run_due_slot_close_captures
from ncaa_quant.pipelines.common import PartitionKey, run_idempotent
from ncaa_quant.pipelines.notifications import AlertKind, notify
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
    """Capture all kickoff slots due at ``as_of``; record missed past-kickoff slots."""
    configure_logging()
    cfg = load_config()
    now = as_of or datetime.now(tz=UTC)
    season_list = seasons if seasons is not None else [cfg.data.end_season]
    partition = to_utc(now).strftime("%Y%m%dT%H%M")
    key = PartitionKey(source="capture_slot_close", partition=partition)

    def _run() -> dict[str, Any]:
        batch = run_due_slot_close_captures(
            seasons=season_list,
            config=cfg,
            as_of=now,
        )
        return {
            "as_of": now.isoformat(),
            "seasons": season_list,
            "captured": batch.captured,
            "skipped": batch.skipped,
            "missed_recorded": batch.missed_recorded,
            "credits_spent": batch.credits_spent,
            "rows_written": batch.rows_written,
        }

    out = run_idempotent(key, _run, config=cfg)
    log.info("capture_slot_close_complete", **out)
    return out


def serve_capture_slot_close() -> None:
    """Block and serve the flow without a cron (operator / external scheduler)."""
    configure_logging()
    get_logger("ncaa_quant.pipelines.slot_close").info(
        "serving_capture_slot_close",
        mode="no_cron_kickoff_aligned",
    )
    capture_slot_close_flow.serve(name="capture_slot_close")
