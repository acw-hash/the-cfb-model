"""Read map — FBS percentile axes (better team × surer read)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from ncaa_quant.social.charts.ridge_map import rank_percentiles, ridge_week_labels
from ncaa_quant.social.copy import trim_caption
from ncaa_quant.social.logos import logo_display_size, store_logo_scales_in_manifest
from ncaa_quant.social.placement import Box, repel_logos
from ncaa_quant.social.specs.common import (
    frame_dict,
    logo_path_for,
    size_pixels,
    week_point,
)
from ncaa_quant.social.specs.net import (
    UNCERTAINTY_FOOTNOTE,
    net_pts,
    net_sd_pts,
    resolve_margin_scale,
)
from ncaa_quant.social.theme import ChartSize

#: Caption names sharpest/widest only when that end of the SD spread clears this gap.
MIN_READ_GAP_PTS = 1.0

#: Preferred max logo displacement (CSS px). Raised to 28 when 14 cannot
#: clear the packer overlap target for a full FBS field (see round-5 notes).
_READMAP_MAX_DISP_CSS_PX = 36.0
_READMAP_MAX_DISP_PREFERRED_CSS_PX = 14.0
_READMAP_LEADER_CSS_PX = 8.0
#: Packer shrink/accept target (stricter than Playwright lint).
_READMAP_PACKER_OVERLAP_TARGET = 0.06
#: Playwright lint ceiling in ``social/render/src/lint.mjs`` (``MAX_LOGO_OVERLAP_FRAC``).
_READMAP_LINT_OVERLAP_LIMIT = 0.08
#: Backward-compatible alias used by older tests; equals the packer target.
_READMAP_OVERLAP_LIMIT = _READMAP_PACKER_OVERLAP_TARGET
_READMAP_SLOT_START = 28
#: Floor for the shrink loop (weekly run 2026-10-13: text–logo trigger).
_READMAP_SLOT_MIN = 22
_READMAP_SLOT_STEP = 2
#: Corner keep-out for inside quadrant labels (CSS px; ≈ label bbox + pad).
_QUAD_ZONE_W = 130.0
_QUAD_ZONE_H = 24.0
#: Left gutter for rotated y-title + ±pts tick labels + Most/Least sure (CSS px).
_Y_GUTTER = 96.0
#: Bottom gutter: x-tick row + separate outside-label / x-title row (CSS px).
_X_GUTTER = 72.0
#: Top pad above the plot frame for the outside quadrant / average row.
_TOP_LABEL_PAD = 22.0
#: Pad each side of the net_pts data range when fitting the x domain.
_X_DOMAIN_PAD_FRAC = 0.06
#: Certainty-percentile positions for y tick marks (100 = sharpest read).
_Y_CERT_TICKS = (0.0, 25.0, 50.0, 75.0, 100.0)
#: Read-map dek may exceed the global 60-char dek cap.
READMAP_DEK_MAX = 90


def _nice_tick_step(span: float, *, target: int = 5) -> float:
    """d3-style 1/2/5 step for roughly ``target`` ticks across ``span``."""
    raw = span / max(target - 1, 1)
    if raw <= 0:
        return 1.0
    exp = math.floor(math.log10(raw))
    base = raw / (10**exp)
    if base <= 1:
        nice = 1.0
    elif base <= 2:
        nice = 2.0
    elif base <= 5:
        nice = 5.0
    else:
        nice = 10.0
    return nice * (10**exp)


def fitted_net_domain(values: list[float]) -> tuple[float, float, list[float], list[str]]:
    """Fit x domain to data + 6% range pad; always include 0; ~5 nice ticks.

    Domain stays at the padded data range (plus 0). Nice ticks are nested
    *inside* that domain so a wide FBS field does not snap out to ±80.
    """
    from ncaa_quant.social.specs.format_text import format_axis_tick

    if not values:
        ticks = [-40.0, -20.0, 0.0, 20.0, 40.0]
        return -40.0, 40.0, ticks, [format_axis_tick(v) for v in ticks]
    lo = float(min(values))
    hi = float(max(values))
    span = max(hi - lo, 1.0)
    pad = span * _X_DOMAIN_PAD_FRAC
    x_min = lo - pad
    x_max = hi + pad
    if x_min > 0.0:
        x_min = 0.0
    if x_max < 0.0:
        x_max = 0.0

    def _inner_ticks(step: float) -> list[float]:
        t0 = math.ceil(x_min / step - 1e-12) * step
        t1 = math.floor(x_max / step + 1e-12) * step
        ticks: list[float] = []
        if t1 + step * 1e-9 < t0:
            return [0.0] if x_min <= 0.0 <= x_max else [round(0.5 * (x_min + x_max), 6)]
        v = t0
        while v <= t1 + step * 1e-9:
            ticks.append(round(v, 6))
            v += step
        if 0.0 not in ticks and x_min <= 0.0 <= x_max:
            ticks = sorted({*ticks, 0.0})
        return ticks

    best: list[float] | None = None
    for target in (5, 4, 6, 3):
        step = _nice_tick_step(x_max - x_min, target=target)
        for _ in range(6):
            ticks = _inner_ticks(step)
            n = len(ticks)
            if n == 5:
                labels = [format_axis_tick(v) for v in ticks]
                return float(x_min), float(x_max), ticks, labels
            if 4 <= n <= 6 and (
                best is None or abs(len(best) - 5) > abs(n - 5)
            ):
                best = ticks
            if n > 5:
                step *= 2.0
                continue
            if n < 4:
                step /= 2.0
                if step < 1e-6:
                    break
                continue
            break
    if best is None:
        best = _inner_ticks(_nice_tick_step(x_max - x_min, target=5))
    labels = [format_axis_tick(v) for v in best]
    return float(x_min), float(x_max), best, labels


def symmetric_net_domain(values: list[float]) -> tuple[float, float, list[float], list[str]]:
    """Backward-compatible alias — week-8 polish uses the fitted (non-symmetric) domain."""
    return fitted_net_domain(values)


def round_sd_half(value: float) -> float:
    """Round ``net_sd_pts`` to the nearest 0.5."""
    return round(float(value) * 2.0) / 2.0


def format_pm_sd(value: float, *, one_decimal: bool = False) -> str:
    """Format a certainty tick as ``±pts`` (half-step or forced 1 decimal)."""
    if one_decimal:
        return f"±{float(value):.1f}"
    rounded = round_sd_half(value)
    if abs(rounded - round(rounded)) < 1e-9:
        return f"±{int(round(rounded))}"
    return f"±{rounded:.1f}"


def certainty_sd_tick_labels(sds: list[float]) -> tuple[list[float], list[float], list[str]]:
    """Y ticks at certainty percentiles 0/25/50/75/100 with ±pts SD labels.

    Position stays certainty percentile (100 = sharpest). Each label is the
    ``net_sd_pts`` quantile at that certainty (``percentile(sds, 100 − c)``),
    rounded to 0.5. Adjacent labels that collide after rounding force 1 decimal
    on those two.
    """
    if not sds:
        ticks = list(_Y_CERT_TICKS)
        return ticks, [0.0] * len(ticks), ["±0"] * len(ticks)
    arr = np.asarray(sds, dtype=float)
    certs = list(_Y_CERT_TICKS)
    raw = [float(np.percentile(arr, 100.0 - c)) for c in certs]
    rounded = [round_sd_half(v) for v in raw]
    one_dec = [False] * len(raw)
    for i in range(len(raw) - 1):
        if abs(rounded[i] - rounded[i + 1]) < 1e-9:
            one_dec[i] = True
            one_dec[i + 1] = True
    labels = [format_pm_sd(v, one_decimal=flag) for v, flag in zip(raw, one_dec, strict=True)]
    return certs, raw, labels


def competition_ranks(net_vals: list[float], team_ids: list[int]) -> list[int]:
    """Ridge power rank among plotted teams: #1 = highest ``net_pts``, ties by ``team_id``."""
    order = sorted(range(len(net_vals)), key=lambda i: (-net_vals[i], team_ids[i]))
    ranks = [0] * len(net_vals)
    for rank_i, idx in enumerate(order, start=1):
        ranks[idx] = rank_i
    return ranks


