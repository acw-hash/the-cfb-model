"""Tile SOC-2 4:5 PNGs into a single contact sheet."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image


def write_contact_sheet(
    png_paths: list[Path],
    *,
    out_path: Path,
    cols: int = 3,
    pad: int = 16,
    bg: tuple[int, int, int] = (245, 245, 247),
) -> Path | None:
    """Write a contact sheet of 4:5 PNGs. Returns path or None if empty."""
    paths = [p for p in png_paths if p.is_file() and "4x5" in p.stem]
    if not paths:
        return None
    images = [Image.open(p).convert("RGBA") for p in paths]
    cell_w = max(im.width for im in images)
    cell_h = max(im.height for im in images)
    n = len(images)
    rows = int(math.ceil(n / cols))
    sheet_w = cols * cell_w + (cols + 1) * pad
    sheet_h = rows * cell_h + (rows + 1) * pad
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (*bg, 255))
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        x = pad + c * (cell_w + pad) + (cell_w - im.width) // 2
        y = pad + r * (cell_h + pad) + (cell_h - im.height) // 2
        sheet.paste(im, (x, y), im)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic-ish write: some Windows viewers lock the destination PNG.
    tmp = out_path.with_suffix(".tmp.png")
    sheet.convert("RGB").save(tmp, format="PNG")
    tmp.replace(out_path)
    return out_path
