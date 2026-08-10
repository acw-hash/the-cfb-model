"""Point-in-time temporal sanity checks (DESIGN §8 / §15 item 7).

These are the quality-layer PIT guards. Feature-store ``pit_audit`` that
recomputes feature rows belongs to :mod:`ncaa_quant.features.pit_audit`; this
module asserts ingestion temporal contracts on staged partitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.quality.validators import CheckFinding, _sample_records


@dataclass
class StagedPitAuditResult:
    """Aggregate temporal PIT audit over staged partitions."""

    seasons: tuple[int, ...]
    partitions_checked: int = 0
    partitions_passed: int = 0
    partition_failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.partition_failures

    def summary_lines(self) -> list[str]:
        lines = [
            f"pit_audit seasons={list(self.seasons)}",
            f"  partitions checked={self.partitions_checked} "
            f"passed={self.partitions_passed} "
            f"failed={len(self.partition_failures)}",
        ]
        for failure in self.partition_failures[:30]:
            week = failure.get("week")
            week_s = f" w{week}" if week is not None else ""
            lines.append(
                f"  FAIL {failure['table']} s{failure['season']}{week_s} — "
                f"{failure['expectation']}: {failure['message']}"
            )
        if len(self.partition_failures) > 30:
            lines.append(f"  ... and {len(self.partition_failures) - 30} more")
        return lines


def check_temporal_sanity(df: pd.DataFrame) -> list[CheckFinding]:
    """Fail on any row where ``event_time > ingested_at``.

    Units: both timestamps are timezone-aware UTC. Equality is allowed
    (``event_time <= ingested_at``).
    """
    if df.empty:
        return []
    if "event_time" not in df.columns or "ingested_at" not in df.columns:
        return [
            CheckFinding(
                expectation="temporal_sanity_columns",
                severity="fail",
                message="partition missing event_time and/or ingested_at",
                sample_rows=_sample_records(df),
                n_failures=len(df),
            )
        ]

    event = pd.to_datetime(df["event_time"], utc=True)
    ingested = pd.to_datetime(df["ingested_at"], utc=True)
    bad_mask = event > ingested
    if not bad_mask.any():
        return []
    bad = df.loc[bad_mask]
    return [
        CheckFinding(
            expectation="temporal_sanity_event_time_le_ingested_at",
            severity="fail",
            message=f"{int(bad_mask.sum())} rows with event_time > ingested_at",
            sample_rows=_sample_records(bad),
            n_failures=int(bad_mask.sum()),
        )
    ]


def check_negative_scores(games: pd.DataFrame) -> list[CheckFinding]:
    """Fail when final points are negative (range also covered by GE; explicit for fixtures)."""
    if games.empty:
        return []
    findings: list[CheckFinding] = []
    for col in ("home_points", "away_points"):
        if col not in games.columns:
            continue
        mask = games[col].notna() & (games[col] < 0)
        if mask.any():
            findings.append(
                CheckFinding(
                    expectation=f"range_non_negative_{col}",
                    severity="fail",
                    message=f"{int(mask.sum())} rows with {col} < 0",
                    sample_rows=_sample_records(games.loc[mask]),
                    n_failures=int(mask.sum()),
                )
            )
    return findings


def assert_no_future_event_times(
    df: pd.DataFrame,
    *,
    as_of: Any,
    ts_col: str = "event_time",
) -> list[CheckFinding]:
    """Fail rows whose ``ts_col`` is strictly after ``as_of`` (PIT consumer guard)."""
    if df.empty or ts_col not in df.columns:
        return []
    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tzinfo is None:
        as_of_ts = as_of_ts.tz_localize("UTC")
    else:
        as_of_ts = as_of_ts.tz_convert("UTC")
    ts = pd.to_datetime(df[ts_col], utc=True)
    bad_mask = ts > as_of_ts
    if not bad_mask.any():
        return []
    return [
        CheckFinding(
            expectation="pit_no_future_event_time",
            severity="fail",
            message=f"{int(bad_mask.sum())} rows with {ts_col} > as_of={as_of_ts.isoformat()}",
            sample_rows=_sample_records(df.loc[bad_mask]),
            n_failures=int(bad_mask.sum()),
        )
    ]


def run_staged_pit_audit(
    seasons: tuple[int, ...] | list[int],
    *,
    staged_dir: Path | str | None = None,
    tables: tuple[str, ...] | None = None,
    sample_rows_per_partition: int = 0,
) -> StagedPitAuditResult:
    """Run ingestion temporal PIT checks over every staged partition in ``seasons``.

    This is the Phase 2 "full pit_audit" over the staged set (amended
    ``event_time`` semantics). Feature-store recomputation audits live in
    :mod:`ncaa_quant.features.pit_audit` and require materialized features.
    """
    from ncaa_quant.config import load_config
    from ncaa_quant.data.schemas import GAME_GRAINED_TABLES, REFERENCE_TABLES
    from ncaa_quant.data.storage import ParquetStore

    cfg = load_config()
    root = Path(staged_dir) if staged_dir is not None else Path(cfg.paths.staged_dir)
    season_tuple = tuple(int(s) for s in seasons)
    target = tables or tuple(sorted(set(GAME_GRAINED_TABLES) | set(REFERENCE_TABLES)))
    result = StagedPitAuditResult(seasons=season_tuple)
    del sample_rows_per_partition  # reserved for future row-level sampling reports

    with ParquetStore(root) as store:
        for season in season_tuple:
            for table in target:
                try:
                    paths = list(store._matching_paths(table, {"season": season}))  # noqa: SLF001
                except Exception:  # noqa: BLE001
                    paths = []
                if not paths:
                    continue
                for path in paths:
                    try:
                        df = pd.read_parquet(path)
                    except Exception as exc:  # noqa: BLE001
                        result.partition_failures.append(
                            {
                                "table": table,
                                "season": season,
                                "path": str(path),
                                "expectation": "parquet_readable",
                                "message": str(exc),
                                "n_failures": 1,
                            }
                        )
                        continue
                    week = _week_from_path(path)
                    findings = check_temporal_sanity(df)
                    if table == "games":
                        findings.extend(check_negative_scores(df))
                    result.partitions_checked += 1
                    if not findings:
                        result.partitions_passed += 1
                        continue
                    for finding in findings:
                        if finding.severity != "fail":
                            continue
                        result.partition_failures.append(
                            {
                                "table": table,
                                "season": season,
                                "week": week,
                                "path": str(path),
                                "expectation": finding.expectation,
                                "message": finding.message,
                                "n_failures": int(finding.n_failures),
                            }
                        )

    return result


def _week_from_path(path: Path) -> int | None:
    for part in path.parts:
        if part.startswith("week="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                return None
    return None