def quadrant_obstacle_boxes(
    *,
    x_lo: float,
    x_hi: float,
    y_lo: float,
    y_hi: float,
    plot_w: float,
    plot_h: float,
) -> list[Box]:
    """Inside-corner keep-outs (~150×28 CSS px) for quadrant labels + average tag."""
    x_span = max(x_hi - x_lo, 1e-6)
    y_span = max(y_hi - y_lo, 1e-6)
    zw = (_QUAD_ZONE_W / plot_w) * x_span
    zh = (_QUAD_ZONE_H / plot_h) * y_span
    # Small keep-out at top of the zero midline for the "average" label.
    avg_w = (56.0 / plot_w) * x_span
    avg_h = (18.0 / plot_h) * y_span
    return [
        Box(x_hi - zw / 2.0, y_hi - zh / 2.0, zw, zh, -1),  # proven good
        Box(x_hi - zw / 2.0, y_lo + zh / 2.0, zw, zh, -2),  # good proving
        Box(x_lo + zw / 2.0, y_hi - zh / 2.0, zw, zh, -3),  # known struggles
        Box(x_lo + zw / 2.0, y_lo + zh / 2.0, zw, zh, -4),  # hard to read
        Box(0.0, y_hi - avg_h / 2.0, avg_w, avg_h, -5),  # average
    ]


