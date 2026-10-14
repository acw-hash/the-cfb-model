"""Ridge Top 25 — net rating leaderboard (SOC-1f)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.patches import FancyBboxPatch
from scipy.stats import rankdata

from ncaa_quant.social.charts._frame import (
    add_fixture_watermark,
    add_footer,
    add_header,
    new_figure,
)
from ncaa_quant.social.charts._logos_draw import draw_logo, draw_text_chip
from ncaa_quant.social.charts.ridge_map import ridge_week_labels
from ncaa_quant.social.logos import LogoAsset, load_logo_asset, override_path
from ncaa_quant.social.theme import (
    ACCENT,
    BG,
    PANEL,
    TEXT,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
    ChartSize,
    save_png,
)

BAR_COLOR = ACCENT  # #FD4D11
_ = BG  # brand navy canvas via theme


@dataclass
class NetRatedTeam:
    """One FBS team net rating at a ratings week."""

    team_id: int
    school: str
    abbreviation: str
    off_epa: float
    def_epa: float
    net: float
    off_rank: int = 0  # 1 = best (highest EPA)
    def_rank: int = 0
    net_rank: int = 0


@dataclass
class Top25Row:
    """One leaderboard row."""

    rank: int
    team_id: int
    school: str
    abbreviation: str
    net: float
    off_epa: float
    def_epa: float
    off_rank: int
    def_rank: int
    prev_rank: int | None
    rank_delta: int | None  # prev − curr; positive = rose; None = NEW


@dataclass
class Top25Result:
    """Render outputs + report fields."""

    rows: list[Top25Row]
    week: int
    week_mode: str
    paths: list[Path] = field(default_factory=list)
    missing_logos: list[dict[str, Any]] = field(default_factory=list)
    logo_fallbacks: list[dict[str, Any]] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    alt_text: str = ""
    caption: str = ""
    biggest_riser: str | None = None
    biggest_faller: str | None = None


def format_rank_change(delta: int | None) -> str:
    """▲3 / ▼2 / — / NEW."""
    if delta is None:
        return "NEW"
    if delta == 0:
        return "—"
    if delta > 0:
        return f"▲{delta}"
    return f"▼{abs(delta)}"


def _extract_week_teams(
    ratings: dict[str, Any],
    *,
    week: int,
    fbs_team_ids: set[int],
    abbrev_by_id: dict[int, str],
) -> list[NetRatedTeam]:
    out: list[NetRatedTeam] = []
    for tid_s, entry in (ratings.get("teams") or {}).items():
        tid = int(tid_s)
        if tid not in fbs_team_ids:
            continue
        row = next(
            (w for w in entry.get("weeks") or [] if int(w.get("week", -1)) == week),
            None,
        )
        if row is None:
            continue
        school = str(entry.get("school") or tid_s)
        off = float(row["off_epa"])
        deff = float(row["def_epa"])
        out.append(
            NetRatedTeam(
                team_id=tid,
                school=school,
                abbreviation=abbrev_by_id.get(tid) or school[:4].upper(),
                off_epa=off,
                def_epa=deff,
                net=off + deff,
            )
        )
    if not out:
        return out
    offs = np.array([t.off_epa for t in out], dtype=float)
    defs = np.array([t.def_epa for t in out], dtype=float)
    nets = np.array([t.net for t in out], dtype=float)
    # Higher EPA = better → rank 1.
    off_ranks = rankdata(-offs, method="average")
    def_ranks = rankdata(-defs, method="average")
    net_ranks = rankdata(-nets, method="average")
    for t, o, d, n in zip(out, off_ranks, def_ranks, net_ranks, strict=True):
        t.off_rank = int(round(float(o)))
        t.def_rank = int(round(float(d)))
        t.net_rank = int(round(float(n)))
    out.sort(key=lambda t: (t.net_rank, t.team_id))
    return out


def extract_top25(
    ratings: dict[str, Any],
    *,
    week: int,
    fbs_team_ids: set[int],
    abbrev_by_id: dict[int, str] | None = None,
    prev_week: int | None = None,
) -> list[Top25Row]:
    """Build Top 25 rows with rank change vs the previous ratings week."""
    abbrev_by_id = abbrev_by_id or {}
    current = _extract_week_teams(
        ratings, week=week, fbs_team_ids=fbs_team_ids, abbrev_by_id=abbrev_by_id
    )
    prev_w = week - 1 if prev_week is None else prev_week
    prev_by_id: dict[int, NetRatedTeam] = {}
    if prev_w >= 1:
        for t in _extract_week_teams(
            ratings, week=prev_w, fbs_team_ids=fbs_team_ids, abbrev_by_id=abbrev_by_id
        ):
            prev_by_id[t.team_id] = t

    rows: list[Top25Row] = []
    for t in current[:25]:
        prev = prev_by_id.get(t.team_id)
        if prev is None:
            prev_rank = None
            delta = None
        else:
            prev_rank = prev.net_rank
            delta = prev.net_rank - t.net_rank
        rows.append(
            Top25Row(
                rank=t.net_rank,
                team_id=t.team_id,
                school=t.school,
                abbreviation=t.abbreviation,
                net=t.net,
                off_epa=t.off_epa,
                def_epa=t.def_epa,
                off_rank=t.off_rank,
                def_rank=t.def_rank,
                prev_rank=prev_rank,
                rank_delta=delta,
            )
        )
    return rows


def _column_layout(size: ChartSize) -> list[tuple[list[int], float, float]]:
    """Return list of (row_indices, x0, x1) columns in figure coords."""
    if size.name == "16x9":
        return [
            (list(range(0, 13)), 0.03, 0.495),
            (list(range(13, 25)), 0.505, 0.97),
        ]
    return [(list(range(0, 25)), 0.04, 0.96)]


def render_top25(
    *,
    rows: list[Top25Row],
    week: int,
    season: int,
    sizes: list[ChartSize],
    out_dir: Path,
    footer: str,
    slate_week: int | None = None,
    logo_dir: Path | None = None,
    allow_missing_logos: bool = False,
    fixture_watermark: bool = False,
    logo_zoom: float = 0.028,
) -> Top25Result:
    """Render Top 25 PNGs (4:5 single column; 16:9 two columns)."""
    if len(rows) < 1:
        msg = "Top 25: no rows to plot"
        raise RuntimeError(msg)

    labels = ridge_week_labels(
        map_week=week,
        slate_week=week if slate_week is None else slate_week,
        season=season,
    )
    if labels.mode == "entering":
        subtitle = f"Entering Week {week} · net = offense + defense efficiency"
    else:
        subtitle = f"Through Week {week} · net = offense + defense efficiency"

    assets: dict[int, LogoAsset] = {}
    missing: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []
    for r in rows:
        asset = load_logo_asset(r.team_id, logo_dir=logo_dir)
        if asset is None:
            missing.append(
                {
                    "team_id": r.team_id,
                    "school": r.school,
                    "override_path": str(
                        override_path(logo_dir or Path("data/social/logos"), r.team_id)
                    ),
                }
            )
            if allow_missing_logos:
                fallbacks.append(
                    {"team_id": r.team_id, "school": r.school, "chip": r.abbreviation}
                )
        else:
            assets[r.team_id] = asset
    if missing and not allow_missing_logos:
        lines = ", ".join(str(m["team_id"]) for m in missing)
        msg = f"Missing logos for team_ids: {lines}"
        raise RuntimeError(msg)

    with_delta = [r for r in rows if r.rank_delta is not None]
    riser = max(with_delta, key=lambda r: r.rank_delta or 0) if with_delta else None
    faller = min(with_delta, key=lambda r: r.rank_delta or 0) if with_delta else None
    if riser is not None and (riser.rank_delta or 0) <= 0:
        riser = None
    if faller is not None and (faller.rank_delta or 0) >= 0:
        faller = None

    caption_parts: list[str] = [f"Ridge Top 25 {labels.caption_week_phrase}."]
    if riser is not None:
        caption_parts.append(
            f"Biggest riser: {riser.school} {format_rank_change(riser.rank_delta)}."
        )
    if faller is not None:
        caption_parts.append(
            f"Biggest faller: {faller.school} {format_rank_change(faller.rank_delta)}."
        )
    caption = " ".join(caption_parts)

    alt = (
        f"Ridge Top 25 by net rating (offense + defense EPA per play) "
        f"{labels.alt_week_phrase} {season}. Ratings are estimates. "
        + "; ".join(
            f"#{r.rank} {r.school} ({r.net:+.3f}, OFF #{r.off_rank} · DEF #{r.def_rank})"
            for r in rows[:5]
        )
        + "."
    )

    max_net = max(r.net for r in rows)
    min_net = min(0.0, min(r.net for r in rows))
    bar_span = max(max_net - min_net, 1e-6)

    paths: list[Path] = []
    for size in sizes:
        fig = new_figure(size)
        add_header(
            fig,
            title="Ridge Top 25",
            subtitle=subtitle,
            size=size,
        )

        ax = fig.add_axes([0, 0, 1, 1], frameon=False, zorder=2)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_axis_off()
        ax.patch.set_alpha(0.0)

        y_top = 0.875
        y_bot = 0.055
        columns = _column_layout(size)

        # Column bar-axis labels
        for indices, x0, x1 in columns:
            width = x1 - x0
            ax.text(
                x0 + width * 0.42,
                y_top - 0.008,
                "Net rating, EPA/play",
                fontsize=9 if size.name == "16x9" else 10,
                color=TEXT_SECONDARY,
                ha="left",
                va="top",
                transform=ax.transData,
            )

        for indices, x0, x1 in columns:
            n_rows = len(indices)
            row_h = (y_top - y_bot - 0.018) / max(n_rows, 1)
            width = x1 - x0
            bar_x0 = x0 + width * 0.42
            bar_x1 = x0 + width * 0.76
            bar_w = bar_x1 - bar_x0
            name_x = x0 + width * 0.20
            chip_x = x0 + width * 0.78
            z = logo_zoom if size.name == "4x5" else logo_zoom * 0.85

            for i, idx in enumerate(indices):
                if idx >= len(rows):
                    continue
                r = rows[idx]
                y = y_top - 0.018 - (i + 1) * row_h
                cy = y + row_h * 0.55

                if i % 2 == 0:
                    ax.add_patch(
                        FancyBboxPatch(
                            (x0, y + row_h * 0.05),
                            width,
                            row_h * 0.90,
                            boxstyle="round,pad=0.001,rounding_size=0.004",
                            facecolor=PANEL,
                            edgecolor="none",
                            alpha=0.55,
                            transform=ax.transData,
                            zorder=0,
                        )
                    )

                ax.text(
                    x0 + width * 0.02,
                    cy,
                    str(r.rank),
                    fontsize=13 if size.name == "4x5" else 11,
                    fontweight=600,
                    color=TEXT,
                    ha="left",
                    va="center",
                    transform=ax.transData,
                    zorder=3,
                )
                chg = format_rank_change(r.rank_delta)
                if (r.rank_delta or 0) > 0:
                    chg_color = ACCENT
                elif (r.rank_delta or 0) < 0:
                    chg_color = TEXT_SECONDARY
                else:
                    chg_color = TEXT_TERTIARY
                ax.text(
                    x0 + width * 0.075,
                    cy,
                    chg,
                    fontsize=9 if size.name == "4x5" else 8,
                    color=chg_color,
                    ha="left",
                    va="center",
                    transform=ax.transData,
                    zorder=3,
                )

                logo_x = x0 + width * 0.155
                asset = assets.get(r.team_id)
                if asset is not None:
                    draw_logo(ax, asset, logo_x, cy, zoom=z, plate_radius=0.010)
                else:
                    draw_text_chip(ax, r.abbreviation, logo_x, cy, fontsize=7)

                # Truncate long school names for narrow 16:9 columns.
                name = r.school
                max_chars = 18 if size.name == "16x9" else 22
                if len(name) > max_chars:
                    name = name[: max_chars - 1] + "…"
                ax.text(
                    name_x,
                    cy,
                    name,
                    fontsize=11 if size.name == "4x5" else 9,
                    color=TEXT,
                    ha="left",
                    va="center",
                    transform=ax.transData,
                    zorder=3,
                )

                frac = (r.net - min_net) / bar_span
                bw = max(bar_w * frac, 0.004)
                bh = row_h * 0.40
                ax.add_patch(
                    FancyBboxPatch(
                        (bar_x0, cy - bh / 2),
                        bw,
                        bh,
                        boxstyle="round,pad=0.0005,rounding_size=0.002",
                        facecolor=BAR_COLOR,
                        edgecolor="none",
                        transform=ax.transData,
                        zorder=2,
                    )
                )
                ax.text(
                    min(bar_x0 + bw + 0.005, bar_x1 + 0.01),
                    cy,
                    f"{r.net:+.3f}",
                    fontsize=8 if size.name == "4x5" else 7,
                    color=TEXT_SECONDARY,
                    ha="left",
                    va="center",
                    transform=ax.transData,
                    zorder=3,
                )

                ax.text(
                    chip_x,
                    cy,
                    f"OFF #{r.off_rank} · DEF #{r.def_rank}",
                    fontsize=8 if size.name == "4x5" else 7,
                    color=TEXT_TERTIARY,
                    ha="left",
                    va="center",
                    transform=ax.transData,
                    zorder=3,
                )

        add_footer(fig, footer)
        if fixture_watermark:
            add_fixture_watermark(fig)
        out_path = out_dir / f"top25_w{week}_{size.name}.png"
        save_png(fig, out_path)
        paths.append(out_path)
        from matplotlib import pyplot as plt

        plt.close(fig)

    notes = {
        "week_mode": labels.mode,
        "week_phrase": labels.week_phrase,
        "bar_label": "Net rating, EPA/play",
        "ratings_are_estimates": "mentioned in alt text only",
    }
    return Top25Result(
        rows=rows,
        week=week,
        week_mode=labels.mode,
        paths=paths,
        missing_logos=missing,
        logo_fallbacks=fallbacks,
        footnotes=[],
        notes=notes,
        alt_text=alt,
        caption=caption,
        biggest_riser=riser.school if riser else None,
        biggest_faller=faller.school if faller else None,
    )
