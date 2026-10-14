"""Deterministic logo placement: repel + beeswarm (no randomness)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from matplotlib.text import Text


@dataclass(frozen=True)
class Box:
    """Axis-aligned bounding box in data (or display) coordinates."""

    x: float
    y: float
    w: float
    h: float
    team_id: int

    @property
    def left(self) -> float:
        return self.x - self.w / 2.0

    @property
    def right(self) -> float:
        return self.x + self.w / 2.0

    @property
    def bottom(self) -> float:
        return self.y - self.h / 2.0

    @property
    def top(self) -> float:
        return self.y + self.h / 2.0

    def overlap_area(self, other: Box) -> float:
        """Intersection area of two boxes."""
        dx = min(self.right, other.right) - max(self.left, other.left)
        dy = min(self.top, other.top) - max(self.bottom, other.bottom)
        if dx <= 0.0 or dy <= 0.0:
            return 0.0
        return dx * dy

    def area(self) -> float:
        return max(self.w, 0.0) * max(self.h, 0.0)


@dataclass(frozen=True)
class PlacedLogo:
    """Logo center after repel, with optional leader line back to the true point."""

    team_id: int
    true_x: float
    true_y: float
    logo_x: float
    logo_y: float
    width: float
    height: float
    draw_leader: bool


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def repel_logos(
    *,
    team_ids: list[int],
    xs: list[float],
    ys: list[float],
    widths: list[float],
    heights: list[float],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    iterations: int = 40,
    step: float = 0.15,
    max_disp_widths: float = 1.25,
    leader_threshold_widths: float = 0.4,
    overlap_allow_fraction: float = 0.0,
    obstacles: list[Box] | None = None,
) -> list[PlacedLogo]:
    """Iterative deterministic repel of logo bounding boxes.

    Ordering is stable by ``team_id``. Displacement is capped. Logos that end
    more than ``leader_threshold_widths`` logo-widths from their dot get a
    leader line. Pairwise overlaps at or below ``overlap_allow_fraction`` of
    the smaller box area are left alone.

    ``obstacles`` are fixed boxes (axis titles, quadrant labels, end labels)
    that logos must not cover — the logo is pushed away; the obstacle stays put.
    """
    if not (len(team_ids) == len(xs) == len(ys) == len(widths) == len(heights)):
        msg = "repel_logos: parallel arrays must have equal length"
        raise ValueError(msg)

    order = sorted(range(len(team_ids)), key=lambda i: team_ids[i])
    pos_x = np.array([xs[i] for i in order], dtype=float)
    pos_y = np.array([ys[i] for i in order], dtype=float)
    true_x = pos_x.copy()
    true_y = pos_y.copy()
    w = np.array([widths[i] for i in order], dtype=float)
    h = np.array([heights[i] for i in order], dtype=float)
    tids = [team_ids[i] for i in order]
    fixed = list(obstacles or [])

    for _ in range(iterations):
        for i in range(len(tids)):
            for j in range(i + 1, len(tids)):
                bi = Box(pos_x[i], pos_y[i], w[i], h[i], tids[i])
                bj = Box(pos_x[j], pos_y[j], w[j], h[j], tids[j])
                overlap = bi.overlap_area(bj)
                if overlap <= 0.0:
                    continue
                denom = min(bi.area(), bj.area())
                if denom > 0.0 and (overlap / denom) <= overlap_allow_fraction:
                    continue
                # Push apart along center delta; stable tie-break by team_id.
                dx = pos_x[j] - pos_x[i]
                dy = pos_y[j] - pos_y[i]
                dist = float(np.hypot(dx, dy))
                if dist < 1e-12:
                    dx = 1.0 if tids[i] < tids[j] else -1.0
                    dy = 0.0
                    dist = 1.0
                ux, uy = dx / dist, dy / dist
                push = step * np.sqrt(overlap)
                pos_x[i] -= ux * push * 0.5
                pos_y[i] -= uy * push * 0.5
                pos_x[j] += ux * push * 0.5
                pos_y[j] += uy * push * 0.5

            # Fixed text / chrome obstacles — push logo only.
            bi = Box(pos_x[i], pos_y[i], w[i], h[i], tids[i])
            for obs in fixed:
                overlap = bi.overlap_area(obs)
                if overlap <= 0.0:
                    continue
                dx = pos_x[i] - obs.x
                dy = pos_y[i] - obs.y
                dist = float(np.hypot(dx, dy))
                if dist < 1e-12:
                    dx, dy, dist = 0.0, 1.0, 1.0
                ux, uy = dx / dist, dy / dist
                push = step * np.sqrt(overlap)
                pos_x[i] += ux * push
                pos_y[i] += uy * push
                bi = Box(pos_x[i], pos_y[i], w[i], h[i], tids[i])

            # Cap displacement from true point.
            max_d = max_disp_widths * max(w[i], 1e-9)
            ddx = pos_x[i] - true_x[i]
            ddy = pos_y[i] - true_y[i]
            disp = float(np.hypot(ddx, ddy))
            if disp > max_d:
                scale = max_d / disp
                pos_x[i] = true_x[i] + ddx * scale
                pos_y[i] = true_y[i] + ddy * scale

            # Keep inside plot area.
            pos_x[i] = _clamp(pos_x[i], x_min + w[i] / 2.0, x_max - w[i] / 2.0)
            pos_y[i] = _clamp(pos_y[i], y_min + h[i] / 2.0, y_max - h[i] / 2.0)

    out: list[PlacedLogo] = []
    for i, tid in enumerate(tids):
        disp = float(np.hypot(pos_x[i] - true_x[i], pos_y[i] - true_y[i]))
        threshold = leader_threshold_widths * max(w[i], 1e-9)
        out.append(
            PlacedLogo(
                team_id=tid,
                true_x=float(true_x[i]),
                true_y=float(true_y[i]),
                logo_x=float(pos_x[i]),
                logo_y=float(pos_y[i]),
                width=float(w[i]),
                height=float(h[i]),
                draw_leader=disp > threshold,
            )
        )
    # Restore caller-facing order by original team_ids sequence stability:
    # return sorted by team_id (deterministic).
    return out


def max_pairwise_overlap_fraction(placed: list[PlacedLogo]) -> float:
    """Max overlap area as a fraction of the smaller logo's area."""
    worst = 0.0
    boxes = [
        Box(p.logo_x, p.logo_y, p.width, p.height, p.team_id) for p in placed
    ]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            overlap = boxes[i].overlap_area(boxes[j])
            denom = min(boxes[i].area(), boxes[j].area())
            if denom <= 0.0:
                continue
            worst = max(worst, overlap / denom)
    return worst


