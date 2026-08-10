"""Orchestrate quality checks across staged partitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.data.schemas import GAME_GRAINED_TABLES, REFERENCE_TABLES
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.quality.expectations.suites import TABLE_SUITES, run_suite_on_dataframe
from ncaa_quant.quality.pit_audit import check_negative_scores, check_temporal_sanity
from ncaa_quant.quality.quarantine import (
    Status,
    ValidationRecord,
    new_run_id,
    partition_status_from_findings,
    utcnow,
    write_validation_records,
)
from ncaa_quant.quality.reports import write_reports
from ncaa_quant.quality.validators import (
    CheckFinding,
    check_cfbd_slot_close_reconciliation,
    check_completeness_game_counts,
    check_duplicates,
    check_line_open_close_move,
    check_pbp_drive_points_reconcile,
    check_play_sequence_monotone,
    check_plays_score_clock_null_rates,
    check_referential_games_venue,
    check_referential_plays_in_games,
    check_score_consistency_box,
    check_snapshot_monotonicity,
)
from ncaa_quant.utils.logging import get_logger

log = get_logger(__name__)

# Natural keys for duplicate detection (beyond GE compound uniqueness).
_DUP_KEYS: dict[str, list[str]] = {
    "games": ["game_id"],
    "plays": ["game_id", "play_id"],
    "drives": ["game_id", "drive_id"],
    "advanced_box": ["game_id", "team_id"],
    "lines_historical": ["game_id", "book", "line_type"],
    "venues": ["venue_id"],
    "odds_snapshots": ["snapshot_id"],
}


@dataclass
class PartitionOutcome:
    """Per-partition summary."""

    table: str
    season: int
    week: int | None
    status: str
    hard_failures: int
    flag_count: int


@dataclass
class FindingView:
    """Flattened finding for reports / CLI."""

    table: str
    season: int
    week: int | None
    expectation: str
    severity: str
    message: str
    n_failures: int


@dataclass
class QualityRunResult:
    """Aggregate result of ``run_quality``."""

    run_id: str
    seasons: tuple[int, ...]
    partitions: list[PartitionOutcome] = field(default_factory=list)
    findings: list[FindingView] = field(default_factory=list)
    report_md: Path | None = None
    report_html: Path | None = None

    @property
    def partitions_checked(self) -> int:
        return len(self.partitions)

    @property
    def partitions_passed(self) -> int:
        return sum(1 for p in self.partitions if p.status == "PASSED")

    @property
    def partitions_quarantined(self) -> int:
        return sum(1 for p in self.partitions if p.status == "QUARANTINED")

    @property
    def partitions_flagged(self) -> int:
        return sum(1 for p in self.partitions if p.status == "FLAGGED")

    @property
    def hard_failure_count(self) -> int:
        return sum(p.hard_failures for p in self.partitions)

    @property
    def flag_count(self) -> int:
        return sum(p.flag_count for p in self.partitions)

    def summary_lines(self) -> list[str]:
        """Human-readable summary lines for CLI echo."""
        lines = [
            f"quality run_id={self.run_id} seasons={list(self.seasons)}",
            (
                f"  partitions checked={self.partitions_checked} "
                f"passed={self.partitions_passed} "
                f"quarantined={self.partitions_quarantined} "
                f"flagged={self.partitions_flagged}"
            ),
            f"  hard_failures={self.hard_failure_count} soft_flags={self.flag_count}",
        ]
        if self.report_md is not None:
            lines.append(f"  report_md={self.report_md}")
        if self.report_html is not None:
            lines.append(f"  report_html={self.report_html}")
        # Show up to 30 findings.
        for finding in self.findings[:30]:
            week = "" if finding.week is None else f" w{finding.week}"
            lines.append(
                f"  [{finding.severity}] {finding.table} s{finding.season}{week} "
                f"{finding.expectation}: {finding.message}"
            )
        if len(self.findings) > 30:
            lines.append(f"  ... {len(self.findings) - 30} more findings")
        return lines


def run_quality(
    seasons: tuple[int, ...] | list[int],
    *,
    config: AppConfig | None = None,
    staged_dir: Path | str | None = None,
    report_dir: Path | str | None = None,
    tables: tuple[str, ...] | None = None,
) -> QualityRunResult:
    """Run GE suites + custom validators for ``seasons``.

    Failures quarantine the affected partition and soft-continue other
    partitions in the same run (DESIGN §8 step 2).
    """
    cfg = config or load_config()
    root = Path(staged_dir) if staged_dir is not None else Path(cfg.paths.staged_dir)
    reports_out = (
        Path(report_dir) if report_dir is not None else Path("docs") / "quality" / "reports"
    )
    season_tuple = tuple(int(s) for s in seasons)
    target_tables = tables or TABLE_SUITES
    run_id = new_run_id()
    validated_at = utcnow()
    result = QualityRunResult(run_id=run_id, seasons=season_tuple)
    records: list[ValidationRecord] = []

    with ParquetStore(root) as store:
        for season in season_tuple:
            venues = _safe_read(store, "venues", {"season": season})
            season_games = _safe_read(store, "games", {"season": season})
            season_lines = _safe_read(store, "lines_historical", {"season": season})
            season_odds = _safe_read(store, "odds_snapshots", {"season": season})

            # Reference: venues (season-partitioned).
            if "venues" in target_tables:
                _validate_partition(
                    result=result,
                    records=records,
                    run_id=run_id,
                    validated_at=validated_at,
                    table="venues",
                    season=season,
                    week=None,
                    df=venues,
                    games=season_games,
                    venues=venues,
                    lines=season_lines,
                    odds=season_odds,
                    plays=pd.DataFrame(),
                    drives=pd.DataFrame(),
                    advanced_box=pd.DataFrame(),
                )

            weeks = _discover_weeks(root, season, target_tables)
            for week in weeks:
                games = _safe_read(store, "games", {"season": season, "week": week})
                plays = _safe_read(store, "plays", {"season": season, "week": week})
                drives = _safe_read(store, "drives", {"season": season, "week": week})
                box = _safe_read(store, "advanced_box", {"season": season, "week": week})
                lines = _safe_read(store, "lines_historical", {"season": season, "week": week})
                odds = _safe_read(store, "odds_snapshots", {"season": season, "week": week})

                week_frames: dict[str, pd.DataFrame] = {
                    "games": games,
                    "plays": plays,
                    "drives": drives,
                    "advanced_box": box,
                    "lines_historical": lines,
                    "odds_snapshots": odds,
                }
                for table in target_tables:
                    if table in REFERENCE_TABLES:
                        continue
                    if table not in week_frames:
                        continue
                    df = week_frames[table]
                    if df.empty and not _partition_exists(root, table, season, week):
                        continue
                    _validate_partition(
                        result=result,
                        records=records,
                        run_id=run_id,
                        validated_at=validated_at,
                        table=table,
                        season=season,
                        week=week,
                        df=df,
                        games=games,
                        venues=venues,
                        lines=lines,
                        odds=odds,
                        plays=plays,
                        drives=drives,
                        advanced_box=box,
                    )

            # Season-scoped soft flags that need full tables (once per season).
            if (
                "lines_historical" in target_tables
                and "odds_snapshots" in target_tables
                and not season_lines.empty
                and not season_odds.empty
            ):
                for finding in check_cfbd_slot_close_reconciliation(season_lines, season_odds):
                    # Attach to season-level lines partition (week=None sentinel via week 0? )
                    # Store under week=None in a synthetic season record on lines.
                    records.append(
                        ValidationRecord(
                            run_id=run_id,
                            table="lines_historical",
                            season=season,
                            week=None,
                            status="FLAGGED",
                            expectation=finding.expectation,
                            severity=finding.severity,
                            message=finding.message,
                            sample_rows=finding.sample_rows,
                            n_failures=finding.n_failures,
                            validated_at=validated_at,
                        )
                    )
                    result.findings.append(
                        FindingView(
                            table="lines_historical",
                            season=season,
                            week=None,
                            expectation=finding.expectation,
                            severity=finding.severity,
                            message=finding.message,
                            n_failures=finding.n_failures,
                        )
                    )

    write_validation_records(root, records)
    md_path, html_path = write_reports(result, reports_out)
    result.report_md = md_path
    result.report_html = html_path
    log.info(
        "quality_run_complete",
        run_id=run_id,
        seasons=list(season_tuple),
        quarantined=result.partitions_quarantined,
        flagged=result.partitions_flagged,
        hard_failures=result.hard_failure_count,
    )
    return result


def _validate_partition(
    *,
    result: QualityRunResult,
    records: list[ValidationRecord],
    run_id: str,
    validated_at: Any,
    table: str,
    season: int,
    week: int | None,
    df: pd.DataFrame,
    games: pd.DataFrame,
    venues: pd.DataFrame,
    lines: pd.DataFrame,
    odds: pd.DataFrame,
    plays: pd.DataFrame,
    drives: pd.DataFrame,
    advanced_box: pd.DataFrame,
) -> None:
    findings: list[CheckFinding] = []

    # GE suite (schema / ranges / uniqueness).
    if table in TABLE_SUITES and not df.empty:
        suite_result = run_suite_on_dataframe(table, df)
        for failure in suite_result.failures:
            findings.append(
                CheckFinding(
                    expectation=failure.expectation,
                    severity="fail",
                    message=failure.message,
                    sample_rows=failure.sample_rows,
                    n_failures=failure.unexpected_count or 1,
                )
            )

    # Temporal sanity on every timed table.
    findings.extend(check_temporal_sanity(df))

    # Duplicate detection (belt-and-suspenders with GE).
    keys = _DUP_KEYS.get(table)
    if keys is not None:
        findings.extend(check_duplicates(df, key_columns=keys))

    if table == "games":
        findings.extend(check_negative_scores(df))
        findings.extend(check_referential_games_venue(df, venues))
    elif table == "plays":
        findings.extend(check_referential_plays_in_games(df, games))
        findings.extend(check_completeness_game_counts(games, df, dependent_name="plays"))
        findings.extend(check_play_sequence_monotone(df))
        findings.extend(check_plays_score_clock_null_rates(df))
    elif table == "drives":
        findings.extend(check_completeness_game_counts(games, df, dependent_name="drives"))
        findings.extend(check_pbp_drive_points_reconcile(games, df))
    elif table == "advanced_box":
        findings.extend(check_completeness_game_counts(games, df, dependent_name="advanced_box"))
        findings.extend(check_score_consistency_box(games, df))
    elif table == "lines_historical":
        findings.extend(check_line_open_close_move(df))
    elif table == "odds_snapshots":
        findings.extend(check_snapshot_monotonicity(df, games))

    hard = [f for f in findings if f.severity == "fail"]
    soft = [f for f in findings if f.severity == "flag"]
    status = partition_status_from_findings(hard_failures=len(hard), flags=len(soft))

    result.partitions.append(
        PartitionOutcome(
            table=table,
            season=season,
            week=week,
            status=status,
            hard_failures=len(hard),
            flag_count=len(soft),
        )
    )

    if not findings:
        records.append(
            ValidationRecord(
                run_id=run_id,
                table=table,
                season=season,
                week=week,
                status="PASSED",
                expectation="all_checks",
                severity="info",
                message="all expectations passed",
                sample_rows=[],
                n_failures=0,
                validated_at=validated_at,
            )
        )
        return

    for finding in findings:
        part_status: Status = "QUARANTINED" if finding.severity == "fail" else "FLAGGED"
        record_status: Status = part_status if status != "PASSED" else "PASSED"
        records.append(
            ValidationRecord(
                run_id=run_id,
                table=table,
                season=season,
                week=week,
                status=record_status,
                expectation=finding.expectation,
                severity=finding.severity,
                message=finding.message,
                sample_rows=finding.sample_rows,
                n_failures=finding.n_failures,
                validated_at=validated_at,
            )
        )
        result.findings.append(
            FindingView(
                table=table,
                season=season,
                week=week,
                expectation=finding.expectation,
                severity=finding.severity,
                message=finding.message,
                n_failures=finding.n_failures,
            )
        )


def _safe_read(store: ParquetStore, table: str, filters: dict[str, Any]) -> pd.DataFrame:
    try:
        return store.read(table, filters)
    except KeyError:
        return pd.DataFrame()
    except Exception:  # noqa: BLE001 — soft-continue other partitions
        log.exception("quality_read_failed", table=table, filters=filters)
        return pd.DataFrame()


def _discover_weeks(root: Path, season: int, tables: tuple[str, ...]) -> list[int]:
    weeks: set[int] = set()
    for table in tables:
        if table not in GAME_GRAINED_TABLES:
            continue
        season_dir = root / table / f"season={season}"
        if not season_dir.exists():
            continue
        for week_dir in season_dir.glob("week=*"):
            try:
                weeks.add(int(week_dir.name.split("=", 1)[1]))
            except ValueError:
                continue
    return sorted(weeks)


def _partition_exists(root: Path, table: str, season: int, week: int | None) -> bool:
    path = root / table / f"season={season}"
    if week is not None:
        path = path / f"week={week}"
    return (path / "part.parquet").exists()
