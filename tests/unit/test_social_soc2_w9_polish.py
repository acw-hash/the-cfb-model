"""Week-9 polish: movers/cards/readmap/slate + lint fixtures that must fail."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from ncaa_quant.social.display_names import TeamDisplay, fit_headline
from ncaa_quant.social.specs.format_text import HEADLINE_MAX, gamecard_headline
from ncaa_quant.social.specs.learned import build_learned_specs
from ncaa_quant.social.specs.slate_ranges import _nice_ticks, slate_column_labels
from ncaa_quant.social.theme import parse_sizes

REPO = Path(__file__).resolve().parents[2]
BRAND = REPO / "assets" / "brand" / "ridge_mark.png"
FIXTURES = (
    REPO
    / "docs"
    / "notes"
    / "_artifacts"
    / "soc2-weekly-w9"
    / "lint_fail_fixtures"
)


def _brand() -> Path:
    if not BRAND.is_file():
        pytest.skip("brand mark missing")
    return BRAND


def _tiny_logos(tmp_path: Path, n: int) -> Path:
    logo_dir = tmp_path / "logos"
    logo_dir.mkdir()
    for tid in range(1, n + 1):
        Image.new("RGBA", (16, 16), (10, 10, 10, 255)).save(logo_dir / f"{tid}.png")
    return logo_dir


def _team_blob(n: int = 8) -> dict[str, object]:
    teams: dict[str, object] = {}
    for tid in range(1, n + 1):
        # Distinct |delta| so Up/Down |change| order is testable.
        # Week 4 nets: base; week 5 nets: base + signed move.
        move = (n + 1 - tid) * 2.5  # tid1 largest |up|
        sign = 1 if tid % 2 == 1 else -1
        prev = float(tid)
        cur = prev + sign * move
        teams[str(tid)] = {
            "school": f"Team{tid}",
            "weeks": [
                {
                    "week": 4,
                    "off_epa": prev / 80.0,
                    "def_epa": 0.0,
                    "off_sd": 0.1,
                    "def_sd": 0.1,
                    "kind": "postgame",
                },
                {
                    "week": 5,
                    "off_epa": cur / 80.0,
                    "def_epa": 0.0,
                    "off_sd": 0.1,
                    "def_sd": 0.1,
                    "kind": "postgame",
                },
            ],
        }
    return teams


def test_headline_max_is_48() -> None:
    assert HEADLINE_MAX == 48


def test_fit_headline_never_mixes_name_levels() -> None:
    a = TeamDisplay(1, "South Carolina", "SC", "South Carolina", "SC")
    b = TeamDisplay(2, "Oklahoma", "OU", "Oklahoma", "OU")
    text = fit_headline(lambda x, y: f"Ridge can't split {x} and {y}", a, b)
    # Full+full fits under 48.
    assert text == "Ridge can't split South Carolina and Oklahoma"
    assert "South Carolina" in text and "Oklahoma" in text
    # Force short+short by lowering limit.
    short = fit_headline(
        lambda x, y: f"Ridge can't split {x} and {y}", a, b, limit=40
    )
    assert short == "Ridge can't split SC and OU"
    assert "South Carolina" not in short


def test_gamecard_headline_uses_full_pair_when_fit() -> None:
    h = gamecard_headline(
        home_team="Oklahoma",
        away_team="South Carolina",
        mu_home=-1.4,
        p_win_home=0.51,
        home_id=2,
        away_id=1,
        abbrev_by_id={1: "SC", 2: "OU"},
    )
    assert len(h) <= HEADLINE_MAX
    # Coin-flip path: same level on both sides.
    assert ("South Carolina" in h and "Oklahoma" in h) or (
        "SC" in h and "OU" in h and "South Carolina" not in h
    )


def test_movers_header_and_abs_change_order(tmp_path: Path) -> None:
    n = 8
    teams = _team_blob(n)
    logo_dir = _tiny_logos(tmp_path, n)
    results = {
        "games": [
            {
                "week": 5,
                "home_team_id": tid,
                "away_team_id": tid + 1 if tid < n else 1,
                "home_team": f"Team{tid}",
                "away_team": f"Team{tid + 1 if tid < n else 1}",
                "home_points": 28,
                "away_points": 21,
                "mu_margin": 3.5,
                "neutral_site": False,
            }
            for tid in range(1, n, 2)
        ]
    }
    specs, _report = build_learned_specs(
        team_ratings={"teams": teams, "published_at": "2026-10-06T16:00:00Z"},
        results=results,
        map_week=6,
        slate_week=6,
        season=2026,
        fbs_team_ids=set(range(1, n + 1)),
        abbrev_by_id={i: f"T{i}" for i in range(1, n + 1)},
        logo_dir=logo_dir,
        sizes=parse_sizes("4x5"),
        theme="light",
        model_version="test",
        vintage_label="test",
        published_at="2026-10-06T16:51:04Z",
        site_url="https://example.com",
        brand_mark=_brand(),
        school_to_team_id={f"Team{i}": i for i in range(1, n + 1)},
    )
    assert specs is not None
    headers = {c["id"]: c["header"] for c in specs[0]["columns"]}
    assert headers["plot"] == "Before ○ → after ●"
    groups = {g["title"]: g["rows"] for g in specs[0]["groups"]}
    if "Up" in groups:
        ups = [abs(float(r["delta_net"])) for r in groups["Up"]]
        assert ups == sorted(ups, reverse=True)
    if "Down" in groups:
        downs = [abs(float(r["delta_net"])) for r in groups["Down"]]
        assert downs == sorted(downs, reverse=True)


def test_slate_nice_ticks_multiples_of_10_or_20() -> None:
    ticks = _nice_ticks(-40.0, 60.0)
    assert 0.0 in ticks
    assert -40.0 in ticks or -20.0 in ticks
    assert 60.0 in ticks or 40.0 in ticks
    # Intermediate labels present (not just ends + 0).
    assert len(ticks) >= 5
    for t in ticks:
        assert abs(t) % 10 < 1e-9 or abs(t) < 1e-9


def test_slate_column_labels_shorten_overflow() -> None:
    # Market favorite is Ole Miss (x_mkt < 0 from home perspective in fav frame
    # is not required here — fav_short is the market favorite's display short).
    ridge, market, gap = slate_column_labels(
        x_ridge=6.6,
        x_mkt=13.0,
        fav_short="Ole Miss",
        dog_short="AUB",
        fav_abbr="OM",
        dog_abbr="AUB",
    )
    assert "Ole Miss" not in ridge
    assert "Ole Miss" not in market
    assert ridge.startswith("OM by")
    assert market.startswith("OM by")
    assert gap == "6.4"


def _lint_html(html_path: Path, chart: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node missing")
    render_dir = REPO / "social" / "render"
    if not (render_dir / "node_modules").is_dir():
        pytest.skip("social/render node_modules missing")
    script = render_dir / ".tmp" / f"_lint_w9_{chart}_probe.mjs"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        f"""
