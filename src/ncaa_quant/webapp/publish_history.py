"""Append-only workstation publish history (never pushed to R2).

Path layout: ``{publish_history_path}/{season}_w{week}.jsonl``.
Each line is one full ``week_predictions`` artifact object. Append-only is
load-bearing: a later overwrite of ``v1/.../tuesday_primary/*`` must not erase
an earlier snapshot needed for pre-kickoff grading.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class SlateRegressionError(ValueError):
    """Raised when a still-future game vanishes between publishes."""


def publish_history_file(root: Path | str, *, season: int, week: int) -> Path:
    """Return the JSONL path for one ``(season, week)``."""
    return Path(root) / f"{int(season)}_w{int(week)}.jsonl"


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except ValueError:
        return None


def load_publish_history_file(path: Path | str) -> list[dict[str, Any]]:
    """Load every JSONL record from one week file (oldest → newest)."""
    file_path = Path(path)
    if not file_path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line_no, raw in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"invalid JSONL at {file_path}:{line_no}: {exc}"
            raise ValueError(msg) from exc
        if not isinstance(payload, dict):
            msg = f"publish history line {line_no} in {file_path} is not an object"
            raise TypeError(msg)
        records.append(payload)
    return records


def latest_publish_record(
    root: Path | str,
    *,
    season: int,
    week: int,
) -> dict[str, Any] | None:
    """Return the most recently appended record for ``(season, week)``, if any."""
    records = load_publish_history_file(publish_history_file(root, season=season, week=week))
    return records[-1] if records else None


def load_season_publish_history(root: Path | str, *, season: int) -> list[dict[str, Any]]:
    """Load all week JSONL files for ``season`` (weeks 1–15), chronologically per file."""
    records: list[dict[str, Any]] = []
    base = Path(root)
    for week in range(1, 16):
        records.extend(load_publish_history_file(publish_history_file(base, season=season, week=week)))
    return records


def append_publish_history(
    artifact: Mapping[str, Any],
    *,
    root: Path | str,
) -> Path:
    """Append one ``week_predictions`` object to the week JSONL. Returns the file path."""
    season = int(artifact["season"])
    week = int(artifact["week"])
    path = publish_history_file(root, season=season, week=week)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(artifact), sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def assert_no_slate_regression(
    prior: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    """Refuse export when a still-future game disappears from the slate.

    Post-kickoff absence is legitimate (kickoff filter) and must pass.
    """
    if prior is None:
        return
    clock = now or datetime.now(tz=UTC)
    clock = clock.astimezone(UTC) if clock.tzinfo else clock.replace(tzinfo=UTC)

    prior_games = {
        str(game.get("game_id")): game
        for game in (prior.get("games") or [])
        if isinstance(game, Mapping) and game.get("game_id") is not None
    }
    current_ids = {
        str(game.get("game_id"))
        for game in (current.get("games") or [])
        if isinstance(game, Mapping) and game.get("game_id") is not None
    }
    vanished_future: list[str] = []
    for game_id, game in prior_games.items():
        if game_id in current_ids:
            continue
        kickoff = _parse_dt(game.get("kickoff_utc"))
        if kickoff is not None and kickoff > clock:
            vanished_future.append(game_id)
    if vanished_future:
        sample = sorted(vanished_future)[:8]
        suffix = (
            f" (+{len(vanished_future) - len(sample)} more)"
            if len(vanished_future) > len(sample)
            else ""
        )
        msg = (
            "slate regression: future-kickoff game_id(s) present in prior publish "
            f"but absent now: {sample!r}{suffix}; refusing export (forgotten as_of?)"
        )
        raise SlateRegressionError(msg)


def history_records_for_grade(
    root: Path | str,
    *,
    season: int,
    explicit: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return publish history for grading: explicit list wins; else load from disk."""
    if explicit is not None:
        return [dict(item) for item in explicit]
    return load_season_publish_history(root, season=season)
