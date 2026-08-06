"""Resumable 2014–2025 CFBD backfill driver (Task 23-FIX-BACKFILL).

Season order by value (not chronological):
  2024, 2025, 2022, 2021, 2019, 2023(verify), then 2018 → 2014.

Per season: games → plays → advanced → lines → venues → roster, returning,
talent, recruiting, portal, coaches.

Uses existing ``run_cfbd_backfill`` / weather / quality CLIs. New code is only
checkpoint + quota pacing under ``ncaa_quant.data.ingest``.

Examples::

  uv run python scripts/backfill_23_cfbd.py --preflight-only
  uv run python scripts/backfill_23_cfbd.py --exit-on-quota
  uv run python scripts/backfill_23_cfbd.py --wait-for-quota
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.data.ingest import (
    BackfillCheckpoint,
    fetch_quota_status,
    load_checkpoint,
    wait_for_quota,
)
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.cfbd import (
    DEFAULT_RATE_LIMIT_RESERVE,
    ENDPOINT_SPECS,
    RateLimitBudgetError,
    is_partition_complete,
    run_cfbd_backfill,
)
from ncaa_quant.utils.logging import configure_logging, get_logger

# Value-first season order (task §6). 2020 included for Stage-1 continuity
# (§7.2 item 5) even though it is excluded from headline metrics.
SEASON_ORDER: tuple[int, ...] = (
    2024,
    2025,
    2022,
    2021,
    2020,
    2019,
    2023,
    2018,
    2017,
    2016,
    2015,
    2014,
)

# Per-season endpoint order (task §7). ``teams`` first for id resolution.
DATASET_ORDER: tuple[str, ...] = (
    "teams",
    "games",
    "plays",
    "advanced",
    "lines",
    "venues",
    "roster",
    "returning",
    "talent",
    "recruiting",
    "portal",
    "coaches",
)

WEEK_GRAIN = frozenset(
    name for name, spec in ENDPOINT_SPECS.items() if spec["grain"] == "season_week"
)
SEASON_GRAIN = frozenset(
    name for name, spec in ENDPOINT_SPECS.items() if spec["grain"] == "season"
)

PROGRESS_PATH = Path("docs/notes/backfill-progress.md")
CHECKPOINT_PATH = Path("data/tmp/backfill_23_checkpoint.json")
DEFAULT_MIN_REMAINING = DEFAULT_RATE_LIMIT_RESERVE


@dataclass
class SeasonReport:
    season: int
    row_counts: dict[str, int] = field(default_factory=dict)
    schema_pass: bool | None = None
    null_anomalies: list[str] = field(default_factory=list)
    calls_consumed: int | None = None
    wall_clock_s: float = 0.0
    notes: str = ""


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _append_progress(line: str) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not PROGRESS_PATH.exists():
        PROGRESS_PATH.write_text(
            "# Backfill progress (Task 23-FIX-BACKFILL)\n\n"
            "One line per completed season.\n\n"
            "```\n"
            "season | dataset row counts | schema pass/fail | null-rate anomalies | "
            "credits/calls consumed | cumulative wall clock\n"
            "```\n\n",
            encoding="utf-8",
        )
    with PROGRESS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")


def _row_count(store: ParquetStore, table: str, season: int) -> int:
    try:
        frame = store.read(table, filters={"season": season})
    except Exception:  # noqa: BLE001
        return 0
    return int(len(frame))


def _dataset_complete(store: ParquetStore, endpoint: str, season: int) -> bool:
    spec = ENDPOINT_SPECS[endpoint]
    table = spec["table"]
    if not table:
        return True
    if spec["grain"] == "season":
        return is_partition_complete(store, table, {"season": season})
    # Week-grain: require at least one week partition and games present.
    root = Path(store.root) / table / f"season={season}"
    if not root.exists():
        return False
    weeks = list(root.glob("week=*"))
    return len(weeks) >= 10


def _null_anomalies(store: ParquetStore, season: int) -> list[str]:
    """Flag known-high null rates; never zero-fill."""
    flags: list[str] = []
    plays = store.read("plays", filters={"season": season})
    if not plays.empty:
        if "wp" in plays.columns and float(plays["wp"].isna().mean()) > 0.9:
            flags.append("plays.wp null~=1.0 (source)")
        if "epa" in plays.columns:
            rate = float(plays["epa"].isna().mean())
            if rate > 0.5:
                flags.append(f"plays.epa null={rate:.2f}")
    returning = store.read("returning_production", filters={"season": season})
    if not returning.empty and "defense_pct" in returning.columns:
        if float(returning["defense_pct"].isna().mean()) > 0.9:
            flags.append("returning.defense_pct null~=1.0")
    recruiting = store.read("recruiting", filters={"season": season})
    if not recruiting.empty and "blue_chip_ratio" in recruiting.columns:
        if float(recruiting["blue_chip_ratio"].isna().mean()) > 0.9:
            flags.append("recruiting.blue_chip_ratio null~=1.0")
    return flags


def preflight(api_key: str) -> dict[str, Any]:
    """Quota tier / window / remaining-work report (no heavy spend)."""
    status = fetch_quota_status(api_key)
    cfg = load_config()
    missing: list[str] = []
    with ParquetStore(cfg.paths.staged_dir) as store:
        for season in SEASON_ORDER:
            for endpoint in DATASET_ORDER:
                if endpoint not in ENDPOINT_SPECS:
                    continue
                if not _dataset_complete(store, endpoint, season):
                    missing.append(f"{season}:{endpoint}")
    payload = {
        "observed_at": _now().isoformat(),
        "tier_name": status.tier_name,
        "patron_level": status.patron_level,
        "monthly_limit": status.monthly_limit,
        "remaining_calls": status.remaining_calls,
        "used_calls": status.used_calls,
        "reset_at": status.reset_at.isoformat(),
        "window": "monthly",
        "missing_partitions": missing,
        "missing_count": len(missing),
        "note": (
            "Probe's ~200-call cliff was leftover monthly budget, not a "
            "200/day tier. Sleep target is resetAt, not an hourly window."
        ),
    }
    out = Path("data/tmp/backfill_23_preflight.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _ensure_quota(
    api_key: str,
    *,
    exit_on_quota: bool,
    min_remaining: int,
) -> None:
    status = fetch_quota_status(api_key)
    if status.remaining_calls >= min_remaining:
        return
    resume = status.reset_at + timedelta(seconds=30)
    msg = (
        f"CFBD quota exhausted: remaining={status.remaining_calls}/"
        f"{status.monthly_limit} tier={status.tier_name} "
        f"resetAt={status.reset_at.isoformat()} resume≈{resume.isoformat()}"
    )
    get_logger(__name__).warning("cfbd_quota_exhausted", message=msg)
    if exit_on_quota:
        raise SystemExit(msg)
    wait_for_quota(api_key, min_remaining=min_remaining)


def _ingest_dataset(
    *,
    season: int,
    endpoint: str,
    force: bool,
) -> None:
    # Always ensure teams map exists before roster-family endpoints.
    endpoints: list[str] = [endpoint]
    if endpoint in {"roster", "returning", "talent", "recruiting", "portal", "coaches"}:
        endpoints = ["teams", endpoint]
    run_cfbd_backfill(seasons=[season], endpoints=endpoints, force=force)


class SchemaValidationFinding(RuntimeError):
    """CFBD payload failed pandera validation — reported, not patched."""


def _ingest_dataset_reporting(
    *,
    season: int,
    endpoint: str,
    force: bool,
) -> str | None:
    """Run ingest; on schema failure return a finding string (do not raise)."""
    try:
        _ingest_dataset(season=season, endpoint=endpoint, force=force)
    except Exception as exc:  # noqa: BLE001
        # Pandera SchemaErrors and PartitionError wrap validation failures.
        name = type(exc).__name__
        if "Schema" in name or "schema" in str(exc).lower() or "DATAFRAME_CHECK" in str(exc):
            return f"SCHEMA_FAIL {season}/{endpoint}: {exc}"
        raise
    return None


def _run_quality(season: int) -> tuple[bool, list[str]]:
    from ncaa_quant.quality.runner import run_quality

    result = run_quality((season,))
    ok = result.partitions_quarantined == 0 and result.hard_failure_count == 0
    return ok, list(result.summary_lines())


def _run_weather(season: int) -> str:
    from ncaa_quant.ingestion.weather import (
        MissingVenueCoordsError,
        run_weather_historical,
    )

    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        venues = store.read("venues", filters={"season": season})
    if venues.empty:
        return "weather skipped (no venues partition)"
    try:
        # Enrich venues (timezone/overrides) via CFBD path — needed for kickoff TZ.
        result = run_weather_historical(
            seasons=(season,), force=False, enrich_venues=True
        )
        return (
            f"weather rows_written={result.rows_written} "
            f"skipped={result.rows_skipped} gaps={len(result.gaps)}"
        )
    except MissingVenueCoordsError as exc:
        # Truncate multi-line venue lists for the progress line.
        msg = str(exc).split("\n")[0]
        return f"weather BLOCKED missing coords: {msg}"


def _materialize_features(season: int) -> str:
    """Best-effort feature materialization via library (CLI is unimplemented)."""
    try:
        from ncaa_quant.features.registry import load_registry

        registry = load_registry()
        n = len(registry.specs)
        return (
            f"features: registry has {n} specs; "
            "per-week materialize deferred to stack wiring "
            "(features CLI NotImplemented — no builder_factory in package)"
        )
    except Exception as exc:  # noqa: BLE001
        return f"features error: {exc}"


def _format_season_line(
    report: SeasonReport,
    *,
    cumulative_s: float,
    calls_before: int | None,
    calls_after: int | None,
) -> str:
    counts = " ".join(f"{k}={v}" for k, v in sorted(report.row_counts.items()))
    schema = (
        "pass"
        if report.schema_pass is True
        else ("fail" if report.schema_pass is False else "n/a")
    )
    anomalies = "; ".join(report.null_anomalies) if report.null_anomalies else "none"
    if calls_before is not None and calls_after is not None:
        consumed = calls_before - calls_after
    else:
        consumed = report.calls_consumed if report.calls_consumed is not None else "?"
    wall = str(timedelta(seconds=int(cumulative_s)))
    note = f" | {report.notes}" if report.notes else ""
    return (
        f"{report.season} | {counts} | schema={schema} | anomalies={anomalies} | "
        f"calls~={consumed} | cumulative={wall}{note}"
    )


CORE_DATASETS: tuple[str, ...] = ("games", "plays", "advanced", "lines")


def _core_complete(store: ParquetStore, season: int) -> bool:
    return all(_dataset_complete(store, ep, season) for ep in CORE_DATASETS)


def _scan_existing_partitions(
    store: ParquetStore,
    ckpt: BackfillCheckpoint,
    seasons: Sequence[int],
    *,
    force: bool,
) -> int:
    """Checkpoint already-staged partitions without spending API calls."""
    marked = 0
    if force:
        return 0
    for season in seasons:
        for endpoint in DATASET_ORDER:
            if ckpt.is_done(season, endpoint, 0):
                continue
            if _dataset_complete(store, endpoint, season):
                ckpt.mark(season, endpoint, 0)
                marked += 1
    return marked


def _season_postprocess(
    season: int,
    *,
    skip_quality: bool,
    skip_weather: bool,
    skip_features: bool,
    log: Any,
) -> tuple[bool | None, list[str], list[str]]:
    notes: list[str] = []
    schema_ok: bool | None = None
    if not skip_quality:
        try:
            schema_ok, lines = _run_quality(season)
            notes.append(f"quality={'pass' if schema_ok else 'fail'}")
            for line in lines[:5]:
                log.info("quality_line", season=season, line=line)
        except Exception as exc:  # noqa: BLE001
            schema_ok = False
            notes.append(f"quality_error={exc}")
            traceback.print_exc()
    if not skip_weather:
        notes.append(_run_weather(season))
    if not skip_features:
        notes.append(_materialize_features(season))
    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        anomalies = _null_anomalies(store, season)
    return schema_ok, notes, anomalies


def run_backfill(
    *,
    seasons: Sequence[int],
    force: bool,
    exit_on_quota: bool,
    skip_quality: bool,
    skip_weather: bool,
    skip_features: bool,
    checkpoint_path: Path,
    postprocess_core_only: bool = False,
) -> int:
    configure_logging()
    log = get_logger(__name__)
    secrets = load_secrets()
    api_key = secrets.cfbd_api_key.get_secret_value()
    cfg = load_config()
    ckpt = load_checkpoint(checkpoint_path)
    t0 = time.monotonic()
    status0 = fetch_quota_status(api_key)
    calls_start = status0.remaining_calls

    with ParquetStore(cfg.paths.staged_dir) as store:
        n_marked = _scan_existing_partitions(store, ckpt, seasons, force=force)
    log.info("backfill_checkpoint_scan", marked=n_marked, path=str(checkpoint_path))

    if postprocess_core_only:
        for season in seasons:
            with ParquetStore(cfg.paths.staged_dir) as store:
                if not _core_complete(store, season):
                    log.info("backfill_core_incomplete_skip", season=season)
                    continue
                counts = {
                    ENDPOINT_SPECS[ep]["table"]: _row_count(
                        store, ENDPOINT_SPECS[ep]["table"], season
                    )
                    for ep in DATASET_ORDER
                    if ENDPOINT_SPECS[ep]["table"]
                }
            schema_ok, notes, anomalies = _season_postprocess(
                season,
                skip_quality=skip_quality,
                skip_weather=skip_weather,
                skip_features=skip_features,
                log=log,
            )
            report = SeasonReport(
                season=season,
                row_counts=counts,
                schema_pass=schema_ok,
                null_anomalies=anomalies,
                notes="; ".join(notes),
            )
            line = _format_season_line(
                report,
                cumulative_s=time.monotonic() - t0,
                calls_before=calls_start,
                calls_after=calls_start,
            )
            print(line, flush=True)
            _append_progress(line)
        return 0

    for season in seasons:
        season_t0 = time.monotonic()
        log.info("backfill_season_start", season=season)
        for endpoint in DATASET_ORDER:
            page = 0  # season-grain unit; week-grain resume is partition-native
            if ckpt.is_done(season, endpoint, page) and not force:
                log.info(
                    "backfill_checkpoint_skip",
                    season=season,
                    dataset=endpoint,
                    page=page,
                )
                continue
            with ParquetStore(cfg.paths.staged_dir) as store:
                if not force and _dataset_complete(store, endpoint, season):
                    ckpt.mark(season, endpoint, page)
                    log.info(
                        "backfill_partition_present",
                        season=season,
                        dataset=endpoint,
                    )
                    continue
            while True:
                try:
                    _ensure_quota(
                        api_key,
                        exit_on_quota=exit_on_quota,
                        min_remaining=DEFAULT_MIN_REMAINING,
                    )
                    finding = _ingest_dataset_reporting(
                        season=season, endpoint=endpoint, force=force
                    )
                    if finding:
                        log.error(
                            "backfill_schema_finding",
                            season=season,
                            dataset=endpoint,
                            detail=finding[:500],
                        )
                        _append_progress(f"FINDING | {finding[:300]}")
                        # Do not mark complete — partition absent until schema allows it.
                        break
                    ckpt.mark(season, endpoint, page)
                    break
                except SystemExit:
                    # Quota hard-stop: still post-process this season's core if present.
                    log.warning(
                        "backfill_quota_stop_continuing_postprocess",
                        season=season,
                        dataset=endpoint,
                    )
                    with ParquetStore(cfg.paths.staged_dir) as store:
                        counts = {
                            ENDPOINT_SPECS[ep]["table"]: _row_count(
                                store, ENDPOINT_SPECS[ep]["table"], season
                            )
                            for ep in DATASET_ORDER
                            if ENDPOINT_SPECS[ep]["table"]
                        }
                        core_ok = _core_complete(store, season)
                    if core_ok:
                        schema_ok, notes, anomalies = _season_postprocess(
                            season,
                            skip_quality=skip_quality,
                            skip_weather=skip_weather,
                            skip_features=skip_features,
                            log=log,
                        )
                        notes = [
                            f"quota_blocked_at={endpoint}",
                            *notes,
                        ]
                        report = SeasonReport(
                            season=season,
                            row_counts=counts,
                            schema_pass=schema_ok,
                            null_anomalies=anomalies,
                            wall_clock_s=time.monotonic() - season_t0,
                            notes="; ".join(notes),
                        )
                        line = _format_season_line(
                            report,
                            cumulative_s=time.monotonic() - t0,
                            calls_before=calls_start,
                            calls_after=fetch_quota_status(api_key).remaining_calls,
                        )
                        print(line, flush=True)
                        _append_progress(line)
                    raise
                except RateLimitBudgetError:
                    log.warning(
                        "backfill_rate_limit_mid_dataset",
                        season=season,
                        dataset=endpoint,
                    )
                    if exit_on_quota:
                        status = fetch_quota_status(api_key)
                        raise SystemExit(
                            f"RateLimitBudgetError mid {season}/{endpoint}; "
                            f"remaining={status.remaining_calls} "
                            f"resetAt={status.reset_at.isoformat()}"
                        ) from None
                    wait_for_quota(api_key, min_remaining=DEFAULT_MIN_REMAINING)

        schema_ok, notes, anomalies = _season_postprocess(
            season,
            skip_quality=skip_quality,
            skip_weather=skip_weather,
            skip_features=skip_features,
            log=log,
        )

        with ParquetStore(cfg.paths.staged_dir) as store:
            counts = {
                ENDPOINT_SPECS[ep]["table"]: _row_count(
                    store, ENDPOINT_SPECS[ep]["table"], season
                )
                for ep in DATASET_ORDER
                if ENDPOINT_SPECS[ep]["table"]
            }

        calls_now = fetch_quota_status(api_key).remaining_calls
        report = SeasonReport(
            season=season,
            row_counts=counts,
            schema_pass=schema_ok,
            null_anomalies=anomalies,
            wall_clock_s=time.monotonic() - season_t0,
            notes="; ".join(notes),
        )
        line = _format_season_line(
            report,
            cumulative_s=time.monotonic() - t0,
            calls_before=calls_start,
            calls_after=calls_now,
        )
        print(line, flush=True)
        _append_progress(line)
        calls_start = calls_now

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Print quota + missing-partition report and exit.",
    )
    parser.add_argument(
        "--exit-on-quota",
        action="store_true",
        help="Exit instead of sleeping to the next monthly window.",
    )
    parser.add_argument(
        "--wait-for-quota",
        action="store_true",
        help="Sleep until resetAt when exhausted (default unless --exit-on-quota).",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--skip-weather", action="store_true")
    parser.add_argument("--skip-features", action="store_true")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=CHECKPOINT_PATH,
        help="Checkpoint JSON path (survives process death).",
    )
    parser.add_argument(
        "--seasons",
        type=str,
        default="",
        help="Optional comma list to override value-order subset.",
    )
    parser.add_argument(
        "--postprocess-core-only",
        action="store_true",
        help=(
            "Skip CFBD fetches; run quality/weather/features for seasons whose "
            "core week-grain (games/plays/advanced/lines) is already staged."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    secrets = load_secrets()
    api_key = secrets.cfbd_api_key.get_secret_value()

    if args.preflight_only:
        payload = preflight(api_key)
        print(json.dumps(payload, indent=2))
        return 0

    exit_on_quota = bool(args.exit_on_quota) or not bool(args.wait_for_quota)
    # Explicit --wait-for-quota wins.
    if args.wait_for_quota:
        exit_on_quota = False

    if args.seasons.strip():
        seasons = tuple(int(s.strip()) for s in args.seasons.split(",") if s.strip())
    else:
        seasons = SEASON_ORDER

    return run_backfill(
        seasons=seasons,
        force=args.force,
        exit_on_quota=exit_on_quota,
        skip_quality=args.skip_quality,
        skip_weather=args.skip_weather,
        skip_features=args.skip_features,
        checkpoint_path=args.checkpoint,
        postprocess_core_only=bool(args.postprocess_core_only),
    )


if __name__ == "__main__":
    sys.exit(main())