def filled_plot_box(
    *,
    canvas_w: int,
    canvas_h: int,
    size_name: str,
) -> dict[str, float]:
    """Non-square plot filling dek→x-axis and content width minus y-label column.

    4:5 lands near 640×720 CSS px; 16:9 uses full remaining height with width
    following the content. No horizontal centering — empty side bands go away.
    """
    # Frame chrome estimates (padding + header + titles + footer).
    # Tuned so 4:5 plot height ≈ 720 and leftover dek/footer bands stay ≤6%.
    chrome_top = 24 + 28 + 76
    chrome_bot = 20 + 28
    side_pad = 40.0
    if size_name == "16x9":
        chrome_top = 24 + 24 + 60
        chrome_bot = 18 + 24
    content_w = canvas_w - 2 * side_pad
    top_label_pad = _TOP_LABEL_PAD
    avail_h = canvas_h - chrome_top - chrome_bot - _X_GUTTER - top_label_pad
    avail_w = content_w - _Y_GUTTER
    plot_w = max(120.0, float(avail_w))
    plot_h = max(120.0, float(avail_h))
    return {
        "x": _Y_GUTTER,
        "y": top_label_pad,
        "w": plot_w,
        "h": plot_h,
        "bottom_axis_gutter": _X_GUTTER,
        "y_gutter": _Y_GUTTER,
        "outer_left": 0.0,
        "outer_right": 0.0,
    }


def centered_square_plot(
    *,
    canvas_w: int,
    canvas_h: int,
    size_name: str,
) -> dict[str, float]:
    """Backward-compatible alias for :func:`filled_plot_box` (square rule dropped)."""
    return filled_plot_box(canvas_w=canvas_w, canvas_h=canvas_h, size_name=size_name)


def logo_span_fractions(
    points: list[dict[str, Any]],
    *,
    x_lo: float,
    x_hi: float,
    y_lo: float,
    y_hi: float,
) -> tuple[float, float]:
    """Fraction of plot width/height spanned by logo boxes (data units)."""
    x_span = max(x_hi - x_lo, 1e-6)
    y_span = max(y_hi - y_lo, 1e-6)
    if not points:
        return 0.0, 0.0
    x0 = min(float(p["logo_x"]) - 0.5 * float(p["width"]) for p in points)
    x1 = max(float(p["logo_x"]) + 0.5 * float(p["width"]) for p in points)
    y0 = min(float(p["logo_y"]) - 0.5 * float(p["height"]) for p in points)
    y1 = max(float(p["logo_y"]) + 0.5 * float(p["height"]) for p in points)
    return (x1 - x0) / x_span, (y1 - y0) / y_span


def readmap_caption(
    *,
    sharpest: list[dict[str, Any]],
    widest: list[dict[str, Any]],
    proven_good_count: int,
    name_sharp: bool,
    name_wide: bool,
    sd_min: float,
    sd_max: float,
) -> str:
    """Caption seed: name extremes only when the SD spread clears the gap rule."""
    if not name_sharp and not name_wide:
        return (
            f"Ridge's reads are tightly bunched this week: "
            f"±{sd_min:.1f} to ±{sd_max:.1f} pts. "
            f"{proven_good_count} FBS teams in Proven good."
        )
    parts: list[str] = []
    if name_sharp:
        sharp_names = [r["abbreviation"] for r in sharpest[:3]]
        parts.append(f"Ridge's sharpest reads: {', '.join(sharp_names)}.")
    if name_wide:
        wide_names = [r["abbreviation"] for r in widest[:3]]
        parts.append(f"Widest: {', '.join(wide_names)}.")
    if not parts:
        parts.append(
            f"Ridge's reads are tightly bunched this week: ±{sd_min:.1f} to ±{sd_max:.1f} pts."
        )
    parts.append(f"{proven_good_count} FBS teams in Proven good.")
    return " ".join(parts)


