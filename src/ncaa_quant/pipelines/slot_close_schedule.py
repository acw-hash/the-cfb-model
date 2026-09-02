"""Wall-clock polling scheduler for forward ``slot_close`` capture (V4-B1a).

Prefect cannot fire per-kickoff triggers; this module wraps the existing
:func:`~ncaa_quant.ingestion.slot_close_capture.run_due_slot_close_captures`
entry point on a fine-grained cron (default every 2 minutes). Each tick derives
due slots and no-ops when none qualify — zero Odds API credits on a no-op.

Integration point (capture logic unchanged)::

    run_due_slot_close_captures(...)  # slot_close_capture.py
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.odds_api import OddsAPIClient, OddsAPIError
from ncaa_quant.ingestion.slot_close_capture import (
    KickoffSlot,
    SlotCloseCaptureResult,
    derive_kickoff_slots,
    is_slot_capture_complete,
    is_slot_missed,
    plan_forward_slot_captures,
    run_due_slot_close_captures,
)
from ncaa_quant.utils.logging import get_logger
from ncaa_quant.utils.timeutils import to_utc

__all__ = (
    "DEFAULT_SLOT_CLOSE_POLL_CRON",
    "DEFAULT_SLOT_CLOSE_POLL_INTERVAL",
    "SlotClosePollResult",
    "SlotClosePollSlotOutcome",
    "SaturdaySimulationResult",
    "append_poll_outcome_ledger",
    "count_poll_invocations_per_day",
    "copy_slots_to_staged",
    "execute_slot_close_poll",
    "outcome_ledger_path",
    "poll_lock_path",
    "read_poll_outcome_ledger",
    "remap_slots_to_calendar_day",
    "simulate_saturday_polling",
    "slots_on_calendar_day",
)

DEFAULT_SLOT_CLOSE_POLL_INTERVAL: timedelta = timedelta(minutes=2)
DEFAULT_SLOT_CLOSE_POLL_CRON: str = "*/2 * * * *"

_POLL_LOCK_NAME: str = ".poll.lock"
_POLL_LOCK_STALE: timedelta = timedelta(minutes=15)
_OUTCOMES_SUBDIR: str = "outcomes"

PollRunOutcome = Literal["no_op", "captured", "skipped_concurrent", "error"]
SlotOutcomeStatus = Literal["captured", "no_op", "missed", "error", "pending", "skipped"]


@dataclass(frozen=True, slots=True)
class SlotClosePollSlotOutcome:
    """Per-slot result for one poll tick."""

    slot_id: str
    season: int
    week: int
    kickoff: str
    capture_at: str
    status: SlotOutcomeStatus
    credits_charged: int = 0
    skipped_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SlotClosePollResult:
    """Summary of one wall-clock poll tick."""

    run_id: str
    as_of: datetime
    outcome: PollRunOutcome
    due_count: int
    captured: int
    skipped: int
    missed_recorded: int
    credits_spent: int
    rows_written: int
    slot_outcomes: tuple[SlotClosePollSlotOutcome, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SaturdaySimulationResult:
    """Acceptance harness for one calendar Saturday."""

    calendar_day: str
    slot_count: int
    poll_invocations: int
    skipped_polls: int
    api_errors_injected: int
    missed_slots: int
    credits_spent: int
    captured_slots: int


def poll_lock_path(raw_root: Path | str) -> Path:
    return Path(raw_root) / "_slot_close" / _POLL_LOCK_NAME


def outcome_ledger_path(raw_root: Path | str, *, day: datetime) -> Path:
    day_utc = to_utc(day)
    return Path(raw_root) / "_slot_close" / _OUTCOMES_SUBDIR / f"{day_utc:%Y-%m-%d}.jsonl"


def _try_acquire_poll_lock(lock_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_file():
        age = datetime.now(tz=UTC) - datetime.fromtimestamp(
            lock_path.stat().st_mtime,
            tz=UTC,
        )
        if age <= _POLL_LOCK_STALE:
            return False
        lock_path.unlink(missing_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        lock_path.write_text(datetime.now(tz=UTC).isoformat() + "\n", encoding="utf-8")
        return True
    except FileExistsError:
        return False


def _release_poll_lock(lock_path: Path) -> None:
    lock_path.unlink(missing_ok=True)


def _slot_outcome_from_capture(result: SlotCloseCaptureResult) -> SlotClosePollSlotOutcome:
    slot = result.slot
    if result.skipped_reason == "api_error_mid_batch":
        status: SlotOutcomeStatus = "error"
    elif result.status == "missed":
        status = "missed"
    elif result.status == "captured" and result.credits_charged > 0:
        status = "captured"
    elif result.status == "captured":
        status = "skipped"
    else:
        status = "pending"
    return SlotClosePollSlotOutcome(
        slot_id=slot.slot_id,
        season=slot.season,
        week=slot.week,
        kickoff=to_utc(slot.kickoff).isoformat(),
        capture_at=slot.capture_at.isoformat(),
        status=status,
        credits_charged=result.credits_charged,
        skipped_reason=result.skipped_reason,
    )


def _classify_poll_outcome(
    *,
    due_count: int,
    captured: int,
    credits_spent: int,
) -> PollRunOutcome:
    if due_count == 0:
        return "no_op"
    if captured > 0 or credits_spent > 0:
        return "captured"
    return "no_op"


def append_poll_outcome_ledger(
    raw_root: Path | str,
    result: SlotClosePollResult,
) -> Path:
    """Append one poll tick to the daily JSONL outcome ledger."""
    path = outcome_ledger_path(raw_root, day=result.as_of)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "run_id": result.run_id,
        "as_of": result.as_of.isoformat(),
        "outcome": result.outcome,
        "due_count": result.due_count,
        "captured": result.captured,
        "skipped": result.skipped,
        "missed_recorded": result.missed_recorded,
        "credits_spent": result.credits_spent,
        "rows_written": result.rows_written,
        "error": result.error,
        "slots": [
            {
                "slot_id": s.slot_id,
                "season": s.season,
                "week": s.week,
                "kickoff": s.kickoff,
                "capture_at": s.capture_at,
                "status": s.status,
                "credits_charged": s.credits_charged,
                "skipped_reason": s.skipped_reason,
            }
            for s in result.slot_outcomes
        ],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return path


def read_poll_outcome_ledger(
    raw_root: Path | str,
    *,
    day: datetime,
) -> list[dict[str, Any]]:
    """Read parsed outcome ledger lines for ``day`` (UTC calendar date)."""
    path = outcome_ledger_path(raw_root, day=day)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def count_poll_invocations_per_day(
    *,
    poll_interval: timedelta = DEFAULT_SLOT_CLOSE_POLL_INTERVAL,
) -> int:
    """Wall-clock poll ticks in one UTC day at ``poll_interval`` cadence."""
    seconds = int(poll_interval.total_seconds())
    if seconds <= 0:
        msg = "poll_interval must be positive"
        raise ValueError(msg)
    return (24 * 60 * 60) // seconds


def slots_on_calendar_day(
    store: ParquetStore,
    season: int,
    *,
    calendar_day: datetime,
    max_week: int = 15,
) -> tuple[KickoffSlot, ...]:
    """All kickoff slots whose ``kickoff`` falls on ``calendar_day`` (UTC date)."""
    target = to_utc(calendar_day).date()
    out: list[KickoffSlot] = []
    games = store.read("games", filters={"season": int(season)})
    if games.empty:
        return ()
    weeks = sorted(int(w) for w in games["week"].dropna().unique() if int(w) <= max_week)
    for week in weeks:
        for slot in derive_kickoff_slots(store, int(season), week):
            if to_utc(slot.kickoff).date() == target:
                out.append(slot)
    return tuple(sorted(out, key=lambda s: s.kickoff))


def execute_slot_close_poll(
    *,
    seasons: Sequence[int],
    as_of: datetime | None = None,
    config: AppConfig | None = None,
    raw_root: Path | str | None = None,
    staged_root: Path | str | None = None,
    client: OddsAPIClient | None = None,
    team_map: Mapping[str, str] | None = None,
    max_week: int = 15,
    run_id: str | None = None,
    write_ledger: bool = True,
) -> SlotClosePollResult:
    """One wall-clock poll tick — delegates capture to ``run_due_slot_close_captures``."""
    cfg = config or load_config()
    log = get_logger(__name__)
    now = to_utc(as_of or datetime.now(tz=UTC))
    rid = run_id or uuid4().hex[:12]
    raw_dir = Path(raw_root) if raw_root is not None else Path(cfg.paths.raw_dir) / "odds_api"
    staged_dir = Path(staged_root) if staged_root is not None else Path(cfg.paths.staged_dir)
    lock = poll_lock_path(raw_dir)

    if not _try_acquire_poll_lock(lock):
        result = SlotClosePollResult(
            run_id=rid,
            as_of=now,
            outcome="skipped_concurrent",
            due_count=0,
            captured=0,
            skipped=0,
            missed_recorded=0,
            credits_spent=0,
            rows_written=0,
            slot_outcomes=(),
            error="concurrent_poll_in_progress",
        )
        if write_ledger:
            append_poll_outcome_ledger(raw_dir, result)
        log.info("slot_close_poll_skipped_concurrent", run_id=rid, as_of=now.isoformat())
        return result

    try:
        with ParquetStore(staged_dir) as store:
            due_before = plan_forward_slot_captures(
                store,
                seasons,
                as_of=now,
                raw_root=raw_dir,
                max_week=max_week,
            )
        batch = run_due_slot_close_captures(
            seasons=seasons,
            config=cfg,
            raw_root=raw_dir,
            staged_root=staged_dir,
            as_of=now,
            client=client,
            team_map=team_map,
            max_week=max_week,
        )
        slot_outcomes = tuple(_slot_outcome_from_capture(r) for r in batch.results)
        outcome = _classify_poll_outcome(
            due_count=len(due_before),
            captured=batch.captured,
            credits_spent=batch.credits_spent,
        )
        result = SlotClosePollResult(
            run_id=rid,
            as_of=now,
            outcome=outcome,
            due_count=len(due_before),
            captured=batch.captured,
            skipped=batch.skipped,
            missed_recorded=batch.missed_recorded,
            credits_spent=batch.credits_spent,
            rows_written=batch.rows_written,
            slot_outcomes=slot_outcomes,
        )
        if write_ledger:
            append_poll_outcome_ledger(raw_dir, result)
        log.info(
            "slot_close_poll_complete",
            run_id=rid,
            outcome=outcome,
            due_count=len(due_before),
            captured=batch.captured,
            credits_spent=batch.credits_spent,
            missed_recorded=batch.missed_recorded,
        )
        return result
    except Exception as exc:
        result = SlotClosePollResult(
            run_id=rid,
            as_of=now,
            outcome="error",
            due_count=0,
            captured=0,
            skipped=0,
            missed_recorded=0,
            credits_spent=0,
            rows_written=0,
            slot_outcomes=(),
            error=str(exc),
        )
        if write_ledger:
            append_poll_outcome_ledger(raw_dir, result)
        log.error("slot_close_poll_failed", run_id=rid, error=str(exc))
        raise
    finally:
        _release_poll_lock(lock)


def remap_slots_to_calendar_day(
    slots: Sequence[KickoffSlot],
    target_day: datetime,
) -> tuple[KickoffSlot, ...]:
    """Preserve kickoff clock time; move all slots onto ``target_day`` (UTC date)."""
    if not slots:
        return ()
    target = to_utc(target_day).date()
    out: list[KickoffSlot] = []
    for slot in slots:
        kick = to_utc(slot.kickoff)
        moved = datetime(
            target.year,
            target.month,
            target.day,
            kick.hour,
            kick.minute,
            kick.second,
            kick.microsecond,
            tzinfo=UTC,
        )
        out.append(
            KickoffSlot(
                season=slot.season,
                week=slot.week,
                kickoff=moved,
                game_ids=slot.game_ids,
            )
        )
    return tuple(out)


def simulate_saturday_polling(
    slots: Sequence[KickoffSlot],
    *,
    calendar_day: datetime,
    staged_root: Path | str,
    raw_root: Path | str,
    seasons: Sequence[int],
    config: AppConfig | None = None,
    poll_interval: timedelta = DEFAULT_SLOT_CLOSE_POLL_INTERVAL,
    skip_poll_at: Sequence[datetime] = (),
    fail_slot_id_once: str | None = None,
    capture_handler: Callable[[KickoffSlot], bytes] | None = None,
    team_map: Mapping[str, str] | None = None,
) -> SaturdaySimulationResult:
    """Replay 2-minute polls across one Saturday slate (fixtures only).

    ``skip_poll_at`` models a worker restart (missed poll ticks). ``fail_slot_id_once``
    injects one API error on the first capture attempt for that slot; the next due
    poll retries within the ``[capture_at, kickoff)`` window.
    """
    if not slots:
        msg = "simulate_saturday_polling requires at least one slot"
        raise ValueError(msg)

    cfg = config or load_config()
    raw_dir = Path(raw_root)
    staged_dir = Path(staged_root)
    skip_set = {to_utc(t) for t in skip_poll_at}
    fail_once: set[str] = {fail_slot_id_once} if fail_slot_id_once else set()

    day = to_utc(calendar_day).date()
    start = min(s.capture_at for s in slots) - poll_interval
    end = max(to_utc(s.kickoff) for s in slots) + poll_interval

    # Minimal Odds API payload per slot (capture filters by kickoff).
    def _default_payload(slot: KickoffSlot) -> bytes:
        kick = to_utc(slot.kickoff).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = [
            {
                "id": f"evt_{slot.slot_id}",
                "sport_key": "americanfootball_ncaaf",
                "commence_time": kick,
                "home_team": "Auburn Tigers",
                "away_team": "Baylor Bears",
                "bookmakers": [
                    {
                        "key": "fanduel",
                        "markets": [
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Auburn Tigers", "price": -102, "point": -7.5},
                                    {"name": "Baylor Bears", "price": -118, "point": 7.5},
                                ],
                            },
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": -105, "point": 48.5},
                                    {"name": "Under", "price": -115, "point": 48.5},
                                ],
                            },
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Auburn Tigers", "price": -280},
                                    {"name": "Baylor Bears", "price": 230},
                                ],
                            },
                        ],
                    }
                ],
            }
        ]
        return json.dumps(body).encode()

    handler = capture_handler or _default_payload
    poll_invocations = 0
    skipped_polls = 0
    credits_spent = 0
    api_errors_injected = 0

    tick = to_utc(start)
    end_utc = to_utc(end)
    while tick <= end_utc:
        if tick in skip_set:
            skipped_polls += 1
            tick += poll_interval
            continue

        poll_at = tick

        def _make_client(at: datetime = poll_at) -> OddsAPIClient:
            def transport_handler(request: httpx.Request) -> httpx.Response:
                del request
                nonlocal api_errors_injected
                with ParquetStore(staged_dir) as store:
                    due = plan_forward_slot_captures(
                        store,
                        seasons,
                        as_of=at,
                        raw_root=raw_dir,
                        max_week=15,
                    )
                if not due:
                    return httpx.Response(
                        200,
                        content=b"[]",
                        headers={"x-requests-remaining": "99990", "x-requests-last": "0"},
                    )
                slot = due[0]
                if slot.slot_id in fail_once:
                    fail_once.discard(slot.slot_id)
                    api_errors_injected += 1
                    return httpx.Response(500, content=b"injected error")
                return httpx.Response(
                    200,
                    content=handler(slot),
                    headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
                )

            return OddsAPIClient(
                "test-key",
                books=cfg.data.odds_books,
                markets=cfg.data.odds_markets,
                regions=cfg.data.odds_regions,
                rate_limit_reserve=cfg.data.odds_rate_limit_reserve,
                budget_kind="live",
                transport=httpx.MockTransport(transport_handler),
            )

        client = _make_client()
        try:
            poll = execute_slot_close_poll(
                seasons=seasons,
                as_of=poll_at,
                config=cfg,
                raw_root=raw_dir,
                staged_root=staged_dir,
                client=client,
                team_map=team_map,
                write_ledger=True,
            )
            poll_invocations += 1
            credits_spent += poll.credits_spent
        except OddsAPIError:
            poll_invocations += 1
        finally:
            client.close()

        tick += poll_interval

    missed = 0
    captured = 0
    for slot in slots:
        if is_slot_missed(raw_dir, slot):
            missed += 1
        elif is_slot_capture_complete(raw_dir, slot):
            captured += 1

    return SaturdaySimulationResult(
        calendar_day=day.isoformat(),
        slot_count=len(slots),
        poll_invocations=poll_invocations,
        skipped_polls=skipped_polls,
        api_errors_injected=api_errors_injected,
        missed_slots=missed,
        credits_spent=credits_spent,
        captured_slots=captured,
    )


def copy_slots_to_staged(
    source_store: ParquetStore,
    staged_root: Path | str,
    slots: Sequence[KickoffSlot],
) -> None:
    """Copy ``games`` and ``teams`` rows needed for ``slots`` into ``staged_root``."""
    if not slots:
        return
    season = slots[0].season
    game_ids = {gid for slot in slots for gid in slot.game_ids}
    weeks = {slot.week for slot in slots}
    game_frames = []
    for week in weeks:
        games = source_store.read("games", filters={"season": season, "week": week})
        if games.empty:
            continue
        game_frames.append(games.loc[games["game_id"].isin(list(game_ids))])
    if not game_frames:
        msg = "no staged games found for simulation slots"
        raise ValueError(msg)
    games_df = pd.concat(game_frames, ignore_index=True)
    kickoff_by_game = {int(gid): to_utc(slot.kickoff) for slot in slots for gid in slot.game_ids}

    def _align_kickoff(row: pd.Series) -> pd.Timestamp:
        kick = kickoff_by_game[int(row["game_id"])]
        return pd.Timestamp(kick)

    games_df = games_df.copy()
    games_df["start_date"] = games_df.apply(_align_kickoff, axis=1)
    games_df["event_time"] = games_df["start_date"]
    games_df["ingested_at"] = games_df["start_date"]
    team_ids = set(games_df["home_team_id"].astype(int)) | set(games_df["away_team_id"].astype(int))
    teams_df = source_store.read("teams", filters={"season": season})
    teams_df = teams_df.loc[teams_df["team_id"].isin(list(team_ids))]
    with ParquetStore(staged_root) as store:
        for week in weeks:
            part = games_df.loc[games_df["week"] == week]
            if not part.empty:
                store.write_partition("games", part, {"season": season, "week": int(week)})
        store.write_partition("teams", teams_df, {"season": season})


def stage_simulation_games(
    staged_root: Path | str,
    slots: Sequence[KickoffSlot],
) -> None:
    """Write minimal ``games`` + ``teams`` partitions for simulation slots."""
    staged_dir = Path(staged_root)
    rows: list[dict[str, object]] = []
    for slot in slots:
        kick = to_utc(slot.kickoff)
        for gid in slot.game_ids:
            rows.append(
                {
                    "game_id": int(gid),
                    "season": slot.season,
                    "week": slot.week,
                    "season_type": "regular",
                    "start_date": kick,
                    "home_team_id": 2,
                    "away_team_id": 3,
                    "home_points": None,
                    "away_points": None,
                    "neutral_site": False,
                    "conference_game": False,
                    "venue_id": None,
                    "completed": False,
                    "event_time_estimated": True,
                    "source_version": "sim",
                    "event_time": kick,
                    "ingested_at": kick,
                }
            )
    teams = pd.DataFrame(
        [
            {
                "team_id": 2,
                "season": slots[0].season,
                "school": "Auburn",
                "conference": "SEC",
                "abbreviation": "AUB",
                "classification": "fbs",
                "source_version": "sim",
                "event_time": to_utc(slots[0].kickoff),
                "ingested_at": to_utc(slots[0].kickoff),
            },
            {
                "team_id": 3,
                "season": slots[0].season,
                "school": "Baylor",
                "conference": "Big 12",
                "abbreviation": "BAY",
                "classification": "fbs",
                "source_version": "sim",
                "event_time": to_utc(slots[0].kickoff),
                "ingested_at": to_utc(slots[0].kickoff),
            },
        ]
    )
    season = slots[0].season
    week = slots[0].week
    with ParquetStore(staged_dir) as store:
        store.write_partition("games", pd.DataFrame(rows), {"season": season, "week": week})
        store.write_partition("teams", teams, {"season": season})
