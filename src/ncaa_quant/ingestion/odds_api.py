"""The Odds API NCAAF snapshot ingestion (live + historical).

Captures unbackfillable live odds and credit-metered historical snapshots.
Every response is archived verbatim *before* parsing so a parser failure never
loses the payload.

**Decision-point schedule (Task 5B).** Pre-registered in
``configs/data.yaml`` as ``odds_historical_decision_points``. Current schedule:

- ``tuesday_0600_et`` — one request per CFB week at Tuesday 06:00 America/New_York
- ``saturday_0600_et`` — one request per CFB week at Saturday 06:00 America/New_York
  (DESIGN §9.8 / ADR 0002 daily-refresh point; ADR 0009)
- ``slot_close`` — one request per distinct kickoff slot at slot minus 5 minutes

DESIGN §9.8 also lists Thu 06:00 ET, T−6h, and T−1h. Those remain deferred.
Changing this schedule later invalidates backtest comparability with earlier runs.

**event_time discipline (historical).** The historical endpoint returns the
live odds schema wrapped in an envelope with ``timestamp`` /
``previous_timestamp`` / ``next_timestamp``. Stored ``event_time`` is always
the envelope's *returned* ``timestamp``, never the requested ``date``. The API
returns the closest snapshot at or before the request (gap up to 10 minutes
before Sept 2022, 5 minutes after). Storing the request time would claim
information was knowable later than it was and corrupt as-of joins.

**Credit buckets.** Live capture uses ``odds_rate_limit_reserve`` as its floor.
Historical refuse when remaining credits would drop below that same live floor,
and also refuse when estimated/run spend exceeds
``odds_historical_credit_ceiling`` without ``--force``. Historical is
structurally incapable of consuming the live reserve.

Team naming mismatches across sources are the #1 integration bug in this
project. Canonical school names come from ``configs/team_names.yaml`` via
:func:`normalize_team_name` / :func:`make_game_key`.

**Canonical game identity (AUDIT-6).** CFBD's numeric ``game_id`` is the only
identity of record. Odds API events are matched via normalized team pair +
kickoff within ±36h and persisted in ``odds_cfbd_game_crosswalk``. Ambiguous
matches are quarantined (``game_id`` null) — never guessed. The derived
``game_key`` is matcher input only. A one-day postpone keeps one CFBD id
because a prior matched ``odds_event_id`` is reused across commence-time shifts.

**Ingest-time line quarantine (ADR 0010).** Out-of-bounds book lines
(``|spread| < 70``, totals in ``[20, 100]`` — same as
``OddsSnapshotsSchema.line_sanity``) are split before write: good rows stage to
``odds_snapshots``; bad rows append to the ``odds_snapshots_quarantine`` sidecar.
This is row-level salvage at ingest, distinct from Task 7's post-hoc partition
quarantine.

**Historical resume.** Archive presence alone does not complete a slot. Skip the
API only when the archive exists *and* staged rows are present for that slot's
returned ``event_time`` (matched on ``decision_point`` +
``snapshot_source='historical'``), or an explicit empty-slot marker was written
after a successful parse of an empty envelope. Otherwise replay parse-and-write
from the archive at zero credits.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

import httpx
import pandas as pd  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ncaa_quant.config import AppConfig, load_config, load_secrets
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.teams import (
    load_team_name_map,
    make_game_key,
    normalize_team_name,
)
from ncaa_quant.utils.logging import get_logger
from ncaa_quant.utils.timeutils import season_of, to_utc, week_of

# Re-export team helpers for callers that imported them from this module.
__all__ = (
    "CalibrationError",
    "CrosswalkRegressionFailure",
    "HistoricalBackfillResult",
    "HistoricalBudgetCeilingError",
    "HistoricalOddsResponse",
    "HistoricalPlan",
    "HistoricalReplayResult",
    "HistoricalUnit",
    "OddsAPIClient",
    "OddsAPIError",
    "OddsIngestResult",
    "RateLimitBudgetError",
    "ReconcileReport",
    "OddsEventRef",
    "archive_historical_response",
    "archive_raw_response",
    "asof_tolerance_for",
    "backfill_live_odds_metadata",
    "dedupe_snapshots",
    "estimate_historical_credits",
    "extract_odds_events",
    "is_unit_complete",
    "load_cfbd_schedule",
    "load_team_name_map",
    "make_game_key",
    "mark_unit_complete",
    "match_odds_events_to_cfbd",
    "normalize_odds_payload",
    "normalize_team_name",
    "parse_historical_envelope",
    "plan_historical_units",
    "preview_crosswalk_game_key_regression",
    "reconcile_cfbd_close_vs_slot_close",
    "replay_historical_from_archives",
    "resolve_event_game_ids",
    "run_historical_backfill",
    "run_odds_ingest",
    "run_odds_raw_capture",
    "saturday_0600_et_for_week",
    "split_odds_by_line_sanity",
    "tuesday_0600_et_for_week",
    "write_odds_cfbd_crosswalk",
    "write_odds_snapshots",
)

# httpx logs full request URLs at INFO, which would leak apiKey query params.
logging.getLogger("httpx").setLevel(logging.WARNING)
SOURCE_VERSION: Final[str] = "odds_api_v4"
SPORT_KEY: Final[str] = "americanfootball_ncaaf"
BASE_URL: Final[str] = "https://api.the-odds-api.com/v4"
_ET: Final[ZoneInfo] = ZoneInfo("America/New_York")
# Odds↔CFBD kickoff match window (AUDIT-6 / Task 4 amended).
KICKOFF_MATCH_TOLERANCE: Final[timedelta] = timedelta(hours=36)
# Snapshot granularity change (DESIGN §3.4).
_GRANULARITY_CUTOVER: Final[datetime] = datetime(2022, 9, 1, tzinfo=UTC)

# Odds API market keys → OddsSnapshotsSchema.market values.
_API_MARKET_TO_SCHEMA: Final[dict[str, str]] = {
    "spreads": "spread",
    "totals": "total",
    "h2h": "h2h",
}

_REMAINING_HEADER: Final[str] = "x-requests-remaining"
_USED_HEADER: Final[str] = "x-requests-used"
_LAST_HEADER: Final[str] = "x-requests-last"

_DEDUPE_COLS: Final[tuple[str, ...]] = (
    "game_key",
    "book",
    "market",
    "side",
    "line",
    "price",
    "captured_at_minute",
)

_ODDS_COLUMNS: Final[tuple[str, ...]] = (
    "snapshot_id",
    "game_key",
    "game_id",
    "season",
    "week",
    "book",
    "market",
    "side",
    "line",
    "price",
    "home_team",
    "away_team",
    "captured_at",
    "source_version",
    "snapshot_source",
    "decision_point",
    "n_books_available",
    "event_time",
    "ingested_at",
)

# Ingest-time row quarantine sidecar (ADR 0010). Not in SCHEMA_REGISTRY.
_ODDS_QUARANTINE_TABLE: Final[str] = "odds_snapshots_quarantine"
_QUARANTINE_PART: Final[str] = "part.parquet"
# Marker written after a successful parse of an empty historical envelope.
_EMPTY_SLOT_DIR: Final[str] = "_empty_slots"

DecisionPoint = Literal["tuesday_0600_et", "saturday_0600_et", "slot_close"]
BudgetKind = Literal["live", "historical"]


class RateLimitBudgetError(RuntimeError):
    """Raised when remaining Odds API credits are below the configured reserve."""


class HistoricalBudgetCeilingError(RuntimeError):
    """Raised when historical spend would exceed the configured ceiling."""


class CalibrationError(RuntimeError):
    """Raised when the historical cost calibration probe disagrees with config."""


class OddsAPIError(RuntimeError):
    """Non-retryable Odds API failure."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


@dataclass(frozen=True)
class OddsIngestResult:
    """Summary of one live snapshot pull (raw + normalize + stage)."""

    raw_path: Path
    rows_written: int
    rows_fetched: int
    captured_at: datetime


@dataclass(frozen=True)
class OddsRawCaptureResult:
    """Summary of a raw-only live capture (Task 4a — no normalize/stage)."""

    raw_path: Path
    captured_at: datetime
    bytes_written: int


@dataclass(frozen=True)
class OddsEventRef:
    """One Odds API event used as crosswalk matcher input (not identity of record)."""

    odds_event_id: str
    game_key: str
    season: int
    home_team: str
    away_team: str
    kickoff: datetime


@dataclass(frozen=True)
class HistoricalOddsResponse:
    """Parsed historical odds envelope plus raw bytes and headers."""

    raw_body: bytes
    headers: httpx.Headers
    requested_at: datetime
    timestamp: datetime
    previous_timestamp: datetime | None
    next_timestamp: datetime | None
    data: list[Any]


@dataclass(frozen=True)
class HistoricalUnit:
    """One resumable backfill unit: (season, week, decision_point)."""

    season: int
    week: int
    decision_point: str
    request_times: tuple[datetime, ...]


@dataclass(frozen=True)
class HistoricalPlan:
    """Enumerated historical requests and credit estimate."""

    units: tuple[HistoricalUnit, ...]
    requests_by_season_dp: Mapping[tuple[int, str], int]
    total_requests: int
    credits_per_call: int
    total_credits: int
    ceiling: int

    @property
    def over_ceiling(self) -> bool:
        return self.total_credits > self.ceiling


@dataclass
class HistoricalBackfillResult:
    """Summary of a historical backfill run."""

    units_written: int = 0
    units_skipped: int = 0
    requests_made: int = 0
    credits_spent: int = 0
    rows_written: int = 0
    rows_quarantined: int = 0
    calibration_last: int | None = None
    raw_paths: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class CrosswalkRegressionFailure:
    """A previously-matched Odds event whose game_key would change on rematch."""

    odds_event_id: str
    season: int
    old_game_key: str
    new_game_key: str