def _css_displacement(
    dx: float,
    dy: float,
    *,
    px_per_x: float,
    px_per_y: float,
) -> float:
    return float(math.hypot(dx * px_per_x, dy * px_per_y))


def _clamp_displacement_css(
    *,
    true_x: float,
    true_y: float,
    logo_x: float,
    logo_y: float,
    px_per_x: float,
    px_per_y: float,
    max_css: float,
) -> tuple[float, float]:
    dx = logo_x - true_x
    dy = logo_y - true_y
    css = _css_displacement(dx, dy, px_per_x=px_per_x, px_per_y=px_per_y)
    if css <= max_css or css < 1e-12:
        return logo_x, logo_y
    scale = max_css / css
    return true_x + dx * scale, true_y + dy * scale


def _overlap_fraction(state: dict[int, dict[str, Any]]) -> float:
    boxes = [
        Box(s["logo_x"], s["logo_y"], s["width"], s["height"], tid)
        for tid, s in state.items()
    ]
    worst = 0.0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            overlap = boxes[i].overlap_area(boxes[j])
            denom = min(boxes[i].area(), boxes[j].area())
            if denom > 0:
                worst = max(worst, overlap / denom)
    return worst


def _logo_text_obstacle_hit(
    state: dict[int, dict[str, Any]],
    obstacles: list[Box],
) -> bool:
    """True when any logo box intersects a quadrant / label keep-out."""
    if not obstacles:
        return False
    for tid, s in state.items():
        box = Box(s["logo_x"], s["logo_y"], s["width"], s["height"], tid)
        for ob in obstacles:
            if box.overlap_area(ob) > 0.0:
                return True
    return False


def _placement_clear(
    state: dict[int, dict[str, Any]],
    obstacles: list[Box],
) -> bool:
    """Logo–logo ≤ packer target and no logo–text (obstacle) overlap."""
    if _overlap_fraction(state) > _READMAP_PACKER_OVERLAP_TARGET:
        return False
    return not _logo_text_obstacle_hit(state, obstacles)


