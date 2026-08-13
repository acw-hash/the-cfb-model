"""STALE mode: last-good data + output stamping (DESIGN §10)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.utils.logging import get_logger

log = get_logger(__name__)


class IngestFailure(RuntimeError):
    """Raised when a live ingestion step fails and STALE mode may apply."""


@dataclass(frozen=True, slots=True)
class StaleSource:
    """One stale input source with age in hours."""

    source: str
    age_hours: float
    last_good_at: datetime

    def stamp(self) -> str:
        """Format ``STALE(source, age)`` per DESIGN §10."""
        return f"STALE({self.source}, {self.age_hours:.1f}h)"


@dataclass(frozen=True, slots=True)
class StaleContext:
    """Aggregate stale state for a prediction publish run."""

    sources: tuple[StaleSource, ...]
    use_last_good: bool

    @property
    def is_stale(self) -> bool:
        return bool(self.sources)

    @property
    def combined_stamp(self) -> str | None:
        if not self.sources:
            return None
        return "; ".join(s.stamp() for s in self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_stale": self.is_stale,
            "combined_stamp": self.combined_stamp,
            "sources": [
                {
                    "source": s.source,
                    "age_hours": s.age_hours,
                    "last_good_at": s.last_good_at.isoformat(),
                    "stamp": s.stamp(),
                }
                for s in self.sources
            ],
        }


def format_stale_stamp(source: str, age: timedelta) -> str:
    """Public helper for ``STALE(source, age)`` formatting."""
    hours = age.total_seconds() / 3600.0
    return StaleSource(source=source, age_hours=hours, last_good_at=datetime.now(tz=UTC)).stamp()


def find_last_good_capture(raw_root: Path | str) -> datetime | None:
    """Return the newest raw odds capture timestamp under ``raw_root``."""
    root = Path(raw_root)
    if not root.is_dir():
        return None
    newest: datetime | None = None
    for path in root.rglob("*.json"):
        # Filename stamp: YYYYMMDDTHHMMSSffffffZ.json
        name = path.stem.split("_")[0]
        try:
            captured = datetime.strptime(name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            continue
        if newest is None or captured > newest:
            newest = captured
    return newest


def resolve_stale_context(
    *,
    ingest_failed: bool,
    source: str = "odds",
    raw_root: Path | str | None = None,
    now: datetime | None = None,
    config: AppConfig | None = None,
) -> StaleContext:
    """Build stale context after an ingest failure.

    When ``ingest_failed`` is False, returns a fresh (non-stale) context.
    When True, locates the last-good snapshot and stamps age; raises if none
    exists (nothing to fall back to).
    """
    if not ingest_failed:
        return StaleContext(sources=(), use_last_good=False)

    cfg = config or load_config()
    clock = now if now is not None else datetime.now(tz=UTC)
    root = Path(raw_root) if raw_root is not None else Path(cfg.paths.raw_dir) / "odds_api"
    last_good = find_last_good_capture(root)
    if last_good is None:
        msg = f"ingest failed and no last-good snapshot under {root}"
        raise IngestFailure(msg)

    age = clock - last_good
    max_age = timedelta(hours=float(cfg.pipeline.stale_odds_max_age_hours))
    if age > max_age:
        log.warning(
            "stale_last_good_beyond_configured_max",
            source=source,
            age_hours=age.total_seconds() / 3600.0,
            max_hours=cfg.pipeline.stale_odds_max_age_hours,
        )

    stale = StaleSource(
        source=source,
        age_hours=age.total_seconds() / 3600.0,
        last_good_at=last_good,
    )
    log.warning("stale_mode_active", stamp=stale.stamp(), last_good_at=last_good.isoformat())
    return StaleContext(sources=(stale,), use_last_good=True)


@dataclass(frozen=True, slots=True)
class StampedPrediction:
    """One prediction row with optional STALE stamp."""

    game_id: str
    mu_margin: float
    sigma_margin: float
    stale_stamp: str | None
    is_stale: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "mu_margin": self.mu_margin,
            "sigma_margin": self.sigma_margin,
            "stale_stamp": self.stale_stamp,
            "is_stale": self.is_stale,
        }


def stamp_predictions(
    rows: list[dict[str, Any]],
    stale_ctx: StaleContext,
) -> list[StampedPrediction]:
    """Apply STALE stamp to every prediction row when context is stale."""
    stamp = stale_ctx.combined_stamp
    out: list[StampedPrediction] = []
    for row in rows:
        out.append(
            StampedPrediction(
                game_id=str(row["game_id"]),
                mu_margin=float(row["mu_margin"]),
                sigma_margin=float(row["sigma_margin"]),
                stale_stamp=stamp,
                is_stale=stale_ctx.is_stale,
            )
        )
    return out
