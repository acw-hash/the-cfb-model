"""Prefect flow and deployment for Odds API snapshots."""

from __future__ import annotations

from typing import Any

from prefect import flow

from ncaa_quant.config import load_config
from ncaa_quant.ingestion.odds_api import OddsIngestResult, run_odds_ingest
from ncaa_quant.utils.logging import configure_logging, get_logger


def notify_ingest_odds_failure(
    flow: Any,  # noqa: ARG001 — Prefect hook signature
    flow_run: Any,
    state: Any,
) -> None:
    """Failure notification stub — logger only until alerting is wired (Task 24)."""
    log = get_logger("ncaa_quant.pipelines.odds")
    log.error(
        "ingest_odds_failed",
        flow_run_id=str(getattr(flow_run, "id", "")),
        state_name=getattr(state, "name", None),
        message=str(getattr(state, "message", "")),
    )


@flow(name="ingest_odds", on_failure=[notify_ingest_odds_failure])
def ingest_odds_flow() -> dict[str, Any]:
    """Pull one Odds API snapshot, archive raw JSON, write staged Parquet."""
    configure_logging()
    log = get_logger("ncaa_quant.pipelines.odds")
    result: OddsIngestResult = run_odds_ingest()
    log.info(
        "ingest_odds_complete",
        rows_written=result.rows_written,
        rows_fetched=result.rows_fetched,
        raw_path=str(result.raw_path),
        captured_at=result.captured_at.isoformat(),
    )
    return {
        "rows_written": result.rows_written,
        "rows_fetched": result.rows_fetched,
        "raw_path": str(result.raw_path),
        "captured_at": result.captured_at.isoformat(),
    }


def serve_ingest_odds(*, cron: str | None = None) -> None:
    """Block and run the ``ingest_odds`` deployment on a cron schedule."""
    cfg = load_config()
    schedule = cron if cron is not None else cfg.pipeline.odds_ingest_cron
    configure_logging()
    get_logger("ncaa_quant.pipelines.odds").info(
        "serving_ingest_odds",
        cron=schedule,
    )
    ingest_odds_flow.serve(name="ingest_odds", cron=schedule)


if __name__ == "__main__":
    serve_ingest_odds()
