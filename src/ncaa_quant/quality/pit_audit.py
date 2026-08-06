"""Point-in-time temporal sanity checks (DESIGN §8 / §15 item 7).

These are the quality-layer PIT guards. Feature-store ``pit_audit`` that
recomputes feature rows belongs to a later task; this module only asserts
ingestion temporal contracts on staged partitions.
"""

from __future__ import annotations

from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.quality.validators import CheckFinding, _sample_records


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