def beeswarm_offsets(
    *,
    xs: list[float],
    radii: list[float],
    team_ids: list[int],
    y_step: float | None = None,
) -> list[float]:
    """Symmetric deterministic beeswarm Y offsets so markers do not overlap.

    Points are processed in ascending ``team_id`` order for stability. For each
    point, candidate Y slots (0, ±step, ±2·step, …) are tried until no
    collision with already-placed points (circle diameter ≈ ``2 * radius`` in
    x/y coordinates where x is already in data space and y is offset space).
    """
    if not (len(xs) == len(radii) == len(team_ids)):
        msg = "beeswarm_offsets: parallel arrays must have equal length"
        raise ValueError(msg)

    order = sorted(range(len(team_ids)), key=lambda i: (xs[i], team_ids[i]))
    placed_x: list[float] = []
    placed_y: list[float] = []
    placed_r: list[float] = []
    ys_by_index = [0.0] * len(team_ids)

    for idx in order:
        x = xs[idx]
        r = max(radii[idx], 1e-9)
        step = y_step if y_step is not None else r * 1.15
        # Candidate offsets: 0, +1, -1, +2, -2, ...
        chosen = 0.0
        for k in range(0, 200):
            candidates = [0.0] if k == 0 else [k * step, -k * step]
            found = False
            for cand in candidates:
                ok = True
                for px, py, pr in zip(placed_x, placed_y, placed_r, strict=True):
                    dx = x - px
                    dy = cand - py
                    min_dist = r + pr
                    if dx * dx + dy * dy < (min_dist * 0.98) ** 2:
                        ok = False
                        break
                if ok:
                    chosen = cand
                    found = True
                    break
            if found:
                break
        placed_x.append(x)
        placed_y.append(chosen)
        placed_r.append(r)
        ys_by_index[idx] = chosen

    return ys_by_index


