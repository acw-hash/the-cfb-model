"""Checkpoint / resume and quota-aware pacing for CFBD backfill.

Wraps the existing :mod:`ncaa_quant.ingestion.cfbd` client without modifying it.
Checkpoint keys are ``(season, dataset, page)``; on quota exhaustion the pacer
sleeps until CFBD ``/info.resetAt`` rather than retrying into the wall.
"""

from ncaa_quant.data.ingest.checkpoint import (
    BackfillCheckpoint,
    checkpoint_key,
    load_checkpoint,
    mark_complete,
    save_checkpoint,
)
from ncaa_quant.data.ingest.quota import (
    CfbdQuotaStatus,
    fetch_quota_status,
    next_resume_at,
    sleep_until_quota_window,
    wait_for_quota,
)

__all__ = [
    "BackfillCheckpoint",
    "CfbdQuotaStatus",
    "checkpoint_key",
    "fetch_quota_status",
    "load_checkpoint",
    "mark_complete",
    "next_resume_at",
    "save_checkpoint",
    "sleep_until_quota_window",
    "wait_for_quota",
]
