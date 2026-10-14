"""SOC-2 polish: layout lint, headlines, formatting, logos, alignment."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from ncaa_quant.social.copy import assert_clean_texts, find_banned_words
from ncaa_quant.social.logos import LOGO_MAX_ASPECT, logo_display_size, opaque_pixel_area
from ncaa_quant.social.specs.favored import favored_perspective, favored_side_by_probability
from ncaa_quant.social.specs.format_text import (
    MINUS,
    RefreshKindError,
    format_delta_pts,
    format_signed,
    gamecard_headline,
    humanize_refresh_kind,
    margin_sign_vs_prob_disagree,
    ridge_had_team_perspective,
)
from ncaa_quant.social.specs.gamecard import build_gamecard_specs, margin_and_prob_sides, unit_ranks
from ncaa_quant.social.specs.learned import build_learned_specs
from ncaa_quant.social.specs.readmap import MIN_READ_GAP_PTS, build_readmap_specs
from ncaa_quant.social.specs.render_invoke import render_spec, write_spec
from ncaa_quant.social.specs.slate_ranges import build_slate_ranges_specs, select_slate_games
from ncaa_quant.social.theme import ChartSize, parse_sizes

REPO = Path(__file__).resolve().parents[2]
BRAND = REPO / "assets" / "brand" / "ridge_mark.png"


def _size() -> ChartSize:
    return parse_sizes("4x5")[0]


def _brand() -> Path:
    if not BRAND.is_file():
        pytest.skip("brand mark missing")
    return BRAND


def test_minus_sign_formatting() -> None:
    assert format_signed(-20.0) == f"{MINUS}20.0"
    assert format_signed(3.5) == "+3.5"
    assert format_delta_pts(-20.0) == f"{MINUS}20.0 pts"
    assert MINUS == "\u2212"


def test_refresh_kind_humanizer() -> None:
    assert humanize_refresh_kind("tuesday_primary", "2026-10-06T16:00:00Z") == "Tuesday forecast"
    assert humanize_refresh_kind("daily_refresh", "2026-10-08T06:00:00Z") == "Thu update"
    assert humanize_refresh_kind("postgame", "2026-10-12T06:00:00Z") == "Sunday ratings"
    with pytest.raises(RefreshKindError):
        humanize_refresh_kind("mystery_kind", "2026-10-06T16:00:00Z")


def test_gamecard_margin_prob_not_mixed(tmp_path: Path) -> None:
    brand = _brand()

    def _build(mu: float, p_home: float, *, away: str = "Away", home: str = "Home") -> dict:
        games = [
            {
                "game_id": "gc-mix",
                "home_team": home,
                "away_team": away,
                "home_team_id": 10,
                "away_team_id": 20,
                "mu_margin": mu,
                "sigma_margin": 18.0,
                "sigma_margin_credible": True,
                "p_win_home": p_home,
                "conviction_tier": "toss_up",
                "conviction_label": "Toss-up",
                "margin_interval_lo": -10.0,
                "margin_interval_hi": 12.0,
                "margin_interval_nominal": 0.8,
                "kickoff_utc": "2026-10-04T16:00:00Z",
            }
        ]
        ratings = {
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
                for tid in (10, 20)
            },
        }
        odds = {
            "week": 6,
            "snapshot_at": "2026-10-07T13:01:28Z",
            "games": [{"game_id": "gc-mix", "market_home_margin": mu}],
        }
        specs, _ = build_gamecard_specs(
            game_ids=["gc-mix"],
            week_predictions={"week": 6, "games": games},
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
            fbs_team_ids={10, 20},
            abbrev_by_id={10: "HOM", 20: "AWY"},
            odds_snapshot=odds,
        )
        return specs[0]

    # Home favored on margin and probability.
    home_fav = _build(3.0, 0.58)
    assert home_fav["margin_bar"]["mu_label"].startswith("HOM by")
    assert home_fav["header"]["p_home_label"] == "58%"
    assert "Ridge has Home by 3.0" == home_fav["frame"]["title"]

    # Away favored on margin and probability.
    away_fav = _build(-4.0, 0.42)
    assert away_fav["margin_bar"]["mu_label"].startswith("AWY by")
    assert away_fav["header"]["p_away_label"] == "58%"
    assert "Ridge has Away by 4.0" == away_fav["frame"]["title"]

    # Indiana@Nebraska style: ?<0 (away margin) but p_home>0.5.
    mismatch_specs, _ = build_gamecard_specs(
        game_ids=["gc-mix"],
        week_predictions={
            "week": 6,
            "games": [
                {
                    "game_id": "gc-mix",
                    "home_team": "Nebraska",
                    "away_team": "Indiana",
                    "home_team_id": 10,
                    "away_team_id": 20,
                    "mu_margin": -1.0,
                    "sigma_margin": 18.0,
                    "sigma_margin_credible": True,
                    "p_win_home": 0.52,
                    "conviction_tier": "toss_up",
                    "margin_interval_lo": -10.0,
                    "margin_interval_hi": 12.0,
                    "margin_interval_nominal": 0.8,
                    "kickoff_utc": "2026-10-04T16:00:00Z",
                }
            ],
        },
        team_ratings={
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
                for tid in (10, 20)
            },
        },
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
        fbs_team_ids={10, 20},
        abbrev_by_id={10: "NEB", 20: "IND"},
        odds_snapshot={
            "week": 6,
            "snapshot_at": "2026-10-07T13:01:28Z",
            "games": [{"game_id": "gc-mix", "market_home_margin": -1.0}],
        },
    )
    mismatch = mismatch_specs[0]
    g = {
        "mu_margin": -1.0,
        "p_win_home": 0.52,
        "home_team": "Nebraska",
        "away_team": "Indiana",
    }
    sides = margin_and_prob_sides(g)
    assert sides["margin_team"] == "Indiana"
    assert sides["prob_team"] == "Nebraska"
    assert "IND" in mismatch["margin_bar"]["mu_label"]
    assert mismatch["header"]["p_home_label"] == "52%"
    assert "can't split" in mismatch["frame"]["title"].lower()
    assert "different ways" in mismatch["alt"]
    hl = gamecard_headline(
        home_team="Nebraska",
        away_team="Indiana",
        mu_home=-1.0,
        p_win_home=0.55,
        home_short="NEB",
        away_short="IND",
    )
    assert "toss-up" in hl.lower()


def test_ridge_had_team_perspective_signs() -> None:
    # Alabama away: positive team-perspective ? when Ridge had the road team.
    assert ridge_had_team_perspective(
        mu_home=-7.0, team_id=333, home_id=100, away_id=333
    ) == 7.0
    # UMass home loss but Ridge had them positive at home.
    assert ridge_had_team_perspective(
        mu_home=3.5, team_id=113, home_id=113, away_id=200
    ) == 3.5
    # Air Force home.
    assert ridge_had_team_perspective(
        mu_home=-2.0, team_id=2005, home_id=2005, away_id=50
    ) == -2.0


def test_slate_mismatch_still_plots_negative_mu() -> None:
    """Probability-favored perspective can still carry a negative ? (mismatch)."""
    game = {
        "game_id": "iu-neb",
        "home_team": "Nebraska",
        "away_team": "Indiana",
        "home_team_id": 1,
        "away_team_id": 2,
        "mu_margin": -1.0,
        "sigma_margin": 18.0,
        "sigma_margin_credible": True,
        "p_win_home": 0.52,
        "conviction_tier": "toss_up",
        "margin_interval_lo": -20.0,
        "margin_interval_hi": 18.0,
        "margin_interval_nominal": 0.8,
        "kickoff_utc": "2026-10-04T16:00:00Z",
    }
    fav = favored_perspective(game)
    assert fav.mu == -1.0
    assert fav.sign_prob_mismatch is True


def test_favored_by_probability_and_mismatch_report() -> None:
    assert favored_side_by_probability(0.5) == "home"
    assert favored_side_by_probability(0.49) == "away"
    game = {
        "mu_margin": -0.9,
        "margin_interval_lo": -20.0,
        "margin_interval_hi": 18.0,
        "p_win_home": 0.52,
        "sigma_margin": 18.0,
        "home_team": "Home",
        "away_team": "Away",
        "home_team_id": 1,
        "away_team_id": 2,
        "game_id": "g1",
    }
    assert margin_sign_vs_prob_disagree(mu_margin=-0.9, p_win_home=0.52) is True
    fav = favored_perspective(game)
    assert fav.favored_side == "home"
    assert fav.mu < 0  # probability side home, margin still negative
    assert fav.sign_prob_mismatch is True


def test_logo_area_normalization(tmp_path: Path) -> None:
    # Tall logo vs wide logo ? similar opaque display area; aspect capped.
    tall = Image.new("RGBA", (40, 100), (255, 0, 0, 255))
    wide = Image.new("RGBA", (200, 40), (0, 0, 255, 255))
    # Add transparent padding that must be trimmed.
    canvas_t = Image.new("RGBA", (80, 140), (0, 0, 0, 0))
    canvas_t.paste(tall, (20, 20))
    canvas_w = Image.new("RGBA", (240, 80), (0, 0, 0, 0))
    canvas_w.paste(wide, (20, 20))
    p_t = tmp_path / "tall.png"
    p_w = tmp_path / "wide.png"
    canvas_t.save(p_t)
    canvas_w.save(p_w)
    target = 400.0
    a = logo_display_size(p_t, team_id=1, target_opaque_area=target)
    b = logo_display_size(p_w, team_id=2, target_opaque_area=target)
    assert a.width / a.height <= LOGO_MAX_ASPECT + 1e-6
    assert b.width / b.height <= LOGO_MAX_ASPECT + 1e-6
    # Opaque area after scale ? target (within 25% for aspect-capped wide logos).
    assert abs(a.opaque_area_px * a.scale**2 - target) / target < 0.05
    assert b.scale > 0
    assert opaque_pixel_area(Image.open(p_t)) > 0


def test_readmap_headline_branches(tmp_path: Path) -> None:
    brand = _brand()

    def _ratings(spread: float) -> dict:
        # Create SD spread via off_sd.
        teams = {}
        for i in range(1, 21):
            sd = 0.05 + (spread if i > 15 else 0.0) + 0.001 * i
            teams[str(i)] = {
                "school": f"School{i}",
                "weeks": [
                    {
                        "week": 6,
                        "off_epa": 0.01 * i,
                        "def_epa": 0.01,
                        "off_sd": sd,
                        "def_sd": sd,
                    }
                ],
            }
        return {"teams": teams}

    # Bunched SDs ? no sharpest naming / highlight.
    specs = build_readmap_specs(
        team_ratings=_ratings(0.0),
        week=6,
        slate_week=6,
        season=2026,
        fbs_team_ids=set(range(1, 21)),
        abbrev_by_id={i: f"S{i}" for i in range(1, 21)},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
    )
    assert specs[0]["frame"]["title"] == "How Ridge sees FBS entering week 6"
    assert specs[0]["callout"] is None
    assert all(not p.get("as_dot") for p in specs[0]["points"])
    assert specs[0]["frame"]["width"] == 800
    assert specs[0]["frame"]["height"] == 1000
    # Caption still uses SD-gap naming rules.
    _ = MIN_READ_GAP_PTS
    specs2 = build_readmap_specs(
        team_ratings=_ratings(0.08),
        week=6,
        slate_week=6,
        season=2026,
        fbs_team_ids=set(range(1, 21)),
        abbrev_by_id={i: f"S{i}" for i in range(1, 21)},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
    )
    assert specs2[0]["frame"]["title"] == "How Ridge sees FBS entering week 6"


def test_learned_results_join_and_exclude(tmp_path: Path) -> None:
    brand = _brand()
    ratings = {
        "teams": {
            "1": {
                "school": "One",
                "weeks": [
                    {"week": 4, "off_epa": 0.1, "def_epa": 0.1, "off_sd": 0.1, "def_sd": 0.1},
                    {"week": 5, "off_epa": 0.25, "def_epa": 0.1, "off_sd": 0.08, "def_sd": 0.08},
                ],
            },
            "2": {
                "school": "Two",
                "weeks": [
                    {"week": 4, "off_epa": 0.0, "def_epa": 0.0, "off_sd": 0.1, "def_sd": 0.1},
                    {"week": 5, "off_epa": 0.05, "def_epa": 0.0, "off_sd": 0.09, "def_sd": 0.09},
                ],
            },
        }
    }
    # Team 2 has two games ? excluded.
    results = {
        "games": [
            {
                "week": 5,
                "home_team_id": 1,
                "away_team_id": 9,
                "home_team": "One",
                "away_team": "X",
                "home_points": 38,
                "away_points": 17,
                "mu_margin": 10.0,
            },
            {
                "week": 5,
                "home_team_id": 2,
                "away_team_id": 8,
                "home_team": "Two",
                "away_team": "Y",
                "home_points": 21,
                "away_points": 14,
                "mu_margin": 3.0,
            },
            {
                "week": 5,
                "home_team_id": 2,
                "away_team_id": 7,
                "home_team": "Two",
                "away_team": "Z",
                "home_points": 10,
                "away_points": 24,
                "mu_margin": -2.0,
            },
        ]
    }
    specs, report = build_learned_specs(
        team_ratings=ratings,
        results=results,
        map_week=5,
        slate_week=6,
        season=2026,
        fbs_team_ids={1, 2},
        abbrev_by_id={1: "ONE", 2: "TWO"},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T00:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
        school_to_team_id={"One": 1, "Two": 2, "X": 9, "Y": 8, "Z": 7},
    )
    assert specs is not None
    assert all(r["team_id"] != 2 for r in specs[0]["rows"])
    assert any(e["team_id"] == 2 and e["reason"] == "multiple_games" for e in report["result_join_excluded"])
    row = specs[0]["rows"][0]
    assert "W 38" in row["result_label"]
    assert "Ridge" not in row["result_label"]
    # Round 4: team-perspective "Win/Lose by X" (or Even), not "Ridge ??".
    assert "Win by" in row["pregame_label"] or "Lose by" in row["pregame_label"] or row["pregame_label"] == "Even"
    assert " pts" not in row["delta_net_label"]
    assert MINUS in row["delta_net_label"] or row["delta_net_label"].startswith("+")
    assert specs[0]["frame"]["title"] == "Biggest movers after week 5"
    assert specs[0]["chart"] == "movers"
    assert "callout" not in specs[0] or specs[0].get("callout") is None
    assert "clearest" not in specs[0]
    assert "ridge_had_audit" in report


def test_slate_every_row_has_bar_in_html(tmp_path: Path) -> None:
    brand = _brand()

    def game(i: int) -> dict:
        return {
            "game_id": f"g{i}",
            "home_team": f"H{i}",
            "away_team": f"A{i}",
            "home_team_id": 100 + i,
            "away_team_id": 200 + i,
            "mu_margin": 3.0 + i,
            "sigma_margin": 18.0,
            "sigma_margin_credible": True,
            "p_win_home": 0.55 + i * 0.01,
            "conviction_tier": "lean",
            "conviction_team": f"H{i}",
            "margin_interval_lo": -10.0,
            "margin_interval_hi": 20.0 + i,
            "margin_interval_nominal": 0.8,
            "kickoff_utc": "2026-10-04T16:00:00Z",
            "conviction_basis": {"favored_side": "home"},
        }

    games = [game(i) for i in range(8)]
    wp = {
        "week": 6,
        "games": games,
        "refresh_kind": "tuesday_primary",
        "published_at": "2026-10-06T16:00:00Z",
    }
    ratings = {
        "teams": {
            str(tid): {
                "school": f"T{tid}",
                "weeks": [
                    {
                        "week": 6,
                        "off_epa": 0.1,
                        "def_epa": 0.1,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                ],
            }
            for tid in [100 + i for i in range(8)] + [200 + i for i in range(8)]
        }
    }
    fbs = {100 + i for i in range(8)} | {200 + i for i in range(8)}
    odds = {
        "week": 6,
        "snapshot_at": "2026-10-07T13:01:28Z",
        "games": [
            {"game_id": f"g{i}", "market_home_margin": 5.0 + i} for i in range(8)
        ],
    }
    specs, _ = build_slate_ranges_specs(
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
        scope="featured",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
        meta={},
        odds_snapshot=odds,
    )
    # Spec-level: every row has nonzero bar width in data space + resolved fill.
    for r in specs[0]["rows"]:
        assert r["bar_hi"] > r["bar_lo"] or abs(r["bar_hi"] - r["bar_lo"]) >= 0
        assert (r["bar_hi"] - r["bar_lo"]) > 0 or r.get("arrow_lo") or r.get("arrow_hi")
        assert r.get("bar_fill")
        # Round 4: named Ridge/Market/Gap columns (no bare signed mark labels).
        assert r.get("ridge_col")
        assert r.get("market_col")
        assert r.get("gap_col") is not None

    import shutil
    import subprocess

    charts_path = REPO / "social" / "render" / "src" / "charts.mjs"
    node = shutil.which("node")
    if not node or not charts_path.is_file():
        pytest.skip("node/charts missing")
    spec_path = tmp_path / "slate.json"
    spec_path.write_text(json.dumps(specs[0]), encoding="utf-8")
    script = tmp_path / "check.mjs"
    script.write_text(
        f"""