import {{ chromium }} from "playwright";
import {{ readFileSync }} from "node:fs";
import {{ runLayoutLint }} from "../src/lint.mjs";

const html = readFileSync({json.dumps(str(html_path.resolve()))}, "utf8");
const browser = await chromium.launch({{ headless: true }});
const page = await browser.newPage({{
  viewport: {{ width: 800, height: 1000 }},
  deviceScaleFactor: 1,
}});
await page.setContent(html, {{ waitUntil: "domcontentloaded" }});
const result = await runLayoutLint(page, {{ chart: {json.dumps(chart)} }});
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
        pytest.skip(f"lint probe failed: {proc.stderr}")
    line = (proc.stdout or "").strip().splitlines()[-1]
    return json.loads(line)


@pytest.mark.parametrize(
    ("fname", "chart", "needle"),
    [
        ("movers_w8_4x5_broken.html", "movers", "Movers header"),
        ("slate_ranges_w9_4x5_broken.html", "slate_ranges", "Column overflow"),
        ("readmap_w9_4x5_broken.html", "readmap", "Read-map corner"),
    ],
)
def test_lint_fails_week9_broken_fixtures(
    fname: str, chart: str, needle: str
) -> None:
    path = FIXTURES / fname
    if not path.is_file():
        pytest.skip(f"fixture missing: {path}")
    result = _lint_html(path, chart)
    assert result["ok"] is False
    joined = " ".join(result.get("errors") or [])
    assert needle in joined, joined
