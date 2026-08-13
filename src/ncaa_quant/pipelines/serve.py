"""Prefect deployment server for all §10 flows."""

from __future__ import annotations

from ncaa_quant.config import load_config
from ncaa_quant.pipelines.odds import ingest_odds_flow
from ncaa_quant.pipelines.postgame import postgame_ingest_flow
from ncaa_quant.pipelines.predict import RefreshKind, predict_publish_flow
from ncaa_quant.pipelines.retrain import retrain_gate_flow
from ncaa_quant.pipelines.settle import settle_clv_flow
from ncaa_quant.pipelines.weekly import weekly_update_flow
from ncaa_quant.utils.logging import configure_logging, get_logger

log = get_logger(__name__)


def serve_all() -> None:
    """Register all §10 deployments with their cron schedules."""
    cfg = load_config()
    pipe = cfg.pipeline
    configure_logging()
    log.info("serving_all_deployments")

    ingest_odds_flow.serve(name="ingest_odds", cron=pipe.odds_ingest_cron)
    postgame_ingest_flow.serve(name="postgame_ingest_sat", cron=pipe.postgame_ingest_cron_sat)
    postgame_ingest_flow.serve(name="postgame_ingest_hourly", cron=pipe.postgame_ingest_cron_hourly)
    weekly_update_flow.serve(name="weekly_update", cron=pipe.weekly_update_cron)
    retrain_gate_flow.serve(name="retrain_gate", cron=pipe.weekly_update_cron)
    predict_publish_flow.serve(
        name="predict_publish_tuesday",
        cron=pipe.predict_publish_cron_tuesday,
        parameters={"refresh_kind": RefreshKind.TUESDAY_PRIMARY},
    )
    predict_publish_flow.serve(
        name="predict_publish_refresh",
        cron=pipe.predict_publish_cron_refresh,
        parameters={"refresh_kind": RefreshKind.DAILY_REFRESH},
    )
    settle_clv_flow.serve(name="settle_clv", cron=pipe.settle_clv_cron)


if __name__ == "__main__":
    serve_all()
