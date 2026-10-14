"""SOC-2 round 5: lint gaps, net_pts read map, movers rename, gamecard baselines."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from ncaa_quant.social.specs.format_text import format_axis_tick
from ncaa_quant.social.specs.learned import build_learned_specs
from ncaa_quant.social.specs.readmap import (
    build_readmap_specs,
    competition_ranks,
    symmetric_net_domain,
)
from ncaa_quant.social.theme import parse_sizes

REPO = Path(__file__).resolve().parents[2]
BRAND = REPO / "assets" / "brand" / "ridge_mark.png"
ROUND4_LINT = (
    REPO / "docs" / "notes" / "_artifacts" / "social-charts-w6" / "round4_lint"
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


def _team_blob(n: int = 20) -> dict[str, object]:
    teams: dict[str, object] = {}
    for tid in range(1, n + 1):
        teams[str(tid)] = {
            "school": f"Team{tid}",
            "weeks": [
                {
                    "week": 4,
                    "as_of_utc": "2026-09-21T00:00:00Z",
                    "off_epa": 0.01 * tid,
                    "def_epa": 0.005 * (n - tid),
                    "off_sd": 0.10,
                    "def_sd": 0.10,
                },
                {
                    "week": 5,
                    "as_of_utc": "2026-09-28T00:00:00Z",
                    "off_epa": 0.015 * tid,
                    "def_epa": 0.008 * (n - tid),
                    "off_sd": 0.09,
                    "def_sd": 0.09,
                },
                {
                    "week": 6,
                    "as_of_utc": "2026-10-01T00:00:00Z",
                    "off_epa": 0.02 * tid,
                    "def_epa": 0.01 * (n - tid),
                    "off_sd": 0.08 + 0.002 * tid,
                    "def_sd": 0.09,
                },
            ],
        }
    return teams


def test_symmetric_net_domain_signed_whole_ticks() -> None:
    lo, hi, ticks, labels = symmetric_net_domain([-31.2, 18.4, 5.0])
    assert lo == -hi
    assert len(ticks) == 5
    assert ticks[2] == 0.0
    assert labels == [format_axis_tick(v) for v in ticks]
    assert all(lab == "0" or lab.startswith(("+", "\u2212")) for lab in labels)
    assert "+" in "".join(labels) or "\u2212" in "".join(labels)


def test_readmap_net_pts_axes_and_centering(tmp_path: Path) -> None:
    n = 24
    teams = _team_blob(n)
    # Drop week 5 so this is a pure read-map fixture.
    for entry in teams.values():
        entry["weeks"] = [w for w in entry["weeks"] if w["week"] == 6]  # type: ignore[index]
    logo_dir = _tiny_logos(tmp_path, n)
    specs = build_readmap_specs(
        team_ratings={"teams": teams, "published_at": "2026-10-06T16:00:00Z"},
        week=6,
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
    )
    spec = specs[0]
    axes = spec["axes"]
    assert axes["x_label"] == "Ridge rating (pts vs. average FBS team)"
    assert axes["y_label"] == "How sure Ridge is about each rating"
    assert axes["x_zero"] == 0.0
    assert axes["zero_midline_label"] == "average"
    assert len(axes["x_ticks"]) == 5
    for lab in axes["x_tick_labels"]:
        assert lab == "0" or lab.startswith(("+", "\u2212"))
    sds = [float(p["net_sd_pts"]) for p in spec["points"]]
    sd_min = int(round(min(sds)))
    sd_max = int(round(max(sds)))
    assert axes["y_top_lines"] == ["Most sure", f"could be off ±{sd_min} pts"]
    assert axes["y_bottom_lines"] == ["Least sure", f"could be off ±{sd_max} pts"]
    assert "↑ more sure" not in json.dumps(axes)
    assert spec["frame"]["subtitle"] == (
        "Right = stronger team · higher = Ridge more confident in that rating"
    )
    assert all(q.get("placement") == "inside" for q in spec["quadrants"])
    plot = spec["plot"]
    assert abs(plot["w"] - plot["h"]) < 0.5
    assert abs(float(plot["outer_left"]) - float(plot["outer_right"])) <= 2.0
    assert "top_net" in spec["highlights"]
    assert len(spec["highlights"]["top_net"]) == 5
    assert "Top by rating:" in spec["alt"]
    assert spec["meta"]["worst_logo_overlap_frac"] <= 0.08 + 1e-9
    assert 12 <= int(spec["meta"]["logo_slot_px"]) <= 28
    # X is net_pts (not a 0–100 percentile).
    xs = [float(p["true_x"]) for p in spec["points"]]
    assert min(xs) < 0 < max(xs) or max(abs(v) for v in xs) > 1.0


def test_competition_ranks_still() -> None:
    nets = [1.0, 5.0, 5.0, 0.0]
    ids = [4, 2, 1, 3]
    ranks = competition_ranks(nets, ids)
    assert ranks[ids.index(1)] == 1
    assert ranks[ids.index(2)] == 2


def test_movers_rename_and_headers(tmp_path: Path) -> None:
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
    specs, report = build_learned_specs(
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
    assert report.get("filename_alias", {}).get("current") == "movers_w{N}_*"
    spec = specs[0]
    assert spec["chart"] == "movers"
    week = int(spec["frame"]["week"])
    assert spec["frame"]["title"] == f"Biggest movers after week {week}"
    assert spec["frame"]["subtitle"] == (
        f"Teams whose Ridge rating changed most after week {week} games"
    )
    headers = {c["id"]: c["header"] for c in spec["columns"]}
    assert headers["plot"] == "Ridge rating: before ○ → after ●"
    assert "Rating change" in headers["delta"]
    assert headers["expected"] == "Ridge expected"


def test_gamecard_name_and_winpct_baselines(tmp_path: Path) -> None:
    """Rendered game card: team names share a baseline; win % share a baseline."""
    from ncaa_quant.social.specs.gamecard import build_gamecard_specs
    from ncaa_quant.social.specs.render_invoke import write_spec

    node = shutil.which("node")
    if node is None:
        pytest.skip("node missing")
    if not (REPO / "social" / "render" / "node_modules").is_dir():
        pytest.skip("social/render node_modules missing")

    logo_dir = _tiny_logos(tmp_path, 2)
    games = [
        {
            "game_id": "gc-base",
            "home_team": "Home U",
            "away_team": "Away U",
            "home_team_id": 1,
            "away_team_id": 2,
            "mu_margin": 3.0,
            "sigma_margin": 18.0,
            "sigma_margin_credible": True,
            "p_win_home": 0.58,
            "conviction_tier": "toss_up",
            "conviction_label": "Toss-up",
            "margin_interval_lo": -10.0,
            "margin_interval_hi": 12.0,
            "margin_interval_nominal": 0.8,
            "kickoff_utc": "2026-10-04T16:00:00Z",
        }
    ]
    team_ratings = {
        "published_at": "2026-10-06T16:00:00Z",
        "teams": {
            str(tid): {
                "school": f"T{tid}",
                "weeks": [
                    {
                        "week": 6,
                        "as_of_utc": "2026-10-01T00:00:00Z",
                        "off_epa": 0.1,
                        "def_epa": 0.05,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                ],
            }
            for tid in (1, 2)
        },
    }
    specs, _report = build_gamecard_specs(
        game_ids=["gc-base"],
        week_predictions={"week": 6, "games": games},
        team_ratings=team_ratings,
        ratings_week=6,
        season=2026,
        week=6,
        logo_dir=logo_dir,
        sizes=parse_sizes("4x5"),
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://example.com",
        brand_mark=_brand(),
        abbrev_by_id={1: "HOM", 2: "AWY"},
        odds_snapshot={
            "week": 6,
            "snapshot_at": "2026-10-07T13:01:28Z",
            "games": [{"game_id": "gc-base", "market_home_margin": 3.0}],
        },
    )
    sp = write_spec(specs[0], tmp_path / "gc.json")
    render_dir = REPO / "social" / "render"
    script = render_dir / ".tmp" / "_gamecard_baseline_probe.mjs"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        f"""