@dataclass(frozen=True)
class PlacedCallout:
    """Short callout label with connector anchor in plot/pixel space."""

    label: str
    anchor_x: float
    anchor_y: float
    label_x: float
    label_y: float
    side: str


def place_callout(
    *,
    label: str,
    anchor_x: float,
    anchor_y: float,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    prefer: str = "right",
    offset: float | None = None,
) -> PlacedCallout:
    """Deterministic callout placement (SOC-1-style preference order).

    Tries ``prefer`` first, then cycles through right/left/above/below,
    clamping the label center inside the plot box.
    """
    span = max(x_max - x_min, y_max - y_min, 1e-6)
    d = offset if offset is not None else span * 0.06
    order = {
        "right": ["right", "left", "above", "below"],
        "left": ["left", "right", "above", "below"],
        "above": ["above", "below", "right", "left"],
        "below": ["below", "above", "right", "left"],
    }.get(prefer, ["right", "left", "above", "below"])

    def _pos(side: str) -> tuple[float, float]:
        if side == "right":
            return anchor_x + d, anchor_y
        if side == "left":
            return anchor_x - d, anchor_y
        if side == "above":
            return anchor_x, anchor_y + d
        return anchor_x, anchor_y - d

    chosen = order[0]
    lx, ly = _pos(chosen)
    for side in order:
        cx, cy = _pos(side)
        if x_min + d * 0.5 <= cx <= x_max - d * 0.5 and y_min + d * 0.5 <= cy <= y_max - d * 0.5:
            chosen, lx, ly = side, cx, cy
            break
    lx = _clamp(lx, x_min, x_max)
    ly = _clamp(ly, y_min, y_max)
    return PlacedCallout(
        label=label,
        anchor_x=float(anchor_x),
        anchor_y=float(anchor_y),
        label_x=float(lx),
        label_y=float(ly),
        side=chosen,
    )


def assert_no_text_bbox_overlap(fig: Any, *, min_sep_px: float = 0.5) -> None:
    """Fail if any visible non-empty Text artists have overlapping window bboxes.

    Includes figure- and axes-level text (titles, labels, band/quadrant labels,
    footers). Tick labels are ignored (they may intentionally pack tightly).
    Fixture watermarks are excluded (diagonal overlay by design).
    """
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    texts: list[Text] = [t for t in fig.texts if isinstance(t, Text)]
    for ax in fig.axes:
        texts.extend(t for t in ax.texts if isinstance(t, Text))
        # Axis labels (not tick labels).
        for lab in (ax.xaxis.label, ax.yaxis.label):
            if isinstance(lab, Text):
                texts.append(lab)

    boxes: list[tuple[str, Any]] = []
    for t in texts:
        if not t.get_visible():
            continue
        s = (t.get_text() or "").strip()
        if not s:
            continue
        if s.startswith("FIXTURE"):
            continue
        bb = t.get_window_extent(renderer=renderer)
        # Shrink slightly so hairline touches do not fail.
        if min_sep_px > 0:
            bb = bb.padded(-min_sep_px)
        boxes.append((s, bb))

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a_txt, a_bb = boxes[i]
            b_txt, b_bb = boxes[j]
            if a_bb.overlaps(b_bb):
                msg = (
                    f"Text bounding boxes overlap: {a_txt!r} vs {b_txt!r} "
                    f"({a_bb} vs {b_bb})"
                )
                raise RuntimeError(msg)