@dataclass
class HistoricalReplayResult:
    """Summary of a zero-API archive replay (name-map / crosswalk repair)."""

    seasons: list[int] = field(default_factory=list)
    archives_replayed: int = 0
    rows_written: int = 0
    rows_quarantined: int = 0
    row_counts_before: dict[int, int] = field(default_factory=dict)
    row_counts_after: dict[int, int] = field(default_factory=dict)
    prior_matched: int = 0
    regression_failures: list[CrosswalkRegressionFailure] = field(default_factory=list)


@dataclass(frozen=True)
class ReconcileReport:
    """CFBD close vs historical slot_close divergence summary."""

    n_games: int
    spread_diffs: tuple[float, ...]
    total_diffs: tuple[float, ...]

    def summary_lines(self) -> list[str]:
        lines = [f"reconcile games with both closes: {self.n_games}"]
        if self.spread_diffs:
            s = pd.Series(self.spread_diffs, dtype="float64")
            lines.append(
                "spread Δ (snapshot − cfbd): "
                f"n={len(s)} mean={s.mean():.3f} median={s.median():.3f} "
                f"p10={s.quantile(0.1):.3f} p90={s.quantile(0.9):.3f}"
            )
        else:
            lines.append("spread Δ: no matched pairs")
        if self.total_diffs:
            t = pd.Series(self.total_diffs, dtype="float64")
            lines.append(
                "total Δ (snapshot − cfbd): "
                f"n={len(t)} mean={t.mean():.3f} median={t.median():.3f} "
                f"p10={t.quantile(0.1):.3f} p90={t.quantile(0.9):.3f}"
            )
        else:
            lines.append("total Δ: no matched pairs")
        return lines


def asof_tolerance_for(
    ts: datetime,
    *,
    pre_sept_2022_minutes: int = 10,
    post_sept_2022_minutes: int = 5,
) -> timedelta:
    """Return the as-of fallback tolerance for a decision timestamp.

    Snapshot intervals are 10 minutes until Sept 2022 and 5 minutes after
    (DESIGN §3.4). A hardcoded 5-minute tolerance silently drops valid
    2020–2022 snapshots.
    """
    utc = to_utc(ts)
    minutes = pre_sept_2022_minutes if utc < _GRANULARITY_CUTOVER else post_sept_2022_minutes
    return timedelta(minutes=minutes)


def within_asof_tolerance(
    requested_at: datetime,
    returned_at: datetime,
    *,
    pre_sept_2022_minutes: int = 10,
    post_sept_2022_minutes: int = 5,
) -> bool:
    """True when the returned snapshot is close enough to cover the request."""
    req = to_utc(requested_at)
    ret = to_utc(returned_at)
    if ret > req:
        return False
    tol = asof_tolerance_for(
        req,
        pre_sept_2022_minutes=pre_sept_2022_minutes,
        post_sept_2022_minutes=post_sept_2022_minutes,
    )
    return (req - ret) <= tol


def archive_raw_response(
    raw_root: Path,
    captured_at: datetime,
    body: bytes | str,
) -> Path:
    """Write the live API payload verbatim under ``raw_root/{date}/{iso}.json``."""
    captured = to_utc(captured_at)
    day = captured.date().isoformat()
    stamp = captured.strftime("%Y%m%dT%H%M%S%fZ")
    directory = raw_root / day
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}.json"
    payload = body if isinstance(body, bytes) else body.encode("utf-8")
    path.write_bytes(payload)
    return path


def archive_historical_response(
    raw_root: Path,
    requested_at: datetime,
    returned_at: datetime,
    body: bytes | str,
) -> Path:
    """Write historical payload under ``{date}/{requested_ts}_{returned_ts}.json``."""
    requested = to_utc(requested_at)
    returned = to_utc(returned_at)
    day = requested.date().isoformat()
    req_stamp = requested.strftime("%Y%m%dT%H%M%S%fZ")
    ret_stamp = returned.strftime("%Y%m%dT%H%M%S%fZ")
    directory = raw_root / day
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{req_stamp}_{ret_stamp}.json"
    payload = body if isinstance(body, bytes) else body.encode("utf-8")
    path.write_bytes(payload)
    return path


def _request_stamp(requested_at: datetime) -> str:
    return to_utc(requested_at).strftime("%Y%m%dT%H%M%S%fZ")


def _find_historical_slot_archive(raw_root: Path, requested_at: datetime) -> Path | None:
    """Return the archive path for ``requested_at``, if any."""
    requested = to_utc(requested_at)
    day = requested.date().isoformat()
    req_stamp = _request_stamp(requested)
    directory = raw_root / day
    if not directory.is_dir():
        return None
    matches = sorted(p for p in directory.glob("*.json") if p.name.startswith(f"{req_stamp}_"))
    return matches[0] if matches else None


def _empty_slot_marker_path(raw_root: Path, requested_at: datetime) -> Path:
    return raw_root / _EMPTY_SLOT_DIR / f"{_request_stamp(requested_at)}.done"


def _is_empty_historical_slot(raw_root: Path, requested_at: datetime) -> bool:
    """True when an empty-envelope marker exists for this request timestamp."""
    return _empty_slot_marker_path(raw_root, requested_at).is_file()


