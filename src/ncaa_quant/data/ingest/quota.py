"""Quota-aware pacing for CFBD monthly call windows.

CFBD exposes remaining budget via ``GET /info`` (does not consume calls) and
``x-calllimit-remaining`` on responses. Free / Patreon tiers are **monthly**
(``resetAt``), not hourly. On exhaustion, sleep until ``resetAt`` instead of
retrying into the wall.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ncaa_quant.utils.logging import get_logger

CFBD_BASE_URL = "https://api.collegefootballdata.com"
# Poll /info this often while waiting; never busy-spin.
DEFAULT_POLL_SECONDS = 300.0
# Safety pad after published reset so the counter has flipped server-side.
RESET_PAD = timedelta(seconds=30)


@dataclass(frozen=True)
class CfbdQuotaStatus:
    """Snapshot of CFBD ``/info`` quota fields."""

    patron_level: int
    tier_name: str
    monthly_limit: int
    remaining_calls: int
    used_calls: int
    reset_at: datetime
    observed_at: datetime
    raw: dict[str, Any]

    @property
    def exhausted(self) -> bool:
        return self.remaining_calls <= 0


def fetch_quota_status(
    api_key: str,
    *,
    base_url: str = CFBD_BASE_URL,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> CfbdQuotaStatus:
    """GET ``/info`` — does not count against the monthly call budget."""
    with httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=timeout,
        transport=transport,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        response = client.get("/info")
        response.raise_for_status()
        body = response.json()
    reset_raw = body.get("resetAt") or body.get("reset_at")
    if not reset_raw:
        msg = f"CFBD /info missing resetAt: {body!r}"
        raise ValueError(msg)
    reset_at = datetime.fromisoformat(str(reset_raw).replace("Z", "+00:00")).astimezone(UTC)
    return CfbdQuotaStatus(
        patron_level=int(body.get("patronLevel") or body.get("patron_level") or 0),
        tier_name=str(body.get("tierName") or body.get("tier_name") or "unknown"),
        monthly_limit=int(body.get("monthlyLimit") or body.get("monthly_limit") or 0),
        remaining_calls=int(body.get("remainingCalls") or body.get("remaining_calls") or 0),
        used_calls=int(body.get("usedCalls") or body.get("used_calls") or 0),
        reset_at=reset_at,
        observed_at=datetime.now(tz=UTC),
        raw=dict(body),
    )


def next_resume_at(status: CfbdQuotaStatus) -> datetime:
    """UTC instant when a fresh window should be usable."""
    return status.reset_at + RESET_PAD


def sleep_until_quota_window(
    status: CfbdQuotaStatus,
    *,
    now: datetime | None = None,
    sleep_fn: Any = time.sleep,
    log: logging.Logger | Any | None = None,
) -> datetime:
    """Block until ``resetAt`` (+ pad). Returns the expected resume UTC time.

    Logs the pause with the expected resume time. If ``resetAt`` is already in
    the past, returns immediately (caller should re-probe ``/info``).
    """
    logger = log or get_logger(__name__)
    resume = next_resume_at(status)
    ts = now or datetime.now(tz=UTC)
    if resume <= ts:
        logger.info(  # type: ignore[call-arg]
            "cfbd_quota_window_already_open",
            resume_at=resume.isoformat(),
            remaining=status.remaining_calls,
            tier=status.tier_name,
        )
        return resume
    wait_s = (resume - ts).total_seconds()
    logger.warning(  # type: ignore[call-arg]
        "cfbd_quota_pause",
        remaining=status.remaining_calls,
        monthly_limit=status.monthly_limit,
        tier=status.tier_name,
        reset_at=status.reset_at.isoformat(),
        resume_at=resume.isoformat(),
        sleep_seconds=round(wait_s, 1),
    )
    sleep_fn(wait_s)
    return resume


def wait_for_quota(
    api_key: str,
    *,
    min_remaining: int = 10,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    sleep_fn: Any = time.sleep,
    max_wait: timedelta | None = None,
    base_url: str = CFBD_BASE_URL,
    transport: httpx.BaseTransport | None = None,
) -> CfbdQuotaStatus:
    """Block until ``remainingCalls >= min_remaining``, sleeping to ``resetAt``.

    Re-probes ``/info`` after each sleep. Raises :class:`TimeoutError` if
    ``max_wait`` elapses without recovery.
    """
    log = get_logger(__name__)
    deadline = datetime.now(tz=UTC) + max_wait if max_wait is not None else None
    while True:
        status = fetch_quota_status(api_key, base_url=base_url, transport=transport)
        if status.remaining_calls >= min_remaining:
            log.info(
                "cfbd_quota_ready",
                remaining=status.remaining_calls,
                monthly_limit=status.monthly_limit,
                tier=status.tier_name,
            )
            return status
        if deadline is not None and datetime.now(tz=UTC) >= deadline:
            msg = (
                f"CFBD quota still exhausted after max_wait "
                f"(remaining={status.remaining_calls}, "
                f"resetAt={status.reset_at.isoformat()})"
            )
            raise TimeoutError(msg)
        resume = next_resume_at(status)
        now = datetime.now(tz=UTC)
        if resume > now:
            sleep_until_quota_window(status, now=now, sleep_fn=sleep_fn, log=log)
        else:
            # Reset claimed past but remaining still low — poll gently.
            log.warning(
                "cfbd_quota_poll_wait",
                remaining=status.remaining_calls,
                min_remaining=min_remaining,
                poll_seconds=poll_seconds,
                resume_at=resume.isoformat(),
            )
            sleep_fn(poll_seconds)
