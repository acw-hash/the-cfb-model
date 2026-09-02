"""Forward kickoff-aligned ``slot_close`` capture (V4-B1).

Historical backfill tags ``decision_point=slot_close`` at 30 credits/call, but
forward season closes are perishable: a kickoff−5min line cannot be recovered
from a later wall-clock ``ingest_odds`` pull. This module captures one live Odds
API snapshot **per distinct kickoff slot** at kickoff minus
:data:`~ncaa_quant.features.market_lines.SLOT_CLOSE_LEAD` (5 minutes), stages
per-book rows for same-book CLV settlement, and records idempotency / missed-slot
state on disk.

DESIGN §2.7 closing line: *the last captured snapshot strictly before kickoff*.
TASKS.md / §9.8 decision point ``slot_close``: one request per distinct kickoff
slot at slot minus 5 minutes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

import httpx
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.betting.clv import ClosingQuote
from ncaa_quant.config import AppConfig, load_config, load_secrets
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.features.market_lines import slot_close_instant
from ncaa_quant.ingestion.odds_api import (
    OddsAPIClient,
    OddsAPIError,
    RateLimitBudgetError,
    _enrich_frame_via_crosswalk,  # noqa: PLC2701
    asof_tolerance_for,
    run_odds_raw_capture,
    write_odds_snapshots,
)
from ncaa_quant.ingestion.teams import load_team_name_map
from ncaa_quant.utils.logging import get_logger
from ncaa_quant.utils.timeutils import to_utc

__all__ = (
    "DECISION_POINT_SLOT_CLOSE",
    "LIVE_CREDITS_PER_SLOT",
    "KickoffSlot",
    "SlotCloseBatchResult",
    "SlotCloseCaptureResult",
    "SlotCloseCreditReport",
    "SlotCloseStatus",
    "closing_quote_from_slot_close",
    "credit_accounting_report",
    "derive_kickoff_slots",
    "filter_payload_to_slot",
    "is_slot_capture_complete",
    "is_slot_missed",
    "mark_slot_missed",
    "plan_forward_slot_captures",
    "record_missed_slots",
    "run_due_slot_close_captures",
    "run_slot_close_capture",
    "slot_capture_tolerance",
    "slot_id_for_kickoff",
)

DECISION_POINT_SLOT_CLOSE: Final[str] = "slot_close"
LIVE_CREDITS_PER_SLOT: Final[int] = 3  # 3 markets × 1 us region (live endpoint)

SlotCloseStatus = Literal["captured", "missed", "pending", "not_due"]

_SLOT_CLOSE_DIR: Final[str] = "_slot_close"
_CAPTURED_SUFFIX: Final[str] = ".done"
_MISSED_SUFFIX: Final[str] = ".missed"

logging.getLogger("httpx").setLevel(logging.WARNING)


@dataclass(frozen=True, slots=True)
class KickoffSlot:
    """One kickoff-aligned capture unit: all games sharing the same ``start_date``."""

    season: int
    week: int
    kickoff: datetime
    game_ids: tuple[int, ...]

    @property
    def capture_at(self) -> datetime:
        """When the live pull should fire (kickoff − 5 minutes, UTC)."""
        return slot_close_instant(self.kickoff)

    @property
    def slot_id(self) -> str:
        return slot_id_for_kickoff(self.season, self.week, self.kickoff)


def slot_id_for_kickoff(season: int, week: int, kickoff: datetime) -> str:
    """Stable filesystem id for one (season, week, kickoff) slot."""
    kick = to_utc(kickoff)
    return f"{int(season)}_w{int(week)}_{kick.strftime('%Y%m%dT%H%M%S%fZ')}"


def slot_capture_tolerance(kickoff: datetime) -> timedelta:
    """As-of tolerance for whether a live capture is close enough to the slot.

    Post-Sept-2022 snapshots are 5-minute granularity (DESIGN §3.4); the capture
    window extends from ``capture_at`` through ``capture_at + tolerance`` but
    never at or after kickoff.
    """
    return asof_tolerance_for(kickoff)


def derive_kickoff_slots(
    store: ParquetStore,
    season: int,
    week: int,
) -> tuple[KickoffSlot, ...]:
    """Derive kickoff slots from staged ``games`` for one CFBD week.

    A slot is the set of games whose ``start_date`` (UTC-normalized) is identical.
    """
    games = store.read("games", filters={"season": int(season), "week": int(week)})
    if games.empty:
        return ()
    by_kick: dict[datetime, list[int]] = {}
    for row in games.itertuples(index=False):
        kick = to_utc(pd.Timestamp(row.start_date).to_pydatetime())
        by_kick.setdefault(kick, []).append(int(row.game_id))
    out: list[KickoffSlot] = []
    for kick in sorted(by_kick):
        out.append(
            KickoffSlot(
                season=int(season),
                week=int(week),
                kickoff=kick,
                game_ids=tuple(sorted(by_kick[kick])),
            )
        )
    return tuple(out)


@dataclass(frozen=True, slots=True)
class SlotCloseCreditReport:
    """Credit projection from staged ``games`` slot derivation (no API spend)."""

    seasons: tuple[int, ...]
    slots_by_season_week: dict[tuple[int, int], int]
    total_slots: int
    credits_per_slot: int
    total_credits: int

    def summary_lines(self) -> list[str]:
        lines = [
            "forward slot_close credit accounting (derived from staged games)",
            f"seasons={list(self.seasons)}",
            f"total_slots={self.total_slots}",
            f"credits_per_slot={self.credits_per_slot}",
            f"total_credits={self.total_credits}",
        ]
        for (season, week), n in sorted(self.slots_by_season_week.items()):
            credits = n * self.credits_per_slot
            lines.append(f"  season {season} week {week}: slots={n} credits={credits}")
        return lines


def credit_accounting_report(
    store: ParquetStore,
    seasons: Sequence[int],
    *,
    max_week: int = 15,
) -> SlotCloseCreditReport:
    """Count forward ``slot_close`` pulls through ``max_week`` from staged games."""
    counts: dict[tuple[int, int], int] = {}
    for season in seasons:
        games = store.read("games", filters={"season": int(season)})
        if games.empty:
            continue
        for week in sorted(int(w) for w in games["week"].dropna().unique() if int(w) <= max_week):
            counts[(int(season), week)] = len(derive_kickoff_slots(store, int(season), week))
    total = sum(counts.values())
    return SlotCloseCreditReport(
        seasons=tuple(int(s) for s in seasons),
        slots_by_season_week=counts,
        total_slots=total,
        credits_per_slot=LIVE_CREDITS_PER_SLOT,
        total_credits=total * LIVE_CREDITS_PER_SLOT,
    )


def _slot_state_root(raw_root: Path) -> Path:
    return raw_root / _SLOT_CLOSE_DIR


def _captured_marker(raw_root: Path, slot: KickoffSlot) -> Path:
    return _slot_state_root(raw_root) / f"{slot.slot_id}{_CAPTURED_SUFFIX}"


def _missed_marker(raw_root: Path, slot: KickoffSlot) -> Path:
    return _slot_state_root(raw_root) / f"{slot.slot_id}{_MISSED_SUFFIX}"


def is_slot_capture_complete(raw_root: Path, slot: KickoffSlot) -> bool:
    """True when a successful capture marker exists for ``slot``."""
    return _captured_marker(raw_root, slot).is_file()


def is_slot_missed(raw_root: Path, slot: KickoffSlot) -> bool:
    """True when the slot was recorded missed (post-kickoff, not backfilled)."""
    return _missed_marker(raw_root, slot).is_file()


def _staged_has_live_slot_close(
    store: ParquetStore,
    slot: KickoffSlot,
) -> bool:
    """True when staged live ``slot_close`` rows exist for this slot's games."""
    odds = store.read(
        "odds_snapshots",
        filters={"season": slot.season, "week": slot.week},
    )
    if odds.empty:
        return False
    mask = (
        (odds["decision_point"] == DECISION_POINT_SLOT_CLOSE)
        & (odds["snapshot_source"] == "live")
        & (odds["game_id"].isin(list(slot.game_ids)))
    )
    return bool(mask.any())


