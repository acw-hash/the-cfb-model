"""Standalone odds cadence watchdog (DESIGN §10).

Independent of ``predict_publish`` — this module must never import the publish
path. Cadence shortfalls alert through :mod:`ncaa_quant.pipelines.notifications`
only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from prefect import flow

from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.pipelines.notifications import AlertKind, notify
from ncaa_quant.utils.logging import configure_logging, get_logger

log = get_logger(__name__)

# Explicit allow-list of imports for audit (S23): no predict / export / webapp /
# social / betting publish modules.
_IMPORT_SURFACE: tuple[str, ...] = (
    "ncaa_quant.config",
    "ncaa_quant.pipelines.notifications",
    "ncaa_quant.utils.logging",
    "prefect",
)


def check_odds_cadence(
    *,
    raw_root: Path | str,
    expected_per_day: int,
    tolerance: int,
    window_hours: int = 24,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return cadence stats; caller decides whether to alert.

    Counts ``*.json`` files under ``raw_root`` whose mtime falls inside the
    trailing ``window_hours`` window (default 24h).
    """
    root = Path(raw_root)
    clock = now if now is not None else datetime.now(tz=UTC)
    cutoff = clock.timestamp() - window_hours * 3600
    count = 0
    if root.is_dir():
        for path in root.rglob("*.json"):
            if path.stat().st_mtime >= cutoff:
                count += 1
    minimum = max(0, expected_per_day - tolerance)
    shortfall = count < minimum
    return {
        "snapshots_24h": count,
        "expected_minimum": minimum,
        "shortfall": shortfall,
    }


def run_odds_cadence_watchdog(
    *,
    config: AppConfig | None = None,
    raw_root: Path | str | None = None,
    expected_per_day: int | None = None,
    tolerance: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate odds raw-archive cadence and notify on shortfall."""
    cfg = config or load_config()
    root = Path(raw_root) if raw_root is not None else Path(cfg.paths.raw_dir) / "odds_api"
    expected = (
        int(expected_per_day)
        if expected_per_day is not None
        else int(cfg.pipeline.odds_snapshots_per_day)
    )
    tol = int(tolerance) if tolerance is not None else int(cfg.pipeline.odds_cadence_tolerance)
    cadence = check_odds_cadence(
        raw_root=root,
        expected_per_day=expected,
        tolerance=tol,
        now=now,
    )
    result: dict[str, Any] = {
        "raw_root": str(root),
        "expected_per_day": expected,
        "tolerance": tol,
        **cadence,
        "notified": False,
    }
    if cadence["shortfall"]:
        try:
            sent = notify(
                AlertKind.CADENCE_SHORTFALL,
                "odds cadence shortfall",
                (
                    f"snapshots_24h={cadence['snapshots_24h']} "
                    f"expected_min={cadence['expected_minimum']}"
                ),
                config=cfg,
                priority=4,
            )
        except Exception as exc:  # noqa: BLE001 — transport errors must not hide shortfall
            log.exception("odds_cadence_notify_failed", error=str(exc))
            sent = False
        result["notified"] = bool(sent)
        log.warning(
            "odds_cadence_shortfall",
            snapshots_24h=cadence["snapshots_24h"],
            expected_minimum=cadence["expected_minimum"],
            notified=result["notified"],
        )
    else:
        log.info(
            "odds_cadence_ok",
            snapshots_24h=cadence["snapshots_24h"],
            expected_minimum=cadence["expected_minimum"],
        )
    return result


@flow(name="odds_cadence_watchdog")
def odds_cadence_watchdog_flow(
    raw_root: str | None = None,
    expected_per_day: int | None = None,
    tolerance: int | None = None,
) -> dict[str, Any]:
    """Hourly (configurable) cadence check — no publish side effects.

    Optional overrides exist only for operator proof / dry-run shortfalls;
    the scheduled deployment passes no parameters (config defaults).
    """
    configure_logging()
    return run_odds_cadence_watchdog(
        raw_root=raw_root,
        expected_per_day=expected_per_day,
        tolerance=tolerance,
    )


def register_odds_cadence_watchdog(
    *,
    work_pool_name: str = "default",
    cron: str | None = None,
    working_dir: Path | str | None = None,
) -> UUID:
    """Deploy the watchdog to ``work_pool_name`` (default process pool)."""
    cfg = load_config()
    schedule = cron if cron is not None else cfg.pipeline.odds_cadence_watchdog_cron
    repo = Path(working_dir) if working_dir is not None else Path.cwd()
    repo = repo.resolve()
    entrypoint = "src/ncaa_quant/pipelines/cadence.py:odds_cadence_watchdog_flow"
    configure_logging()
    log.info(
        "registering_odds_cadence_watchdog",
        work_pool=work_pool_name,
        cron=schedule,
        import_surface=list(_IMPORT_SURFACE),
    )
    deployment_id = odds_cadence_watchdog_flow.from_source(
        source=str(repo),
        entrypoint=entrypoint,
    ).deploy(
        name="odds_cadence_watchdog",
        work_pool_name=work_pool_name,
        cron=schedule,
        build=False,
        push=False,
        print_next_steps=False,
        ignore_warnings=True,
        tags=["odds", "cadence", "watchdog"],
        description="Standalone DESIGN §10 cadence shortfall watchdog (no publish).",
        job_variables={
            "working_dir": str(repo),
            "env": {
                "PREFECT_API_URL": "http://127.0.0.1:4200/api",
            },
        },
    )
    return deployment_id


if __name__ == "__main__":
    register_odds_cadence_watchdog()
