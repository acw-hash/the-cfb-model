"""Home-perspective market line orientation + per-game feature as-of (MKT-ASOF-FIX).

Odds API spread ``line`` values are **side-relative** (one row per team name as
``side``, with ``outcome.point`` attached to that name — see 5b-patch2). CFBD
margins and ATS grading are **home-perspective**. Resolving a home spread
therefore means filtering to ``side == CFBD home school`` by name match, then
aggregating across books — never across sides.

Feature as-of (DESIGN §7.2 item 8 / WEEK-ALIGN-FIX)
----------------------------------------------------
Walk-forward passes a per-week decision timestamp (default Tuesday 06:00 ET)
derived from that CFBD week's actual kickoffs. When that instant is strictly
before kickoff it is the feature as-of. When it falls at or after kickoff
(Week-0 / midweek exceptions), fall back to the latest configured
decision-point instant strictly before kickoff (typically ``slot_close``).
The ladder then enforces ``event_time <= feature_as_of`` **and**
``event_time < kickoff``. Close is never a feature (MKT-2019-FIX /
DESIGN §7.2 item 8 Tuesday-knowability).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Final

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.utils.timeutils import (
    DECISION_POINT_TZ,
    assert_tz_aware,
    resolve_decision_point,
    to_utc,
)

__all__ = [
    "DEFAULT_FEATURE_DECISION_POINTS",
    "SLOT_CLOSE_LEAD",
    "candidate_decision_instants",
    "feature_as_of_for_game",
    "filter_home_side_spreads",
    "median_home_spread",
    "median_total_line",
    "provenance_from_line_source",
    "slot_close_instant",
]

#: Default Odds historical decision points (``configs/data.yaml``).
DEFAULT_FEATURE_DECISION_POINTS: Final[tuple[str, ...]] = (
    "tuesday_0600_et",
    "saturday_0600_et",
    "slot_close",
)

#: Slot-close request is kickoff minus this lead (Task 5B / odds ingester).
SLOT_CLOSE_LEAD: Final[timedelta] = timedelta(minutes=5)

#: Provenance labels stamped from the resolving ``line_source`` (never inferred
#: from non-nullness or from ``market_feature_source`` config).
_NULL_LINE_SOURCES: Final[frozenset[str]] = frozenset({"", "null", "nan", "none"})


def provenance_from_line_source(line_source: str | None) -> str:
    """Stamp ``market_provenance`` from the resolving source only.

    Units: dimensionless label. Time semantics: none (label of the row that
    already passed the as-of bound).

    - ``null`` / missing ``line_source`` → ``null``
    - ``cfbd_*`` → ``cfbd`` (a CFBD-sourced row can never read ``snapshots``)
    - Odds snapshot sources → ``snapshots``
    - anything else → ``null`` (do not guess ``snapshots`` from non-nullness)
    """
    if line_source is None:
        return "null"
    src = str(line_source).strip()
    if src.casefold() in _NULL_LINE_SOURCES:
        return "null"
    lower = src.casefold()
    if lower.startswith("cfbd"):
        return "cfbd"
    if "snapshot" in lower or lower.startswith("odds_api"):
        return "snapshots"
    return "null"


def slot_close_instant(kickoff: datetime) -> datetime:
    """Configured ``slot_close`` instant for ``kickoff`` (kick − 5 minutes, UTC)."""
    return to_utc(kickoff) - SLOT_CLOSE_LEAD


def _saturday_from_week_as_of(week_as_of: datetime) -> datetime:
    """Saturday 06:00 ET in the same America/New_York week as ``week_as_of``."""
    local = to_utc(week_as_of).astimezone(DECISION_POINT_TZ)
    monday = local.date() - timedelta(days=local.weekday())
    return resolve_decision_point("saturday_0600_et", monday + timedelta(days=5))


def candidate_decision_instants(
    kickoff: datetime,
    *,
    week_as_of: datetime,
    saturday_0600_et: datetime | None = None,
    decision_points: Sequence[str] = DEFAULT_FEATURE_DECISION_POINTS,
) -> list[tuple[str, datetime]]:
    """Configured decision-point instants for one game (name, UTC instant).

    Wall-clock points are derived from the harness week decision (corrected
    CFBD-week as_of), not Labor-Day arithmetic. ``slot_close`` is kickoff
    minus :data:`SLOT_CLOSE_LEAD`.
    """
    assert_tz_aware(kickoff)
    assert_tz_aware(week_as_of)
    kick = to_utc(kickoff)
    tue = to_utc(week_as_of)
    if saturday_0600_et is not None:
        sat = to_utc(saturday_0600_et)
    else:
        sat = _saturday_from_week_as_of(tue)
    out: list[tuple[str, datetime]] = []
    for name in decision_points:
        if name == "slot_close":
            out.append((name, slot_close_instant(kick)))
        elif name == "tuesday_0600_et":
            out.append((name, tue))
        elif name == "saturday_0600_et":
            out.append((name, sat))
        else:
            msg = f"Unsupported decision point for feature as-of: {name!r}"
            raise ValueError(msg)
    return out


def feature_as_of_for_game(
    kickoff: datetime,
    week_as_of: datetime,
    *,
    saturday_0600_et: datetime | None = None,
    decision_points: Sequence[str] = DEFAULT_FEATURE_DECISION_POINTS,
    # Deprecated kwargs retained for call-site compatibility; ignored.
    season: int | None = None,
    week: int | None = None,
) -> datetime | None:
    """Per-game feature as-of under the MKT-ASOF-FIX / WEEK-ALIGN-FIX rule.

    When the harness week decision is strictly before kickoff, it is the
    feature as-of (Tuesday primary path). When the week decision falls at or
    after kickoff, fall back to the latest configured decision-point instant
    strictly before kickoff (typically ``slot_close``). Returns ``None`` when
    no configured point qualifies — caller must emit null + ``is_missing``,
    never a later snapshot.
    """
    del season, week  # Labor-Day week math removed; calendar supplies week_as_of.
    assert_tz_aware(kickoff)
    assert_tz_aware(week_as_of)
    kick = to_utc(kickoff)
    week_ao = to_utc(week_as_of)
    if week_ao < kick:
        return week_ao
    qualified = [
        instant
        for _name, instant in candidate_decision_instants(
            kick,
            week_as_of=week_ao,
            saturday_0600_et=saturday_0600_et,
            decision_points=decision_points,
        )
        if instant < kick
    ]
    if not qualified:
        return None
    return max(qualified)


def filter_home_side_spreads(
    spread_rows: pd.DataFrame,
    home_side: str,
) -> pd.DataFrame:
    """Keep spread rows whose ``side`` matches ``home_side`` (name-based, casefold).

    Does **not** use Odds listing ``home_team`` — neutrals may swap listings
    (5b-patch2); CFBD designated home is the orientation anchor.
    """
    if spread_rows.empty:
        return spread_rows.iloc[0:0].copy()
    if "side" not in spread_rows.columns:
        # Legacy single-sided frames without a side column — caller must not
        # pass paired ±S rows in this shape.
        return spread_rows.copy()
    ht = str(home_side).casefold()
    return spread_rows.loc[spread_rows["side"].astype(str).str.casefold() == ht].copy()


def median_home_spread(
    spread_rows: pd.DataFrame,
    home_side: str,
) -> tuple[float, dict[str, Any]]:
    """Median home-side spread across books; metadata for provenance.

    Returns
    -------
    spread :
        Median of home-side lines, or NaN if none match.
    meta :
        ``side``, ``book`` (book of a median-matching row, else ``consensus``),
        ``source_row_id`` (``snapshot_id`` when present), ``n_books``.
    """
    meta: dict[str, Any] = {
        "side": str(home_side),
        "book": None,
        "source_row_id": None,
        "n_books": 0,
    }
    home = filter_home_side_spreads(spread_rows, home_side)
    if home.empty or "line" not in home.columns:
        return float("nan"), meta
    lines = home["line"].dropna()
    if lines.empty:
        return float("nan"), meta
    if "book" in home.columns:
        meta["n_books"] = int(home.loc[lines.index, "book"].nunique())
    else:
        meta["n_books"] = int(len(lines))
    spread = float(lines.median())
    # Provenance: a row whose line equals the median (ties → first).
    match = home.loc[lines.index].loc[np.isclose(lines.to_numpy(dtype=float), spread)]
    if match.empty:
        match = home.loc[lines.index].iloc[[0]]
    row0 = match.iloc[0]
    if "book" in match.columns and pd.notna(row0.get("book")):
        meta["book"] = str(row0["book"])
    else:
        meta["book"] = "consensus"
    if "snapshot_id" in match.columns and pd.notna(row0.get("snapshot_id")):
        meta["source_row_id"] = str(row0["snapshot_id"])
    return spread, meta


def median_total_line(total_rows: pd.DataFrame) -> float:
    """Median total line; over/under share the number so side filter is a no-op."""
    if total_rows.empty or "line" not in total_rows.columns:
        return float("nan")
    lines = total_rows["line"].dropna()
    if lines.empty:
        return float("nan")
    return float(lines.median())