def _mark_slot_captured(raw_root: Path, slot: KickoffSlot, *, result: dict[str, Any]) -> Path:
    path = _captured_marker(raw_root, slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def mark_slot_missed(
    raw_root: Path,
    slot: KickoffSlot,
    *,
    reason: str,
    as_of: datetime,
) -> Path:
    """Record that a slot was not captured before kickoff — never backfill later."""
    path = _missed_marker(raw_root, slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "slot_id": slot.slot_id,
        "season": slot.season,
        "week": slot.week,
        "kickoff": to_utc(slot.kickoff).isoformat(),
        "capture_at": slot.capture_at.isoformat(),
        "reason": reason,
        "recorded_at": to_utc(as_of).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def slot_status(
    raw_root: Path,
    slot: KickoffSlot,
    *,
    as_of: datetime,
    store: ParquetStore | None = None,
) -> SlotCloseStatus:
    """Classify one slot relative to ``as_of`` and on-disk state."""
    now = to_utc(as_of)
    kick = to_utc(slot.kickoff)
    if is_slot_capture_complete(raw_root, slot) or (
        store is not None and _staged_has_live_slot_close(store, slot)
    ):
        return "captured"
    if is_slot_missed(raw_root, slot):
        return "missed"
    if now >= kick:
        return "missed"
    if now < slot.capture_at:
        return "not_due"
    return "pending"


def filter_payload_to_slot(
    payload: bytes | str | list[Any],
    *,
    kickoff: datetime,
) -> list[Any]:
    """Keep Odds API events whose ``commence_time`` matches ``kickoff`` (UTC)."""
    if isinstance(payload, (bytes, str)):
        import json as _json

        data: Any = _json.loads(payload)
    else:
        data = payload
    if not isinstance(data, list):
        msg = "Odds API odds payload must be a JSON array"
        raise OddsAPIError(msg)
    target = to_utc(kickoff)
    kept: list[Any] = []
    for event in data:
        if not isinstance(event, dict):
            continue
        commence_raw = event.get("commence_time")
        if not isinstance(commence_raw, str):
            continue
        commence = to_utc(datetime.fromisoformat(commence_raw.replace("Z", "+00:00")))
        if commence == target:
            kept.append(event)
    return kept


@dataclass(frozen=True, slots=True)
class SlotCloseCaptureResult:
    """Summary of one forward ``slot_close`` capture attempt."""

    slot: KickoffSlot
    status: SlotCloseStatus
    raw_path: Path | None
    rows_written: int
    rows_fetched: int
    credits_charged: int
    captured_at: datetime | None
    skipped_reason: str | None = None


@dataclass
class SlotCloseBatchResult:
    """Summary of a due-slot batch run."""

    captured: int = 0
    skipped: int = 0
    missed_recorded: int = 0
    credits_spent: int = 0
    rows_written: int = 0
    results: list[SlotCloseCaptureResult] = field(default_factory=list)


def plan_forward_slot_captures(
    store: ParquetStore,
    seasons: Sequence[int],
    *,
    as_of: datetime,
    raw_root: Path | str,
    max_week: int = 15,
) -> tuple[KickoffSlot, ...]:
    """Return slots that are due for capture at ``as_of`` (not yet captured/missed)."""
    now = to_utc(as_of)
    raw_dir = Path(raw_root)
    due: list[KickoffSlot] = []
    for season in seasons:
        games = store.read("games", filters={"season": int(season)})
        if games.empty:
            continue
        weeks = sorted(int(w) for w in games["week"].dropna().unique() if int(w) <= max_week)
        for week in weeks:
            for slot in derive_kickoff_slots(store, int(season), week):
                if slot_status(raw_dir, slot, as_of=now, store=store) == "captured":
                    continue
                if is_slot_missed(raw_dir, slot):
                    continue
                if now < slot.capture_at:
                    continue
                if now >= to_utc(slot.kickoff):
                    continue
                due.append(slot)
    return tuple(due)


def record_missed_slots(
    store: ParquetStore,
    raw_root: Path,
    seasons: Sequence[int],
    *,
    as_of: datetime,
    max_week: int = 15,
) -> int:
    """Mark past-kickoff slots as missed when never captured. Returns new markers."""
    now = to_utc(as_of)
    recorded = 0
    for season in seasons:
        games = store.read("games", filters={"season": int(season)})
        if games.empty:
            continue
        weeks = sorted(int(w) for w in games["week"].dropna().unique() if int(w) <= max_week)
        for week in weeks:
            for slot in derive_kickoff_slots(store, int(season), week):
                if is_slot_capture_complete(raw_root, slot) or is_slot_missed(raw_root, slot):
                    continue
                if store is not None and _staged_has_live_slot_close(store, slot):
                    continue
                if now >= to_utc(slot.kickoff):
                    mark_slot_missed(
                        raw_root,
                        slot,
                        reason="kickoff_passed_without_capture",
                        as_of=now,
                    )
                    recorded += 1
    return recorded


def run_slot_close_capture(
    slot: KickoffSlot,
    *,
    config: AppConfig | None = None,
    api_key: str | None = None,
    raw_root: Path | str | None = None,
    staged_root: Path | str | None = None,
    captured_at: datetime | None = None,
    client: OddsAPIClient | None = None,
    team_map: Mapping[str, str] | None = None,
    force: bool = False,
) -> SlotCloseCaptureResult:
    """Capture one kickoff slot: live pull → filter → stage ``slot_close`` rows.

    Idempotent: skips the API when a capture marker or staged rows already exist.
    Refuses post-kickoff capture (records missed unless already captured).
    On API or staging failure, does not write a capture marker (safe retry).
    """
    cfg = config or load_config()
    log = get_logger(__name__)
    key = api_key if api_key is not None else load_secrets().odds_api_key.get_secret_value()
    raw_dir = Path(raw_root) if raw_root is not None else Path(cfg.paths.raw_dir) / "odds_api"
    staged_dir = Path(staged_root) if staged_root is not None else Path(cfg.paths.staged_dir)
    names = (
        dict(team_map)
        if team_map is not None
        else load_team_name_map(Path(cfg.data.team_names_path))
    )
    captured = to_utc(captured_at or datetime.now(tz=UTC))
    kick = to_utc(slot.kickoff)

    if not force and is_slot_capture_complete(raw_dir, slot):
        log.info("slot_close_skipped", slot_id=slot.slot_id, reason="capture_marker")
        return SlotCloseCaptureResult(
            slot=slot,
            status="captured",
            raw_path=None,
            rows_written=0,
            rows_fetched=0,
            credits_charged=0,
            captured_at=None,
            skipped_reason="capture_marker",
        )

    with ParquetStore(staged_dir) as store:
        if not force and _staged_has_live_slot_close(store, slot):
            log.info("slot_close_skipped", slot_id=slot.slot_id, reason="staged_rows_present")
            if not is_slot_capture_complete(raw_dir, slot):
                _mark_slot_captured(
                    raw_dir,
                    slot,
                    result={"reason": "staged_rows_present", "recorded_at": captured.isoformat()},
                )
            return SlotCloseCaptureResult(
                slot=slot,
                status="captured",
                raw_path=None,
                rows_written=0,
                rows_fetched=0,
                credits_charged=0,
                captured_at=None,
                skipped_reason="staged_rows_present",
            )

        if captured >= kick:
            mark_slot_missed(
                raw_dir,
                slot,
                reason="capture_requested_at_or_after_kickoff",
                as_of=captured,
            )
            log.warning("slot_close_missed", slot_id=slot.slot_id, reason="post_kickoff")
            return SlotCloseCaptureResult(
                slot=slot,
                status="missed",
                raw_path=None,
                rows_written=0,
                rows_fetched=0,
                credits_charged=0,
                captured_at=None,
                skipped_reason="post_kickoff",
            )

        if is_slot_missed(raw_dir, slot):
            log.info("slot_close_skipped", slot_id=slot.slot_id, reason="missed_marker")
            return SlotCloseCaptureResult(
                slot=slot,
                status="missed",
                raw_path=None,
                rows_written=0,
                rows_fetched=0,
                credits_charged=0,
                captured_at=None,
                skipped_reason="missed_marker",
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
            raw = run_odds_raw_capture(
                config=cfg,
                api_key=key,
                raw_root=raw_dir,
                captured_at=captured,
                client=odds_client,
            )
            body = raw.raw_path.read_bytes()
            filtered = filter_payload_to_slot(body, kickoff=slot.kickoff)
            ingested = datetime.now(tz=UTC)
            frame = _enrich_frame_via_crosswalk(
                store,
                filtered,
                names,
                captured_at=captured,
                ingested_at=ingested,
                snapshot_source="live",
                decision_point=DECISION_POINT_SLOT_CLOSE,
                event_time=captured,
            )
            added, _quarantined = write_odds_snapshots(
                store,
                frame,
                raw_archive_path=raw.raw_path,
            )
            credits_charged = (
                int(odds_client.last_requests_last)
                if odds_client.last_requests_last is not None
                else LIVE_CREDITS_PER_SLOT
            )
            _mark_slot_captured(
                raw_dir,
                slot,
                result={
                    "raw_path": str(raw.raw_path),
                    "rows_written": added,
                    "rows_fetched": len(frame),
                    "captured_at": captured.isoformat(),
                    "credits_charged": credits_charged,
                },
            )
            log.info(
                "slot_close_captured",
                slot_id=slot.slot_id,
                season=slot.season,
                week=slot.week,
                kickoff=kick.isoformat(),
                rows=added,
                credits_charged=credits_charged,
            )
            return SlotCloseCaptureResult(
                slot=slot,
                status="captured",
                raw_path=raw.raw_path,
                rows_written=added,
                rows_fetched=len(frame),
                credits_charged=credits_charged,
                captured_at=captured,
            )
        except (OddsAPIError, RateLimitBudgetError, httpx.HTTPError) as exc:
            log.error(
                "slot_close_api_failed",
                slot_id=slot.slot_id,
                error=str(exc),
            )
            raise
        finally:
            if owns_client:
                odds_client.close()


def run_due_slot_close_captures(
    *,
    seasons: Sequence[int],
    config: AppConfig | None = None,
    api_key: str | None = None,
    raw_root: Path | str | None = None,
    staged_root: Path | str | None = None,
    as_of: datetime | None = None,
    client: OddsAPIClient | None = None,
    team_map: Mapping[str, str] | None = None,
    max_week: int = 15,
) -> SlotCloseBatchResult:
    """Capture every slot due at ``as_of``; record past-kickoff slots as missed."""
    cfg = config or load_config()
    now = to_utc(as_of or datetime.now(tz=UTC))
    raw_dir = Path(raw_root) if raw_root is not None else Path(cfg.paths.raw_dir) / "odds_api"
    staged_dir = Path(staged_root) if staged_root is not None else Path(cfg.paths.staged_dir)
    batch = SlotCloseBatchResult()

    with ParquetStore(staged_dir) as store:
        batch.missed_recorded = record_missed_slots(
            store,
            raw_dir,
            seasons,
            as_of=now,
            max_week=max_week,
        )
        due = plan_forward_slot_captures(
            store,
            seasons,
            as_of=now,
            raw_root=raw_dir,
            max_week=max_week,
        )

    owns_client = client is None
    odds_client = client
    if owns_client:
        key = api_key if api_key is not None else load_secrets().odds_api_key.get_secret_value()
        odds_client = OddsAPIClient(
            key,
            books=cfg.data.odds_books,
            markets=cfg.data.odds_markets,
            regions=cfg.data.odds_regions,
            rate_limit_reserve=cfg.data.odds_rate_limit_reserve,
            budget_kind="live",
        )

    try:
        for slot in due:
            try:
                result = run_slot_close_capture(
                    slot,
                    config=cfg,
                    raw_root=raw_dir,
                    staged_root=staged_dir,
                    captured_at=now,
                    client=odds_client,
                    team_map=team_map,
                )
            except (OddsAPIError, RateLimitBudgetError, httpx.HTTPError):
                batch.results.append(
                    SlotCloseCaptureResult(
                        slot=slot,
                        status="pending",
                        raw_path=None,
                        rows_written=0,
                        rows_fetched=0,
                        credits_charged=0,
                        captured_at=None,
                        skipped_reason="api_error_mid_batch",
                    )
                )
                break
            batch.results.append(result)
            if result.status == "captured" and result.credits_charged > 0:
                batch.captured += 1
                batch.credits_spent += result.credits_charged
                batch.rows_written += result.rows_written
            elif result.skipped_reason:
                batch.skipped += 1
            elif result.status == "missed":
                batch.missed_recorded += 1
    finally:
        if owns_client and odds_client is not None:
            odds_client.close()

    return batch


def closing_quote_from_slot_close(
    store: ParquetStore,
    *,
    game_id: str | int,
    book: str,
    home_team: str,
    away_team: str,
    bet_on: str,
    bet_line_home: float,
    season: int | None = None,
    week: int | None = None,
) -> ClosingQuote | None:
    """Build a :class:`ClosingQuote` from staged forward ``slot_close`` rows.

    Per-book identity is preserved in ``odds_snapshots.book``; ``source_row_id``
    is the row's ``snapshot_id`` (required by :func:`settle`).
    """
    gid = str(game_id)
    filters: dict[str, int] = {}
    if season is not None:
        filters["season"] = int(season)
    if week is not None:
        filters["week"] = int(week)
    if filters:
        odds = store.read("odds_snapshots", filters=filters)
    else:
        odds = store.read("odds_snapshots")
    if odds.empty:
        return None
    sub = odds.loc[
        (odds["game_id"].astype(str) == gid)
        & (odds["book"].astype(str).str.casefold() == str(book).casefold())
        & (odds["market"].astype(str) == "spread")
        & (odds["decision_point"].astype(str) == DECISION_POINT_SLOT_CLOSE)
        & (odds["snapshot_source"].astype(str) == "live")
    ].copy()
    if sub.empty:
        return None
    sub["event_time"] = pd.to_datetime(sub["event_time"], utc=True)
    home_rows = sub.loc[sub["side"].astype(str).str.casefold() == str(home_team).casefold()]
    if home_rows.empty:
        return None
    home_rows = home_rows.assign(line_f=pd.to_numeric(home_rows["line"], errors="coerce"))
    match = home_rows.loc[(home_rows["line_f"] - float(bet_line_home)).abs() <= 0.51]
    use = match if not match.empty else home_rows
    latest = use["event_time"].max()
    at = sub.loc[sub["event_time"] == latest]
    h = at.loc[at["side"].astype(str).str.casefold() == str(home_team).casefold()]
    a = at.loc[at["side"].astype(str).str.casefold() == str(away_team).casefold()]
    if h.empty or a.empty:
        return None
    hrow, arow = h.iloc[0], a.iloc[0]
    home_line = float(hrow["line"])
    home_px = float(hrow["price"])
    away_px = float(arow["price"])
    if bet_on.casefold() == str(home_team).casefold():
        side_px, other_px, line = home_px, away_px, home_line
        side_id = str(hrow["snapshot_id"])
    else:
        side_px, other_px, line = away_px, home_px, -home_line
        side_id = str(arow["snapshot_id"])
    return ClosingQuote(
        side_american=side_px,
        other_american=other_px,
        book=str(book),
        line=line,
        source_row_id=side_id,
    )
