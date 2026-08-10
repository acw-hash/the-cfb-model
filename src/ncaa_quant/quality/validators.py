"""Custom validators not expressible as single-table Great Expectations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd  # type: ignore[import-untyped]

Severity = Literal["fail", "flag"]

# Drive points often omit PAT / defensive scores in CFBD; allow one TD+PAT slack.
# Documented in docs/notes/07.md — not loosened further to clear real-data noise.
DRIVE_POINTS_TOLERANCE = 8

# Task 7: open vs close move >= 20 points is interesting, not a hard fail.
LINE_MOVE_FLAG_THRESHOLD = 20.0

# CFBD-close vs Odds-API slot_close divergence (docs/historical_odds_change_set.md).
CFBD_SLOT_CLOSE_TOLERANCE = 1.5

# GT-FIX: max allowed null fraction for Connelly GT inputs on staged plays.
# WP is intentionally excluded — CFBD /plays archives do not ship it.
PLAYS_GT_INPUT_MAX_NULL_FRAC = 0.05

_SAMPLE_LIMIT = 5


@dataclass(frozen=True)
class CheckFinding:
    """One custom-check failure or soft flag."""

    expectation: str
    severity: Severity
    message: str
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    n_failures: int = 0


def check_duplicates(
    df: pd.DataFrame,
    *,
    key_columns: list[str],
    expectation: str = "duplicate_detection",
) -> list[CheckFinding]:
    """Fail when ``key_columns`` are duplicated."""
    if df.empty or any(c not in df.columns for c in key_columns):
        return []
    dup_mask = df.duplicated(subset=key_columns, keep=False)
    if not dup_mask.any():
        return []
    bad = df.loc[dup_mask]
    return [
        CheckFinding(
            expectation=expectation,
            severity="fail",
            message=f"duplicate rows on {key_columns}: {int(dup_mask.sum())} rows",
            sample_rows=_sample_records(bad),
            n_failures=int(dup_mask.sum()),
        )
    ]


def check_referential_plays_in_games(
    plays: pd.DataFrame,
    games: pd.DataFrame,
) -> list[CheckFinding]:
    """Every ``plays.game_id`` must exist in ``games``."""
    if plays.empty:
        return []
    game_ids = set(games["game_id"].tolist()) if not games.empty else set()
    orphan_mask = ~plays["game_id"].isin(game_ids)
    if not orphan_mask.any():
        return []
    bad = plays.loc[orphan_mask]
    return [
        CheckFinding(
            expectation="referential_plays_game_id",
            severity="fail",
            message=f"{orphan_mask.sum()} play rows reference missing game_id",
            sample_rows=_sample_records(bad),
            n_failures=int(orphan_mask.sum()),
        )
    ]


def check_plays_score_clock_null_rates(
    plays: pd.DataFrame,
    *,
    max_null_frac: float = PLAYS_GT_INPUT_MAX_NULL_FRAC,
) -> list[CheckFinding]:
    """Fail when offense/defense score or clock are mostly null (GT-FIX guard).

    Prevents silent recurrence of Task 5 dropping Connelly GT inputs at ingest.
    ``wp`` is not gated — source archives leave it null by design.
    """
    if plays.empty:
        return []
    findings: list[CheckFinding] = []
    for col in ("offense_score", "defense_score", "clock", "score_margin"):
        if col not in plays.columns:
            findings.append(
                CheckFinding(
                    expectation=f"plays_gt_input_null_rate_{col}",
                    severity="fail",
                    message=(
                        f"plays missing required GT input column {col!r} "
                        "(null-rate guard cannot run)"
                    ),
                    sample_rows=[],
                    n_failures=1,
                )
            )
            continue
        null_frac = float(plays[col].isna().mean())
        if null_frac > max_null_frac:
            bad = plays.loc[plays[col].isna()]
            findings.append(
                CheckFinding(
                    expectation=f"plays_gt_input_null_rate_{col}",
                    severity="fail",
                    message=(
                        f"plays.{col} null_frac={null_frac:.4f} exceeds "
                        f"max_null_frac={max_null_frac:.4f} "
                        "(Connelly garbage-time inputs must be staged)"
                    ),
                    sample_rows=_sample_records(bad),
                    n_failures=int(plays[col].isna().sum()),
                )
            )
    return findings


def check_referential_games_venue(
    games: pd.DataFrame,
    venues: pd.DataFrame,
) -> list[CheckFinding]:
    """Every non-null ``games.venue_id`` must exist in ``venues``."""
    if games.empty or "venue_id" not in games.columns:
        return []
    venue_ids = set(venues["venue_id"].tolist()) if not venues.empty else set()
    keyed = games[games["venue_id"].notna()]
    orphan_mask = ~keyed["venue_id"].isin(venue_ids)
    if not orphan_mask.any():
        return []
    bad = keyed.loc[orphan_mask]
    return [
        CheckFinding(
            expectation="referential_games_venue_id",
            severity="fail",
            message=f"{orphan_mask.sum()} games reference missing venue_id",
            sample_rows=_sample_records(bad),
            n_failures=int(orphan_mask.sum()),
        )
    ]


def check_completeness_game_counts(
    games: pd.DataFrame,
    dependent: pd.DataFrame,
    *,
    dependent_name: str,
    only_completed: bool = True,
) -> list[CheckFinding]:
    """Completeness: completed games should appear in ``dependent`` by game_id.

    Expected count is the unique ``game_id`` count in ``games`` (optionally
    completed only). Missing coverage fails the dependent partition.
    """
    if games.empty:
        return []
    expected = games
    if only_completed and "completed" in games.columns:
        expected = games[games["completed"].astype(bool)]
    expected_ids = set(expected["game_id"].tolist())
    if not expected_ids:
        return []
    present = set(dependent["game_id"].tolist()) if not dependent.empty else set()
    missing = sorted(expected_ids - present)
    if not missing:
        return []
    sample = expected[expected["game_id"].isin(missing)]
    return [
        CheckFinding(
            expectation=f"completeness_{dependent_name}_vs_games",
            severity="fail",
            message=(
                f"{dependent_name} missing {len(missing)}/{len(expected_ids)} "
                f"expected game_ids for partition"
            ),
            sample_rows=_sample_records(sample),
            n_failures=len(missing),
        )
    ]


def check_score_consistency_box(
    games: pd.DataFrame,
    advanced_box: pd.DataFrame,
) -> list[CheckFinding]:
    """Box ``points`` per team must match final ``home_points`` / ``away_points``.

    Rows with null box points are skipped (CFBD advanced currently leaves
    ``points`` unset — see notes). Fixtures with populated points are enforced.
    """
    if games.empty or advanced_box.empty or "points" not in advanced_box.columns:
        return []
    box = advanced_box[advanced_box["points"].notna()].copy()
    if box.empty:
        return []

    findings: list[CheckFinding] = []
    games_idx = games.set_index("game_id", drop=False)
    bad_rows: list[pd.Series] = []
    for _, row in box.iterrows():
        gid = int(row["game_id"])
        if gid not in games_idx.index:
            continue
        game = games_idx.loc[gid]
        if isinstance(game, pd.DataFrame):
            game = game.iloc[0]
        team_id = int(row["team_id"])
        pts = int(row["points"])
        if team_id == int(game["home_team_id"]):
            expected = game["home_points"]
        elif team_id == int(game["away_team_id"]):
            expected = game["away_points"]
        else:
            bad_rows.append(row)
            continue
        if pd.isna(expected) or int(expected) != pts:
            bad_rows.append(row)

    if bad_rows:
        bad_df = pd.DataFrame(bad_rows)
        findings.append(
            CheckFinding(
                expectation="score_consistency_box_vs_final",
                severity="fail",
                message=f"{len(bad_rows)} advanced_box point rows disagree with final score",
                sample_rows=_sample_records(bad_df),
                n_failures=len(bad_rows),
            )
        )
    return findings


def check_pbp_drive_points_reconcile(
    games: pd.DataFrame,
    drives: pd.DataFrame,
    *,
    tolerance: int = DRIVE_POINTS_TOLERANCE,
) -> list[CheckFinding]:
    """Drive-level points per offense must reconcile to final score within tolerance."""
    if games.empty or drives.empty:
        return []
    offense_pts = (
        drives.groupby(["game_id", "offense_id"], as_index=False)["points"]
        .sum()
        .rename(columns={"points": "drive_points"})
    )
    home = games.merge(
        offense_pts,
        left_on=["game_id", "home_team_id"],
        right_on=["game_id", "offense_id"],
        how="left",
    ).rename(columns={"drive_points": "home_drive_points"})
    away = (
        games[["game_id", "away_team_id", "away_points"]]
        .merge(
            offense_pts,
            left_on=["game_id", "away_team_id"],
            right_on=["game_id", "offense_id"],
            how="left",
        )
        .rename(columns={"drive_points": "away_drive_points"})
    )
    merged = home.merge(
        away[["game_id", "away_drive_points"]],
        on="game_id",
        how="left",
    )
    merged["home_drive_points"] = merged["home_drive_points"].fillna(0)
    merged["away_drive_points"] = merged["away_drive_points"].fillna(0)

    completed = merged
    if "completed" in merged.columns:
        completed = merged[merged["completed"].astype(bool)]
    # Only rows with known finals.
    completed = completed[completed["home_points"].notna() & completed["away_points"].notna()]
    if completed.empty:
        return []

    home_bad = (completed["home_drive_points"] - completed["home_points"]).abs() > tolerance
    away_bad = (completed["away_drive_points"] - completed["away_points"]).abs() > tolerance
    bad = completed[home_bad | away_bad]
    if bad.empty:
        return []
    return [
        CheckFinding(
            expectation="pbp_drive_points_reconcile",
            severity="fail",
            message=(
                f"{len(bad)} games where drive points disagree with final by > {tolerance} pts"
            ),
            sample_rows=_sample_records(
                bad[
                    [
                        "game_id",
                        "home_points",
                        "home_drive_points",
                        "away_points",
                        "away_drive_points",
                    ]
                ]
            ),
            n_failures=len(bad),
        )
    ]


def check_play_sequence_monotone(plays: pd.DataFrame) -> list[CheckFinding]:
    """``play_id`` must be unique within a drive; period monotone when ids are usable.

    Staged plays have no separate sequence column; CFBD ``play_id`` is the
    sequence proxy. Negative CFBD play_ids are opaque (see Task 5 normalizer) and
    are **not** chronologically ordered when sorted ascending, so the period
    monotone check applies only when all ``play_id`` values in the drive are
    non-negative.
    """
    if plays.empty or "drive_id" not in plays.columns:
        return []
    frame = plays.dropna(subset=["drive_id"]).copy()
    if frame.empty:
        return []

    bad_parts: list[pd.DataFrame] = []
    for (_, _), group in frame.groupby(["game_id", "drive_id"], sort=False):
        if group["play_id"].duplicated().any():
            bad_parts.append(group)
            continue
        if (group["play_id"] < 0).any():
            continue
        ordered = group.sort_values("play_id", kind="mergesort")
        periods = ordered["period"].tolist()
        if any(periods[i] > periods[i + 1] for i in range(len(periods) - 1)):
            bad_parts.append(ordered)

    if not bad_parts:
        return []
    bad = pd.concat(bad_parts, ignore_index=True)
    return [
        CheckFinding(
            expectation="play_sequence_monotone_within_drive",
            severity="fail",
            message=(
                f"{len(bad_parts)} drives have non-unique play_id or "
                "period regression along play_id order"
            ),
            sample_rows=_sample_records(bad),
            n_failures=len(bad_parts),
        )
    ]


def check_line_open_close_move(
    lines: pd.DataFrame,
    *,
    threshold: float = LINE_MOVE_FLAG_THRESHOLD,
) -> list[CheckFinding]:
    """Flag (not fail) when open->close spread/total move by >= ``threshold``."""
    if lines.empty:
        return []
    needed = {"game_id", "book", "line_type", "spread", "total"}
    if not needed.issubset(lines.columns):
        return []

    open_rows = lines[lines["line_type"] == "open"]
    close_rows = lines[lines["line_type"] == "close"]
    if open_rows.empty or close_rows.empty:
        return []

    merged = open_rows.merge(
        close_rows,
        on=["game_id", "book"],
        suffixes=("_open", "_close"),
        how="inner",
    )
    if merged.empty:
        return []

    spread_move = (merged["spread_close"] - merged["spread_open"]).abs()
    total_move = (merged["total_close"] - merged["total_open"]).abs()
    flagged = merged[(spread_move >= threshold) | (total_move >= threshold)].copy()
    if flagged.empty:
        return []
    flagged["spread_move"] = spread_move.loc[flagged.index]
    flagged["total_move"] = total_move.loc[flagged.index]
    return [
        CheckFinding(
            expectation="line_open_close_move",
            severity="flag",
            message=(
                f"{len(flagged)} book/game open->close moves >= {threshold} points "
                "(flag only; genuine large moves exist)"
            ),
            sample_rows=_sample_records(
                flagged[
                    [
                        "game_id",
                        "book",
                        "spread_open",
                        "spread_close",
                        "total_open",
                        "total_close",
                        "spread_move",
                        "total_move",
                    ]
                ]
            ),
            n_failures=len(flagged),
        )
    ]


def check_snapshot_monotonicity(
    snapshots: pd.DataFrame,
    games: pd.DataFrame | None = None,
) -> list[CheckFinding]:
    """Within (game_key, book, market), event_time must be unique; last pre-kickoff < kickoff.

    From docs/historical_odds_change_set.md Task 7 additions.
    """
    if snapshots.empty:
        return []
    findings: list[CheckFinding] = []
    key_cols = ["game_key", "book", "market", "event_time"]
    if all(c in snapshots.columns for c in key_cols):
        dup = snapshots.duplicated(subset=key_cols, keep=False)
        if dup.any():
            findings.append(
                CheckFinding(
                    expectation="snapshot_event_time_unique",
                    severity="fail",
                    message=f"duplicate snapshot timestamps: {int(dup.sum())} rows",
                    sample_rows=_sample_records(snapshots.loc[dup]),
                    n_failures=int(dup.sum()),
                )
            )

    if games is None or games.empty or "start_date" not in games.columns:
        return findings
    if "game_id" not in snapshots.columns:
        return findings

    kickoff = games.set_index("game_id")["start_date"]
    keyed = snapshots[snapshots["game_id"].notna()].copy()
    if keyed.empty:
        return findings
    keyed["kickoff"] = keyed["game_id"].map(kickoff)
    keyed = keyed[keyed["kickoff"].notna()]
    # Last snapshot at or before kickoff should still be strictly before kickoff
    # for the closing capture used as slot_close.
    late = keyed[keyed["event_time"] >= keyed["kickoff"]]
    if not late.empty:
        findings.append(
            CheckFinding(
                expectation="snapshot_precedes_kickoff",
                severity="flag",
                message=(
                    f"{len(late)} snapshots have event_time >= kickoff "
                    "(flag; may be live post-start captures)"
                ),
                sample_rows=_sample_records(
                    late[["game_key", "game_id", "book", "market", "event_time", "kickoff"]]
                ),
                n_failures=len(late),
            )
        )
    return findings


def check_cfbd_slot_close_reconciliation(
    lines: pd.DataFrame,
    snapshots: pd.DataFrame,
    *,
    tolerance: float = CFBD_SLOT_CLOSE_TOLERANCE,
) -> list[CheckFinding]:
    """Flag games where CFBD close spread diverges from snapshot slot_close beyond tolerance."""
    if lines.empty or snapshots.empty:
        return []
    cfbd_close = lines[lines["line_type"] == "close"].copy()
    if cfbd_close.empty or "spread" not in cfbd_close.columns:
        return []
    # Consensus CFBD close: median spread across books per game.
    cfbd = (
        cfbd_close.groupby("game_id", as_index=False)["spread"]
        .median()
        .rename(columns={"spread": "cfbd_close_spread"})
    )

    slot = snapshots[(snapshots["market"] == "spread") & snapshots["game_id"].notna()].copy()
    if slot.empty:
        return []
    if "decision_point" in slot.columns and (slot["decision_point"] == "slot_close").any():
        slot = slot[slot["decision_point"] == "slot_close"]
    slot = (
        slot.sort_values("event_time")
        .groupby("game_id", as_index=False)
        .tail(1)
        .rename(columns={"line": "slot_close_spread"})
    )

    merged = cfbd.merge(slot[["game_id", "slot_close_spread"]], on="game_id", how="inner")
    merged = merged[merged["cfbd_close_spread"].notna() & merged["slot_close_spread"].notna()]
    if merged.empty:
        return []
    merged["abs_diff"] = (merged["cfbd_close_spread"] - merged["slot_close_spread"]).abs()
    bad = merged[merged["abs_diff"] > tolerance]
    if bad.empty:
        return []
    return [
        CheckFinding(
            expectation="cfbd_slot_close_reconciliation",
            severity="flag",
            message=(
                f"{len(bad)} games with |CFBD close - slot_close| > {tolerance} "
                "(flag; systematic divergence is a data bug)"
            ),
            sample_rows=_sample_records(bad),
            n_failures=len(bad),
        )
    ]


def _sample_records(df: pd.DataFrame, limit: int = _SAMPLE_LIMIT) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out: list[dict[str, Any]] = []
    for row in df.head(limit).to_dict(orient="records"):
        cleaned: dict[str, Any] = {}
        for key, value in row.items():
            if pd.isna(value):
                cleaned[key] = None
            elif hasattr(value, "isoformat"):
                cleaned[key] = value.isoformat()
            else:
                item = getattr(value, "item", None)
                if callable(item):
                    try:
                        cleaned[key] = item()
                    except (AttributeError, ValueError):
                        cleaned[key] = value
                else:
                    cleaned[key] = value
        out.append(cleaned)
    return out
