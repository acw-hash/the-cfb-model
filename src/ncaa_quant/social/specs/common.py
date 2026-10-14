"""Shared frame/footer helpers for SOC-2 chart specs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ncaa_quant.social.logos import resolve_logo_path
from ncaa_quant.social.specs.format_text import published_day_et
from ncaa_quant.social.theme import ChartSize

ThemeName = Literal["light", "dark"]

#: On-image site host (no scheme) required by SOC-2 polish footer.
FOOTER_SITE_HOST = "the-cfb-model.vercel.app"

# CSS layout sizes at 1×; Playwright screenshots at deviceScaleFactor 2.
CSS_LAYOUT_SIZES: dict[str, tuple[int, int]] = {
    "4x5": (800, 1000),
    "16x9": (960, 540),
}
DEVICE_SCALE_FACTOR = 2

# Light theme matches DESIGN §4.1 / webapp tokens.css (read-only mirror for specs).
LIGHT_TOKENS = {
    "bg_primary": "#FFFFFF",
    "bg_secondary": "#F5F5F7",
    "text_primary": "#1D1D1F",
    "text_secondary": "#6E6E73",
    "text_tertiary": "#75757A",
    "border": "#D2D2D7",
    "accent": "#0071E3",
}

DARK_TOKENS = {
    "bg_primary": "#000000",
    "bg_secondary": "#1C1C1E",
    "text_primary": "#F5F5F7",
    "text_secondary": "#98989D",
    "text_tertiary": "#8E8E93",
    "border": "#38383A",
    "accent": "#0A84FF",
}

TIER_COLORS_LIGHT = {
    "strong_lean": "#0071E3",
    "clear_lean": "#4A9AED",
    "lean": "#8FC0F5",
    "toss_up": "#AEAEB2",
}

TIER_COLORS_DARK = {
    "strong_lean": "#0A84FF",
    "clear_lean": "#409CFF",
    "lean": "#64B0FF",
    "toss_up": "#636366",
}

TIER_CHIP_LABELS = {
    "strong_lean": "Strong",
    "clear_lean": "Clear",
    "lean": "Lean",
    "toss_up": "Toss-up",
}

# Equal logo axis fraction for read-map (highlights use rings, not size).
SOC1_LOGO_AXIS_FRACTION = 0.055
READMAP_LOGO_AXIS_FRACTION = SOC1_LOGO_AXIS_FRACTION

# Target opaque-pixel area (CSS px²) for equalized logos within a chart.
LOGO_TARGET_OPAQUE_AREA = {
    "readmap": 28 * 28,
    "learned": 32 * 32,
    "movers": 32 * 32,
    "slate_ranges": 20 * 20,
    "gamecard": 120 * 120,
}

# Bounding-box slot caps passed to ``logo_display_size(..., slot_px=)``.
SLOT_PX: dict[str, int] = {
    "readmap": 28,
    "learned": 32,
    "movers": 32,
    "slate_ranges": 20,
    "gamecard": 120,
}


@dataclass(frozen=True)
class FrameMeta:
    """Chrome shared by every SOC-2 chart."""

    season: int
    week: int
    week_stamp: str
    title: str
    subtitle: str
    model_version: str
    vintage_label: str
    published_at: str
    published_at_et: str
    site_url: str
    theme: ThemeName
    size_name: str
    width: int
    height: int


def tokens_for(theme: ThemeName) -> dict[str, str]:
    return dict(LIGHT_TOKENS if theme == "light" else DARK_TOKENS)


def tier_colors(theme: ThemeName) -> dict[str, str]:
    return dict(TIER_COLORS_LIGHT if theme == "light" else TIER_COLORS_DARK)


def soc2_footer(*, published_at: str, extra: str | None = None) -> str:
    """Lean on-image footer (model/vintage/refresh live in alt + run_report)."""
    day = published_day_et(published_at)
    base = (
        f"Ridge · Forecasts, not betting advice · {FOOTER_SITE_HOST} · "
        f"published {day} ET"
    )
    if extra:
        return f"{base} · {extra}"
    return base


def size_pixels(size: ChartSize | str) -> tuple[str, int, int]:
    """Return CSS 1× layout size for SOC-2 specs (not output PNG pixels)."""
    if isinstance(size, ChartSize):
        name = size.name
    else:
        name = size
    if name not in CSS_LAYOUT_SIZES:
        msg = f"Unknown size {name!r}; expected one of {sorted(CSS_LAYOUT_SIZES)}"
        raise ValueError(msg)
    w, h = CSS_LAYOUT_SIZES[name]
    return name, w, h


def logo_path_for(
    team_id: int,
    *,
    logo_dir: Path,
    theme: ThemeName,
) -> Path | None:
    """Standard (light) logos on light theme; dark variant preferred on dark."""
    prefer_dark = theme == "dark"
    path, _from_override = resolve_logo_path(team_id, logo_dir=logo_dir, prefer_dark=prefer_dark)
    return path


def week_point(team: dict[str, Any], week: int) -> dict[str, Any] | None:
    for row in team.get("weeks") or []:
        if int(row["week"]) == week:
            return dict(row)
    return None


def frame_dict(
    *,
    season: int,
    week: int,
    week_stamp: str,
    title: str,
    subtitle: str,
    theme: str,
    size_name: str,
    width: int,
    height: int,
    published_at: str,
    model_version: str,
    vintage_label: str,
    brand_mark: Path | None = None,
    footer_extra: str | None = None,
) -> dict[str, Any]:
    """Build the shared ``frame`` object for a SOC-2 spec."""
    from ncaa_quant.social.charts._frame import published_at_et

    return {
        "season": season,
        "week": week,
        "week_stamp": week_stamp,
        "title": title,
        "subtitle": subtitle,
        "wordmark": "RIDGE",
        "brand_mark": str(brand_mark) if brand_mark else None,
        "footer": soc2_footer(published_at=published_at, extra=footer_extra),
        "theme": theme,
        "tokens": tokens_for(theme),  # type: ignore[arg-type]
        "tier_colors": tier_colors(theme),  # type: ignore[arg-type]
        "size": size_name,
        "width": width,
        "height": height,
        "device_scale_factor": DEVICE_SCALE_FACTOR,
        "published_at_et": published_at_et(published_at),
        "published_day_et": published_day_et(published_at),
        "model_version": model_version,
        "vintage_label": vintage_label,
    }
