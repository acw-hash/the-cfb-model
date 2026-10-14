"""Ridge map — offense × defense national percentiles with team logos."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.patches import Rectangle
from scipy.stats import rankdata

from ncaa_quant.social.charts._frame import (
    add_fixture_watermark,
    add_footer,
    add_header,
    add_panel_axes,
    new_figure,
)
from ncaa_quant.social.charts._logos_draw import (
    draw_exact_dot,
    draw_leader,
    draw_logo,
    draw_text_chip,
)
from ncaa_quant.social.logos import LogoAsset, load_logo_asset, override_path
from ncaa_quant.social.placement import (
    PlacedLogo,
    assert_no_text_bbox_overlap,
    max_pairwise_overlap_fraction,
    repel_logos,
)
from ncaa_quant.social.theme import (
    ACCENT,
    BORDER,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
    ChartSize,
    save_png,
)

# Visual logo size matches prior EPA-axis maps (~5.5% of axis span).
_LOGO_AXIS_FRACTION = 0.055
_AXIS_MIN = 0.0
_AXIS_MAX = 100.0
_MID = 50.0


@dataclass
class RidgeMapPoint:
    """One FBS team at the resolved ratings week."""

    team_id: int
    school: str
    abbreviation: str
    off_epa: float
    def_epa: float
    off_sd: float
    def_sd: float
    off_pct: float = 0.0
    def_pct: float = 0.0


@dataclass(frozen=True)
class RidgeWeekLabels:
    """Week chrome derived from export semantics (entering vs through)."""

    mode: str  # "entering" | "through"
    through_line: str
    week_phrase: str
    ratings_through_note: str | None
    caption_week_phrase: str
    alt_week_phrase: str


def ridge_week_labels(*, map_week: int, slate_week: int, season: int) -> RidgeWeekLabels:
    """Label ridge map week from export semantics.

    When ``map_week >= slate_week``, the artifact point is the live current-week
    ``weekly`` open circle (``predict._append_current_week_rating_points``), i.e.
    the posterior **entering** week N after games through N−1. Otherwise the
    preferred ``postgame`` row for week N is the posterior **after** week N games.
    """
    if map_week >= slate_week:
        prior = map_week - 1
        return RidgeWeekLabels(
            mode="entering",
            through_line=f"Entering Week {map_week} · {season}",
            week_phrase=f"entering Week {map_week}",
            ratings_through_note=f"ratings through Week {prior} games",
            caption_week_phrase=f"entering Week {map_week}",
            alt_week_phrase=f"entering week {map_week}",
        )
    return RidgeWeekLabels(
        mode="through",
        through_line=f"Through Week {map_week} · {season}",
        week_phrase=f"through Week {map_week}",
        ratings_through_note=None,
        caption_week_phrase=f"through Week {map_week}",
        alt_week_phrase=f"through week {map_week}",
    )


def rank_percentiles(values: np.ndarray) -> np.ndarray:
    """National percentile: ``100 * (rank − 1) / (n − 1)`` with average ranks for ties.

    Rank 1 = lowest value; higher EPA → higher percentile (better).
    """
    n = int(values.size)
    if n == 0:
        return np.array([], dtype=float)
    if n == 1:
        return np.array([50.0], dtype=float)
    ranks = rankdata(values, method="average")
    return 100.0 * (ranks - 1.0) / (n - 1.0)


@dataclass
class RidgeMapResult:
    """Render outputs + run-report fields for the ridge map."""

    points: list[RidgeMapPoint]
    week: int
    mean_off: float
    mean_def: float
    week_mode: str = "through"
    paths: list[Path] = field(default_factory=list)
    missing_logos: list[dict[str, Any]] = field(default_factory=list)
    logo_fallbacks: list[dict[str, Any]] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    alt_text: str = ""
    caption: str = ""
    overlap_fraction: float = 0.0
    leader_line_count: int = 0


def extract_ridge_points(
    ratings: dict[str, Any],
    *,
    week: int,
    fbs_team_ids: set[int],
    abbrev_by_id: dict[int, str] | None = None,
) -> list[RidgeMapPoint]:
    """Pull one point per FBS team at ``week`` from team_ratings."""
    abbrev_by_id = abbrev_by_id or {}
    points: list[RidgeMapPoint] = []
    for tid_s, entry in (ratings.get("teams") or {}).items():
        tid = int(tid_s)
        if tid not in fbs_team_ids:
            continue
        row = None
        for w in entry.get("weeks") or []:
            if int(w.get("week", -1)) == week:
                row = w
                break
        if row is None:
            continue
        school = str(entry.get("school") or tid_s)
        points.append(
            RidgeMapPoint(
                team_id=tid,
                school=school,
                abbreviation=abbrev_by_id.get(tid) or school[:4].upper(),
                off_epa=float(row["off_epa"]),
                def_epa=float(row["def_epa"]),
                off_sd=float(row.get("off_sd") or 0.0),
                def_sd=float(row.get("def_sd") or 0.0),
            )
        )
    points.sort(key=lambda p: p.team_id)
    return points


def assign_percentiles(points: list[RidgeMapPoint]) -> None:
    """Fill ``off_pct`` / ``def_pct`` in place from national ranks among ``points``."""
    offs = np.array([p.off_epa for p in points], dtype=float)
    defs = np.array([p.def_epa for p in points], dtype=float)
    off_pcts = rank_percentiles(offs)
    def_pcts = rank_percentiles(defs)
    for p, op, dp in zip(points, off_pcts, def_pcts, strict=True):
        p.off_pct = float(op)
        p.def_pct = float(dp)


def render_ridge_map(
    *,
    points: list[RidgeMapPoint],
    week: int,
    season: int,
    sizes: list[ChartSize],
    out_dir: Path,
    footer: str,
    slate_week: int | None = None,
    logo_dir: Path | None = None,
    allow_missing_logos: bool = False,
    fixture_watermark: bool = False,
    logo_zoom: float = 0.045,
) -> RidgeMapResult:
    """Render PEM-style ridge map PNGs (national percentile axes)."""
    if not points:
        msg = "Ridge map: no FBS points at resolved week"
        raise RuntimeError(msg)

    assign_percentiles(points)

    offs = np.array([p.off_epa for p in points], dtype=float)
    defs = np.array([p.def_epa for p in points], dtype=float)
    mean_off = float(offs.mean())
    mean_def = float(defs.mean())
    n = len(points)

    labels = ridge_week_labels(
        map_week=week,
        slate_week=week if slate_week is None else slate_week,
        season=season,
    )

    subtitle = (
        f"All {n} FBS teams · Ridge offense vs defense · "
        f"national percentiles · {labels.week_phrase}"
    )
    closeness_note = (
        "Percentiles of Ridge rating estimates. Teams within ~10 "
        "percentile points of each other are often statistically close."
    )
    footnotes = [closeness_note]
    if labels.ratings_through_note:
        footnotes.insert(0, labels.ratings_through_note)
    notes: dict[str, Any] = {
        "statistical_closeness": closeness_note,
        "ratings_through_note": labels.ratings_through_note,
        "week_mode": labels.mode,
        "week_phrase": labels.week_phrase,
    }

    # Resolve logos.
    assets: dict[int, LogoAsset] = {}
    missing: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []
    for p in points:
        asset = load_logo_asset(p.team_id, logo_dir=logo_dir)
        if asset is None:
            missing.append(
                {
                    "team_id": p.team_id,
                    "school": p.school,
                    "override_path": str(
                        override_path(logo_dir or Path("data/social/logos"), p.team_id)
                    ),
                }
            )
            if allow_missing_logos:
                fallbacks.append(
                    {"team_id": p.team_id, "school": p.school, "chip": p.abbreviation}
                )
        else:
            assets[p.team_id] = asset
    if missing and not allow_missing_logos:
        lines = ", ".join(str(m["team_id"]) for m in missing)
        msg = f"Missing logos for team_ids: {lines}"
        raise RuntimeError(msg)

    axis_span = _AXIS_MAX - _AXIS_MIN
    logo_w = axis_span * _LOGO_AXIS_FRACTION
    logo_h = axis_span * _LOGO_AXIS_FRACTION

    placed = repel_logos(
        team_ids=[p.team_id for p in points],
        xs=[p.off_pct for p in points],
        ys=[p.def_pct for p in points],
        widths=[logo_w] * n,
        heights=[logo_h] * n,
        x_min=_AXIS_MIN,
        x_max=_AXIS_MAX,
        y_min=_AXIS_MIN,
        y_max=_AXIS_MAX,
        leader_threshold_widths=0.5,
        overlap_allow_fraction=0.15,
    )
    by_id = {pl.team_id: pl for pl in placed}
    overlap = max_pairwise_overlap_fraction(placed)
    leader_line_count = sum(1 for pl in placed if pl.draw_leader)

    top5 = sorted(
        points,
        key=lambda p: (p.off_pct + p.def_pct) / 2.0,
        reverse=True,
    )[:5]

    alt = (
        f"Ridge map of {n} FBS teams {labels.alt_week_phrase} {season}. "
        f"X is offense national percentile, Y is defense national percentile. "
        f"{closeness_note} "
        + (f"{labels.ratings_through_note}. " if labels.ratings_through_note else "")
        + "Top completeness (mean percentile): "
        + "; ".join(
            f"{p.school} (off {p.off_pct:.0f}, def {p.def_pct:.0f})" for p in top5
        )
        + "."
    )
    caption = (
        f"Who is actually complete {labels.caption_week_phrase}? "
        + ", ".join(p.school for p in top5)
        + " lead by mean national percentile (offense + defense) / 2."
    )

    paths: list[Path] = []
    for size in sizes:
        fig = new_figure(size)
        add_header(
            fig,
            title="Who is actually complete?",
            subtitle=subtitle,
            size=size,
        )
        ax = add_panel_axes(fig)
        ax.set_xlim(_AXIS_MIN, _AXIS_MAX)
        ax.set_ylim(_AXIS_MIN, _AXIS_MAX)

        # Lightly shade the upper-right (both above average) quadrant.
        ax.add_patch(
            Rectangle(
                (_MID, _MID),
                _AXIS_MAX - _MID,
                _AXIS_MAX - _MID,
                facecolor="#FFFFFF",
                edgecolor="none",
                alpha=0.06,
                zorder=0,
            )
        )
        ax.axhline(_MID, color=BORDER, linewidth=0.8, zorder=1)
        ax.axvline(_MID, color=BORDER, linewidth=0.8, zorder=1)

        # Keep corner labels inside their quadrants (scale down on small test canvases).
        corner_fs = 12 if size.width >= 1200 else 7
        pad = 3.0 if size.width >= 1200 else 1.5
        ax.text(
            _AXIS_MAX - pad,
            _AXIS_MAX - pad,
            "Both above average",
            color=ACCENT,
            ha="right",
            va="top",
            fontsize=corner_fs,
            clip_on=True,
        )
        ax.text(
            _AXIS_MIN + pad,
            _AXIS_MAX - pad,
            "Defense above average",
            color=TEXT_TERTIARY,
            ha="left",
            va="top",
            fontsize=max(corner_fs - 1, 6),
            clip_on=True,
        )
        ax.text(
            _AXIS_MAX - pad,
            _AXIS_MIN + pad,
            "Offense above average",
            color=TEXT_TERTIARY,
            ha="right",
            va="bottom",
            fontsize=max(corner_fs - 1, 6),
            clip_on=True,
        )
        ax.text(
            _AXIS_MIN + pad,
            _AXIS_MIN + pad,
            "Both below average",
            color=TEXT_TERTIARY,
            ha="left",
            va="bottom",
            fontsize=max(corner_fs - 1, 6),
            clip_on=True,
        )

        for p in points:
            draw_exact_dot(ax, p.off_pct, p.def_pct)
            pl: PlacedLogo = by_id[p.team_id]
            if pl.draw_leader:
                draw_leader(ax, pl.true_x, pl.true_y, pl.logo_x, pl.logo_y)
            asset = assets.get(p.team_id)
            if asset is not None:
                draw_logo(ax, asset, pl.logo_x, pl.logo_y, zoom=logo_zoom)
            else:
                draw_text_chip(ax, p.abbreviation, pl.logo_x, pl.logo_y)

        ax.set_xlabel("Offense percentile →", color=TEXT_SECONDARY)
        ax.set_ylabel("Defense percentile →", color=TEXT_SECONDARY)
        add_footer(fig, footer)
        if fixture_watermark:
            add_fixture_watermark(fig)
        assert_no_text_bbox_overlap(fig)
        out_path = out_dir / f"ridge_map_w{week}_{size.name}.png"
        save_png(fig, out_path)
        paths.append(out_path)
        from matplotlib import pyplot as plt

        plt.close(fig)

    return RidgeMapResult(
        points=points,
        week=week,
        mean_off=mean_off,
        mean_def=mean_def,
        week_mode=labels.mode,
        paths=paths,
        missing_logos=missing,
        logo_fallbacks=fallbacks,
        footnotes=footnotes,
        notes=notes,
        alt_text=alt,
        caption=caption,
        overlap_fraction=overlap,
        leader_line_count=leader_line_count,
    )
