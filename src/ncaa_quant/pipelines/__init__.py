"""Prefect flows for ingestion, training, prediction, and weekly refresh."""

from ncaa_quant.pipelines.odds import ingest_odds_flow, serve_ingest_odds

__all__ = ["ingest_odds_flow", "serve_ingest_odds"]
