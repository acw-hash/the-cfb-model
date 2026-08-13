"""Prefect flows for ingestion, training, prediction, and weekly refresh."""

from ncaa_quant.pipelines.odds import ingest_odds_flow, serve_ingest_odds
from ncaa_quant.pipelines.postgame import postgame_ingest_flow
from ncaa_quant.pipelines.predict import (
    RefreshKind,
    predict_publish_flow,
    run_chaos_stale_publish,
    run_fixture_week_publish,
)
from ncaa_quant.pipelines.retrain import retrain_gate_flow
from ncaa_quant.pipelines.serve import serve_all
from ncaa_quant.pipelines.settle import settle_clv_flow
from ncaa_quant.pipelines.weekly import weekly_update_flow

__all__ = [
    "RefreshKind",
    "ingest_odds_flow",
    "postgame_ingest_flow",
    "predict_publish_flow",
    "retrain_gate_flow",
    "run_chaos_stale_publish",
    "run_fixture_week_publish",
    "serve_all",
    "serve_ingest_odds",
    "settle_clv_flow",
    "weekly_update_flow",
]
