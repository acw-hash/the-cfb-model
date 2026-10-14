"""Matplotlib logo annotation helpers."""

from __future__ import annotations

from typing import Any

import numpy as np
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from PIL import Image

from ncaa_quant.social.charts._frame import draw_round_plate
from ncaa_quant.social.logos import LogoAsset
from ncaa_quant.social.theme import BORDER, DOT, LEADER, PANEL, TEXT


def pil_to_offset_image(img: Image.Image, *, zoom: float) -> OffsetImage:
    """Convert a PIL RGBA image to an OffsetImage."""
    arr = np.asarray(img)
    return OffsetImage(arr, zoom=zoom, resample=True)


def draw_logo(
    ax: Any,
    asset: LogoAsset,
    x: float,
    y: float,
    *,
    zoom: float,
    plate_radius: float | None = None,
    zorder: float = 4.0,
    alpha: float = 1.0,
) -> None:
    """Draw a logo centered at (x, y), with optional light plate."""
    if asset.needs_plate:
        r = plate_radius if plate_radius is not None else max(abs(ax.get_xlim()[1] - ax.get_xlim()[0]) * 0.012, 1e-3)
        draw_round_plate(ax, x, y, r)
    img = asset.image
    if alpha < 1.0:
        arr = np.asarray(img).copy()
        arr[..., 3] = (arr[..., 3].astype(np.float64) * float(alpha)).astype(np.uint8)
        img = Image.fromarray(arr, mode="RGBA")
    oi = pil_to_offset_image(img, zoom=zoom)
    ab = AnnotationBbox(
        oi,
        (x, y),
        frameon=False,
        pad=0.0,
        box_alignment=(0.5, 0.5),
        zorder=zorder,
    )
    ax.add_artist(ab)


def draw_text_chip(ax: Any, label: str, x: float, y: float, *, fontsize: float = 7) -> None:
    """Fallback abbreviation chip when logos are missing (allow-missing mode)."""
    ax.text(
        x,
        y,
        label,
        fontsize=fontsize,
        color=TEXT,
        ha="center",
        va="center",
        zorder=4,
        bbox={
            "boxstyle": "round,pad=0.25",
            "facecolor": PANEL,
            "edgecolor": BORDER,
            "linewidth": 0.6,
        },
    )


def draw_exact_dot(ax: Any, x: float, y: float, *, size: float = 8.0, color: str = DOT) -> None:
    """Small marker at the exact data coordinate."""
    ax.scatter([x], [y], s=size, c=color, zorder=3, linewidths=0)


def draw_leader(ax: Any, x0: float, y0: float, x1: float, y1: float) -> None:
    """Thin leader from exact dot to displaced logo."""
    ax.plot([x0, x1], [y0, y1], color=LEADER, linewidth=0.6, zorder=2.8, solid_capstyle="round")
