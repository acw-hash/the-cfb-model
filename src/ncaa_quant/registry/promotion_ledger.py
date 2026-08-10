"""Promotion-attempt ledger with within-year Bonferroni alpha (audit A-11).

The champion/challenger gate re-tests candidates against the same walk-forward
seasons at α = 0.10. Without multiplicity control, mid-season gates, offseason
retrains and research sprints accumulate looks until a spurious promotion is
near-certain. This ledger counts attempts per calendar year and exposes the
Bonferroni-adjusted threshold ``α₀ / k`` for the k-th attempt that year.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Same default as :data:`ncaa_quant.registry.promote.PROMOTION_ALPHA`.
#: Duplicated here so this module does not import ``promote`` (circular).
DEFAULT_BASE_ALPHA: float = 0.10


class LedgerError(ValueError):
    """Raised for promotion-ledger contract violations."""


@dataclass(frozen=True)
class LedgerEntry:
    """One recorded promotion attempt."""

    year: int
    attempt_index: int
    created_at: str
    candidate_version: int
    champion_version: int | None
    alpha_base: float
    alpha_adjusted: float
    passed: bool
    force_override: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LedgerEntry:
        return cls(
            year=int(payload["year"]),
            attempt_index=int(payload["attempt_index"]),
            created_at=str(payload["created_at"]),
            candidate_version=int(payload["candidate_version"]),
            champion_version=(
                None
                if payload.get("champion_version") is None
                else int(payload["champion_version"])
            ),
            alpha_base=float(payload["alpha_base"]),
            alpha_adjusted=float(payload["alpha_adjusted"]),
            passed=bool(payload["passed"]),
            force_override=bool(payload.get("force_override", False)),
            reason=str(payload.get("reason", "")),
        )


def bonferroni_alpha(base_alpha: float, attempt_index: int) -> float:
    """Bonferroni-adjusted α for the ``attempt_index``-th look this year.

    ``attempt_index`` is 1-based (the first attempt of the year uses α₀ / 1).
    """
    if base_alpha <= 0.0 or base_alpha > 1.0:
        msg = f"base_alpha must be in (0, 1], got {base_alpha}"
        raise LedgerError(msg)
    if attempt_index < 1:
        msg = f"attempt_index must be ≥1, got {attempt_index}"
        raise LedgerError(msg)
    return float(base_alpha) / float(attempt_index)


class PromotionLedger:
    """Append-only JSONL ledger keyed under a registry root."""

    FILENAME = "promotion_ledger.jsonl"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / self.FILENAME
        self.root.mkdir(parents=True, exist_ok=True)

    def entries(self, *, year: int | None = None) -> list[LedgerEntry]:
        """Load entries, optionally filtered to one calendar year."""
        if not self.path.exists():
            return []
        out: list[LedgerEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entry = LedgerEntry.from_dict(json.loads(line))
            if year is None or entry.year == int(year):
                out.append(entry)
        return out

    def attempts_this_year(self, *, year: int | None = None) -> int:
        """How many attempts have already been recorded for ``year``."""
        y = int(year) if year is not None else datetime.now(tz=UTC).year
        return len(self.entries(year=y))

    def next_attempt_index(self, *, year: int | None = None) -> int:
        """1-based index the next attempt will receive."""
        return self.attempts_this_year(year=year) + 1

    def planned_alpha(
        self,
        *,
        base_alpha: float = DEFAULT_BASE_ALPHA,
        year: int | None = None,
    ) -> tuple[int, float]:
        """Return ``(attempt_index, adjusted_alpha)`` for the next attempt."""
        idx = self.next_attempt_index(year=year)
        return idx, bonferroni_alpha(base_alpha, idx)

    def record(
        self,
        *,
        candidate_version: int,
        champion_version: int | None,
        alpha_base: float,
        alpha_adjusted: float,
        attempt_index: int,
        passed: bool,
        force_override: bool = False,
        reason: str = "",
        year: int | None = None,
        created_at: str | None = None,
    ) -> LedgerEntry:
        """Append one attempt. Caller must pass the alpha actually used."""
        y = int(year) if year is not None else datetime.now(tz=UTC).year
        expected = bonferroni_alpha(alpha_base, attempt_index)
        if abs(float(alpha_adjusted) - expected) > 1e-12:
            msg = (
                f"alpha_adjusted={alpha_adjusted} != Bonferroni "
                f"{alpha_base}/{attempt_index}={expected}"
            )
            raise LedgerError(msg)
        entry = LedgerEntry(
            year=y,
            attempt_index=int(attempt_index),
            created_at=created_at or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            candidate_version=int(candidate_version),
            champion_version=champion_version,
            alpha_base=float(alpha_base),
            alpha_adjusted=float(alpha_adjusted),
            passed=bool(passed),
            force_override=bool(force_override),
            reason=reason,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
        return entry


__all__ = [
    "LedgerEntry",
    "LedgerError",
    "PromotionLedger",
    "bonferroni_alpha",
]
