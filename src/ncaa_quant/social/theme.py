"""Brand tokens and matplotlib helpers for Ridge social charts.

Visual tokens follow the SOC-1e navy brand (not webapp dark mode). Charts are
a new surface (not the webapp UI); logos are allowed here per the 2026-10-08
owner decision recorded in docs/notes/social-charts.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib as mpl
from matplotlib import font_manager

# SOC-1e brand
BG = "#07152F"
# Plot panel: 6% lighter navy (blend BG toward white by 6%).
PANEL = "#16233B"
TEXT = "#F5F5F7"
TEXT_SECONDARY = "#A8B0BC"
TEXT_TERTIARY = "#7A8494"
BORDER = "#2A3A52"
ACCENT = "#FD4D11"
TOSS_UP_GRAY = "#5A6A80"
MISS_ACCENT = "#FD4D11"
HIT_RING = "#A8B0BC"
DOT = "#F5F5F7"
LEADER = "#5A6A80"
PLATE = "#F5F5F7"
LABEL_WHITE_70 = (1.0, 1.0, 1.0, 0.70)

#: Mean luminance of opaque logo pixels below this → draw a light plate.
#: Tuned for navy (#07152F / #16233B) backgrounds (slightly higher than black).
LOGO_LUMINANCE_THRESHOLD = 0.42

SIZES: dict[str, tuple[int, int]] = {
    "4x5": (1600, 2000),
    "16x9": (1920, 1080),
}

REPO_ROOT = Path(__file__).resolve().parents[3]
FONT_DIR = REPO_ROOT / "assets" / "fonts"
BRAND_MARK_PATH = REPO_ROOT / "assets" / "brand" / "ridge_mark.png"

_FONT_FAMILY: str | None = None


@dataclass(frozen=True)
class ChartSize:
    """Pixel canvas size for a rendered chart."""

    name: str
    width: int
    height: int

    @property
    def figsize(self) -> tuple[float, float]:
        """Matplotlib figsize in inches at 100 dpi."""
        return (self.width / 100.0, self.height / 100.0)


def parse_sizes(spec: str) -> list[ChartSize]:
    """Parse ``4x5,16x9`` size flags into :class:`ChartSize` values."""
    out: list[ChartSize] = []
    for part in spec.split(","):
        key = part.strip().lower()
        if key not in SIZES:
            msg = f"Unknown size {key!r}; expected one of {sorted(SIZES)}"
            raise ValueError(msg)
        w, h = SIZES[key]
        out.append(ChartSize(name=key, width=w, height=h))
    return out


def resolve_font_family() -> str:
    """Resolve SF Pro Display if installed, else bundled Inter. Never DejaVu.

    Raises
    ------
    RuntimeError
        If neither SF Pro nor Inter can be registered.
    """
    global _FONT_FAMILY
    if _FONT_FAMILY is not None:
        return _FONT_FAMILY

    # Prefer SF Pro when the OS has it (macOS / some Windows installs).
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for candidate in ("SF Pro Display", "SF Pro Text", ".SF NS Display"):
        if candidate in installed:
            _FONT_FAMILY = candidate
            return _FONT_FAMILY

    inter_files = [
        FONT_DIR / "Inter-Regular.ttf",
        FONT_DIR / "Inter-Medium.ttf",
        FONT_DIR / "Inter-SemiBold.ttf",
    ]
    found = [p for p in inter_files if p.is_file()]
    if not found:
        msg = (
            "No chart font resolved: SF Pro Display is not installed and "
            f"Inter was not found under {FONT_DIR}. Bundle Inter (OFL) there."
        )
        raise RuntimeError(msg)
    for path in found:
        font_manager.fontManager.addfont(str(path))
    # After addfont the family name is "Inter".
    _FONT_FAMILY = "Inter"
    return _FONT_FAMILY


def apply_matplotlib_theme() -> str:
    """Configure matplotlib rcParams for deterministic navy charts.

    Returns
    -------
    str
        Resolved font family name.
    """
    family = resolve_font_family()
    mpl.rcParams.update(
        {
            "figure.facecolor": BG,
            "axes.facecolor": PANEL,
            "savefig.facecolor": BG,
            "text.color": TEXT,
            "axes.labelcolor": TEXT_SECONDARY,
            "axes.edgecolor": BORDER,
            "xtick.color": TEXT_SECONDARY,
            "ytick.color": TEXT_SECONDARY,
            "font.family": family,
            "font.size": 14,
            "axes.titlesize": 22,
            "axes.labelsize": 13,
            "figure.dpi": 100,
            "savefig.dpi": 100,
            "axes.grid": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    return family


def fmt_margin(value: float) -> str:
    """Format a signed margin to 1 decimal (DESIGN §4.2)."""
    return f"{value:+.1f}"


def fmt_margin_abs(value: float) -> str:
    """Format an unsigned magnitude to 1 decimal."""
    return f"{abs(value):.1f}"


def fmt_pct(value: float) -> str:
    """Format a probability as a percent (DESIGN §4.2)."""
    pct = 100.0 * value
    if pct < 10.0:
        return f"{pct:.1f}%"
    return f"{pct:.0f}%"


def save_png(fig: Any, path: Path) -> None:
    """Write a PNG with metadata stripped for byte-identical reruns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        path,
        format="png",
        dpi=100,
        facecolor=BG,
        edgecolor="none",
        bbox_inches=None,
        pad_inches=0,
        metadata={"Software": None},
    )


def site_url_from_config() -> str:
    """Public site URL for footers (from revalidate host or documented default)."""
    from ncaa_quant.config import load_config

    cfg = load_config()
    reval = (cfg.webapp.revalidate_url or "").strip()
    if reval:
        # https://host/api/revalidate → https://host
        if "/api/" in reval:
            return reval.split("/api/", 1)[0].rstrip("/")
        return reval.rstrip("/")
    public = (cfg.webapp.r2_public_base_url or "").strip()
    if public:
        return public.rstrip("/")
    return "https://the-cfb-model.vercel.app"


def site_host_for_footer(site_url: str | None = None) -> str:
    """Hostname (no scheme) for the on-image footer line."""
    raw = (site_url or site_url_from_config()).strip()
    for prefix in ("https://", "http://"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    return raw.rstrip("/")
