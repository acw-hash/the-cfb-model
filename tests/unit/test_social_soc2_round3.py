"""SOC-2 round 3: market perspective, simplified cards, percentile read map."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ncaa_quant.social.charts.ridge_map import rank_percentiles
from ncaa_quant.social.copy import assert_clean_texts, find_banned_words
from ncaa_quant.social.specs.market import (
    MarketSnapshotError,
    load_market_by_game,
    prove_market_sign_convention,
    require_week_market_snapshot,
    to_market_favorite_perspective,
)
from ncaa_quant.social.specs.readmap import build_readmap_specs
from ncaa_quant.social.specs.slate_ranges import apply_market_and_sort, select_slate_games
from ncaa_quant.social.theme import parse_sizes

REPO = Path(__file__).resolve().parents[2]
BRAND = REPO / "assets" / "brand" / "ridge_mark.png"


def _brand() -> Path:
    if not BRAND.is_file():
        pytest.skip("brand mark missing")
    return BRAND


def test_market_sign_home_and_away_favorites() -> None:
    # Home favorite: book home −34.5 → market expects home by +34.5
    prove_market_sign_convention(spread_home_points=-34.5, market_home_margin=34.5)
    # Away favorite: book home +13.5 → market expects home by −13.5
    prove_market_sign_convention(spread_home_points=13.5, market_home_margin=-13.5)
    with pytest.raises(MarketSnapshotError):
        prove_market_sign_convention(spread_home_points=-3.0, market_home_margin=-3.0)


def test_market_favorite_perspective_conversion() -> None:
    # Home is market favorite by 34.5; Ridge has home by 23.1
    home = to_market_favorite_perspective(
        mkt_home=34.5,
        mu_home=23.1,
        lo_home=5.0,
        hi_home=40.0,
        p_win_home=0.90,
        home_team="Ohio State",
        away_team="Maryland",
        home_team_id=194,
        away_team_id=120,
    )
    assert home.favored_side == "home"
    assert home.x_mkt == 34.5
    assert home.x_ridge == pytest.approx(23.1)
    assert home.lo == pytest.approx(5.0)
    assert home.hi == pytest.approx(40.0)
    assert home.p_fav == pytest.approx(0.90)
    assert home.gap == pytest.approx(23.1 - 34.5)
    assert home.leans_team == "Maryland"
    assert "Maryland" in home.leans_label

    # Away is market favorite by 13.5 (mkt_home = −13.5)
    away = to_market_favorite_perspective(
        mkt_home=-13.5,
        mu_home=-10.7,
        lo_home=-30.0,
        hi_home=5.0,
        p_win_home=0.30,
        home_team="Arkansas",
        away_team="Tennessee",
        home_team_id=8,
        away_team_id=2633,
    )
    assert away.favored_side == "away"
    assert away.x_mkt == 13.5
    assert away.x_ridge == pytest.approx(10.7)
    assert away.lo == pytest.approx(-5.0)
    assert away.hi == pytest.approx(30.0)
    assert away.p_fav == pytest.approx(0.70)


def test_gap_dash_when_under_one() -> None:
    near = to_market_favorite_perspective(
        mkt_home=7.0,
        mu_home=7.4,
        lo_home=-5.0,
        hi_home=20.0,
        p_win_home=0.65,
        home_team="Home",
        away_team="Away",
        home_team_id=1,
        away_team_id=2,
    )
    assert near.leans_label == "—"
    assert near.leans_team is None


def test_slate_sort_and_missing_market() -> None:
    rows = [
        {
            "game": {
                "game_id": "g1",
                "home_team": "H1",
                "away_team": "A1",
                "home_team_id": 1,
                "away_team_id": 2,
                "mu_margin": 10.0,
                "margin_interval_lo": 0.0,
                "margin_interval_hi": 20.0,
                "p_win_home": 0.7,
                "kickoff_utc": "2026-10-11T16:00:00Z",
            },
            "combined_net": 1.0,
        },
        {
            "game": {
                "game_id": "g2",
                "home_team": "H2",
                "away_team": "A2",
                "home_team_id": 3,
                "away_team_id": 4,
                "mu_margin": 5.0,
                "margin_interval_lo": -5.0,
                "margin_interval_hi": 15.0,
                "p_win_home": 0.6,
                "kickoff_utc": "2026-10-11T19:00:00Z",
            },
            "combined_net": 2.0,
        },
        {
            "game": {
                "game_id": "g3",
                "home_team": "H3",
                "away_team": "A3",
                "home_team_id": 5,
                "away_team_id": 6,
                "mu_margin": 3.0,
                "margin_interval_lo": -10.0,
                "margin_interval_hi": 10.0,
                "p_win_home": 0.55,
                "kickoff_utc": "2026-10-11T12:00:00Z",
            },
            "combined_net": 0.5,
        },
    ]
    market = {"g1": 20.0, "g2": 6.0}  # g3 missing
    kept, missing = apply_market_and_sort(
        rows, market_by_game=market, abbrev_by_id={}
    )
    assert [r["game"]["game_id"] for r in kept] == ["g1", "g2"]
    assert kept[0]["abs_gap"] > kept[1]["abs_gap"]
    assert len(missing) == 1
    assert missing[0]["game_id"] == "g3"
    assert missing[0]["reason"] == "no_market_snapshot"


def test_require_week_market_snapshot() -> None:
    with pytest.raises(MarketSnapshotError):
        require_week_market_snapshot(None, week=6)
    snap = {
        "week": 6,
        "snapshot_at": "2026-10-07T13:01:28Z",
        "games": [{"game_id": "1", "market_home_margin": 3.5}],
    }
    assert require_week_market_snapshot(snap, week=6) is snap
    with pytest.raises(MarketSnapshotError):
        require_week_market_snapshot(snap, week=5)


def test_gamecard_margin_bar_away_left() -> None:
    """Home-perspective μ: negative = away favored = left of 0."""
    from ncaa_quant.social.specs.gamecard import build_gamecard_specs

    odds = {
        "week": 6,
        "snapshot_at": "2026-10-07T13:01:28Z",
        "games": [{"game_id": "gc1", "market_home_margin": -3.0}],
    }
    games = [
        {
            "game_id": "gc1",
            "home_team": "Home",
            "away_team": "Away",
            "home_team_id": 10,
            "away_team_id": 20,
            "mu_margin": -8.0,
            "sigma_margin": 18.0,
            "sigma_margin_credible": True,
            "p_win_home": 0.35,
            "conviction_tier": "lean",
            "conviction_label": "Lean",
            "margin_interval_lo": -25.0,
            "margin_interval_hi": 10.0,
            "margin_interval_nominal": 0.8,
            "kickoff_utc": "2026-10-11T16:00:00Z",
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
                        "off_epa": 0.2 if tid == 20 else 0.05,
                        "def_epa": 0.1,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                ],
            }
            for tid in (10, 20)
        },
    }
    specs, _ = build_gamecard_specs(
        game_ids=["gc1"],
        week_predictions={"games": games},
        team_ratings=ratings,
        ratings_week=6,
        week=6,
        season=2026,
        logo_dir=REPO / "data" / "social" / "logos",
        sizes=parse_sizes("4x5"),
        theme="light",
        model_version="test",
        vintage_label="test",
        published_at="2026-10-06T16:51:04Z",
        site_url="https://example.com",
        brand_mark=_brand(),
        fbs_team_ids={10, 20},
        abbrev_by_id={10: "HOM", 20: "AWY"},
        odds_snapshot=odds,
    )
    bar = specs[0]["margin_bar"]
    assert bar["orientation"] == "away_left_home_right"
    assert bar["mu"] == pytest.approx(-8.0)
    assert bar["market"] == pytest.approx(-3.0)
    assert bar["mu"] < 0  # away side (left)
    assert "Market:" in bar["market_label"]
    # Market label is a separate below-bar field (renderer places it under the bar).
    assert bar["market_label"] != bar["mu_label"]


def test_readmap_percentiles_ties_and_all_logos(tmp_path: Path) -> None:
    # Ties → average ranks.
    pcts = rank_percentiles(np.array([1.0, 2.0, 2.0, 4.0]))
    assert pcts[1] == pytest.approx(pcts[2])

    teams: dict[str, object] = {}
    for tid in range(1, 21):
        teams[str(tid)] = {
            "school": f"Team{tid}",
            "weeks": [
                {
                    "week": 6,
                    "as_of_utc": "2026-10-01T00:00:00Z",
                    "off_epa": 0.01 * tid,
                    "def_epa": 0.01 * (21 - tid),
                    "off_sd": 0.10 + 0.001 * tid,
                    "def_sd": 0.10,
                }
            ],
        }
    # Tiny 1×1 PNG so logo_display_size can open a file.
    from PIL import Image

    logo_dir = tmp_path / "logos"
    logo_dir.mkdir()
    for tid in range(1, 21):
        Image.new("RGBA", (16, 16), (10, 10, 10, 255)).save(logo_dir / f"{tid}.png")

    specs = build_readmap_specs(
        team_ratings={"teams": teams, "published_at": "2026-10-06T16:00:00Z"},
        week=6,
        slate_week=6,
        season=2026,
        fbs_team_ids=set(range(1, 21)),
        abbrev_by_id={i: f"T{i}" for i in range(1, 21)},
        logo_dir=logo_dir,
        sizes=parse_sizes("4x5"),
        theme="light",
        model_version="test",
        vintage_label="test",
        published_at="2026-10-06T16:51:04Z",
        site_url="https://example.com",
        brand_mark=_brand(),
    )
    pts = specs[0]["points"]
    assert len(pts) == 20
    assert all(not p.get("as_dot") for p in pts)
    assert all(p.get("logo_path") for p in pts)
    assert specs[0]["meta"]["max_displacement_css_px"] <= 36.0 + 1e-6
    assert specs[0]["meta"]["worst_logo_overlap_frac"] <= 0.08 + 1e-6
    assert specs[0]["frame"]["title"].startswith("How Ridge sees FBS entering week")
    assert specs[0]["callout"] is None
    # Round 5: x = net_pts; y = certainty percentile.
    xs = [p["true_x"] for p in pts]
    ys = [p["y_pct"] for p in pts]
    assert min(ys) >= 0 and max(ys) <= 100
    assert any(v != 0 for v in xs)


def test_banned_words_allow_market() -> None:
    texts = [
        "Ridge vs. the market · week 6",
        "Dot = Ridge · ◇ = market · sorted by gap (pts)",
        "Market: OSU by 34.5",
        "Ridge · Forecasts, not betting advice · the-cfb-model.vercel.app",
    ]
    assert_clean_texts(texts, label="round3")
    assert not find_banned_words("market projection for the favorite")
    assert find_banned_words("big edge on the spread")


def test_load_market_by_game() -> None:
    snap = {
        "games": [
            {"game_id": "a", "market_home_margin": 3.5},
            {"game_id": "b", "market_home_margin": None},
            {"game_id": 7, "market_home_margin": -2.0},
        ]
    }
    by = load_market_by_game(snap)
    assert by == {"a": 3.5, "7": -2.0}
