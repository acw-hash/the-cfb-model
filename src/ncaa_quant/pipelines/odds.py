"""Prefect flow and deployment for Odds API live snapshot ingestion (Task 4).

Migrated from Task 4a raw-only capture: every scheduled run still archives the
raw JSON first, then normalizes to ``odds_snapshots`` (with crosswalk) and
stages Parquet. The 6×/day cron must stay up — live odds cannot be backfilled.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from prefect import flow

from ncaa_quant.config import load_config
from ncaa_quant.ingestion.odds_api import OddsIngestResult, run_odds_ingest
from ncaa_quant.pipelines.common import PartitionKey, run_idempotent
from ncaa_quant.pipelines.notifications import AlertKind, notify
from ncaa_quant.utils.logging import configure_logging, get_logger

log = get_logger(__name__)


def notify_ingest_odds_failure(
    flow_obj: Any,  # noqa: ARG001 — Prefect hook signature
    flow_run: Any,
    state: Any,
) -> None:
    """Failure notification — wired to ntfy/Telegram via Task 24 notifier."""
    configure_logging()
    notify(
        AlertKind.FLOW_FAILURE,
        "ingest_odds failed",
        f"flow_run_id={getattr(flow_run, 'id', '')} "
        f"state={getattr(state, 'name', '')} msg={getattr(state, 'message', '')}",
    )


@flow(name="ingest_odds", on_failure=[notify_ingest_odds_failure])  # type: ignore[list-item]
def ingest_odds_flow() -> dict[str, Any]:
    """Pull one Odds API snapshot: raw archive → normalize → stage."""
    configure_logging()
    log = get_logger("ncaa_quant.pipelines.odds")
    cfg = load_config()
    captured = datetime.now(tz=UTC)
    partition = captured.strftime("%Y%m%dT%H%M")
    key = PartitionKey(source="ingest_odds", partition=partition)

    def _run() -> dict[str, Any]:
        result: OddsIngestResult = run_odds_ingest()
        return {
            "raw_path": str(result.raw_path),
            "rows_written": result.rows_written,
            "rows_fetched": result.rows_fetched,
            "captured_at": result.captured_at.isoformat(),
        }

    out = run_idempotent(key, _run, config=cfg)
    log.info(
        "ingest_odds_complete",
        raw_path=out.get("raw_path"),
        rows_written=out.get("rows_written"),
        rows_fetched=out.get("rows_fetched"),
        captured_at=out.get("captured_at"),
    )
    return out


def serve_ingest_odds(*, cron: str | None = None) -> None:
    """Block and run the ``ingest_odds`` deployment on a cron schedule."""
    cfg = load_config()
    schedule = cron if cron is not None else cfg.pipeline.odds_ingest_cron
    configure_logging()
    get_logger("ncaa_quant.pipelines.odds").info(
        "serving_ingest_odds",
        cron=schedule,
        mode="normalize_and_stage",
    )
    ingest_odds_flow.serve(name="ingest_odds", cron=schedule)


if __name__ == "__main__":
    serve_ingest_odds()
