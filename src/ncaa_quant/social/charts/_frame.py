"""Shared chart chrome: brand mark header, lean footer, fixture watermark."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from PIL import Image

from ncaa_quant.social.theme import (
    BG,
    BORDER,
    BRAND_MARK_PATH,
    PANEL,
    PLATE,
    TEXT,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
    ChartSize,
    apply_matplotlib_theme,
)

_ET = ZoneInfo("America/New_York")

# Brand mark height on the 4:5 canvas; scales with figure height.
_MARK_HEIGHT_4X5_PX = 64
_REF_HEIGHT_4X5 = 2000


class BrandMarkError(RuntimeError):
    """Required ``assets/brand/ridge_mark.png`` is missing or unreadable."""


def published_at_et(iso_utc: str | None) -> str:
    """Format an ISO UTC timestamp as a short ET string."""
    if not iso_utc:
        return "—"
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(_ET)
    return _fmt_et(dt)


def publish_day_short(iso_utc: str | None) -> str:
    """Short weekday in ET (``Tue``, ``Thu``) for subtitle 'as of' clauses."""
    if not iso_utc:
        return "—"
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(_ET)
    return dt.strftime("%a")


def _fmt_et(dt: datetime) -> str:
    # Windows strftime lacks %-I; build manually.
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%a')} {hour}:{dt.strftime('%M')} {ampm} ET"


def refresh_subtitle(refresh_kind: str | None, published_at: str | None) -> str:
    """e.g. ``as of Thu 2:00 AM ET refresh`` (off-image / alt use)."""
    kind = (refresh_kind or "refresh").replace("_", " ")
    return f"as of {published_at_et(published_at)} {kind}"


def require_brand_mark(path: Path | None = None) -> Path:
    """Return the brand-mark path or raise if missing."""
    mark = path or BRAND_MARK_PATH
    if not mark.is_file():
        msg = f"Missing brand mark: {mark}. Place ridge_mark.png under assets/brand/."
        raise BrandMarkError(msg)
    return mark


def new_figure(size: ChartSize) -> Figure:
    """Create a themed figure at the requested pixel size."""
    apply_matplotlib_theme()
    fig = plt.figure(figsize=size.figsize, dpi=100)
    fig.patch.set_facecolor(BG)
    return fig


def add_header(
    fig: Figure,
    *,
    title: str,
    subtitle: str,
    size: ChartSize,
    brand_mark_path: Path | None = None,
) -> None:
    """Top chrome: brand mark + title beside it, one subtitle line (no RIDGE text)."""
    mark_path = require_brand_mark(brand_mark_path)
    mark_img = Image.open(mark_path).convert("RGBA")
    mark_h_px = max(
        40,
        int(round(_MARK_HEIGHT_4X5_PX * float(size.height) / float(_REF_HEIGHT_4X5))),
    )
    scale = mark_h_px / float(mark_img.height)
    mark_w_px = max(1, int(round(mark_img.width * scale)))
    # OffsetImage zoom is relative to the native image at figure dpi (100).
    zoom = mark_h_px / float(mark_img.height)

    # Place mark in figure coordinates (top-left).
    x0, y0 = 0.055, 0.955
    oi = OffsetImage(np.asarray(mark_img), zoom=zoom, resample=True)
    ab = AnnotationBbox(
        oi,
        (x0, y0),
        xycoords=fig.transFigure,
        frameon=False,
        pad=0.0,
        box_alignment=(0.0, 1.0),
        zorder=10,
    )
    fig.add_artist(ab)

    # Title beside the mark, vertically centered on the mark block.
    title_x = x0 + (mark_w_px / float(size.width)) + 0.018
    title_y = y0 - (mark_h_px / float(size.height)) * 0.55
    fig.text(
        title_x,
        title_y,
        title,
        fontsize=20 if size.height >= 1600 else 18,
        fontweight=600,
        color=TEXT,
        ha="left",
        va="center",
        transform=fig.transFigure,
    )
    # One subtitle line under the mark+title row.
    sub_y = y0 - (mark_h_px / float(size.height)) - 0.018
    fig.text(
        x0,
        sub_y,
        subtitle,
        fontsize=12,
        color=TEXT_SECONDARY,
        ha="left",
        va="top",
        transform=fig.transFigure,
    )


def add_footer(fig: Figure, footer: str) -> None:
    """Left-aligned single-row provenance strip (never stacked)."""
    fig.text(
        0.055,
        0.028,
        footer,
        fontsize=10,
        color=TEXT_TERTIARY,
        ha="left",
        va="bottom",
        transform=fig.transFigure,
        clip_on=False,
    )


def add_fixture_watermark(fig: Figure) -> None:
    """Large diagonal watermark when --allow-fixture is used."""
    fig.text(
        0.5,
        0.5,
        "FIXTURE — NOT REAL DATA",
        fontsize=36,
        color="#FF9F0A",
        alpha=0.35,
        ha="center",
        va="center",
        rotation=35,
        transform=fig.transFigure,
        fontweight="bold",
    )


def add_panel_axes(
    fig: Figure,
    *,
    rect: tuple[float, float, float, float] = (0.08, 0.10, 0.84, 0.72),
) -> Any:
    """Create the main plot axes on a navy panel."""
    ax = fig.add_axes(rect)
    ax.set_facecolor(PANEL)
    for spine in ax.spines.values():
        spine.set_color(BORDER)
    return ax


def stamp_footnote(
    fig: Figure,
    lines: list[str],
    *,
    y0: float | None = None,
    y_floor: float = 0.048,
    line_step: float = 0.016,
) -> None:
    """Stack short footnotes above the provenance footer (legacy; prefer text diet)."""
    if not lines:
        return
    n = len(lines)
    top = y_floor + (n - 1) * line_step if y0 is None else y0
    last = top - (n - 1) * line_step
    if last < y_floor:
        top = y_floor + (n - 1) * line_step
    for i, line in enumerate(lines):
        fig.text(
            0.055,
            top - i * line_step,
            line,
            fontsize=9,
            color=TEXT_TERTIARY,
            ha="left",
            va="bottom",
            transform=fig.transFigure,
        )


def draw_round_plate(ax: Any, x: float, y: float, radius: float) -> None:
    """Subtle light circular plate behind a dark logo on navy."""
    from matplotlib.patches import Circle

    circ = Circle(
        (x, y),
        radius=radius,
        facecolor=PLATE,
        edgecolor="none",
        alpha=0.28,
        zorder=2.5,
        transform=ax.transData,
    )
    ax.add_patch(circ)


def now_utc() -> datetime:
    return datetime.now(UTC)
