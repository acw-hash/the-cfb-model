"""The Odds API NCAAF snapshot ingestion (live + historical).

Captures unbackfillable live odds and credit-metered historical snapshots.
Every response is archived verbatim *before* parsing so a parser failure never
loses the payload.

**Decision-point schedule (Task 5B v1).** Pre-registered in
``configs/data.yaml`` as ``odds_historical_decision_points``. v1 pulls only:

- ``tuesday_0600_et`` — one request per CFB week at Tuesday 06:00 America/New_York
- ``slot_close`` — one request per distinct kickoff slot at slot minus 5 minutes

DESIGN §9.8 lists additional production decision points (Thu/Sat 06:00 ET,
T−6h, T−1h). Those are intentionally deferred for budget; adding or removing a
decision point later invalidates backtest comparability with earlier runs.

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
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

import httpx
import pandas as pd  # type: ignore[import-untyped]
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
    "HistoricalBudgetCeilingError",
    "HistoricalOddsResponse",
    "HistoricalPlan",
    "HistoricalUnit",
    "OddsAPIClient",
    "OddsAPIError",
    "OddsIngestResult",
    "RateLimitBudgetError",
    "ReconcileReport",
    "archive_historical_response",
    "archive_raw_response",
    "asof_tolerance_for",
    "backfill_live_odds_metadata",
    "dedupe_snapshots",
    "estimate_historical_credits",
    "is_unit_complete",
    "load_team_name_map",
    "make_game_key",
    "mark_unit_complete",
    "normalize_odds_payload",
    "normalize_team_name",
    "parse_historical_envelope",
    "plan_historical_units",
    "reconcile_cfbd_close_vs_slot_close",
    "run_historical_backfill",
    "run_odds_ingest",
    "run_odds_raw_capture",
    "tuesday_0600_et_for_week",
    "write_odds_snapshots",
)

# httpx logs full request URLs at INFO, which would leak apiKey query params.
logging.getLogger("httpx").setLevel(logging.WARNING)
SOURCE_VERSION: Final[str] = "odds_api_v4"
SPORT_KEY: Final[str] = "americanfootball_ncaaf"
BASE_URL: Final[str] = "https://api.the-odds-api.com/v4"
_ET: Final[ZoneInfo] = ZoneInfo("America/New_York")
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

DecisionPoint = Literal["tuesday_0600_et", "slot_close"]
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
    calibration_last: int | None = None
    raw_paths: list[Path] = field(default_factory=list)


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


def _historical_slot_archive_exists(raw_root: Path, requested_at: datetime) -> bool:
    """True if any archive for this requested timestamp already exists."""
    requested = to_utc(requested_at)
    day = requested.date().isoformat()
    req_stamp = requested.strftime("%Y%m%dT%H%M%S%fZ")
    directory = raw_root / day
    if not directory.is_dir():
        return False
    return any(p.name.startswith(f"{req_stamp}_") for p in directory.glob("*.json"))


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
    """Write the resumability marker for a completed unit."""
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
) -> pd.DataFrame:
    """Parse Odds API JSON into an ``odds_snapshots``-shaped DataFrame.

    For live pulls, ``event_time`` defaults to ``captured_at``. For historical
    pulls, pass the envelope's returned ``timestamp`` as both ``captured_at``
    and ``event_time`` — never the request ``date``.
    """
    captured = to_utc(captured_at)
    ingested = to_utc(ingested_at)
    knowable = to_utc(event_time) if event_time is not None else captured
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
                            "game_id": None,
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


def write_odds_snapshots(store: ParquetStore, df: pd.DataFrame) -> int:
    """Append ``df`` into ``odds_snapshots`` partitions with minute-level dedupe.

    Returns the number of new rows actually added across all partitions.
    """
    if df.empty:
        return 0
    frame = dedupe_snapshots(_ensure_odds_metadata_columns(df))
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
    return written


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
        frame = normalize_odds_payload(
            body,
            captured_at=captured,
            ingested_at=ingested,
            team_map=names,
            snapshot_source="live",
            decision_point=None,
            event_time=captured,
        )
        with ParquetStore(staged_dir) as store:
            added = write_odds_snapshots(store, frame)
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
                    if not force and _historical_slot_archive_exists(raw_dir, req_time):
                        log.info(
                            "historical_slot_skipped",
                            season=unit.season,
                            week=unit.week,
                            decision_point=unit.decision_point,
                            requested_at=req_time.isoformat(),
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

                    ingested = datetime.now(tz=UTC)
                    # CRITICAL: event_time = returned timestamp, not request date.
                    frame = normalize_odds_payload(
                        envelope.data,
                        captured_at=envelope.timestamp,
                        ingested_at=ingested,
                        team_map=names,
                        snapshot_source="historical",
                        decision_point=unit.decision_point,
                        event_time=envelope.timestamp,
                    )
                    added = write_odds_snapshots(store, frame)
                    result.rows_written += added
                    result.credits_spent = odds_client.credits_spent
                    log.info(
                        "historical_slot_complete",
                        season=unit.season,
                        week=unit.week,
                        decision_point=unit.decision_point,
                        requested_at=envelope.requested_at.isoformat(),
                        returned_at=envelope.timestamp.isoformat(),
                        rows=added,
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