def _place_all_logos(
    *,
    rows: list[dict[str, Any]],
    xs: list[float],
    ys: list[float],
    x_lo: float,
    x_hi: float,
    y_lo: float,
    y_hi: float,
    plot_w: float,
    plot_h: float,
    logo_dir: Path,
    theme: str,
    obstacles: list[Box] | None = None,
) -> tuple[list[dict[str, Any]], int, float, float, dict[int, float], list[dict[str, Any]]]:
    """Try slot sizes from 28→22 until logo–logo ≤ packer target and logos clear keep-outs."""
    x_span = max(x_hi - x_lo, 1e-6)
    y_span = max(y_hi - y_lo, 1e-6)
    px_per_x = plot_w / x_span
    px_per_y = plot_h / y_span
    team_ids = [r["team_id"] for r in rows]
    scales: dict[int, float] = {}
    missing: list[dict[str, Any]] = []
    obs = obstacles or []

    slot = _READMAP_SLOT_START
    best: list[dict[str, Any]] | None = None
    best_max_disp = 0.0
    best_overlap = 1.0
    final_slot = slot

    while slot >= _READMAP_SLOT_MIN:
        # Area target slightly under the slot so equalized logos leave a hairline gap.
        target_area = float((slot - 2) * (slot - 2))
        logo_w_data = []
        logo_h_data = []
        disp_sizes: dict[int, tuple[float, float, Path | None]] = {}
        missing = []
        for r in rows:
            tid = r["team_id"]
            path = logo_path_for(tid, logo_dir=logo_dir, theme=theme)  # type: ignore[arg-type]
            if path is None:
                missing.append({"team_id": tid, "school": r["school"]})
                # Still reserve a slot so placement stays uniform.
                dw = dh = float(slot)
                disp_sizes[tid] = (dw, dh, None)
            else:
                ld = logo_display_size(
                    path,
                    team_id=tid,
                    target_opaque_area=target_area,
                    slot_px=slot,
                )
                dw, dh = float(ld.width), float(ld.height)
                scales[tid] = ld.scale
                disp_sizes[tid] = (dw, dh, path)
            # Convert CSS px slot to data units for the repel solver.
            logo_w_data.append(dw / px_per_x)
            logo_h_data.append(dh / px_per_y)

        # Inset placement bounds by half a slot so logos stay inside the plot
        # stroke and clear the "average" tag above the top edge.
        inset_x = (slot * 0.55) / px_per_x
        inset_y = (slot * 0.55) / px_per_y
        # Try preferred 14px CSS cap first; fall back to 28px if overlap stays hot.
        chosen: list[dict[str, Any]] | None = None
        chosen_max = 0.0
        chosen_ov = 1.0
        chosen_state: dict[int, dict[str, Any]] | None = None
        for max_css in (
            _READMAP_MAX_DISP_PREFERRED_CSS_PX,
            28.0,
            _READMAP_MAX_DISP_CSS_PX,
        ):
            max_disp_widths = max(0.55, max_css / float(slot))
            placed = repel_logos(
                team_ids=team_ids,
                xs=xs,
                ys=ys,
                widths=logo_w_data,
                heights=logo_h_data,
                x_min=x_lo + inset_x,
                x_max=x_hi - inset_x,
                y_min=y_lo + inset_y,
                y_max=y_hi - inset_y,
                obstacles=obs,
                iterations=100,
                max_disp_widths=max_disp_widths,
                overlap_allow_fraction=_READMAP_PACKER_OVERLAP_TARGET,
            )
            by_tid = {p.team_id: p for p in placed}
            state: dict[int, dict[str, Any]] = {}
            max_disp = 0.0
            for r in rows:
                tid = r["team_id"]
                pl = by_tid[tid]
                lx, ly = _clamp_displacement_css(
                    true_x=float(pl.true_x),
                    true_y=float(pl.true_y),
                    logo_x=float(pl.logo_x),
                    logo_y=float(pl.logo_y),
                    px_per_x=px_per_x,
                    px_per_y=px_per_y,
                    max_css=max_css,
                )
                dx = lx - float(pl.true_x)
                dy = ly - float(pl.true_y)
                css = _css_displacement(dx, dy, px_per_x=px_per_x, px_per_y=px_per_y)
                max_disp = max(max_disp, css)
                dw, dh, path = disp_sizes[tid]
                state[tid] = {
                    "true_x": float(pl.true_x),
                    "true_y": float(pl.true_y),
                    "logo_x": lx,
                    "logo_y": ly,
                    "width": dw / px_per_x,
                    "height": dh / px_per_y,
                    "logo_display_w": dw,
                    "logo_display_h": dh,
                    "logo_path": str(path) if path else None,
                    "css_disp": css,
                    "as_dot": False,
                    "highlight": False,
                    "draw_leader": css > _READMAP_LEADER_CSS_PX,
                }
            _nudge_apart_css(
                state,
                px_per_x=px_per_x,
                px_per_y=px_per_y,
                x_lo=x_lo,
                x_hi=x_hi,
                y_lo=y_lo,
                y_hi=y_hi,
                limit=_READMAP_PACKER_OVERLAP_TARGET,
                max_css=max_css,
                obstacles=obs,
            )
            max_disp = (
                max(float(s["css_disp"]) for s in state.values()) if state else 0.0
            )
            ov = _overlap_fraction(state)
            chosen = [{**r, **state[r["team_id"]]} for r in rows]
            chosen_max = max_disp
            chosen_ov = ov
            chosen_state = state
            if _placement_clear(state, obs):
                break

        assert chosen is not None and chosen_state is not None
        points_out = chosen
        max_disp = chosen_max
        ov = chosen_ov
        best = points_out
        best_max_disp = max_disp
        best_overlap = ov
        final_slot = slot
        if _placement_clear(chosen_state, obs):
            break
        slot -= _READMAP_SLOT_STEP

    assert best is not None
    return best, final_slot, best_max_disp, best_overlap, scales, missing


