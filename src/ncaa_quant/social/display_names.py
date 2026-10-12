"""Owner-editable team display names for SOC-2 headlines and labels.

CSV path: ``social/display_names.csv`` (columns: team_id, display_name,
display_short). Missing rows fall back to CFBD school / abbreviation.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from ncaa_quant.social.specs.format_text import HEADLINE_MAX, clip_chars

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CSV = REPO_ROOT / "social" / "display_names.csv"


@dataclass(frozen=True)
class TeamDisplay:
    """Resolved names for one team."""

    team_id: int
    school: str
    abbreviation: str
    display_name: str
    display_short: str

    def candidates(self) -> tuple[str, str, str]:
        """Headline cascade: full → short → abbreviation."""
        full = self.display_name or self.school
        short = self.display_short or full
        abbr = self.abbreviation or short
        return full, short, abbr


@lru_cache(maxsize=4)
def load_display_overrides(csv_path: str | None = None) -> dict[int, tuple[str, str]]:
    """Load ``team_id → (display_name, display_short)`` from CSV."""
    path = Path(csv_path) if csv_path else DEFAULT_CSV
    if not path.is_file():
        return {}
    out: dict[int, tuple[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid_raw = (row.get("team_id") or "").strip()
            if not tid_raw:
                continue
            try:
                tid = int(tid_raw)
            except ValueError:
                continue
            dname = (row.get("display_name") or "").strip()
            dshort = (row.get("display_short") or "").strip() or dname
            if dname:
                out[tid] = (dname, dshort)
    return out


def resolve_team_display(
    team_id: int,
    school: str,
    abbrev_by_id: dict[int, str],
    *,
    overrides: dict[int, tuple[str, str]] | None = None,
) -> TeamDisplay:
    """Resolve display names for ``team_id``."""
    ov = overrides if overrides is not None else load_display_overrides()
    abbr = str(abbrev_by_id.get(team_id) or "")
    school_s = school or f"Team {team_id}"
    if team_id in ov:
        dname, dshort = ov[team_id]
    else:
        dname, dshort = school_s, (abbr or school_s.split()[-1][:8])
    return TeamDisplay(
        team_id=team_id,
        school=school_s,
        abbreviation=abbr or dshort,
        display_name=dname,
        display_short=dshort,
    )


def fit_headline(
    build: Callable[[str, str], str],
    team: TeamDisplay,
    opp: TeamDisplay,
    *,
    limit: int = HEADLINE_MAX,
) -> str:
    """Pick the longest name pair that keeps ``build(t, opp)`` ≤ ``limit``.

    Cascade (team, then opp independently): full → short → abbreviation.
    """
    t_cands = team.candidates()
    o_cands = opp.candidates()
    # Prefer fullest names; try all pairs by total length descending.
    pairs = [(t, o) for t in t_cands for o in o_cands]
    pairs.sort(key=lambda p: -(len(p[0]) + len(p[1])))
    seen: set[tuple[str, str]] = set()
    for t, o in pairs:
        key = (t, o)
        if key in seen:
            continue
        seen.add(key)
        text = build(t, o)
        if len(text) <= limit:
            return text
    return clip_chars(build(t_cands[-1], o_cands[-1]), limit)


def fit_single_name_headline(
    build: Callable[[str], str],
    team: TeamDisplay,
    *,
    limit: int = HEADLINE_MAX,
) -> str:
    """Cascade full → short → abbr for a one-team headline template."""
    for name in team.candidates():
        text = build(name)
        if len(text) <= limit:
            return text
    return clip_chars(build(team.candidates()[-1]), limit)


def label_name(
    team_id: int,
    school: str,
    abbrev_by_id: dict[int, str],
    *,
    prefer: str = "short",
    overrides: dict[int, tuple[str, str]] | None = None,
) -> str:
    """On-image row/chip label: ``short`` (default) or ``full`` / ``abbr``."""
    d = resolve_team_display(
        team_id, school, abbrev_by_id, overrides=overrides
    )
    if prefer == "full":
        return d.display_name
    if prefer == "abbr":
        return d.abbreviation or d.display_short
    return d.display_short