import {{ readFileSync }} from "node:fs";
import {{ chromium }} from "playwright";
import {{ renderChartHtml }} from "../src/charts.mjs";
import {{ loadTokensCss }} from "../src/tokens.mjs";
const spec = JSON.parse(readFileSync({json.dumps(str(sp.resolve()))}, "utf8"));
const html = `<!doctype html><html><head><style>${{loadTokensCss()}}</style></head><body style="margin:0">${{renderChartHtml(spec)}}</body></html>`;
const browser = await chromium.launch({{ headless: true }});
const page = await browser.newPage({{ viewport: {{ width: 800, height: 1000 }} }});
await page.setContent(html, {{ waitUntil: "domcontentloaded" }});
const metrics = await page.evaluate(() => {{
  const midY = (sel) =>
    [...document.querySelectorAll(sel)].map((el) => {{
      const r = el.getBoundingClientRect();
      return r.top + r.height / 2;
    }});
  return {{ names: midY("[data-role='team-name']"), wins: midY("[data-role='win-pct']") }};
}});
await browser.close();
console.log(JSON.stringify(metrics));
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
    assert proc.returncode == 0, proc.stderr or proc.stdout
    metrics = json.loads(proc.stdout.strip().splitlines()[-1])
    assert len(metrics["names"]) == 2
    assert len(metrics["wins"]) == 2
    assert abs(metrics["names"][0] - metrics["names"][1]) <= 1.0
    assert abs(metrics["wins"][0] - metrics["wins"][1]) <= 1.0


def test_lint_round4_fixtures_fail_as_specified() -> None:
    learned_html = ROUND4_LINT / "learned_w5_4x5.html"
    readmap_html = ROUND4_LINT / "readmap_w6_4x5.html"
    if not learned_html.is_file() or not readmap_html.is_file():
        pytest.skip("round-4 lint fixtures missing")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node missing")
    render_dir = REPO / "social" / "render"
    if not (render_dir / "node_modules").is_dir():
        pytest.skip("social/render node_modules missing")
    probe = render_dir / "src" / "lint_round4_probe.mjs"
    proc = subprocess.run(
        [node, str(probe)],
        cwd=str(render_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    start = proc.stdout.find("{")
    end = proc.stdout.rfind("}")
    assert start >= 0 and end > start
    payload = json.loads(proc.stdout[start : end + 1])
    assert payload["learned"]["ok"] is False
    assert any("Truncated" in e for e in payload["learned"]["errors"])
    assert payload["readmap"]["ok"] is False
    joined = " ".join(payload["readmap"]["errors"])
    assert "Overlapping text" in joined or "Logo overlap" in joined