def _nudge_apart_css(
    state: dict[int, dict[str, Any]],
    *,
    px_per_x: float,
    px_per_y: float,
    x_lo: float,
    x_hi: float,
    y_lo: float,
    y_hi: float,
    limit: float,
    max_css: float,
    obstacles: list[Box] | None = None,
) -> None:
    """Push overlapping logo pairs apart in CSS px, re-clamping to max displacement."""
    ids = list(state.keys())
    obs = obstacles or []

    def _apply(s: dict[str, Any], nx: float, ny: float) -> None:
        nx = min(max(nx, x_lo), x_hi)
        ny = min(max(ny, y_lo), y_hi)
        nx, ny = _clamp_displacement_css(
            true_x=float(s["true_x"]),
            true_y=float(s["true_y"]),
            logo_x=nx,
            logo_y=ny,
            px_per_x=px_per_x,
            px_per_y=px_per_y,
            max_css=max_css,
        )
        s["logo_x"] = nx
        s["logo_y"] = ny
        css = _css_displacement(
            nx - float(s["true_x"]),
            ny - float(s["true_y"]),
            px_per_x=px_per_x,
            px_per_y=px_per_y,
        )
        s["css_disp"] = css
        s["draw_leader"] = css > _READMAP_LEADER_CSS_PX

    for _ in range(16):
        moved = False
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a = state[ids[i]]
                b = state[ids[j]]
                box_a = Box(a["logo_x"], a["logo_y"], a["width"], a["height"], ids[i])
                box_b = Box(b["logo_x"], b["logo_y"], b["width"], b["height"], ids[j])
                denom = min(box_a.area(), box_b.area())
                if denom <= 0:
                    continue
                frac = box_a.overlap_area(box_b) / denom
                if frac <= limit:
                    continue
                dx_css = (b["logo_x"] - a["logo_x"]) * px_per_x
                dy_css = (b["logo_y"] - a["logo_y"]) * px_per_y
                dist = math.hypot(dx_css, dy_css)
                if dist < 1e-6:
                    dx_css, dy_css, dist = 1.0, 0.0, 1.0
                need = 3.0 + frac * 10.0
                ux, uy = dx_css / dist, dy_css / dist
                step = need / 2.0
                _apply(a, a["logo_x"] - (step / px_per_x) * ux, a["logo_y"] - (step / px_per_y) * uy)
                _apply(b, b["logo_x"] + (step / px_per_x) * ux, b["logo_y"] + (step / px_per_y) * uy)
                moved = True
        # Push logos out of quadrant / average obstacles toward plot center.
        mid_x = 0.5 * (x_lo + x_hi)
        mid_y = 0.5 * (y_lo + y_hi)
        for tid in ids:
            s = state[tid]
            box = Box(s["logo_x"], s["logo_y"], s["width"], s["height"], tid)
            for ob in obs:
                if box.overlap_area(ob) <= 0:
                    continue
                _apply(
                    s,
                    0.55 * s["logo_x"] + 0.45 * mid_x,
                    0.55 * s["logo_y"] + 0.45 * mid_y,
                )
                box = Box(s["logo_x"], s["logo_y"], s["width"], s["height"], tid)
                moved = True
        if not moved:
            break


