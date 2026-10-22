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


def test_fitted_net_domain_pads_and_includes_zero() -> None:
    from ncaa_quant.social.specs.readmap import fitted_net_domain

    lo, hi, ticks, labels = fitted_net_domain([-31.2, 18.4, 5.0])
    # 6% of range ≈ 2.976 → domain ≈ [-34.18, 21.38]; ticks nest inside.
    span = 18.4 - (-31.2)
    assert lo == pytest.approx(-31.2 - 0.06 * span)
    assert hi == pytest.approx(18.4 + 0.06 * span)
    assert lo <= 0.0 <= hi
    assert 0.0 in ticks
    assert 4 <= len(ticks) <= 6
    assert all(lo - 1e-9 <= t <= hi + 1e-9 for t in ticks)
    assert labels == [format_axis_tick(v) for v in ticks]
    assert all(lab == "0" or lab.startswith(("+", "\u2212")) for lab in labels)
    # Empty / all-positive / all-negative still include 0 and stay padded.
    e_lo, e_hi, e_ticks, _ = fitted_net_domain([])
    assert e_lo < 0 < e_hi and 0.0 in e_ticks
    p_lo, p_hi, p_ticks, _ = fitted_net_domain([5.0, 12.0, 20.0])
    assert p_lo == 0.0 and p_hi >= 20.0 and 0.0 in p_ticks
    n_lo, n_hi, n_ticks, _ = fitted_net_domain([-20.0, -8.0, -3.0])
    assert n_lo <= -20.0 and n_hi == 0.0 and 0.0 in n_ticks


def test_symmetric_net_domain_signed_whole_ticks() -> None:
    """Alias still returns the fitted (non-symmetric) domain."""
    lo, hi, ticks, labels = symmetric_net_domain([-31.2, 18.4, 5.0])
    assert lo <= 0.0 <= hi
    assert 0.0 in ticks
    assert labels == [format_axis_tick(v) for v in ticks]


def test_certainty_sd_tick_labels_match_quantiles() -> None:
    from ncaa_quant.social.specs.readmap import (
        certainty_sd_tick_labels,
        format_pm_sd,
        round_sd_half,
    )

    sds = [9.1, 9.6, 10.0, 10.4, 10.9, 11.2, 12.0]
    ticks, raw, labels = certainty_sd_tick_labels(sds)
    assert ticks == [0.0, 25.0, 50.0, 75.0, 100.0]
    import numpy as np

    expected = [float(np.percentile(sds, 100.0 - c)) for c in ticks]
    assert raw == pytest.approx(expected)
    # Default half-step formatting (no adjacent collision on this set).
    for v, lab in zip(raw, labels, strict=True):
        assert lab == format_pm_sd(v, one_decimal=False)
        assert lab.startswith("±")
    # Collision → force 1 decimal on the colliding adjacent pair.
    collide = [10.1, 10.2, 10.2, 10.2, 10.15]
    _t, raw2, labs2 = certainty_sd_tick_labels(collide)
    assert round_sd_half(raw2[0]) == round_sd_half(raw2[1])
    assert labs2[0] == f"±{raw2[0]:.1f}"
    assert labs2[1] == f"±{raw2[1]:.1f}"


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
    assert axes["y_label"] == "How sure Ridge is about each rating (± pts)"
    assert axes["x_zero"] == 0.0
    assert axes["zero_midline_label"] == "average"
    assert 4 <= len(axes["x_ticks"]) <= 6
    assert 0.0 in axes["x_ticks"]
    for lab in axes["x_tick_labels"]:
        assert lab == "0" or lab.startswith(("+", "\u2212"))
    assert axes["y_ticks"] == [0.0, 25.0, 50.0, 75.0, 100.0]
    assert len(axes["y_tick_labels"]) == 5
    assert all(lab.startswith("±") for lab in axes["y_tick_labels"])
    sds = [float(p["net_sd_pts"]) for p in spec["points"]]
    from ncaa_quant.social.specs.readmap import certainty_sd_tick_labels

    _t, _raw, expected_labs = certainty_sd_tick_labels(sds)
    assert axes["y_tick_labels"] == expected_labs
    assert axes["y_top_lines"] == ["Most sure"]
    assert axes["y_bottom_lines"] == ["Least sure"]
    assert "could be off" not in json.dumps(axes)
    assert "↑ more sure" not in json.dumps(axes)
    assert spec["frame"]["subtitle"] == (
        "Right = stronger team · higher = Ridge more confident in that rating"
    )
    assert all(q.get("placement") == "outside" for q in spec["quadrants"])
    plot = spec["plot"]
    # Non-square fill: height ≥ 70% of 4:5 canvas.
    assert float(plot["h"]) / float(spec["frame"]["height"]) >= 0.70
    assert float(plot["w"]) > float(plot["h"]) * 0.7  # not forced square
    assert float(plot["outer_left"]) == 0.0
    assert "top_net" in spec["highlights"]
    assert len(spec["highlights"]["top_net"]) == 5
    assert "Top by rating:" in spec["alt"]
    assert spec["meta"]["worst_logo_overlap_frac"] <= 0.08 + 1e-9
    assert 22 <= int(spec["meta"]["logo_slot_px"]) <= 28
    assert "logo_span_width_frac" in spec["meta"]
    assert "logo_span_height_frac" in spec["meta"]
    # X is net_pts (not a 0–100 percentile).
    xs = [float(p["true_x"]) for p in spec["points"]]
    assert min(xs) < 0 < max(xs) or max(abs(v) for v in xs) > 1.0


def test_readmap_packer_target_below_lint_limit() -> None:
    """Packer shrink target stays strictly below Playwright lint ceiling."""
    from ncaa_quant.social.specs.readmap import (
        _READMAP_LINT_OVERLAP_LIMIT,
        _READMAP_PACKER_OVERLAP_TARGET,
        _READMAP_SLOT_MIN,
    )

    assert _READMAP_SLOT_MIN == 22
    assert _READMAP_PACKER_OVERLAP_TARGET == 0.06
    assert _READMAP_LINT_OVERLAP_LIMIT == 0.08
    assert _READMAP_PACKER_OVERLAP_TARGET < _READMAP_LINT_OVERLAP_LIMIT


def test_readmap_shrink_treats_text_obstacle_as_trigger() -> None:
    """Logo shrink accepts a slot only when logos clear text keep-outs too."""
    from ncaa_quant.social.placement import Box
    from ncaa_quant.social.specs.readmap import (
        _READMAP_PACKER_OVERLAP_TARGET,
        _READMAP_SLOT_MIN,
        _logo_text_obstacle_hit,
        _overlap_fraction,
        _placement_clear,
    )

    assert _READMAP_SLOT_MIN == 22
    # Two non-overlapping logos; one sits inside a quadrant keep-out.
    state = {
        1: {
            "logo_x": 0.0,
            "logo_y": 0.0,
            "width": 0.1,
            "height": 0.1,
        },
        2: {
            "logo_x": 1.0,
            "logo_y": 1.0,
            "width": 0.1,
            "height": 0.1,
        },
    }
    obs = [Box(0.0, 0.0, 0.2, 0.2, -3)]  # covers team 1
    assert _overlap_fraction(state) <= _READMAP_PACKER_OVERLAP_TARGET
    assert _logo_text_obstacle_hit(state, obs) is True
    assert _placement_clear(state, obs) is False
    # Move team 1 clear of the keep-out → placement accepted.
    state[1]["logo_x"] = 0.5
    state[1]["logo_y"] = 0.5
    assert _logo_text_obstacle_hit(state, obs) is False
    assert _placement_clear(state, obs) is True


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