def _mark_empty_historical_slot(raw_root: Path, requested_at: datetime) -> Path:
    """Record that an empty historical envelope was successfully parsed."""
    path = _empty_slot_marker_path(raw_root, requested_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("empty\n", encoding="utf-8")
    return path


def _staged_has_historical_slot(
    store: ParquetStore,
    *,
    season: int,
    week: int,
    decision_point: str,
    event_time: datetime,
) -> bool:
    """True if staged historical rows exist for this returned event_time + DP."""
    existing = store.read(
        "odds_snapshots",
        filters={"season": int(season), "week": int(week)},
    )
    if existing.empty:
        return False
    target = pd.Timestamp(to_utc(event_time))
    et = pd.to_datetime(existing["event_time"], utc=True)
    mask = (
        (existing["snapshot_source"] == "historical")
        & (existing["decision_point"] == decision_point)
        & (et == target)
    )
    return bool(mask.any())


def _progress_path(raw_root: Path, season: int, week: int, decision_point: str) -> Path:
    return raw_root / "_progress" / f"{season}_{week}_{decision_point}.done"


def is_unit_complete(
    raw_root: Path,
    season: int,
    week: int,
    decision_point: str,
) -> bool:
    """True when the (season, week, decision_point) progress marker exists."""
    return _progress_path(raw_root, season, week, decision_point).is_file()


def mark_unit_complete(
    raw_root: Path,
    season: int,
    week: int,
    decision_point: str,
) -> Path:
    """Write the resumability marker for a completed unit.

    Call only after every ``request_time`` in the unit has a successful
    parse-and-write (including zero-credit archive replays).
    """
    path = _progress_path(raw_root, season, week, decision_point)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ok\n", encoding="utf-8")
    return path


def parse_historical_envelope(
    payload: bytes | str | Mapping[str, Any],
    *,
    requested_at: datetime,
) -> HistoricalOddsResponse:
    """Parse the historical odds envelope; ``timestamp`` is the knowable-at time."""
    if isinstance(payload, (bytes, str)):
        raw_body = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        parsed: Any = json.loads(payload)
    else:
        raw_body = json.dumps(payload).encode("utf-8")
        parsed = payload
    if not isinstance(parsed, dict):
        msg = "Historical odds payload must be a JSON object envelope"
        raise OddsAPIError(msg)
    ts_raw = parsed.get("timestamp")
    if not isinstance(ts_raw, str):
        msg = "Historical envelope missing timestamp"
        raise OddsAPIError(msg)
    timestamp = to_utc(datetime.fromisoformat(ts_raw.replace("Z", "+00:00")))
    prev_raw = parsed.get("previous_timestamp")
    next_raw = parsed.get("next_timestamp")
    previous = (
        to_utc(datetime.fromisoformat(prev_raw.replace("Z", "+00:00")))
        if isinstance(prev_raw, str)
        else None
    )
    nxt = (
        to_utc(datetime.fromisoformat(next_raw.replace("Z", "+00:00")))
        if isinstance(next_raw, str)
        else None
    )
    data = parsed.get("data")
    if not isinstance(data, list):
        msg = "Historical envelope data must be a JSON array"
        raise OddsAPIError(msg)
    return HistoricalOddsResponse(
        raw_body=raw_body,
        headers=httpx.Headers(),
        requested_at=to_utc(requested_at),
        timestamp=timestamp,
        previous_timestamp=previous,
        next_timestamp=nxt,
        data=data,
    )


class OddsAPIClient:
    """Typed httpx client for live and historical ``americanfootball_ncaaf`` odds."""

    def __init__(
        self,
        api_key: str,
        *,
        books: Sequence[str],
        markets: Sequence[str],
        regions: str = "us",
        rate_limit_reserve: int = 50,
        budget_kind: BudgetKind = "live",
        historical_credit_ceiling: int | None = None,
        credits_per_historical_call: int = 30,
        force_ceiling: bool = False,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        if not api_key:
            msg = "ODDS_API_KEY is empty"
            raise ValueError(msg)
        self._api_key = api_key
        self._books = list(books)
        self._markets = list(markets)
        self._regions = regions
        self._reserve = rate_limit_reserve
        self._budget_kind: BudgetKind = budget_kind
        self._historical_ceiling = historical_credit_ceiling
        self._credits_per_call = credits_per_historical_call
        self._force_ceiling = force_ceiling
        self._remaining: int | None = None
        self._credits_spent: int = 0
        self._last_requests_last: int | None = None
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    @property
    def remaining_requests(self) -> int | None:
        """Last-seen ``x-requests-remaining``, or None before the first call."""
        return self._remaining

    @property
    def credits_spent(self) -> int:
        """Credits consumed by this client instance (historical path)."""
        return self._credits_spent

    @property
    def last_requests_last(self) -> int | None:
        """Last-seen ``x-requests-last`` header value."""
        return self._last_requests_last

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> OddsAPIClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _guard_budget(self) -> None:
        # Live floor applies to both paths — historical must leave live reserve.
        if self._budget_kind == "historical":
            if self._remaining is not None:
                # Refuse when this call would drop remaining below the live floor.
                projected_remaining = self._remaining - self._credits_per_call
                if projected_remaining < self._reserve:
                    msg = (
                        f"Odds API remaining requests {self._remaining} "
                        f"would fall below live reserve {self._reserve} "
                        f"after a {self._credits_per_call}-credit historical call "
                        f"(budget_kind={self._budget_kind})"
                    )
                    raise RateLimitBudgetError(msg)
            if self._historical_ceiling is not None and not self._force_ceiling:
                projected = self._credits_spent + self._credits_per_call
                if projected > self._historical_ceiling:
                    msg = (
                        f"Historical credit spend {projected} would exceed "
                        f"ceiling {self._historical_ceiling}"
                    )
                    raise HistoricalBudgetCeilingError(msg)
            return

        if self._remaining is not None and self._remaining < self._reserve:
            msg = (
                f"Odds API remaining requests {self._remaining} "
                f"below reserve threshold {self._reserve} "
                f"(budget_kind={self._budget_kind})"
            )
            raise RateLimitBudgetError(msg)

    def _update_budget(self, headers: httpx.Headers) -> None:
        log = get_logger(__name__)
        raw_remaining = headers.get(_REMAINING_HEADER)
        raw_used = headers.get(_USED_HEADER)
        raw_last = headers.get(_LAST_HEADER)
        if raw_remaining is not None:
            try:
                self._remaining = int(raw_remaining)
            except ValueError:
                log.warning("unparseable_rate_limit_header", header=raw_remaining)
        if raw_last is not None:
            try:
                self._last_requests_last = int(raw_last)
                if self._budget_kind == "historical":
                    self._credits_spent += self._last_requests_last
            except ValueError:
                log.warning("unparseable_requests_last_header", header=raw_last)
        log.info(
            "odds_api_rate_limit",
            requests_remaining=raw_remaining,
            requests_used=raw_used,
            requests_last=raw_last,
            reserve=self._reserve,
            budget_kind=self._budget_kind,
            credits_spent=self._credits_spent,
        )

    def fetch_odds(self) -> tuple[bytes, httpx.Headers]:
        """GET live NCAAF odds; return ``(raw_body, response_headers)``."""
        self._guard_budget()
        return self._fetch_odds_with_retry()

    def _fetch_odds_with_retry(self) -> tuple[bytes, httpx.Headers]:
        @retry(
            reraise=True,
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception(_is_retryable),
        )
        def _once() -> tuple[bytes, httpx.Headers]:
            params: dict[str, str] = {
                "apiKey": self._api_key,
                "regions": self._regions,
                "markets": ",".join(self._markets),
                "oddsFormat": "american",
            }
            if self._books:
                params["bookmakers"] = ",".join(self._books)
            response = self._client.get(
                f"/sports/{SPORT_KEY}/odds",
                params=params,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                self._update_budget(response.headers)
                raise
            self._update_budget(response.headers)
            return response.content, response.headers

        return _once()

    def fetch_historical_odds(self, date: datetime) -> HistoricalOddsResponse:
        """GET historical NCAAF odds at-or-before ``date``.

        ``event_time`` for stored rows must be the envelope ``timestamp``, not
        the requested ``date``.
        """
        if self._budget_kind != "historical":
            msg = "fetch_historical_odds requires budget_kind='historical'"
            raise OddsAPIError(msg)
        self._guard_budget()
        return self._fetch_historical_with_retry(to_utc(date))

    def _fetch_historical_with_retry(self, date: datetime) -> HistoricalOddsResponse:
        @retry(
            reraise=True,
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception(_is_retryable),
        )
        def _once() -> HistoricalOddsResponse:
            params: dict[str, str] = {
                "apiKey": self._api_key,
                "regions": self._regions,
                "markets": ",".join(self._markets),
                "oddsFormat": "american",
                "date": date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if self._books:
                params["bookmakers"] = ",".join(self._books)
            response = self._client.get(
                f"/historical/sports/{SPORT_KEY}/odds",
                params=params,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                self._update_budget(response.headers)
                raise
            self._update_budget(response.headers)
            envelope = parse_historical_envelope(response.content, requested_at=date)
            return HistoricalOddsResponse(
                raw_body=envelope.raw_body,
                headers=response.headers,
                requested_at=envelope.requested_at,
                timestamp=envelope.timestamp,
                previous_timestamp=envelope.previous_timestamp,
                next_timestamp=envelope.next_timestamp,
                data=envelope.data,
            )

        return _once()


def _parse_commence(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return to_utc(ts)


def _snapshot_id(
    game_key: str,
    book: str,
    market: str,
    side: str,
    line: float | None,
    price: float,
    captured_at: datetime,
) -> str:
    line_part = "" if line is None else f"{line:.3f}"
    material = f"{game_key}|{book}|{market}|{side}|{line_part}|{price}|{captured_at.isoformat()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def normalize_odds_payload(
    payload: bytes | str | list[Any],
    *,
    captured_at: datetime,
    ingested_at: datetime,
    team_map: Mapping[str, str],
    source_version: str = SOURCE_VERSION,
    snapshot_source: Literal["live", "historical"] = "live",
    decision_point: str | None = None,
    event_time: datetime | None = None,
    event_game_ids: Mapping[str, int] | None = None,
) -> pd.DataFrame:
    """Parse Odds API JSON into an ``odds_snapshots``-shaped DataFrame.

    For live pulls, ``event_time`` defaults to ``captured_at``. For historical
    pulls, pass the envelope's returned ``timestamp`` as both ``captured_at``
    and ``event_time`` — never the request ``date``.

    ``event_game_ids`` maps Odds API event id → CFBD ``game_id`` for rows whose
    crosswalk match has already resolved (AUDIT-6). Unmapped events keep
    ``game_id`` null.
    """
    captured = to_utc(captured_at)
    ingested = to_utc(ingested_at)
    knowable = to_utc(event_time) if event_time is not None else captured
    id_map = dict(event_game_ids) if event_game_ids is not None else {}
    data = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    if not isinstance(data, list):
        msg = "Odds API odds payload must be a JSON array"
        raise OddsAPIError(msg)

    rows: list[dict[str, Any]] = []
    for event in data:
        if not isinstance(event, dict):
            continue
        home_raw = str(event.get("home_team", ""))
        away_raw = str(event.get("away_team", ""))
        commence_raw = event.get("commence_time")
        if not home_raw or not away_raw or not isinstance(commence_raw, str):
            continue
        kickoff = _parse_commence(commence_raw)
        season = season_of(kickoff)
        week = week_of(kickoff, season)
        home = normalize_team_name(home_raw, team_map)
        away = normalize_team_name(away_raw, team_map)
        game_key = make_game_key(season, home, away, kickoff.date())
        odds_event_id = str(event.get("id", ""))
        game_id = id_map.get(odds_event_id)

        bookmakers = event.get("bookmakers") or []
        if not isinstance(bookmakers, list):
            continue
        n_books = len([b for b in bookmakers if isinstance(b, dict)])
        for book in bookmakers:
            if not isinstance(book, dict):
                continue
            book_key = str(book.get("key", ""))
            markets = book.get("markets") or []
            if not isinstance(markets, list):
                continue
            for market in markets:
                if not isinstance(market, dict):
                    continue
                api_market = str(market.get("key", ""))
                schema_market = _API_MARKET_TO_SCHEMA.get(api_market)
                if schema_market is None:
                    continue
                outcomes = market.get("outcomes") or []
                if not isinstance(outcomes, list):
                    continue
                for outcome in outcomes:
                    if not isinstance(outcome, dict):
                        continue
                    price_raw = outcome.get("price")
                    if price_raw is None:
                        continue
                    price = float(price_raw)
                    point = outcome.get("point")
                    line = float(point) if point is not None else None
                    name = str(outcome.get("name", ""))
                    if schema_market == "total":
                        side = name.strip().casefold()
                    else:
                        side = normalize_team_name(name, team_map)
                    rows.append(
                        {
                            "snapshot_id": _snapshot_id(
                                game_key,
                                book_key,
                                schema_market,
                                side,
                                line,
                                price,
                                captured,
                            ),
                            "game_key": game_key,
                            "game_id": game_id,
                            "season": season,
                            "week": week,
                            "book": book_key,
                            "market": schema_market,
                            "side": side,
                            "line": line,
                            "price": price,
                            "home_team": home,
                            "away_team": away,
                            "captured_at": captured,
                            "source_version": source_version,
                            "snapshot_source": snapshot_source,
                            "decision_point": decision_point,
                            "n_books_available": n_books,
                            "event_time": knowable,
                            "ingested_at": ingested,
                        }
                    )

    if not rows:
        return pd.DataFrame(columns=list(_ODDS_COLUMNS))
    return pd.DataFrame(rows)


def extract_odds_events(
    payload: bytes | str | list[Any],
    team_map: Mapping[str, str],
) -> list[OddsEventRef]:
    """Extract unique Odds API events as crosswalk matcher inputs."""
    data = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    if not isinstance(data, list):
        msg = "Odds API odds payload must be a JSON array"
        raise OddsAPIError(msg)
    out: list[OddsEventRef] = []
    seen: set[str] = set()
    for event in data:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id", "")).strip()
        home_raw = str(event.get("home_team", ""))
        away_raw = str(event.get("away_team", ""))
        commence_raw = event.get("commence_time")
        if not event_id or not home_raw or not away_raw or not isinstance(commence_raw, str):
            continue
        if event_id in seen:
            continue
        seen.add(event_id)
        kickoff = _parse_commence(commence_raw)
        season = season_of(kickoff)
        home = normalize_team_name(home_raw, team_map)
        away = normalize_team_name(away_raw, team_map)
        out.append(
            OddsEventRef(
                odds_event_id=event_id,
                game_key=make_game_key(season, home, away, kickoff.date()),
                season=season,
                home_team=home,
                away_team=away,
                kickoff=kickoff,
            )
        )
    return out


def load_cfbd_schedule(
    store: ParquetStore,
    seasons: Sequence[int],
    team_map: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Load CFBD games with canonical school names for Odds↔CFBD matching.

    Returns columns: ``game_id``, ``season``, ``home_team``, ``away_team``,
    ``start_date`` (UTC-aware).
    """
    mapping = dict(team_map) if team_map is not None else {}
    frames: list[pd.DataFrame] = []
    for season in seasons:
        games = store.read("games", filters={"season": int(season)})
        if games.empty:
            continue
        teams = store.read("teams", filters={"season": int(season)})
        if teams.empty:
            continue
        id_to_school = {
            int(r.team_id): normalize_team_name(str(r.school), mapping)
            if mapping
            else str(r.school)
            for r in teams.itertuples(index=False)
        }
        rows: list[dict[str, Any]] = []
        for g in games.itertuples(index=False):
            home = id_to_school.get(int(g.home_team_id))
            away = id_to_school.get(int(g.away_team_id))
            if home is None or away is None:
                continue
            rows.append(
                {
                    "game_id": int(g.game_id),
                    "season": int(g.season),
                    "home_team": home,
                    "away_team": away,
                    "start_date": to_utc(pd.Timestamp(g.start_date).to_pydatetime()),
                }
            )
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        return pd.DataFrame(columns=["game_id", "season", "home_team", "away_team", "start_date"])
    return pd.concat(frames, ignore_index=True)


def match_odds_events_to_cfbd(
    events: Sequence[OddsEventRef],
    schedule: pd.DataFrame,
    *,
    existing: pd.DataFrame | None = None,
    ingested_at: datetime,
    source_version: str = SOURCE_VERSION,
    tolerance: timedelta = KICKOFF_MATCH_TOLERANCE,
) -> pd.DataFrame:
    """Match Odds events to CFBD ``game_id`` via team pair + kickoff ±tolerance.

    Prior ``matched`` rows for the same ``odds_event_id`` are reused so a
    one-day postpone keeps a single canonical key. Ambiguous windows are
    ``quarantined`` with null ``game_id`` — never guessed.
    """
    ingested = to_utc(ingested_at)
    prior_ids: dict[str, int] = {}
    if existing is not None and not existing.empty:
        matched = existing[(existing["match_status"] == "matched") & existing["game_id"].notna()]
        for row in matched.itertuples(index=False):
            prior_ids[str(row.odds_event_id)] = int(row.game_id)

    rows: list[dict[str, Any]] = []
    for ev in events:
        game_id: int | None = None
        status = "unmatched"
        delta_hours: float | None = None

        if ev.odds_event_id in prior_ids:
            game_id = prior_ids[ev.odds_event_id]
            status = "matched"
            if not schedule.empty:
                hit = schedule[schedule["game_id"] == game_id]
                if not hit.empty:
                    cfbd_kick = to_utc(pd.Timestamp(hit.iloc[0]["start_date"]).to_pydatetime())
                    delta_hours = abs((ev.kickoff - cfbd_kick).total_seconds()) / 3600.0
        else:
            if schedule.empty:
                cands = schedule
            else:
                cands = schedule[
                    (schedule["season"] == ev.season)
                    & (schedule["home_team"] == ev.home_team)
                    & (schedule["away_team"] == ev.away_team)
                ]
            if not cands.empty:
                kick = ev.kickoff
                delta_vals: list[float] = []
                for ts in cands["start_date"]:
                    cfbd_kick = to_utc(pd.Timestamp(ts).to_pydatetime())
                    delta_vals.append(abs((kick - cfbd_kick).total_seconds()) / 3600.0)
                deltas = pd.Series(delta_vals, index=cands.index)
                within = cands.loc[deltas <= tolerance.total_seconds() / 3600.0].copy()
                within["_delta"] = deltas.loc[within.index]
                if len(within) == 1:
                    game_id = int(within.iloc[0]["game_id"])
                    status = "matched"
                    delta_hours = float(within.iloc[0]["_delta"])
                elif len(within) > 1:
                    status = "quarantined"
                    delta_hours = float(within["_delta"].min())

        rows.append(
            {
                "odds_event_id": ev.odds_event_id,
                "game_id": game_id,
                "game_key": ev.game_key,
                "season": ev.season,
                "home_team": ev.home_team,
                "away_team": ev.away_team,
                "kickoff": ev.kickoff,
                "kickoff_delta_hours": delta_hours,
                "match_status": status,
                "source_version": source_version,
                # Knowable-at is match time, not future kickoff (PIT / schema).
                "event_time": ingested,
                "ingested_at": ingested,
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "odds_event_id",
                "game_id",
                "game_key",
                "season",
                "home_team",
                "away_team",
                "kickoff",
                "kickoff_delta_hours",
                "match_status",
                "source_version",
                "event_time",
                "ingested_at",
            ]
        )
    out = pd.DataFrame(rows)
    # Keep nullable int dtype when every game_id is null (unmatched season).
    out["game_id"] = out["game_id"].astype("Int64")
    out["season"] = out["season"].astype("int32")
    return out


def resolve_event_game_ids(crosswalk: pd.DataFrame) -> dict[str, int]:
    """Return ``odds_event_id`` → CFBD ``game_id`` for matched crosswalk rows."""
    if crosswalk.empty:
        return {}
    matched = crosswalk[(crosswalk["match_status"] == "matched") & crosswalk["game_id"].notna()]
    return {str(row.odds_event_id): int(row.game_id) for row in matched.itertuples(index=False)}


def write_odds_cfbd_crosswalk(store: ParquetStore, df: pd.DataFrame) -> int:
    """Upsert crosswalk rows by ``odds_event_id`` within each season partition.

    Returns the number of rows written across partitions (post-upsert counts).
    """
    if df.empty:
        return 0
    written = 0
    for season, part in df.groupby("season", sort=True):
        season_i = int(season)
        existing = store.read(
            "odds_cfbd_game_crosswalk",
            filters={"season": season_i},
        )
        if not existing.empty:
            ids = set(part["odds_event_id"].astype(str))
            kept = existing[~existing["odds_event_id"].astype(str).isin(ids)]
            combined = pd.concat([kept, part], ignore_index=True)
        else:
            combined = part
        store.write_partition(
            "odds_cfbd_game_crosswalk",
            combined,
            {"season": season_i},
            mode="overwrite",
        )
        written += len(combined)
    return written


def _enrich_frame_via_crosswalk(
    store: ParquetStore,
    payload: bytes | str | list[Any],
    team_map: Mapping[str, str],
    *,
    captured_at: datetime,
    ingested_at: datetime,
    snapshot_source: Literal["live", "historical"],
    decision_point: str | None,
    event_time: datetime | None,
) -> pd.DataFrame:
    """Match payload events, persist crosswalk, normalize with resolved game ids."""
    events = extract_odds_events(payload, team_map)
    seasons = sorted({ev.season for ev in events})
    if not seasons:
        seasons = [season_of(to_utc(captured_at))]
    schedule = load_cfbd_schedule(store, seasons, team_map)
    existing_parts: list[pd.DataFrame] = []
    for season in seasons:
        part = store.read("odds_cfbd_game_crosswalk", filters={"season": int(season)})
        if not part.empty:
            existing_parts.append(part)
    existing = pd.concat(existing_parts, ignore_index=True) if existing_parts else None
    crosswalk = match_odds_events_to_cfbd(
        events,
        schedule,
        existing=existing,
        ingested_at=ingested_at,
    )
    if not crosswalk.empty:
        write_odds_cfbd_crosswalk(store, crosswalk)
    return normalize_odds_payload(
        payload,
        captured_at=captured_at,
        ingested_at=ingested_at,
        team_map=team_map,
        snapshot_source=snapshot_source,
        decision_point=decision_point,
        event_time=event_time,
        event_game_ids=resolve_event_game_ids(crosswalk),
    )


def _captured_at_minute(series: pd.Series) -> pd.Series:
    return series.dt.floor("min")


def dedupe_snapshots(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per (game_key, book, market, side, line, price, minute).

    ``snapshot_source`` is intentionally excluded so historical rows covering
    the same moment collapse against live rows.
    """
    if df.empty:
        return df.copy()
    work = df.copy()
    work["captured_at_minute"] = _captured_at_minute(work["captured_at"])
    work = work.drop_duplicates(subset=list(_DEDUPE_COLS), keep="first")
    return work.drop(columns=["captured_at_minute"]).reset_index(drop=True)


def _ensure_odds_metadata_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Fill Task 5B columns on legacy live rows missing them."""
    if df.empty:
        return df
    work = df.copy()
    if "snapshot_source" not in work.columns:
        work["snapshot_source"] = "live"
    else:
        work["snapshot_source"] = work["snapshot_source"].fillna("live")
    if "decision_point" not in work.columns:
        work["decision_point"] = None
    if "n_books_available" not in work.columns:
        work["_minute"] = work["captured_at"].dt.floor("min")
        counts = work.groupby(["game_key", "_minute"], sort=False)["book"].transform("nunique")
        work["n_books_available"] = counts.astype("int32")
        work = work.drop(columns=["_minute"])
    else:
        missing = work["n_books_available"].isna()
        if missing.any():
            work["_minute"] = work["captured_at"].dt.floor("min")
            counts = work.groupby(["game_key", "_minute"], sort=False)["book"].transform("nunique")
            work.loc[missing, "n_books_available"] = counts[missing]
            work = work.drop(columns=["_minute"])
        work["n_books_available"] = work["n_books_available"].fillna(0).astype("int32")
    return work


def split_odds_by_line_sanity(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split rows by ``OddsSnapshotsSchema.line_sanity`` bounds (DESIGN §8).

    Bounds: ``|spread| < 70``, totals in ``[20, 100]``. Bad rows gain a
    ``quarantine_reason`` of ``spread_out_of_bounds`` or ``total_out_of_bounds``.
    Never drops rows — callers must stage good rows and quarantine bad ones.
    """
    if df.empty:
        empty = df.copy()
        return empty, empty
    work = df.copy()
    line = pd.to_numeric(work["line"], errors="coerce")
    spread_bad = line.notna() & (work["market"] == "spread") & ~((line > -70.0) & (line < 70.0))
    total_bad = line.notna() & (work["market"] == "total") & ~((line >= 20.0) & (line <= 100.0))
    bad_mask = spread_bad | total_bad
    good = work.loc[~bad_mask].copy().reset_index(drop=True)
    bad = work.loc[bad_mask].copy()
    if bad.empty:
        return good, bad.reset_index(drop=True)
    reason = pd.Series("total_out_of_bounds", index=work.index, dtype="object")
    reason.loc[spread_bad] = "spread_out_of_bounds"
    bad["quarantine_reason"] = reason.loc[bad_mask].to_numpy()
    return good, bad.reset_index(drop=True)


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{_QUARANTINE_PART}.tmp.{uuid.uuid4().hex}"
    try:
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, tmp, compression="snappy")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def append_odds_snapshots_quarantine(
    staged_root: Path | str,
    bad: pd.DataFrame,
    *,
    raw_archive_path: Path | str | None = None,
    requested_at: datetime | None = None,
) -> int:
    """Append out-of-bounds snapshot rows to the ingest quarantine sidecar.

    Layout::

        {staged}/odds_snapshots_quarantine/season={Y}/week={W}/part.parquet

    Adds ``quarantine_reason`` (required on ``bad``), ``raw_archive_path``,
    and ``requested_at``. Returned ``event_time`` and ``decision_point`` stay on
    the snapshot columns. Dedupes on ``snapshot_id``. Returns new rows added.
    """
    if bad.empty:
        return 0
    if "quarantine_reason" not in bad.columns:
        msg = "quarantine frame requires quarantine_reason"
        raise ValueError(msg)

    root = Path(staged_root)
    work = bad.copy()
    work["raw_archive_path"] = None if raw_archive_path is None else str(raw_archive_path)
    work["requested_at"] = pd.NaT if requested_at is None else pd.Timestamp(to_utc(requested_at))
    added = 0
    for (season, week), part in work.groupby(["season", "week"], sort=True):
        season_i = int(season)
        week_i = int(week)
        path = (
            root
            / _ODDS_QUARANTINE_TABLE
            / f"season={season_i}"
            / f"week={week_i}"
            / _QUARANTINE_PART
        )
        existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        before = len(existing)
        combined = pd.concat([existing, part], ignore_index=True) if before else part
        if "snapshot_id" in combined.columns:
            combined = combined.drop_duplicates(subset=["snapshot_id"], keep="first")
        _atomic_write_parquet(combined, path)
        added += len(combined) - before
    return added


def write_odds_snapshots(
    store: ParquetStore,
    df: pd.DataFrame,
    *,
    raw_archive_path: Path | str | None = None,
    requested_at: datetime | None = None,
) -> tuple[int, int]:
    """Append ``df`` into ``odds_snapshots`` partitions with minute-level dedupe.

    Out-of-bounds lines (same bounds as ``OddsSnapshotsSchema.line_sanity``) are
    split to ``odds_snapshots_quarantine`` before the pandera-validated write so
    staged ``odds_snapshots`` stays §8-clean. Never drops a bad row silently.

    Returns ``(rows_written, rows_quarantined)`` — new good rows staged and new
    quarantine sidecar rows appended.
    """
    log = get_logger(__name__)
    if df.empty:
        return 0, 0
    good, bad = split_odds_by_line_sanity(df)
    n_q = 0
    if not bad.empty:
        n_q = append_odds_snapshots_quarantine(
            store.root,
            bad,
            raw_archive_path=raw_archive_path,
            requested_at=requested_at,
        )
        sample_cols = [
            c
            for c in ("book", "market", "side", "line", "price", "quarantine_reason")
            if c in bad.columns
        ]
        sample = bad.head(5)[sample_cols]
        log.warning(
            "odds_line_sanity_quarantined",
            n_quarantined=n_q,
            n_bad_input=len(bad),
            raw_archive_path=None if raw_archive_path is None else str(raw_archive_path),
            sample=sample.to_dict(orient="records"),
        )
    if good.empty:
        return 0, n_q
    frame = dedupe_snapshots(_ensure_odds_metadata_columns(good))
    written = 0
    grouped = frame.groupby(["season", "week"], sort=True)
    for (season, week), part in grouped:
        season_i = int(season)
        week_i = int(week)
        partition = {"season": season_i, "week": week_i}
        existing = store.read(
            "odds_snapshots",
            filters={"season": season_i, "week": week_i},
        )
        before = len(existing)
        if not existing.empty:
            existing = _ensure_odds_metadata_columns(existing)
            combined = pd.concat([existing, part], ignore_index=True)
        else:
            combined = part
        combined = dedupe_snapshots(combined)
        store.write_partition(
            "odds_snapshots",
            combined,
            partition,
            mode="overwrite",
        )
        written += len(combined) - before
    return written, n_q


def backfill_live_odds_metadata(store: ParquetStore) -> int:
    """Fill ``snapshot_source`` / ``decision_point`` / ``n_books_available`` on live rows.

    Returns the number of partitions rewritten.
    """
    log = get_logger(__name__)
    rewritten = 0
    root = store.root / "odds_snapshots"
    if not root.is_dir():
        return 0
    partitions: list[tuple[int, int]] = []
    for season_dir in sorted(root.glob("season=*")):
        try:
            season = int(season_dir.name.split("=", 1)[1])
        except ValueError:
            continue
        for week_dir in sorted(season_dir.glob("week=*")):
            try:
                week = int(week_dir.name.split("=", 1)[1])
            except ValueError:
                continue
            partitions.append((season, week))

    for season, week in partitions:
        df = store.read("odds_snapshots", filters={"season": season, "week": week})
        if df.empty:
            continue
        work = _ensure_odds_metadata_columns(df)
        work.loc[work["snapshot_source"] == "live", "decision_point"] = None
        store.write_partition(
            "odds_snapshots",
            work,
            {"season": season, "week": week},
            mode="overwrite",
        )
        rewritten += 1
        log.info(
            "backfilled_live_odds_metadata",
            season=season,
            week=week,
            rows=len(work),
        )
    return rewritten


def _week_monday_utc(season: int, week: int) -> datetime:
    """UTC Monday 00:00 of CFB ``week`` within ``season`` (week_of convention)."""
    from ncaa_quant.utils.timeutils import _labor_day_monday

    week1 = _labor_day_monday(season)
    return week1 + timedelta(weeks=week - 1)


def tuesday_0600_et_for_week(season: int, week: int) -> datetime:
    """Tuesday 06:00 America/New_York for the given CFB week, as UTC."""
    monday_utc = _week_monday_utc(season, week)
    # Anchor on the UTC Monday date, then take that week's Tuesday 06:00 ET.
    monday_date = monday_utc.date()
    tuesday_et = datetime(
        monday_date.year,
        monday_date.month,
        monday_date.day,
        6,
        0,
        0,
        tzinfo=_ET,
    ) + timedelta(days=1)
    return to_utc(tuesday_et)


def saturday_0600_et_for_week(season: int, week: int) -> datetime:
    """Saturday 06:00 America/New_York for the given CFB week, as UTC.

    DESIGN §9.8 / ADR 0002 daily-refresh decision point. ZoneInfo handles DST.
    """
    from ncaa_quant.utils.timeutils import resolve_decision_point

    monday_utc = _week_monday_utc(season, week)
    saturday_date = monday_utc.date() + timedelta(days=5)
    return resolve_decision_point("saturday_0600_et", saturday_date)


def plan_historical_units(
    store: ParquetStore,
    seasons: Sequence[int],
    *,
    decision_points: Sequence[str] | None = None,
    config: AppConfig | None = None,
) -> HistoricalPlan:
    """Enumerate (season, week, decision_point) units from staged ``games``."""
    cfg = config or load_config()
    dps = list(decision_points or cfg.data.odds_historical_decision_points)
    units: list[HistoricalUnit] = []
    counts: dict[tuple[int, str], int] = {}

    for season in seasons:
        games = store.read("games", filters={"season": int(season)})
        if games.empty:
            continue
        weeks = sorted(int(w) for w in games["week"].dropna().unique())
        for week in weeks:
            week_games = games[games["week"] == week]
            if week_games.empty:
                continue
            for dp in dps:
                reqs: tuple[datetime, ...]
                if dp == "tuesday_0600_et":
                    reqs = (tuesday_0600_et_for_week(int(season), week),)
                elif dp == "saturday_0600_et":
                    reqs = (saturday_0600_et_for_week(int(season), week),)
                elif dp == "slot_close":
                    kicks = [
                        to_utc(pd.Timestamp(ts).to_pydatetime())
                        for ts in week_games["start_date"].unique()
                    ]
                    reqs = tuple(sorted({k - timedelta(minutes=5) for k in kicks}))
                else:
                    msg = f"Unknown decision point: {dp}"
                    raise ValueError(msg)
                units.append(
                    HistoricalUnit(
                        season=int(season),
                        week=week,
                        decision_point=dp,
                        request_times=reqs,
                    )
                )
                key = (int(season), dp)
                counts[key] = counts.get(key, 0) + len(reqs)

    total_requests = sum(len(u.request_times) for u in units)
    credits_per = int(cfg.data.odds_historical_credits_per_call)
    return HistoricalPlan(
        units=tuple(units),
        requests_by_season_dp=counts,
        total_requests=total_requests,
        credits_per_call=credits_per,
        total_credits=total_requests * credits_per,
        ceiling=int(cfg.data.odds_historical_credit_ceiling),
    )


def estimate_historical_credits(
    store: ParquetStore,
    seasons: Sequence[int],
    *,
    config: AppConfig | None = None,
    remaining_quota: int | None = None,
) -> tuple[HistoricalPlan, list[str]]:
    """Build the plan and human-readable estimate lines (no network)."""
    plan = plan_historical_units(store, seasons, config=config)
    lines = [
        "Odds API historical cost estimate (no spend)",
        f"seasons={list(seasons)}",
        f"credits_per_call={plan.credits_per_call} (10 x markets x regions)",
        f"total_requests={plan.total_requests}",
        f"total_credits={plan.total_credits}",
        f"ceiling={plan.ceiling}",
    ]
    by_season: dict[int, dict[str, int]] = {}
    for (season, dp), n in sorted(plan.requests_by_season_dp.items()):
        by_season.setdefault(season, {})[dp] = n
    for season, dps in by_season.items():
        parts = " ".join(f"{dp}={n}" for dp, n in sorted(dps.items()))
        season_reqs = sum(dps.values())
        lines.append(
            f"  season {season}: requests={season_reqs} "
            f"credits={season_reqs * plan.credits_per_call} ({parts})"
        )
    if remaining_quota is not None:
        lines.append(
            f"projected_remaining_after={remaining_quota - plan.total_credits} "
            f"(from remaining={remaining_quota})"
        )
    else:
        lines.append("projected_remaining_after=N/A (remaining quota unknown)")
    if plan.over_ceiling:
        lines.append(
            f"REFUSE: total_credits {plan.total_credits} exceeds "
            f"ceiling {plan.ceiling} (pass --force to override)"
        )
    return plan, lines


def run_odds_raw_capture(
    *,
    config: AppConfig | None = None,
    api_key: str | None = None,
    raw_root: Path | str | None = None,
    captured_at: datetime | None = None,
    client: OddsAPIClient | None = None,
) -> OddsRawCaptureResult:
    """Fetch live Odds API payload and archive it verbatim (Task 4a).

    Uses the same httpx client retries (5× exponential on 429/5xx/timeout) and
    rate-limit reserve guard as the full ingest path. Does **not** normalize or
    write Parquet — those land in the remainder of Task 4. From this point,
    every scheduled pull persists a recoverable raw JSON snapshot.
    """
    cfg = config or load_config()
    key = api_key if api_key is not None else load_secrets().odds_api_key.get_secret_value()
    captured = to_utc(captured_at or datetime.now(tz=UTC))
    raw_dir = Path(raw_root) if raw_root is not None else Path(cfg.paths.raw_dir) / "odds_api"

    owns_client = client is None
    odds_client = client or OddsAPIClient(
        key,
        books=cfg.data.odds_books,
        markets=cfg.data.odds_markets,
        regions=cfg.data.odds_regions,
        rate_limit_reserve=cfg.data.odds_rate_limit_reserve,
        budget_kind="live",
    )
    try:
        body, _headers = odds_client.fetch_odds()
        raw_path = archive_raw_response(raw_dir, captured, body)
        return OddsRawCaptureResult(
            raw_path=raw_path,
            captured_at=captured,
            bytes_written=len(body),
        )
    finally:
        if owns_client:
            odds_client.close()


def run_odds_ingest(
    *,
    config: AppConfig | None = None,
    api_key: str | None = None,
    raw_root: Path | str | None = None,
    staged_root: Path | str | None = None,
    captured_at: datetime | None = None,
    client: OddsAPIClient | None = None,
    team_map: Mapping[str, str] | None = None,
) -> OddsIngestResult:
    """Fetch → archive raw → normalize → dedupe → Parquet (live path).

    Raw archival happens before parse so parser exceptions never lose the body.
    Prefer :func:`run_odds_raw_capture` when only archival is required (Task 4a).
    """
    cfg = config or load_config()
    key = api_key if api_key is not None else load_secrets().odds_api_key.get_secret_value()
    captured = to_utc(captured_at or datetime.now(tz=UTC))
    ingested = datetime.now(tz=UTC)

    raw_dir = Path(raw_root) if raw_root is not None else Path(cfg.paths.raw_dir) / "odds_api"
    staged_dir = Path(staged_root) if staged_root is not None else Path(cfg.paths.staged_dir)

    names = (
        dict(team_map)
        if team_map is not None
        else load_team_name_map(Path(cfg.data.team_names_path))
    )

    owns_client = client is None
    odds_client = client or OddsAPIClient(
        key,
        books=cfg.data.odds_books,
        markets=cfg.data.odds_markets,
        regions=cfg.data.odds_regions,
        rate_limit_reserve=cfg.data.odds_rate_limit_reserve,
        budget_kind="live",
    )
    try:
        # Archive first via the raw-capture path so parse failures never lose bytes.
        raw = run_odds_raw_capture(
            config=cfg,
            api_key=key,
            raw_root=raw_dir,
            captured_at=captured,
            client=odds_client,
        )
        body = raw.raw_path.read_bytes()
        with ParquetStore(staged_dir) as store:
            frame = _enrich_frame_via_crosswalk(
                store,
                body,
                names,
                captured_at=captured,
                ingested_at=ingested,
                snapshot_source="live",
                decision_point=None,
                event_time=captured,
            )
            added, _quarantined = write_odds_snapshots(
                store,
                frame,
                raw_archive_path=raw.raw_path,
            )
        return OddsIngestResult(
            raw_path=raw.raw_path,
            rows_written=added,
            rows_fetched=len(frame),
            captured_at=captured,
        )
    finally:
        if owns_client:
            odds_client.close()


def run_historical_backfill(
    *,
    seasons: Sequence[int],
    config: AppConfig | None = None,
    api_key: str | None = None,
    raw_root: Path | str | None = None,
    staged_root: Path | str | None = None,
    client: OddsAPIClient | None = None,
    team_map: Mapping[str, str] | None = None,
    force: bool = False,
    skip_calibration: bool = False,
    backfill_live_meta: bool = True,
) -> HistoricalBackfillResult:
    """Resumable historical odds backfill keyed by (season, week, decision_point).

    The first real call is a calibration probe asserting ``x-requests-last``
    equals the configured credits-per-call. On mismatch the run aborts before
    the loop.
    """
    cfg = config or load_config()
    log = get_logger(__name__)
    key = api_key if api_key is not None else load_secrets().odds_api_key.get_secret_value()
    raw_dir = (
        Path(raw_root) if raw_root is not None else Path(cfg.paths.raw_dir) / "odds_api_historical"
    )
    staged_dir = Path(staged_root) if staged_root is not None else Path(cfg.paths.staged_dir)
    names = (
        dict(team_map)
        if team_map is not None
        else load_team_name_map(Path(cfg.data.team_names_path))
    )

    result = HistoricalBackfillResult()
    owns_client = client is None
    odds_client = client or OddsAPIClient(
        key,
        books=cfg.data.odds_books,
        markets=cfg.data.odds_markets,
        regions=cfg.data.odds_regions,
        rate_limit_reserve=cfg.data.odds_rate_limit_reserve,
        budget_kind="historical",
        historical_credit_ceiling=cfg.data.odds_historical_credit_ceiling,
        credits_per_historical_call=cfg.data.odds_historical_credits_per_call,
        force_ceiling=force,
    )

    try:
        with ParquetStore(staged_dir) as store:
            if backfill_live_meta:
                backfill_live_odds_metadata(store)

            plan = plan_historical_units(store, seasons, config=cfg)
            if plan.over_ceiling and not force:
                msg = (
                    f"Estimated credits {plan.total_credits} exceed ceiling "
                    f"{plan.ceiling}; pass --force to override"
                )
                raise HistoricalBudgetCeilingError(msg)

            calibrated = skip_calibration
            for unit in plan.units:
                if (
                    is_unit_complete(raw_dir, unit.season, unit.week, unit.decision_point)
                    and not force
                ):
                    result.units_skipped += 1
                    log.info(
                        "historical_unit_skipped",
                        season=unit.season,
                        week=unit.week,
                        decision_point=unit.decision_point,
                    )
                    continue

                for req_time in unit.request_times:
                    archive_path = (
                        None if force else _find_historical_slot_archive(raw_dir, req_time)
                    )
                    if archive_path is not None:
                        if _is_empty_historical_slot(raw_dir, req_time):
                            log.info(
                                "historical_slot_skipped",
                                season=unit.season,
                                week=unit.week,
                                decision_point=unit.decision_point,
                                requested_at=req_time.isoformat(),
                                reason="empty_slot_marker",
                            )
                            continue
                        envelope = parse_historical_envelope(
                            archive_path.read_bytes(),
                            requested_at=req_time,
                        )
                        if _staged_has_historical_slot(
                            store,
                            season=unit.season,
                            week=unit.week,
                            decision_point=unit.decision_point,
                            event_time=envelope.timestamp,
                        ):
                            log.info(
                                "historical_slot_skipped",
                                season=unit.season,
                                week=unit.week,
                                decision_point=unit.decision_point,
                                requested_at=req_time.isoformat(),
                                returned_at=envelope.timestamp.isoformat(),
                                reason="staged_rows_present",
                            )
                            continue
                        # Archive exists but staged is empty → zero-credit replay.
                        added, quarantined = _stage_historical_envelope(
                            store,
                            envelope,
                            team_map=names,
                            decision_point=unit.decision_point,
                            raw_path=archive_path,
                        )
                        if not envelope.data:
                            _mark_empty_historical_slot(raw_dir, req_time)
                        result.rows_written += added
                        result.rows_quarantined += quarantined
                        log.info(
                            "historical_slot_replayed",
                            season=unit.season,
                            week=unit.week,
                            decision_point=unit.decision_point,
                            requested_at=envelope.requested_at.isoformat(),
                            returned_at=envelope.timestamp.isoformat(),
                            rows=added,
                            rows_quarantined=quarantined,
                            raw_path=str(archive_path),
                        )
                        continue

                    if not calibrated:
                        probe = odds_client.fetch_historical_odds(req_time)
                        last = odds_client.last_requests_last
                        expected = cfg.data.odds_historical_credits_per_call
                        log.info(
                            "historical_calibration_probe",
                            x_requests_last=last,
                            expected=expected,
                        )
                        if last != expected:
                            msg = (
                                f"Calibration failed: x-requests-last={last} "
                                f"!= expected {expected}; aborting before backfill loop"
                            )
                            raise CalibrationError(msg)
                        result.calibration_last = last
                        calibrated = True
                        # Use the probe response rather than re-fetching.
                        envelope = probe
                    else:
                        envelope = odds_client.fetch_historical_odds(req_time)

                    raw_path = archive_historical_response(
                        raw_dir,
                        envelope.requested_at,
                        envelope.timestamp,
                        envelope.raw_body,
                    )
                    result.raw_paths.append(raw_path)
                    result.requests_made += 1

                    added, quarantined = _stage_historical_envelope(
                        store,
                        envelope,
                        team_map=names,
                        decision_point=unit.decision_point,
                        raw_path=raw_path,
                    )
                    if not envelope.data:
                        _mark_empty_historical_slot(raw_dir, req_time)
                    result.rows_written += added
                    result.rows_quarantined += quarantined
                    result.credits_spent = odds_client.credits_spent
                    log.info(
                        "historical_slot_complete",
                        season=unit.season,
                        week=unit.week,
                        decision_point=unit.decision_point,
                        requested_at=envelope.requested_at.isoformat(),
                        returned_at=envelope.timestamp.isoformat(),
                        rows=added,
                        rows_quarantined=quarantined,
                        credits_spent=result.credits_spent,
                        remaining=odds_client.remaining_requests,
                    )

                mark_unit_complete(raw_dir, unit.season, unit.week, unit.decision_point)
                result.units_written += 1
                log.info(
                    "historical_unit_complete",
                    season=unit.season,
                    week=unit.week,
                    decision_point=unit.decision_point,
                    credits_spent=result.credits_spent,
                )
        return result
    finally:
        if owns_client:
            odds_client.close()


def _stage_historical_envelope(
    store: ParquetStore,
    envelope: HistoricalOddsResponse,
    *,
    team_map: Mapping[str, str],
    decision_point: str,
    raw_path: Path,
) -> tuple[int, int]:
    """Normalize → quarantine-split → stage one historical envelope.

    ``event_time`` is the envelope's returned ``timestamp``, never the request.
    Returns ``(rows_written, rows_quarantined)``.
    """
    ingested = datetime.now(tz=UTC)
    frame = _enrich_frame_via_crosswalk(
        store,
        envelope.data,
        team_map,
        captured_at=envelope.timestamp,
        ingested_at=ingested,
        snapshot_source="historical",
        decision_point=decision_point,
        event_time=envelope.timestamp,
    )
    return write_odds_snapshots(
        store,
        frame,
        raw_archive_path=raw_path,
        requested_at=envelope.requested_at,
    )


def _game_keys_from_games(store: ParquetStore, seasons: Sequence[int]) -> pd.DataFrame:
    """Build game_id → game_key mapping from staged games + teams."""
    frames: list[pd.DataFrame] = []
    for season in seasons:
        games = store.read("games", filters={"season": int(season)})
        if games.empty:
            continue
        teams = store.read("teams", filters={"season": int(season)})
        if teams.empty:
            continue
        id_to_school = {int(r.team_id): str(r.school) for r in teams.itertuples(index=False)}
        rows: list[dict[str, Any]] = []
        for g in games.itertuples(index=False):
            home = id_to_school.get(int(g.home_team_id))
            away = id_to_school.get(int(g.away_team_id))
            if home is None or away is None:
                continue
            kickoff = to_utc(pd.Timestamp(g.start_date).to_pydatetime())
            rows.append(
                {
                    "game_id": int(g.game_id),
                    "season": int(g.season),
                    "week": int(g.week),
                    "game_key": make_game_key(int(g.season), home, away, kickoff.date()),
                    "home_team": home,
                }
            )
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        return pd.DataFrame(columns=["game_id", "season", "week", "game_key", "home_team"])
    return pd.concat(frames, ignore_index=True)


def reconcile_cfbd_close_vs_slot_close(
    store: ParquetStore,
    seasons: Sequence[int],
) -> ReconcileReport:
    """Compare CFBD close lines to historical ``slot_close`` snapshot lines.

    Bias beyond tolerance is a finding to write up — never corrected with an
    offset here.
    """
    keys = _game_keys_from_games(store, seasons)
    if keys.empty:
        return ReconcileReport(n_games=0, spread_diffs=(), total_diffs=())

    spread_diffs: list[float] = []
    total_diffs: list[float] = []
    matched_games: set[int] = set()

    for season in seasons:
        lines = store.read("lines_historical", filters={"season": int(season)})
        odds = store.read("odds_snapshots", filters={"season": int(season)})
        if lines.empty or odds.empty:
            continue
        closes = lines[lines["line_type"] == "close"].copy()
        slots = odds[
            (odds["decision_point"] == "slot_close")
            & (odds["snapshot_source"] == "historical")
            & (odds["market"].isin(["spread", "total"]))
        ].copy()
        if closes.empty or slots.empty:
            continue
        season_keys = keys[keys["season"] == int(season)]
        closes = closes.merge(season_keys[["game_id", "game_key", "home_team"]], on="game_id")

        for game_id, gclose in closes.groupby("game_id"):
            game_key = str(gclose.iloc[0]["game_key"])
            home = str(gclose.iloc[0]["home_team"])
            gslots = slots[slots["game_key"] == game_key]
            if gslots.empty:
                continue

            # Prefer matched book names (casefold); else median across books.
            cfbd_books = {str(b).casefold(): b for b in gclose["book"].unique()}
            snap_books = {str(b).casefold(): b for b in gslots["book"].unique()}
            shared = set(cfbd_books) & set(snap_books)

            home_team = home

            def _cfbd_spread(book_mask: pd.DataFrame) -> float | None:
                vals = book_mask["spread"].dropna()
                return float(vals.median()) if not vals.empty else None

            def _cfbd_total(book_mask: pd.DataFrame) -> float | None:
                vals = book_mask["total"].dropna()
                return float(vals.median()) if not vals.empty else None

            def _snap_home_spread(
                book_mask: pd.DataFrame,
                *,
                home_side: str = home_team,
            ) -> float | None:
                home_rows = book_mask[
                    (book_mask["market"] == "spread") & (book_mask["side"] == home_side)
                ]
                vals = home_rows["line"].dropna()
                return float(vals.median()) if not vals.empty else None

            def _snap_total(book_mask: pd.DataFrame) -> float | None:
                tot = book_mask[book_mask["market"] == "total"]
                vals = tot["line"].dropna()
                return float(vals.median()) if not vals.empty else None

            if shared:
                for bkey in shared:
                    c_sub = gclose[gclose["book"].str.casefold() == bkey]
                    s_sub = gslots[gslots["book"].str.casefold() == bkey]
                    cs = _cfbd_spread(c_sub)
                    ss = _snap_home_spread(s_sub)
                    if cs is not None and ss is not None:
                        spread_diffs.append(ss - cs)
                        matched_games.add(int(game_id))
                    ct = _cfbd_total(c_sub)
                    st = _snap_total(s_sub)
                    if ct is not None and st is not None:
                        total_diffs.append(st - ct)
                        matched_games.add(int(game_id))
            else:
                cs = _cfbd_spread(gclose)
                ss = _snap_home_spread(gslots)
                if cs is not None and ss is not None:
                    spread_diffs.append(ss - cs)
                    matched_games.add(int(game_id))
                ct = _cfbd_total(gclose)
                st = _snap_total(gslots)
                if ct is not None and st is not None:
                    total_diffs.append(st - ct)
                    matched_games.add(int(game_id))

    return ReconcileReport(
        n_games=len(matched_games),
        spread_diffs=tuple(spread_diffs),
        total_diffs=tuple(total_diffs),
    )


def coverage_report(
    store: ParquetStore,
    seasons: Sequence[int],
    *,
    decision_points: Sequence[str] | None = None,
    config: AppConfig | None = None,
) -> list[str]:
    """Coverage % and n_books_available trajectory lines for notes/acceptance."""
    cfg = config or load_config()
    dps = list(decision_points or cfg.data.odds_historical_decision_points)
    plan = plan_historical_units(store, seasons, decision_points=dps, config=cfg)
    lines: list[str] = ["snapshot coverage by season × decision_point:"]
    for season in seasons:
        odds = store.read("odds_snapshots", filters={"season": int(season)})
        planned = [u for u in plan.units if u.season == season]
        for dp in dps:
            units_dp = [u for u in planned if u.decision_point == dp]
            if not units_dp:
                lines.append(f"  {season} {dp}: planned=0")
                continue
            if odds.empty:
                covered = 0
            else:
                covered = 0
                for u in units_dp:
                    part = odds[
                        (odds["week"] == u.week)
                        & (odds["decision_point"] == dp)
                        & (odds["snapshot_source"] == "historical")
                    ]
                    if not part.empty:
                        covered += 1
            pct = 100.0 * covered / len(units_dp) if units_dp else 0.0
            lines.append(f"  {season} {dp}: {covered}/{len(units_dp)} units ({pct:.1f}%)")
        if not odds.empty:
            hist = odds[odds["snapshot_source"] == "historical"]
            if not hist.empty and "n_books_available" in hist.columns:
                mean_books = float(hist["n_books_available"].mean())
                lines.append(f"  {season} mean n_books_available={mean_books:.2f}")
    return lines


def preview_crosswalk_game_key_regression(
    store: ParquetStore,
    seasons: Sequence[int],
    team_map: Mapping[str, str],
) -> list[CrosswalkRegressionFailure]:
    """Return previously-matched events whose ``game_key`` would change under ``team_map``.

    Re-normalizes stored crosswalk home/away through ``team_map`` and compares the
    derived matcher key. Does not write. Used as a pre-flight gate before archive
    replay so a name-map patch cannot silently re-key settled matches.
    """
    failures: list[CrosswalkRegressionFailure] = []
    for season in seasons:
        cw = store.read("odds_cfbd_game_crosswalk", filters={"season": int(season)})
        if cw.empty:
            continue
        matched = cw[(cw["match_status"] == "matched") & cw["game_id"].notna()]
        for row in matched.itertuples(index=False):
            home = normalize_team_name(str(row.home_team), team_map)
            away = normalize_team_name(str(row.away_team), team_map)
            kick = to_utc(pd.Timestamp(row.kickoff).to_pydatetime())
            new_key = make_game_key(int(row.season), home, away, kick.date())
            old_key = str(row.game_key)
            if new_key != old_key:
                failures.append(
                    CrosswalkRegressionFailure(
                        odds_event_id=str(row.odds_event_id),
                        season=int(row.season),
                        old_game_key=old_key,
                        new_game_key=new_key,
                    )
                )
    return failures


def _wipe_historical_odds_partitions(store: ParquetStore, seasons: Sequence[int]) -> None:
    """Drop historical ``odds_snapshots`` rows for ``seasons``; keep live rows."""
    root = store.root / "odds_snapshots"
    if not root.is_dir():
        return
    for season in seasons:
        season_dir = root / f"season={int(season)}"
        if not season_dir.is_dir():
            continue
        for week_dir in sorted(season_dir.glob("week=*")):
            try:
                week = int(week_dir.name.split("=", 1)[1])
            except ValueError:
                continue
            df = store.read(
                "odds_snapshots",
                filters={"season": int(season), "week": week},
            )
            if df.empty:
                continue
            live = df[df["snapshot_source"] == "live"]
            part_path = week_dir / _QUARANTINE_PART
            if live.empty:
                part_path.unlink(missing_ok=True)
                continue
            store.write_partition(
                "odds_snapshots",
                live,
                {"season": int(season), "week": week},
                mode="overwrite",
            )


def _wipe_crosswalk_partitions(store: ParquetStore, seasons: Sequence[int]) -> None:
    """Remove ``odds_cfbd_game_crosswalk`` season partitions so replay rebuilds them."""
    root = store.root / "odds_cfbd_game_crosswalk"
    if not root.is_dir():
        return
    for season in seasons:
        part = root / f"season={int(season)}" / _QUARANTINE_PART
        part.unlink(missing_ok=True)


def _wipe_odds_quarantine_seasons(staged_root: Path, seasons: Sequence[int]) -> None:
    """Remove ingest quarantine sidecars for ``seasons`` before archive replay."""
    root = Path(staged_root) / _ODDS_QUARANTINE_TABLE
    if not root.is_dir():
        return
    season_set = {int(s) for s in seasons}
    for season_dir in sorted(root.glob("season=*")):
        try:
            season = int(season_dir.name.split("=", 1)[1])
        except ValueError:
            continue
        if season not in season_set:
            continue
        for week_dir in season_dir.glob("week=*"):
            (week_dir / _QUARANTINE_PART).unlink(missing_ok=True)


def replay_historical_from_archives(
    seasons: Sequence[int],
    *,
    config: AppConfig | None = None,
    raw_root: Path | str | None = None,
    staged_root: Path | str | None = None,
    team_map: Mapping[str, str] | None = None,
    allow_game_key_regression: bool = False,
) -> HistoricalReplayResult:
    """Re-normalize historical odds + re-resolve crosswalk from raw archives only.

    Zero API spend: walks ``plan_historical_units`` request times, loads each
    slot's on-disk archive (or empty-slot marker), and re-stages. Historical
    ``odds_snapshots`` rows for ``seasons`` are wiped first (live rows kept);
    crosswalk and ingest-quarantine partitions for those seasons are rebuilt.

    Raises ``RuntimeError`` if a required archive is missing (would imply an API
    call) or if previously-matched events would change ``game_key`` unless
    ``allow_game_key_regression`` is true.
    """
    cfg = config or load_config()
    log = get_logger(__name__)
    raw_dir = (
        Path(raw_root) if raw_root is not None else Path(cfg.paths.raw_dir) / "odds_api_historical"
    )
    staged_dir = Path(staged_root) if staged_root is not None else Path(cfg.paths.staged_dir)
    names = (
        dict(team_map)
        if team_map is not None
        else load_team_name_map(Path(cfg.data.team_names_path))
    )
    season_list = [int(s) for s in seasons]
    result = HistoricalReplayResult(seasons=list(season_list))

    with ParquetStore(staged_dir) as store:
        for season in season_list:
            odds = store.read("odds_snapshots", filters={"season": season})
            hist_n = 0 if odds.empty else int((odds["snapshot_source"] == "historical").sum())
            result.row_counts_before[season] = hist_n

        regressions = preview_crosswalk_game_key_regression(store, season_list, names)
        result.prior_matched = 0
        for season in season_list:
            cw = store.read("odds_cfbd_game_crosswalk", filters={"season": season})
            if cw.empty:
                continue
            result.prior_matched += int(
                ((cw["match_status"] == "matched") & cw["game_id"].notna()).sum()
            )
        if regressions and not allow_game_key_regression:
            result.regression_failures = list(regressions)
            msg = (
                "STOP: previously-matched crosswalk events would change game_key "
                f"({len(regressions)} events). Refusing replay."
            )
            raise RuntimeError(msg)

        plan = plan_historical_units(store, season_list, config=cfg)
        # Preflight: every non-empty request must have an archive on disk.
        missing: list[str] = []
        for unit in plan.units:
            for req_time in unit.request_times:
                if _is_empty_historical_slot(raw_dir, req_time):
                    continue
                if _find_historical_slot_archive(raw_dir, req_time) is None:
                    missing.append(
                        f"{unit.season}_w{unit.week}_{unit.decision_point}_"
                        f"{to_utc(req_time).isoformat()}"
                    )
        if missing:
            sample = ", ".join(missing[:10])
            msg = (
                "STOP: archive missing for replay (would require API spend). "
                f"missing={len(missing)} sample=[{sample}]"
            )
            raise RuntimeError(msg)

        _wipe_historical_odds_partitions(store, season_list)
        _wipe_crosswalk_partitions(store, season_list)
        _wipe_odds_quarantine_seasons(staged_dir, season_list)
        log.info(
            "historical_replay_wiped",
            seasons=season_list,
            prior_matched=result.prior_matched,
        )

        for unit in plan.units:
            for req_time in unit.request_times:
                if _is_empty_historical_slot(raw_dir, req_time):
                    continue
                archive_path = _find_historical_slot_archive(raw_dir, req_time)
                if archive_path is None:  # pragma: no cover — guarded above
                    msg = f"archive disappeared during replay: {req_time.isoformat()}"
                    raise RuntimeError(msg)
                envelope = parse_historical_envelope(
                    archive_path.read_bytes(),
                    requested_at=req_time,
                )
                added, quarantined = _stage_historical_envelope(
                    store,
                    envelope,
                    team_map=names,
                    decision_point=unit.decision_point,
                    raw_path=archive_path,
                )
                result.archives_replayed += 1
                result.rows_written += added
                result.rows_quarantined += quarantined
                log.info(
                    "historical_replay_slot",
                    season=unit.season,
                    week=unit.week,
                    decision_point=unit.decision_point,
                    requested_at=req_time.isoformat(),
                    returned_at=envelope.timestamp.isoformat(),
                    rows=added,
                    rows_quarantined=quarantined,
                    raw_path=str(archive_path),
                )

        # Post-replay regression: previously-matched keys must still match.
        post = preview_crosswalk_game_key_regression(store, season_list, names)
        # preview compares stored keys to remapped names — after successful
        # rewrite stored keys already use the new map, so post should be empty.
        # Also verify prior event ids remain matched with same game_key via
        # caller-held snapshot; here we only flag internal inconsistency.
        result.regression_failures = list(post)

        for season in season_list:
            odds = store.read("odds_snapshots", filters={"season": season})
            hist_n = 0 if odds.empty else int((odds["snapshot_source"] == "historical").sum())
            result.row_counts_after[season] = hist_n

    return result
