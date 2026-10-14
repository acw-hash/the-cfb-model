"""Invoke the Node / Playwright SOC-2 renderer."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
RENDER_DIR = REPO_ROOT / "social" / "render"
RENDER_ENTRY = RENDER_DIR / "src" / "cli.mjs"


class RenderError(RuntimeError):
    """Node renderer failed or is not installed."""


def _node_bin() -> str:
    node = shutil.which("node")
    if node is None:
        msg = (
            "Node.js is required for SOC-2 charts. Install Node 20+, then:\n"
            f"  cd {RENDER_DIR}\n"
            "  npm install\n"
            "  npx playwright install chromium"
        )
        raise RenderError(msg)
    return node


def ensure_render_ready() -> None:
    """Fail with install commands if node_modules or Playwright are missing."""
    _node_bin()
    if not (RENDER_DIR / "node_modules").is_dir():
        msg = (
            "SOC-2 renderer dependencies missing. Run:\n"
            f"  cd {RENDER_DIR}\n"
            "  npm install\n"
            "  npx playwright install chromium"
        )
        raise RenderError(msg)
    if not RENDER_ENTRY.is_file():
        msg = f"SOC-2 renderer entry missing: {RENDER_ENTRY}"
        raise RenderError(msg)


def render_spec(
    spec_path: Path,
    out_png: Path,
    *,
    no_lint: bool = False,
    final: bool = True,
) -> dict[str, Any]:
    """Render one spec JSON to a PNG via Node/Playwright."""
    ensure_render_ready()
    node = _node_bin()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        node,
        str(RENDER_ENTRY),
        "--spec",
        str(spec_path.resolve()),
        "--out",
        str(out_png.resolve()),
    ]
    if final:
        cmd.append("--final")
    if no_lint:
        cmd.append("--no-lint")
    proc = subprocess.run(
        cmd,
        cwd=str(RENDER_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if "playwright" in detail.lower() or "chromium" in detail.lower():
            detail += (
                f"\nInstall Chromium with:\n  cd {RENDER_DIR}\n  npx playwright install chromium"
            )
        msg = f"SOC-2 renderer failed ({proc.returncode}): {detail}"
        raise RenderError(msg)
    meta: dict[str, Any] = {}
    for line in (proc.stdout or "").splitlines():
        if line.startswith("{") and line.endswith("}"):
            with contextlib.suppress(json.JSONDecodeError):
                meta = json.loads(line)
    return meta


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "item"):
        return obj.item()
    msg = f"Object of type {type(obj).__name__} is not JSON serializable"
    raise TypeError(msg)


def write_spec(spec: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(spec, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return path


def collect_numbers(obj: Any, out: set[str] | None = None) -> set[str]:
    """Collect decimal/integer number strings present in a spec tree."""
    if out is None:
        out = set()
    if isinstance(obj, bool):
        return out
    if isinstance(obj, int):
        out.add(str(obj))
        return out
    if isinstance(obj, float):
        # Keep a few common renderings.
        out.add(f"{obj:g}")
        out.add(f"{obj:.1f}")
        out.add(f"{obj:.2f}")
        return out
    if isinstance(obj, str):
        return out
    if isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, out)
        return out
    if isinstance(obj, list):
        for v in obj:
            collect_numbers(v, out)
        return out
    return out