def build_readmap_specs(
    *,
    team_ratings: dict[str, Any],
    week: int,
    slate_week: int,
    season: int,
    fbs_team_ids: set[int],
    abbrev_by_id: dict[int, str],
    logo_dir: Path,
    sizes: list[ChartSize],
    theme: str,
    model_version: str,
    vintage_label: str,
    published_at: str,
    site_url: str,
    brand_mark: Path,
    protected_team_ids: set[int] | None = None,
    conference_by_id: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Build one spec JSON dict per size (net_pts × certainty; all FBS logos)."""
    del site_url, protected_team_ids, conference_by_id
    from ncaa_quant.social.display_names import label_name

    scale = resolve_margin_scale()
    teams = team_ratings.get("teams") or {}
    rows: list[dict[str, Any]] = []
    for tid in sorted(fbs_team_ids):
        entry = teams.get(str(tid))
        if not entry:
            continue
        pt = week_point(entry, week)
        if pt is None:
            continue
        off = pt.get("off_epa")
        de = pt.get("def_epa")
        osd = pt.get("off_sd")
        dsd = pt.get("def_sd")
        if None in (off, de, osd, dsd):
            continue
        np_ = net_pts(off_epa=float(off), def_epa=float(de), margin_scale=scale.value)
        nsd = net_sd_pts(off_sd=float(osd), def_sd=float(dsd), margin_scale=scale.value)
        school = str(entry.get("school") or f"Team {tid}")
        abbr = abbrev_by_id.get(tid, school[:4].upper())
        rows.append(
            {
                "team_id": tid,
                "school": school,
                "abbreviation": abbr,
                "short_name": label_name(tid, school, abbrev_by_id, prefer="short"),
                "net_pts": np_,
                "net_sd_pts": nsd,
                "off_epa": float(off),
                "def_epa": float(de),
                "off_sd": float(osd),
                "def_sd": float(dsd),
            }
        )
    if not rows:
        msg = f"No FBS ratings for read map week {week}"
        raise RuntimeError(msg)

    # X = net_pts (points); Y = certainty percentile among plotted FBS.
    cert_arr = -np.array([r["net_sd_pts"] for r in rows], dtype=float)
    y_pcts = rank_percentiles(cert_arr)
    ranks = competition_ranks(
        [r["net_pts"] for r in rows],
        [r["team_id"] for r in rows],
    )
    for i, r in enumerate(rows):
        r["x"] = float(r["net_pts"])
        r["y_pct"] = float(y_pcts[i])
        r["power_rank"] = ranks[i]

    sds = [r["net_sd_pts"] for r in rows]
    median_sd = float(np.median(sds))
    by_sd = sorted(rows, key=lambda r: (r["net_sd_pts"], r["team_id"]))
    sharpest = by_sd[:5]
    widest = by_sd[-5:][::-1]
    by_net = sorted(rows, key=lambda r: (-r["net_pts"], r["team_id"]))
    top_net = by_net[:5]

    x_lo, x_hi, x_ticks, x_tick_labels = fitted_net_domain(
        [r["net_pts"] for r in rows]
    )
    y_lo, y_hi = 0.0, 100.0
    y_mid = 50.0  # median certainty percentile
    n_teams = len(rows)
    y_ticks, y_sd_raw, y_tick_labels = certainty_sd_tick_labels(sds)

    proven_good = [
        r for r in rows if r["net_pts"] >= 0.0 and r["y_pct"] >= y_mid
    ]

    xs = [r["x"] for r in rows]
    ys = [r["y_pct"] for r in rows]

    labels = ridge_week_labels(map_week=week, slate_week=slate_week, season=season)

    sds_sorted = sorted(sds)
    fifth_smallest = sds_sorted[min(4, len(sds_sorted) - 1)]
    fifth_largest = sds_sorted[max(0, len(sds_sorted) - 5)]
    sd_min = sds_sorted[0]
    sd_max = sds_sorted[-1]
    sd_min_whole = int(round(sd_min))
    sd_max_whole = int(round(sd_max))
    sharp_gap = median_sd - fifth_smallest
    wide_gap = fifth_largest - median_sd
    name_sharp = sharp_gap >= MIN_READ_GAP_PTS
    name_wide = wide_gap >= MIN_READ_GAP_PTS

    headline = f"How Ridge sees FBS entering week {week}"
    dek = "Right = stronger team · higher = Ridge more confident in that rating"
    assert len(dek) <= READMAP_DEK_MAX

    # End captions only — numeric ±pts live on the y tick labels.
    y_top_lines = ["Most sure"]
    y_bottom_lines = ["Least sure"]
    y_axis_title = "How sure Ridge is about each rating (± pts)"

    caption = trim_caption(
        readmap_caption(
            sharpest=sharpest,
            widest=widest,
            proven_good_count=len(proven_good),
            name_sharp=name_sharp,
            name_wide=name_wide,
            sd_min=sd_min,
            sd_max=sd_max,
        )
    )

    sharp_alt = ", ".join(
        f"{r['abbreviation']} (±{r['net_sd_pts']:.2f})" for r in sharpest
    )
    wide_alt = ", ".join(
        f"{r['abbreviation']} (±{r['net_sd_pts']:.2f})" for r in widest
    )
    top_alt = ", ".join(
        f"{r['abbreviation']} ({r['net_pts']:+.1f})" for r in top_net
    )
    alt_parts = [
        f"How Ridge sees FBS entering week {week}, {season}.",
        "X = Ridge rating (pts vs average FBS); "
        "Y = how sure Ridge is (certainty percentile).",
        UNCERTAINTY_FOOTNOTE,
        f"Top by rating: {top_alt}.",
        f"Sharpest reads: {sharp_alt}.",
        f"Widest reads: {wide_alt}.",
    ]
    if labels.ratings_through_note:
        alt_parts.append(labels.ratings_through_note)
    alt = " ".join(alt_parts)

    out: list[dict[str, Any]] = []
    for size in sizes:
        name, width, height = size_pixels(size)
        plot = filled_plot_box(
            canvas_w=width, canvas_h=height, size_name=name
        )
        obstacles = quadrant_obstacle_boxes(
            x_lo=x_lo,
            x_hi=x_hi,
            y_lo=y_lo,
            y_hi=y_hi,
            plot_w=float(plot["w"]),
            plot_h=float(plot["h"]),
        )
        points_out, slot_px, max_disp, worst_ov, scales, missing = _place_all_logos(
            rows=rows,
            xs=xs,
            ys=ys,
            x_lo=x_lo,
            x_hi=x_hi,
            y_lo=y_lo,
            y_hi=y_hi,
            plot_w=float(plot["w"]),
            plot_h=float(plot["h"]),
            logo_dir=logo_dir,
            theme=theme,
            obstacles=obstacles,
        )
        span_w, span_h = logo_span_fractions(
            [
                {
                    "logo_x": p["logo_x"],
                    "logo_y": p["logo_y"],
                    "width": p["width"],
                    "height": p["height"],
                }
                for p in points_out
            ],
            x_lo=x_lo,
            x_hi=x_hi,
            y_lo=y_lo,
            y_hi=y_hi,
        )
        for p in points_out:
            p["logo_w_data"] = p.pop("width")
            p["logo_h_data"] = p.pop("height")
            p["true_x"] = p["x"]
            p["true_y"] = p["y_pct"]

        out.append(
            {
                "chart": "readmap",
                "schema": "soc2-spec/1",
                "frame": frame_dict(
                    season=season,
                    week=week,
                    week_stamp=labels.through_line,
                    title=headline,
                    subtitle=dek,
                    theme=theme,
                    size_name=name,
                    width=width,
                    height=height,
                    published_at=published_at,
                    model_version=model_version,
                    vintage_label=vintage_label,
                    brand_mark=brand_mark,
                ),
                "plot": plot,
                "axes": {
                    "x_label": "Ridge rating (pts vs. average FBS team)",
                    "y_label": y_axis_title,
                    "x_min": x_lo,
                    "x_max": x_hi,
                    "y_min": y_lo,
                    "y_max": y_hi,
                    "y_inverted": False,
                    "x_zero": 0.0,
                    "y_mid": y_mid,
                    "zero_midline_label": "average",
                    "median_sd": None,
                    "y_tick_prefix": "±",
                    "y_tick_suffix": "",
                    "x_ticks": x_ticks,
                    "x_tick_labels": x_tick_labels,
                    "y_ticks": y_ticks,
                    "y_tick_labels": y_tick_labels,
                    "y_tick_sd_raw": y_sd_raw,
                    "y_top_lines": y_top_lines,
                    "y_bottom_lines": y_bottom_lines,
                    "zero_label": "0",
                },
                "quadrants": [
                    {
                        "id": "proven_good",
                        "label": "Proven good",
                        "x_sign": 1,
                        "y_side": "upper",
                        "placement": "outside",
                    },
                    {
                        "id": "good_proving",
                        "label": "Good, still proving it",
                        "x_sign": 1,
                        "y_side": "lower",
                        "placement": "outside",
                    },
                    {
                        "id": "known_struggles",
                        "label": "Known struggles",
                        "x_sign": -1,
                        "y_side": "upper",
                        "placement": "outside",
                    },
                    {
                        "id": "hard_to_read",
                        "label": "Hard to read",
                        "x_sign": -1,
                        "y_side": "lower",
                        "placement": "outside",
                    },
                ],
                "points": points_out,
                "highlights": {
                    "sharpest": [r["team_id"] for r in sharpest],
                    "widest": [r["team_id"] for r in widest],
                    "top_net": [r["team_id"] for r in top_net],
                    "proven_good_count": len(proven_good),
                },
                "footnotes": [],
                "caption": caption,
                "alt": alt,
                "callout": None,
                "meta": {
                    "margin_scale": scale.value,
                    "margin_scale_source": scale.source,
                    "median_sd": median_sd,
                    "n_fbs": n_teams,
                    "missing_logos": missing,
                    "dot_teams": [],
                    "logo_slot_px": slot_px,
                    "max_displacement_css_px": max_disp,
                    "worst_logo_overlap_frac": worst_ov,
                    "logo_span_width_frac": span_w,
                    "logo_span_height_frac": span_h,
                    "logo_span_width_below_target": span_w < 0.80,
                    "logo_span_height_below_target": span_h < 0.90,
                    "sd_min_whole": sd_min_whole,
                    "sd_max_whole": sd_max_whole,
                    "sd_spread": {
                        "min": sd_min,
                        "fifth_smallest": fifth_smallest,
                        "median": median_sd,
                        "fifth_largest": fifth_largest,
                        "max": sd_max,
                        "sharp_gap": sharp_gap,
                        "wide_gap": wide_gap,
                        "min_read_gap_pts": MIN_READ_GAP_PTS,
                        "name_sharp": name_sharp,
                        "name_sharp_in_caption": name_sharp,
                        "name_wide_in_caption": name_wide,
                    },
                },
            }
        )
        if scales:
            store_logo_scales_in_manifest(logo_dir, scales)
    return out