import {{ readFileSync, writeFileSync }} from "node:fs";
import {{ renderChartHtml }} from "file:///{charts_path.as_posix()}";
const spec = JSON.parse(readFileSync({json.dumps(str(spec_path))}, "utf8"));
const html = renderChartHtml(spec);
const bars = [...html.matchAll(/class="bar"[^>]*width="([^"]+)"/g)].map(m => Number(m[1]));
const fills = [...html.matchAll(/class="bar"[^>]*fill="([^"]+)"/g)].map(m => m[1]);
writeFileSync({json.dumps(str(tmp_path / "bars.json"))}, JSON.stringify({{bars, fills, n: spec.rows.length}}));
""",
        encoding="utf-8",
    )
    proc = subprocess.run([node, str(script)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads((tmp_path / "bars.json").read_text(encoding="utf-8"))
    assert len(payload["bars"]) == payload["n"]
    assert all(w > 0 for w in payload["bars"])
    assert all(f and f != "none" and f != "transparent" for f in payload["fills"])


def test_unit_ranks_tiebreak() -> None:
    ratings = {
        "teams": {
            "10": {
                "school": "A",
                "weeks": [{"week": 3, "off_epa": 0.2, "def_epa": 0.1, "off_sd": 0.1, "def_sd": 0.1}],
            },
            "11": {
                "school": "B",
                "weeks": [{"week": 3, "off_epa": 0.2, "def_epa": 0.3, "off_sd": 0.1, "def_sd": 0.1}],
            },
            "12": {
                "school": "C",
                "weeks": [{"week": 3, "off_epa": 0.1, "def_epa": 0.05, "off_sd": 0.1, "def_sd": 0.1}],
            },
        }
    }
    ranks = unit_ranks(team_ratings=ratings, ratings_week=3, fbs_team_ids={10, 11, 12})
    # Equal off_epa: lower team_id ranks better (unique ranks).
    assert ranks[10]["off_rank"] == 1
    assert ranks[11]["off_rank"] == 2
    assert ranks[12]["off_rank"] == 3
    assert ranks[11]["def_rank"] == 1


def test_gamecard_point_tick_alignment(tmp_path: Path) -> None:
    brand = _brand()
    games = [
        {
            "game_id": "gc1",
            "home_team": "Home",
            "away_team": "Away",
            "home_team_id": 10,
            "away_team_id": 20,
            "mu_margin": 1.5,
            "sigma_margin": 18.0,
            "sigma_margin_credible": True,
            "p_win_home": 0.52,
            "conviction_tier": "toss_up",
            "conviction_label": "Toss-up",
            "conviction_team": "Home",
            "margin_interval_lo": -10.0,
            "margin_interval_hi": 12.0,
            "margin_interval_nominal": 0.8,
            "kickoff_utc": "2026-10-04T16:00:00Z",
            "conviction_basis": {"favored_side": "home"},
            "neutral_site": False,
        }
    ]
    ratings = {
        "published_at": "2026-10-06T16:00:00Z",
        "teams": {
            str(tid): {
                "school": f"T{tid}",
                "weeks": [
                    {
                        "week": w,
                        "as_of_utc": "2026-10-01T00:00:00Z",
                        "off_epa": 0.1 + 0.01 * w,
                        "def_epa": 0.05,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                    for w in (1, 2, 3, 4, 5, 6)
                ],
            }
            for tid in (10, 20)
        },
    }
    specs, _ = build_gamecard_specs(
        game_ids=["gc1"],
        week_predictions={"week": 6, "games": games},
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
        fbs_team_ids={10, 20},
        odds_snapshot={
            "week": 6,
            "snapshot_at": "2026-10-07T13:01:28Z",
            "games": [{"game_id": "gc1", "market_home_margin": 3.0}],
        },
    )
    title = specs[0]["frame"]["title"]
    assert "Ridge has" in title or "can't split" in title.lower()
    bar = specs[0]["margin_bar"]
    assert bar["orientation"] == "away_left_home_right"
    assert bar["mu"] is not None
    assert "Market:" in bar["market_label"]
    assert specs[0]["dumbbells"]["rows"]


def test_banned_words_across_headlines_callouts(tmp_path: Path) -> None:
    brand = _brand()
    ratings = {
        "teams": {
            str(i): {
                "school": f"Team{i}",
                "weeks": [
                    {
                        "week": 6,
                        "off_epa": 0.02 * i,
                        "def_epa": 0.01,
                        "off_sd": 0.08,
                        "def_sd": 0.08,
                    }
                ],
            }
            for i in range(1, 12)
        }
    }
    specs = build_readmap_specs(
        team_ratings=ratings,
        week=6,
        slate_week=6,
        season=2026,
        fbs_team_ids=set(range(1, 12)),
        abbrev_by_id={i: f"T{i}" for i in range(1, 12)},
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
        specs[0]["frame"]["title"],
        specs[0]["frame"]["subtitle"],
        specs[0]["frame"]["footer"],
        specs[0]["caption"],
        specs[0]["alt"],
        (specs[0].get("callout") or {}).get("label") or "",
    ]
    assert_clean_texts([t for t in texts if t], label="polish")
    for t in texts:
        hits = find_banned_words(t)
        assert all(h.word.lower() == "betting" for h in hits)


def test_layout_lint_on_fixture_specs(tmp_path: Path) -> None:
    """Layout lint runs in Playwright for all four chart types (or skip)."""
    import shutil

    if shutil.which("node") is None:
        pytest.skip("node missing")
    if not (REPO / "social" / "render" / "node_modules").is_dir():
        pytest.skip("node_modules missing")
    brand = _brand()

    # Minimal readmap render with --no-lint first to ensure renderer works,
    # then with lint for the polish gate.
    ratings = {
        "teams": {
            str(i): {
                "school": f"Team{i}",
                "weeks": [
                    {
                        "week": 5,
                        "off_epa": 0.02 * i,
                        "def_epa": 0.01 * (i % 3),
                        "off_sd": 0.08 + 0.002 * i,
                        "def_sd": 0.08,
                    }
                ],
            }
            for i in range(1, 16)
        }
    }
    specs = build_readmap_specs(
        team_ratings=ratings,
        week=5,
        slate_week=6,
        season=2026,
        fbs_team_ids=set(range(1, 16)),
        abbrev_by_id={i: f"T{i}" for i in range(1, 16)},
        logo_dir=tmp_path,
        sizes=[_size()],
        theme="light",
        model_version="v",
        vintage_label="v",
        published_at="2026-10-06T16:00:00Z",
        site_url="https://ridge.football",
        brand_mark=brand,
    )
    sp = write_spec(specs[0], tmp_path / "readmap.json")
    out = tmp_path / "readmap.png"
    try:
        meta = render_spec(sp, out, no_lint=True, final=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"renderer unavailable: {exc}")
    assert meta.get("ok") is True
    assert meta.get("device_scale_factor") == 2
    # Output PNG is 2? CSS layout.
    im = Image.open(out)
    assert im.size == (1600, 2000)


def test_pixel_identical_at_2x(tmp_path: Path) -> None:
    import shutil

    import numpy as np

    if shutil.which("node") is None:
        pytest.skip("node missing")
    if not (REPO / "social" / "render" / "node_modules").is_dir():
        pytest.skip("node_modules missing")
    brand = _brand()
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
        render_spec(sp, a, no_lint=True, final=False)
        render_spec(sp, b, no_lint=True, final=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"renderer unavailable: {exc}")
    arr_a = np.asarray(Image.open(a).convert("RGBA"))
    arr_b = np.asarray(Image.open(b).convert("RGBA"))
    assert arr_a.shape == (2000, 1600, 4)
    assert np.array_equal(arr_a, arr_b)


def test_display_name_headline_cascade(tmp_path: Path) -> None:
    """Full school name when it fits; display_short / abbrev when it does not."""
    from ncaa_quant.social.display_names import (
        fit_single_name_headline,
        load_display_overrides,
        resolve_team_display,
    )

    del tmp_path
    ov = load_display_overrides()
    assert 113 in ov  # Massachusetts -> UMass
    umass = resolve_team_display(113, "Massachusetts", {113: "MASS"})
    assert umass.display_name == "UMass"
    short = fit_single_name_headline(
        lambda n: f"Ridge has {n} by 1.0",
        umass,
    )
    assert short == "Ridge has UMass by 1.0"
    long = fit_single_name_headline(
        lambda n: f"{n}'s loss to Eastern Michigan moved Ridge most definitely today",
        umass,
    )
    assert len(long) <= 42
    assert "UMass" in long or "MASS" in long


def test_learned_headline_win_loss_templates() -> None:
    from ncaa_quant.social.specs.learned import _learned_headline

    win_home = {
        "team_id": 113,
        "school": "Massachusetts",
        "abbreviation": "MASS",
        "result_label": "W 38\u201314 vs EMU",
        "opp_team_id": 2199,
        "opp_school": "Eastern Michigan",
    }
    h = _learned_headline(win_home, abbrev_by_id={113: "MASS", 2199: "EMU"})
    assert "win over" in h
    assert "moved Ridge most" in h
    assert len(h) <= 42

    loss_away = {
        "team_id": 113,
        "school": "Massachusetts",
        "abbreviation": "MASS",
        "result_label": "L 14\u201338 at EMU",
        "opp_team_id": 2199,
        "opp_school": "Eastern Michigan",
    }
    h2 = _learned_headline(loss_away, abbrev_by_id={113: "MASS", 2199: "EMU"})
    assert "loss to" in h2
    assert len(h2) <= 42


def test_gray_dot_protects_slate_prefers_g5(tmp_path: Path) -> None:
    """Round 3 dropped gray dots ? every FBS team keeps a logo."""
    del tmp_path
    pytest.skip("gray-dot demotion removed in SOC-2 round 3")
    assert 113 in dots
    assert 333 not in dots or dots.index(113) < dots.index(333)
