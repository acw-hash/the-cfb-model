"""Holdout-open ledger for the situational-angle harness (DESIGN §16 item 1).

The holdout split (2023–2025) may be opened **once** per hypothesis, with the
config hash of the locked definition recorded. A second open, or an open after
the definition changed (hash differs from the recorded one), refuses unless
``force=True`` — in which case the override itself is written to the ledger
(and the caller must print it in the report).

The ledger is an append-only JSONL file committed under ``docs/notes/angles``
so the count of looks is part of the repository history, not a local state.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

DEFAULT_LEDGER_PATH: Final[Path] = Path("docs/notes/angles/holdout_ledger.jsonl")


class HoldoutLockedError(RuntimeError):
    """Raised when a holdout open is refused (already opened / definition changed)."""


@dataclass(frozen=True)
class LedgerEntry:
    """One holdout-open event."""

    hypothesis_id: str
    config_hash: str
    opened_at: str
    git_sha: str
    forced: bool
    override_kind: str | None
    prior_config_hash: str | None
    n_prior_opens: int
    reason: str
    split: str = "holdout"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def current_git_sha() -> str:
    """Short git SHA of HEAD, or ``unknown`` outside a repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def read_ledger(path: Path | str = DEFAULT_LEDGER_PATH) -> list[LedgerEntry]:
    """All entries in file order (missing / empty file → ``[]``)."""
    p = Path(path)
    if not p.is_file():
        return []
    entries: list[LedgerEntry] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        raw = json.loads(text)
        entries.append(
            LedgerEntry(
                hypothesis_id=str(raw["hypothesis_id"]),
                config_hash=str(raw["config_hash"]),
                opened_at=str(raw["opened_at"]),
                git_sha=str(raw.get("git_sha", "unknown")),
                forced=bool(raw.get("forced", False)),
                override_kind=raw.get("override_kind"),
                prior_config_hash=raw.get("prior_config_hash"),
                n_prior_opens=int(raw.get("n_prior_opens", 0)),
                reason=str(raw.get("reason", "")),
                split=str(raw.get("split", "holdout")),
            )
        )
    return entries


def entries_for(path: Path | str, hypothesis_id: str) -> list[LedgerEntry]:
    return [e for e in read_ledger(path) if e.hypothesis_id == hypothesis_id]


def open_holdout(
    hypothesis_id: str,
    config_hash: str,
    *,
    path: Path | str = DEFAULT_LEDGER_PATH,
    force: bool = False,
    reason: str = "",
    git_sha: str | None = None,
    now: datetime | None = None,
) -> LedgerEntry:
    """Record a holdout open for ``hypothesis_id``; refuse re-opens without ``force``.

    Refusal cases (``HoldoutLockedError``):
    - an entry already exists for the hypothesis (any hash) — a second look;
    - the latest entry's hash differs from ``config_hash`` — the definition
      changed after the holdout was opened (this is also a second look, but is
      labelled separately because it is the more dangerous one).

    With ``force=True`` the entry is appended with ``forced=True`` and
    ``override_kind`` ∈ {``reopen``, ``definition_changed``} so the override is
    permanent and visible.
    """
    prior = entries_for(path, hypothesis_id)
    override_kind: str | None = None
    prior_hash: str | None = None
    if prior:
        latest = prior[-1]
        prior_hash = latest.config_hash
        override_kind = "definition_changed" if latest.config_hash != config_hash else "reopen"
        if not force:
            if override_kind == "definition_changed":
                msg = (
                    f"holdout for {hypothesis_id} was opened on {latest.opened_at} with config "
                    f"hash {latest.config_hash}; the definition has since changed (now "
                    f"{config_hash}). Opening it again would be a second look at the holdout "
                    f"with a different hypothesis. Refusing without --force."
                )
            else:
                msg = (
                    f"holdout for {hypothesis_id} was already opened on {latest.opened_at} "
                    f"(config hash {latest.config_hash}; {len(prior)} prior open(s)). "
                    f"Refusing a second open without --force."
                )
            raise HoldoutLockedError(msg)
    entry = LedgerEntry(
        hypothesis_id=hypothesis_id,
        config_hash=config_hash,
        opened_at=(now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds"),
        git_sha=git_sha if git_sha is not None else current_git_sha(),
        forced=bool(force and prior),
        override_kind=override_kind if (force and prior) else None,
        prior_config_hash=prior_hash,
        n_prior_opens=len(prior),
        reason=reason,
    )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.as_dict(), sort_keys=True) + "\n")
    return entry


__all__ = [
    "DEFAULT_LEDGER_PATH",
    "HoldoutLockedError",
    "LedgerEntry",
    "current_git_sha",
    "entries_for",
    "open_holdout",
    "read_ledger",
]
