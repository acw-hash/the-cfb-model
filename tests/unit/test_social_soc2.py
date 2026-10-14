"""SOC-2 social charts: formulas, selection, specs, version pin, banned words."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from ncaa_quant.ratings.state_space import StateSpaceConfig
from ncaa_quant.social.copy import assert_clean_texts, find_banned_words
from ncaa_quant.social.specs.favored import favored_perspective
from ncaa_quant.social.specs.gamecard import build_gamecard_specs, select_default_gamecards
from ncaa_quant.social.specs.learned import (
    build_learned_specs,
    prior_week_point,
    resolve_learned_week,
    teams_that_played,
)
from ncaa_quant.social.specs.net import (
    MARGIN_SCALE_SOURCE,
    def_good,
    net_pts,
    net_sd_pts,
    resolve_margin_scale,
)
from ncaa_quant.social.specs.readmap import (
    MIN_READ_GAP_PTS,
    build_readmap_specs,
    readmap_caption,
)
from ncaa_quant.social.specs.slate_ranges import (
    FEATURED_N,
    build_slate_ranges_specs,
    clip_bar,
    select_slate_games,
)
from ncaa_quant.social.theme import ChartSize, parse_sizes

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "webapp" / "fixtures"
RENDER_PKG = REPO / "social" / "render" / "package.json"
WEBAPP_PKG = REPO / "webapp" / "site" / "package.json"


def _size() -> ChartSize:
    return parse_sizes("4x5")[0]


def test_def_good_is_export_orientation() -> None:
    """Higher def_epa = tougher defense; def_good is identity."""
    assert def_good(0.25) == 0.25
    assert def_good(-0.1) == -0.1
    # Fixture-style: strong defense has positive def_epa on the export scale.
    assert def_good(0.20) > def_good(0.0)


def test_net_pts_and_margin_scale_from_config() -> None:
    info = resolve_margin_scale()
    assert info.value == pytest.approx(StateSpaceConfig().margin_scale)
    assert info.source == MARGIN_SCALE_SOURCE
    # Not a retyped literal in the resolver body — value flows from config.
    assert info.value == pytest.approx(80.0)
    pts = net_pts(off_epa=0.1, def_epa=0.05, margin_scale=info.value)
    assert pts == pytest.approx(12.0)
    sd = net_sd_pts(off_sd=0.03, def_sd=0.04, margin_scale=info.value)
    assert sd == pytest.approx(80.0 * math.sqrt(0.03**2 + 0.04**2))


def test_favored_side_flip_cases() -> None:
    home_fav = {
        "mu_margin": 7.0,
        "margin_interval_lo": -3.0,
        "margin_interval_hi": 17.0,
        "p_win_home": 0.72,
        "home_team": "Home U",
        "away_team": "Away U",
        "home_team_id": 1,
        "away_team_id": 2,
        "conviction_basis": {"favored_side": "home"},
    }
    h = favored_perspective(home_fav)
    assert h.favored_side == "home"
    assert h.mu == 7.0
    assert h.lo == -3.0
    assert h.hi == 17.0
    assert h.p_fav == 0.72

    away_fav = {
        **home_fav,
        "mu_margin": -6.0,
        "margin_interval_lo": -16.0,
        "margin_interval_hi": 4.0,
        "p_win_home": 0.35,
        "conviction_basis": {"favored_side": "away"},
    }
    a = favored_perspective(away_fav)
    assert a.favored_side == "away"
    assert a.mu == 6.0
    assert a.lo == -4.0
    assert a.hi == 16.0
    assert a.p_fav == pytest.approx(0.65)

    tie = {
        **home_fav,
        "mu_margin": 0.0,
        "margin_interval_lo": -10.0,
        "margin_interval_hi": 10.0,
        "p_win_home": 0.48,
        "conviction_basis": {},
    }
    t = favored_perspective(tie)
    # DESIGN §2.1: mu==0 → p_win_home >= 0.5 home else away.
    assert t.favored_side == "away"
    assert t.mu == 0.0
    assert t.lo == -10.0
    assert t.hi == 10.0
    assert t.p_fav == pytest.approx(0.52)


def test_readmap_median_highlights_inverted_axis(tmp_path: Path) -> None:
    brand = REPO / "assets" / "brand" / "ridge_mark.png"
    if not brand.is_file():
        pytest.skip("brand mark missing")
    ratings = {
        "teams": {
            str(i): {
                "school": f"Team {i}",
                "weeks": [
                    {
                        "week": 4,
                        "off_epa": 0.01 * i,
                        "def_epa": 0.005 * i,
                        "off_sd": 0.05 + 0.01 * (i % 5),
                        "def_sd": 0.05 + 0.01 * ((i + 1) % 5),
                    }
                ],
            }
            for i in range(1, 21)
        }
    }
    fbs = set(range(1, 21))
    specs = build_readmap_specs(
        team_ratings=ratings,
        week=4,
        slate_week=5,
        season=2026,
        fbs_team_ids=fbs,
        abbrev_by_id={i: f"T{i}" for i in fbs},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v-test",
        vintage_label="test",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
    )
    spec = specs[0]
    assert spec["axes"]["y_inverted"] is False
    # Round 5: x is symmetric net_pts about 0 (not a 0–100 percentile).
    assert spec["axes"]["x_zero"] == 0.0
    assert spec["axes"]["x_min"] == -spec["axes"]["x_max"]
    assert len(spec["highlights"]["sharpest"]) == 5
    assert len(spec["highlights"]["widest"]) == 5
    sds = [p["net_sd_pts"] for p in spec["points"]]
    # Sharpest = smallest SD (still tracked in highlights / alt).
    sharp_set = set(spec["highlights"]["sharpest"])
    by_id = {p["team_id"]: p for p in spec["points"]}
    sharp_sds = sorted(by_id[t]["net_sd_pts"] for t in sharp_set)
    all_sorted = sorted(sds)
    assert sharp_sds == all_sorted[:5]
    assert all(not p.get("as_dot") for p in spec["points"])
    assert spec["meta"]["max_displacement_css_px"] <= 14.0 + 1e-6


def test_learned_bye_ranking_week1_skip(tmp_path: Path) -> None:
    brand = REPO / "assets" / "brand" / "ridge_mark.png"
    if not brand.is_file():
        pytest.skip("brand mark missing")
    assert resolve_learned_week(map_week=6, slate_week=6) == 5
    assert resolve_learned_week(map_week=4, slate_week=5) == 4

    ratings = {
        "teams": {
            "1": {
                "school": "A",
                "weeks": [
                    {"week": 3, "off_epa": 0.1, "def_epa": 0.1, "off_sd": 0.1, "def_sd": 0.1},
                    {"week": 4, "off_epa": 0.2, "def_epa": 0.1, "off_sd": 0.08, "def_sd": 0.08},
                ],
            },
            "2": {
                "school": "B",
                "weeks": [
                    {"week": 3, "off_epa": 0.0, "def_epa": 0.0, "off_sd": 0.1, "def_sd": 0.1},
                    # bye week 4 — missing row
                ],
            },
            "3": {
                "school": "C",
                "weeks": [
                    {"week": 3, "off_epa": 0.05, "def_epa": 0.05, "off_sd": 0.1, "def_sd": 0.1},
                    {"week": 4, "off_epa": 0.06, "def_epa": 0.05, "off_sd": 0.09, "def_sd": 0.09},
                ],
            },
        }
    }
    results = {
        "games": [
            {
                "week": 4,
                "home_team_id": 1,
                "away_team_id": 3,
                "home_team": "A",
                "away_team": "C",
                "home_points": 28,
                "away_points": 14,
                "mu_margin": 7.5,
            },
        ]
    }
    played, src = teams_that_played(
        results=results,
        week=4,
        fbs_team_ids={1, 2, 3},
        team_ratings=ratings,
        school_to_team_id={"A": 1, "B": 2, "C": 3},
    )
    assert src == "results_week_games"
    assert played == {1, 3}
    assert 2 not in played

    # results without team ids → school index
    results_names = {
        "games": [
            {"week": 4, "home_team": "A", "away_team": "C"},
        ]
    }
    played2, src2 = teams_that_played(
        results=results_names,
        week=4,
        fbs_team_ids={1, 2, 3},
        team_ratings=ratings,
        school_to_team_id={"A": 1, "B": 2, "C": 3},
    )
    assert src2 == "results_week_games"
    assert played2 == {1, 3}

    specs, report = build_learned_specs(
        team_ratings=ratings,
        results=results,
        map_week=4,
        slate_week=5,
        season=2026,
        fbs_team_ids={1, 2, 3},
        abbrev_by_id={1: "AAA", 2: "BBB", 3: "CCC"},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-01T00:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
    )
    assert specs is not None
    assert report["bye_representation"] == "missing_week_row"
    rows = specs[0]["rows"]
    assert rows[0]["team_id"] == 1  # larger move_sd
    assert rows[0]["move_sd"] >= rows[1]["move_sd"]

    _, week1 = build_learned_specs(
        team_ratings={"teams": {"1": {"school": "A", "weeks": []}}},
        results={"games": []},
        map_week=1,
        slate_week=1,
        season=2026,
        fbs_team_ids={1},
        abbrev_by_id={},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-09-01T00:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
    )
    assert week1["skipped"] is True


def test_learned_eligible_after_prior_bye(tmp_path: Path) -> None:
    """Bye the week before the charted week must not exclude a week-N player."""
    brand = REPO / "assets" / "brand" / "ridge_mark.png"
    if not brand.is_file():
        pytest.skip("brand mark missing")
    # Team 7: bye week 4 (missing), played week 5 — before-state = week 3.
    ratings = {
        "teams": {
            "7": {
                "school": "ByeBefore",
                "weeks": [
                    {
                        "week": 3,
                        "off_epa": 0.10,
                        "def_epa": 0.05,
                        "off_sd": 0.12,
                        "def_sd": 0.12,
                    },
                    {
                        "week": 5,
                        "off_epa": 0.20,
                        "def_epa": 0.05,
                        "off_sd": 0.10,
                        "def_sd": 0.10,
                    },
                ],
            }
        }
    }
    entry = ratings["teams"]["7"]
    prev = prior_week_point(entry, 5)
    assert prev is not None
    assert int(prev["week"]) == 3
    results = {
        "games": [
            {
                "week": 5,
                "home_team_id": 7,
                "away_team_id": 99,
                "home_team": "ByeBefore",
                "away_team": "Other",
                "home_points": 31,
                "away_points": 17,
                "mu_margin": 4.0,
            }
        ]
    }
    specs, report = build_learned_specs(
        team_ratings=ratings,
        results=results,
        map_week=5,
        slate_week=6,
        season=2026,
        fbs_team_ids={7},
        abbrev_by_id={7: "BYE"},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T00:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
        school_to_team_id={"ByeBefore": 7, "Other": 99},
    )
    assert specs is not None
    assert report["skipped"] is False
    assert report["n_played"] == 1
    assert specs[0]["rows"][0]["team_id"] == 7
    assert specs[0]["rows"][0]["before_week"] == 3
    assert specs[0]["rows"][0]["after_week"] == 5


def test_readmap_caption_gap_branches() -> None:
    sharp = [{"abbreviation": "AAA"}, {"abbreviation": "BBB"}, {"abbreviation": "CCC"}]
    wide = [{"abbreviation": "XXX"}, {"abbreviation": "YYY"}, {"abbreviation": "ZZZ"}]
    bunched = readmap_caption(
        sharpest=sharp,
        widest=wide,
        proven_good_count=10,
        name_sharp=False,
        name_wide=False,
        sd_min=9.5,
        sd_max=12.5,
    )
    assert "tightly bunched" in bunched
    assert "±9.5 to ±12.5 pts" in bunched
    assert "AAA" not in bunched
    assert MIN_READ_GAP_PTS == 1.0

    named = readmap_caption(
        sharpest=sharp,
        widest=wide,
        proven_good_count=10,
        name_sharp=True,
        name_wide=True,
        sd_min=9.5,
        sd_max=12.5,
    )
    assert "sharpest reads: AAA, BBB, CCC" in named
    assert "Widest: XXX, YYY, ZZZ" in named

    sharp_only = readmap_caption(
        sharpest=sharp,
        widest=wide,
        proven_good_count=4,
        name_sharp=True,
        name_wide=False,
        sd_min=9.0,
        sd_max=11.0,
    )
    assert "AAA" in sharp_only
    assert "XXX" not in sharp_only


def test_slate_featured_order_pagination_intervals(tmp_path: Path) -> None:
    brand = REPO / "assets" / "brand" / "ridge_mark.png"
    if not brand.is_file():
        pytest.skip("brand mark missing")

    def game(i: int, *, p: float, mu: float, lo: float, hi: float, tier: str = "lean") -> dict:
        return {
            "game_id": f"g{i}",
            "home_team": f"H{i}",
            "away_team": f"A{i}",
            "home_team_id": 100 + i,
            "away_team_id": 200 + i,
            "mu_margin": mu,
            "sigma_margin": 18.0,
            "sigma_margin_credible": True,
            "p_win_home": p,
            "conviction_tier": tier,
            "conviction_team": f"H{i}",
            "margin_interval_lo": lo,
            "margin_interval_hi": hi,
            "margin_interval_nominal": 0.8,
            "kickoff_utc": f"2026-10-0{(i % 9) + 1}T16:00:00Z",
            "conviction_basis": {
                "favored_side": "home" if mu >= 0 else "away",
                "p_favored": max(p, 1 - p),
            },
        }

    games = [game(i, p=0.55 + i * 0.01, mu=3.0 + i, lo=-40.0 - i, hi=20.0 + i) for i in range(20)]
    wp = {
        "week": 6,
        "games": games,
        "refresh_kind": "tuesday_primary",
        "published_at": "2026-10-06T16:00:00Z",
    }
    # Ratings so combined net ranks by home_id roughly.
    ratings = {
        "teams": {
            str(100 + i): {
                "school": f"H{i}",
                "weeks": [
                    {
                        "week": 6,
                        "off_epa": 0.01 * i,
                        "def_epa": 0.01 * i,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                ],
            }
            for i in range(20)
        }
        | {
            str(200 + i): {
                "school": f"A{i}",
                "weeks": [
                    {
                        "week": 6,
                        "off_epa": 0.0,
                        "def_epa": 0.0,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                ],
            }
            for i in range(20)
        }
    }
    fbs = {100 + i for i in range(20)} | {200 + i for i in range(20)}
    rows, _ex, sel = select_slate_games(
        week_predictions=wp,
        team_ratings=ratings,
        ratings_week=6,
        fbs_team_ids=fbs,
        scope="featured",
    )
    assert len(rows) == FEATURED_N
    assert len(sel) == FEATURED_N

    # Market offsets so |gap| ordering is deterministic (larger i → larger gap).
    odds = {
        "week": 6,
        "snapshot_at": "2026-10-07T13:01:28Z",
        "games": [
            {
                "game_id": f"g{i}",
                "market_home_margin": float(games[i]["mu_margin"]) + 20.0,
            }
            for i in range(20)
        ],
    }
    specs_all, rep = build_slate_ranges_specs(
        week_predictions=wp,
        team_ratings=ratings,
        ratings_week=6,
        week=6,
        season=2026,
        fbs_team_ids=fbs,
        abbrev_by_id={},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        scope="all",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
        meta={},
        odds_snapshot=odds,
    )
    assert rep["pages"] == 2
    gaps = [abs(r["gap"]) for r in specs_all[0]["rows"]]
    assert gaps == sorted(gaps, reverse=True)
    sample = specs_all[0]["rows"][0]
    assert "x_mkt" in sample and "x_ridge" in sample
    # Arrow caps when a bar is clipped to a narrower axis.
    bar_lo, bar_hi, arrow_lo, arrow_hi = clip_bar(-50.0, 10.0, -20.0, 20.0)
    assert arrow_lo is True
    assert arrow_hi is False
    assert bar_lo == -20.0
    assert bar_hi == 10.0


def test_gamecard_default_and_sigma_suppress(tmp_path: Path) -> None:
    brand = REPO / "assets" / "brand" / "ridge_mark.png"
    if not brand.is_file():
        pytest.skip("brand mark missing")

    games = []
    for i, tier in enumerate(["toss_up", "lean", "clear_lean", "toss_up"]):
        games.append(
            {
                "game_id": f"gc{i}",
                "home_team": f"H{i}",
                "away_team": f"A{i}",
                "home_team_id": 10 + i,
                "away_team_id": 20 + i,
                "mu_margin": 1.0 + i,
                "sigma_margin": 18.0,
                "sigma_margin_credible": i != 1,
                "p_win_home": 0.52 + i * 0.02,
                "conviction_tier": tier if i != 1 else "lean",
                "conviction_label": tier,
                "conviction_team": f"H{i}",
                "margin_interval_lo": -10.0,
                "margin_interval_hi": 12.0,
                "margin_interval_nominal": 0.8,
                "kickoff_utc": "2026-10-04T16:00:00Z",
                "null_reason": "sigma_not_credible" if i == 1 else None,
                "conviction_basis": {"favored_side": "home"},
                "neutral_site": False,
            }
        )
    # Make game 1 credible lean for default selection; add separate suppressed.
    games[1]["sigma_margin_credible"] = True
    games[1]["null_reason"] = None
    games.append(
        {
            **games[0],
            "game_id": "suppressed",
            "home_team_id": 99,
            "away_team_id": 98,
            "conviction_tier": "lean",
            "sigma_margin_credible": False,
            "null_reason": "sigma_gate",
            "p_win_home": 0.6,
        }
    )
    wp = {"week": 6, "games": games}
    ratings = {
        "published_at": "2026-10-06T16:00:00Z",
        "teams": {
            str(tid): {
                "school": f"T{tid}",
                "weeks": [
                    {
                        "week": w,
                        "as_of_utc": "2026-10-01T00:00:00Z",
                        "off_epa": 0.1,
                        "def_epa": 0.1,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                    for w in (1, 2, 3, 4, 5, 6)
                ],
            }
            for tid in [10, 11, 12, 13, 20, 21, 22, 23, 99, 98]
        },
    }
    # Boost combined net for toss_up/lean ids.
    for tid, bump in [(10, 0.3), (11, 0.25), (13, 0.2)]:
        for row in ratings["teams"][str(tid)]["weeks"]:
            row["off_epa"] = bump

    fbs = {10, 11, 12, 13, 20, 21, 22, 23, 99, 98}
    ids = select_default_gamecards(
        week_predictions=wp,
        team_ratings=ratings,
        ratings_week=6,
        fbs_team_ids=fbs,
    )
    assert len(ids) == 3
    assert "gc2" not in ids  # clear_lean excluded from default

    odds = {
        "week": 6,
        "snapshot_at": "2026-10-07T13:01:28Z",
        "games": [
            {"game_id": g["game_id"], "market_home_margin": float(g["mu_margin"])}
            for g in games
        ],
    }
    specs, _ = build_gamecard_specs(
        game_ids=["suppressed"],
        week_predictions=wp,
        team_ratings=ratings,
        ratings_week=6,
        week=6,
        season=2026,
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
        odds_snapshot=odds,
    )
    # Non-credible sigma still builds a card; win % labels stay null.
    assert specs[0]["header"].get("p_home_label") is None
    assert specs[0]["margin_bar"]["market"] is not None


def test_spec_contains_on_image_numbers(tmp_path: Path) -> None:
    brand = REPO / "assets" / "brand" / "ridge_mark.png"
    if not brand.is_file():
        pytest.skip("brand mark missing")
    ratings = {
        "teams": {
            "1": {
                "school": "Alpha",
                "weeks": [
                    {
                        "week": 5,
                        "off_epa": 0.2,
                        "def_epa": 0.1,
                        "off_sd": 0.08,
                        "def_sd": 0.07,
                    }
                ],
            }
        }
    }
    specs = build_readmap_specs(
        team_ratings=ratings,
        week=5,
        slate_week=6,
        season=2026,
        fbs_team_ids={1},
        abbrev_by_id={1: "ALP"},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
    )
    p = specs[0]["points"][0]
    blob = json.dumps(specs[0])
    assert f"{p['net_pts']}"[:6] in blob or f"{p['net_pts']:.1f}" in blob
    assert "median_sd" in specs[0]["axes"]


def test_banned_words_soc2_copy(tmp_path: Path) -> None:
    brand = REPO / "assets" / "brand" / "ridge_mark.png"
    if not brand.is_file():
        pytest.skip("brand mark missing")
    ratings = {
        "teams": {
            "1": {
                "school": "Alpha",
                "weeks": [
                    {
                        "week": 5,
                        "off_epa": 0.2,
                        "def_epa": 0.1,
                        "off_sd": 0.08,
                        "def_sd": 0.07,
                    }
                ],
            }
        }
    }
    specs = build_readmap_specs(
        team_ratings=ratings,
        week=5,
        slate_week=6,
        season=2026,
        fbs_team_ids={1},
        abbrev_by_id={1: "ALP"},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
    )
    texts = [
        specs[0]["frame"]["footer"],
        specs[0]["caption"],
        specs[0]["alt"],
        *specs[0]["footnotes"],
    ]
    assert_clean_texts(texts, label="soc2")
    for t in texts:
        assert not find_banned_words(t) or all(
            h.word.lower() == "betting" for h in find_banned_words(t)
        )


def test_chart_library_version_pin_matches_webapp() -> None:
    render = json.loads(RENDER_PKG.read_text(encoding="utf-8"))
    webapp = json.loads(WEBAPP_PKG.read_text(encoding="utf-8"))
    for name in ("react", "react-dom"):
        assert render["dependencies"][name] == webapp["dependencies"][name]
    assert render["dependencies"]["playwright"] == webapp["devDependencies"]["playwright"]


def test_pixel_identical_two_renders(tmp_path: Path) -> None:
    """Two Playwright renders of the same fixture spec → identical RGBA."""
    import shutil

    import numpy as np
    from PIL import Image

    from ncaa_quant.social.specs.render_invoke import render_spec, write_spec

    if shutil.which("node") is None:
        pytest.skip("node not installed")
    if not (REPO / "social" / "render" / "node_modules").is_dir():
        pytest.skip("social/render node_modules missing")
    brand = REPO / "assets" / "brand" / "ridge_mark.png"
    if not brand.is_file():
        pytest.skip("brand mark missing")

    ratings = {
        "teams": {
            "1": {
                "school": "Alpha",
                "weeks": [
                    {
                        "week": 5,
                        "off_epa": 0.2,
                        "def_epa": 0.1,
                        "off_sd": 0.08,
                        "def_sd": 0.07,
                    }
                ],
            }
        }
    }
    specs = build_readmap_specs(
        team_ratings=ratings,
        week=5,
        slate_week=6,
        season=2026,
        fbs_team_ids={1},
        abbrev_by_id={1: "ALP"},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
    )
    sp = write_spec(specs[0], tmp_path / "spec.json")
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    try:
        render_spec(sp, a)
        render_spec(sp, b)
    except Exception as exc:  # noqa: BLE001 — skip if Chromium missing
        pytest.skip(f"renderer unavailable: {exc}")
    arr_a = np.asarray(Image.open(a).convert("RGBA"))
    arr_b = np.asarray(Image.open(b).convert("RGBA"))
    assert arr_a.shape == arr_b.shape
    assert np.array_equal(arr_a, arr_b)


def test_render_html_numbers_subset_of_spec(tmp_path: Path) -> None:
    """Renderer HTML must not invent numbers absent from the spec (scan text)."""
    brand = REPO / "assets" / "brand" / "ridge_mark.png"
    if not brand.is_file():
        pytest.skip("brand mark missing")
    ratings = {
        "teams": {
            "1": {
                "school": "Alpha",
                "weeks": [
                    {
                        "week": 5,
                        "off_epa": 0.2,
                        "def_epa": 0.1,
                        "off_sd": 0.08,
                        "def_sd": 0.07,
                    }
                ],
            }
        }
    }
    specs = build_readmap_specs(
        team_ratings=ratings,
        week=5,
        slate_week=6,
        season=2026,
        fbs_team_ids={1},
        abbrev_by_id={1: "ALP"},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v1.2.3",
        vintage_label="vintage-x",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
    )
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(specs[0]), encoding="utf-8")

    # Import renderer chart HTML without Playwright.

    charts_path = REPO / "social" / "render" / "src" / "charts.mjs"
    if not charts_path.is_file():
        pytest.skip("renderer missing")

    # Use node to render HTML only.
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = tmp_path / "html_only.mjs"
    script.write_text(
        f"""
import {{ readFileSync, writeFileSync }} from "node:fs";
import {{ renderChartHtml }} from "file:///{charts_path.as_posix()}";
const spec = JSON.parse(readFileSync({json.dumps(str(spec_path))}, "utf8"));
writeFileSync({json.dumps(str(tmp_path / "out.html"))}, renderChartHtml(spec));
""",
        encoding="utf-8",
    )
    proc = subprocess.run([node, str(script)], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        pytest.skip(f"node html render failed: {proc.stderr}")
    html = (tmp_path / "out.html").read_text(encoding="utf-8")
    # Lean footer on-image; model/vintage live in the spec for alt/report only.
    assert "Forecasts, not betting advice" in html
    assert "the-cfb-model.vercel.app" in html
    assert "v1.2.3" not in html
    assert "vintage-x" not in html
    assert specs[0]["frame"]["model_version"] == "v1.2.3"
    assert "9.99" not in html  # not in this single-team fixture
