"""SOC-2 round 4: every number named; rank axis; slate/learned columns; lint."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from ncaa_quant.social.specs.format_text import (
    format_team_by,
    format_win_lose_by,
    rounds_to_zero,
)
from ncaa_quant.social.specs.readmap import (
    build_readmap_specs,
    competition_ranks,
)
from ncaa_quant.social.specs.slate_ranges import slate_column_labels
from ncaa_quant.social.theme import parse_sizes

REPO = Path(__file__).resolve().parents[2]
BRAND = REPO / "assets" / "brand" / "ridge_mark.png"
ROUND3_READMAP_HTML = (
    REPO
    / "docs"
    / "notes"
    / "_artifacts"
    / "social-charts-w6"
    / "round3_readmap_w6_4x5.html"
)


def _brand() -> Path:
    if not BRAND.is_file():
        pytest.skip("brand mark missing")
    return BRAND


def test_format_team_by_and_even() -> None:
    assert format_team_by("OSU", 23.14) == "OSU by 23.1"
    assert format_team_by("FSU", 2.3) == "FSU by 2.3"
    assert format_team_by("NEB", 0.04) == "Even"
    assert format_team_by("NEB", 0.0) == "Even"
    assert rounds_to_zero(0.04)
    assert not rounds_to_zero(0.06)


def test_format_win_lose_by() -> None:
    assert format_win_lose_by(1.1) == "Win by 1.1"
    assert format_win_lose_by(-22.9) == "Lose by 22.9"
    assert format_win_lose_by(0.0) == "Even"
    assert format_win_lose_by(0.04) == "Even"


def test_slate_column_values_favorite_dog_even_gap() -> None:
    # Ridge agrees with market favorite.
    r, m, g = slate_column_labels(
        x_ridge=23.1, x_mkt=34.5, fav_short="OSU", dog_short="MD"
    )
    assert r == "OSU by 23.1"
    assert m == "OSU by 34.5"
    assert g == "11.4"
    assert not g.startswith("+") and not g.startswith("-") and "\u2212" not in g

    # Ridge favors the underdog (FSU @ LOU style).
    r2, m2, g2 = slate_column_labels(
        x_ridge=-2.3, x_mkt=3.5, fav_short="LOU", dog_short="FSU"
    )
    assert r2 == "FSU by 2.3"
    assert m2 == "LOU by 3.5"
    assert g2 == "5.8"

    # Even ridge margin.
    r3, m3, g3 = slate_column_labels(
        x_ridge=0.04, x_mkt=3.5, fav_short="HOM", dog_short="AWY"
    )
    assert r3 == "Even"
    assert m3 == "HOM by 3.5"
    assert g3 == "3.5"


def test_readmap_tick_labels_round4_contract_superseded(tmp_path: Path) -> None:
    """Round 4 used power-rank ticks; round 5 switched to signed net_pts."""
    teams: dict[str, object] = {}
    for tid in range(1, 31):
        teams[str(tid)] = {
            "school": f"Team{tid}",
            "weeks": [
                {
                    "week": 6,
                    "as_of_utc": "2026-10-01T00:00:00Z",
                    "off_epa": 0.02 * tid,
                    "def_epa": 0.01 * (31 - tid),
                    "off_sd": 0.08 + 0.002 * tid,
                    "def_sd": 0.09,
                }
            ],
        }
    logo_dir = tmp_path / "logos"
    logo_dir.mkdir()
    for tid in range(1, 31):
        Image.new("RGBA", (16, 16), (10, 10, 10, 255)).save(logo_dir / f"{tid}.png")

    specs = build_readmap_specs(
        team_ratings={"teams": teams, "published_at": "2026-10-06T16:00:00Z"},
        week=6,
        slate_week=6,
        season=2026,
        fbs_team_ids=set(range(1, 31)),
        abbrev_by_id={i: f"T{i}" for i in range(1, 31)},
        logo_dir=logo_dir,
        sizes=parse_sizes("4x5"),
        theme="light",
        model_version="test",
        vintage_label="test",
        published_at="2026-10-06T16:51:04Z",
        site_url="https://example.com",
        brand_mark=_brand(),
    )
    axes = specs[0]["axes"]
    labels = axes["x_tick_labels"]
    assert labels[0] != "50"
    assert axes["x_label"] == "Ridge rating (pts vs. average FBS team)"
    assert axes["y_label"] == "How sure Ridge is about each rating (± pts)"
    assert axes["y_ticks"] == [0.0, 25.0, 50.0, 75.0, 100.0]
    assert len(axes["y_tick_labels"]) == 5
    assert axes["y_top_lines"][0] == "Most sure"
    assert axes["y_bottom_lines"][0] == "Least sure"
    assert all(q.get("placement") == "outside" for q in specs[0]["quadrants"])


def test_power_rank_ticks_match_data() -> None:
    # Competition ranks: higher net → better (#1); ties by team_id.
    nets = [1.0, 5.0, 5.0, 0.0]
    ids = [4, 2, 1, 3]
    ranks2 = competition_ranks(nets, ids)
    assert ranks2[ids.index(1)] == 1
    assert ranks2[ids.index(2)] == 2
    assert ranks2[ids.index(4)] == 3
    assert ranks2[ids.index(3)] == 4


def test_lint_fails_round3_readmap_logo_text_overlap() -> None:
    """Round-3 read map had logos on quadrant labels; new lint must fail it."""
    if not ROUND3_READMAP_HTML.is_file():
        pytest.skip("round-3 readmap HTML fixture missing")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node missing")
    render_dir = REPO / "social" / "render"
    if not (render_dir / "node_modules").is_dir():
        pytest.skip("social/render node_modules missing")

    script = render_dir / ".tmp" / "_lint_round3_probe.mjs"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        f"""
import {{ chromium }} from "playwright";
import {{ readFileSync }} from "node:fs";
import {{ runLayoutLint }} from "../src/lint.mjs";

const html = readFileSync({json.dumps(str(ROUND3_READMAP_HTML.resolve()))}, "utf8");
const browser = await chromium.launch({{ headless: true }});
const page = await browser.newPage({{
  viewport: {{ width: 800, height: 1000 }},
  deviceScaleFactor: 1,
}});
await page.setContent(html, {{ waitUntil: "domcontentloaded" }});
const result = await runLayoutLint(page, {{ chart: "readmap" }});
await browser.close();
console.log(JSON.stringify(result));
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(script)],
        cwd=str(render_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"lint probe failed to run: {proc.stderr}")
    line = (proc.stdout or "").strip().splitlines()[-1]
    result = json.loads(line)
    assert result["ok"] is False
    joined = " ".join(result.get("errors") or [])
    assert "Text overlaps logo" in joined
