"""Home-perspective market line orientation (ATS-GRADE-FIX).

Odds API spread ``line`` values are **side-relative** (one row per team name as
``side``, with ``outcome.point`` attached to that name — see 5b-patch2). CFBD
margins and ATS grading are **home-perspective**. Resolving a home spread
therefore means filtering to ``side == CFBD home school`` by name match, then
aggregating across books — never across sides.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

__all__ = [
    "filter_home_side_spreads",
    "median_home_spread",
    "median_total_line",
]


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
