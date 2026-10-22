"""SOC-1 social charts: load, math, placement, logos, banned words, PNG determinism."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from scipy.stats import norm

from ncaa_quant.social.charts.ridge_map import (
    extract_ridge_points,
    render_ridge_map,
    ridge_week_labels,
)
from ncaa_quant.social.charts.slate_map import (
    PHI_CHECK_TOLERANCE,
    extract_slate_points,
    favored_win_prob,
    find_sign_disagreements,
    phi_check,
    render_slate_map,
    tier_boundary_zs,
    tier_enter_thresholds,
)
from ncaa_quant.social.charts.surprise_strip import (
    extract_surprise_points,
    favored_perspective_z,
    interval_hit_headline,
    render_surprise_strip,
)
from ncaa_quant.social.copy import assert_clean_texts, find_banned_words, footer_line
from ncaa_quant.social.load import (
    ArtifactLoadError,
    latest_graded_week,
    latest_ratings_week,
    load_artifacts,
    read_supported_schema_major,
    resolve_weeks,
    schema_major,
    validate_staleness,
    validate_team_ratings_populated,
)
from ncaa_quant.social.logos import (
    cached_path,
    load_logo_asset,
    normalize_logo,
    override_path,
    resolve_logo_path,
)
from ncaa_quant.social.placement import beeswarm_offsets, max_pairwise_overlap_fraction, repel_logos
from ncaa_quant.social.theme import ChartSize, parse_sizes
from ncaa_quant.webapp.export import TIER_CLEAR_ENTER, TIER_LEAN_ENTER, TIER_STRONG_ENTER

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "webapp" / "fixtures"


def _write_png(path: Path, *, color: tuple[int, int, int, int] = (20, 20, 20, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=color)
    img.save(path)


@pytest.fixture()
def logo_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logos"
    d.mkdir()
    (d / "overrides").mkdir()
    return d


def test_supported_schema_major_matches_webapp() -> None:
    assert read_supported_schema_major() == 1
    assert schema_major("1.3.0") == 1


def test_week_resolution_and_ratings_behind_warning() -> None:
    meta = {"week": 7}
    ratings = {
        "teams": {
            "1": {"school": "A", "weeks": [{"week": 5, "off_epa": 0, "def_epa": 0}]},
            "2": {"school": "B", "weeks": [{"week": 5, "off_epa": 0, "def_epa": 0}]},
        }
    }
    results = {
        "games": [
            {"week": 6, "grade_status": "graded"},
            {"week": 5, "grade_status": "graded"},
        ]
    }
    assert latest_ratings_week(ratings) == 5
    assert latest_graded_week(results) == 6
    resolved = resolve_weeks(
        meta=meta,
        team_ratings=ratings,
        results=results,
        week="auto",
    )
    assert resolved.map_week == 5
    assert resolved.slate_week == 7
    assert resolved.surprise_week == 6
    assert any("ratings week" in w for w in resolved.warnings)


def test_schema_fixture_stale_refusals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "arts"
    src.mkdir()
    for name in (
        "meta.json",
        "week_predictions.json",
        "team_ratings_2024.json",
        "results_2024.json",
    ):
        shutil.copy(FIXTURES / name, src / name)

    # Fixture refusal
    with pytest.raises(ArtifactLoadError, match="fixture"):
        load_artifacts(
            season=2024,
            source="local",
            artifact_dir=src,
            allow_fixture=False,
            allow_stale=True,
            now=datetime(2024, 9, 24, 12, tzinfo=UTC),
        )

    # Schema major mismatch
    meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    meta["schema_version"] = "2.0.0"
    meta["fixture"] = False
    (src / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    for name in ("week_predictions.json", "team_ratings_2024.json", "results_2024.json"):
        data = json.loads((src / name).read_text(encoding="utf-8"))
        data["fixture"] = False
        data["schema_version"] = "2.0.0"
        (src / name).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ArtifactLoadError, match="schema_version major"):
        load_artifacts(
            season=2024,
            source="local",
            artifact_dir=src,
            allow_fixture=True,
            allow_stale=True,
            now=datetime(2024, 9, 24, 12, tzinfo=UTC),
        )

    # Staleness — DESIGN §3.2: age>36h AND past next_expected
    from ncaa_quant.social.load import ArtifactBlob

    now = datetime(2024, 9, 24, tzinfo=UTC)
    # Age > 36h but still before next_expected → do not refuse
    early = ArtifactBlob(
        name="meta.json",
        data={"next_expected_publish_utc": "2024-09-25T00:00:00Z"},
        source="x",
        sha256="0",
        published_at="2024-09-01T00:00:00Z",
    )
    validate_staleness(early, now=now, allow_stale=False)

    # Age > 36h and past next_expected → refuse
    late = ArtifactBlob(
        name="meta.json",
        data={"next_expected_publish_utc": "2024-09-20T00:00:00Z"},
        source="x",
        sha256="0",
        published_at="2024-09-01T00:00:00Z",
    )
    with pytest.raises(ArtifactLoadError, match="stale"):
        validate_staleness(late, now=now, allow_stale=False)


def test_empty_team_ratings_hard_fail() -> None:
    from ncaa_quant.social.load import ArtifactBlob

    blob = ArtifactBlob(
        name="team_ratings_2026.json",
        data={"teams": {}, "schema_version": "1.3.0"},
        source="x",
        sha256="0",
        published_at=None,
    )
    with pytest.raises(ArtifactLoadError, match="empty or stub"):
        validate_team_ratings_populated(blob, target_week=5)


def test_favored_perspective_z_signs() -> None:
    # Home favored, favorite overperformed
    z, side = favored_perspective_z(actual_margin=14.0, mu_margin=7.0, sigma_margin=7.0)
    assert side == "home"
    assert z == pytest.approx(1.0)

    # Away favored (mu < 0): underdog (home) overperformed → negative z
    z, side = favored_perspective_z(actual_margin=0.0, mu_margin=-7.0, sigma_margin=7.0)
    assert side == "away"
    # s=-1; (0 - (-7))/7 = 1; z = -1
    assert z == pytest.approx(-1.0)

    # mu == 0 tie-break: p_win_home >= 0.5 → home
    z, side = favored_perspective_z(
        actual_margin=7.0,
        mu_margin=0.0,
        sigma_margin=7.0,
        p_win_home=0.55,
    )
    assert side == "home"
    assert z == pytest.approx(1.0)

    z, side = favored_perspective_z(
        actual_margin=-7.0,
        mu_margin=0.0,
        sigma_margin=7.0,
        p_win_home=0.4,
    )
    assert side == "away"
    assert z == pytest.approx(1.0)


def test_tier_boundary_zs_match_export_constants() -> None:
    zs = tier_boundary_zs()
    assert zs["lean"] == pytest.approx(float(norm.ppf(TIER_LEAN_ENTER)))
    assert zs["clear_lean"] == pytest.approx(float(norm.ppf(TIER_CLEAR_ENTER)))
    assert zs["strong_lean"] == pytest.approx(float(norm.ppf(TIER_STRONG_ENTER)))


def test_tier_enter_thresholds_match_export_constants() -> None:
    th = tier_enter_thresholds()
    assert th["lean"] == TIER_LEAN_ENTER
    assert th["clear_lean"] == TIER_CLEAR_ENTER
    assert th["strong_lean"] == TIER_STRONG_ENTER


def test_ridge_week_labels_entering_vs_through() -> None:
    entering = ridge_week_labels(map_week=6, slate_week=6, season=2026)
    assert entering.mode == "entering"
    assert entering.through_line == "Entering Week 6 · 2026"
    assert entering.ratings_through_note == "ratings through Week 5 games"
    assert entering.week_phrase == "entering Week 6"

    through = ridge_week_labels(map_week=5, slate_week=6, season=2026)
    assert through.mode == "through"
    assert through.through_line == "Through Week 5 · 2026"
    assert through.ratings_through_note is None
    assert through.week_phrase == "through Week 5"


def test_rank_percentiles_ties_averaged() -> None:
    from ncaa_quant.social.charts.ridge_map import rank_percentiles
    import numpy as np

    # Three values, middle tie of two lows → ranks 1.5, 1.5, 3
    pct = rank_percentiles(np.array([1.0, 1.0, 3.0]))
    assert pct[0] == pytest.approx(100.0 * (1.5 - 1.0) / 2.0)
    assert pct[1] == pytest.approx(100.0 * (1.5 - 1.0) / 2.0)
    assert pct[2] == pytest.approx(100.0)


def test_top25_rank_change_and_render(tmp_path: Path, logo_dir: Path) -> None:
    from ncaa_quant.social.charts.top25 import (
        extract_top25,
        format_rank_change,
        render_top25,
    )

    assert format_rank_change(3) == "▲3"
    assert format_rank_change(-2) == "▼2"
    assert format_rank_change(0) == "—"
    assert format_rank_change(None) == "NEW"

    for tid in range(1, 4):
        _write_png(cached_path(logo_dir, tid, dark=True))
    ratings = {
        "teams": {
            "1": {
                "school": "Alpha",
                "weeks": [
                    {"week": 5, "off_epa": 0.20, "def_epa": 0.10, "off_sd": 0.1, "def_sd": 0.1},
                    {"week": 6, "off_epa": 0.30, "def_epa": 0.20, "off_sd": 0.09, "def_sd": 0.09},
                ],
            },
            "2": {
                "school": "Beta",
                "weeks": [
                    {"week": 5, "off_epa": 0.25, "def_epa": 0.20, "off_sd": 0.1, "def_sd": 0.1},
                    {"week": 6, "off_epa": 0.10, "def_epa": 0.05, "off_sd": 0.09, "def_sd": 0.09},
                ],
            },
            "3": {
                "school": "Gamma",
                "weeks": [
                    {"week": 6, "off_epa": 0.15, "def_epa": 0.10, "off_sd": 0.09, "def_sd": 0.09},
                ],
            },
        }
    }
    rows = extract_top25(
        ratings, week=6, fbs_team_ids={1, 2, 3}, abbrev_by_id={1: "ALP", 2: "BET", 3: "GAM"}
    )
    assert len(rows) == 3
    assert rows[0].school == "Alpha"
    assert rows[0].rank == 1
    # Alpha was #2 in week 5 → rose
    assert rows[0].rank_delta == 1
    gamma = next(r for r in rows if r.school == "Gamma")
    assert gamma.rank_delta is None  # NEW

    result = render_top25(
        rows=rows,
        week=6,
        season=2026,
        sizes=[ChartSize("4x5", 400, 500), ChartSize("16x9", 640, 360)],
        out_dir=tmp_path,
        footer="example.com · Forecasts, not betting advice",
        slate_week=6,
        logo_dir=logo_dir,
    )
    assert len(result.paths) == 2
    assert all(p.is_file() for p in result.paths)
    assert "Ratings are estimates" in result.alt_text
    assert_clean_texts([result.alt_text, result.caption, footer_line(site_host="example.com")])


def test_slate_toss_up_ships_with_sign_footnote(tmp_path: Path, logo_dir: Path) -> None:
    from ncaa_quant.social.charts.slate_map import SlatePoint, chart_p_max

    assert chart_p_max(0.45) == pytest.approx(0.55)
    points = [
        SlatePoint(
            game_id="1",
            home_team="Home",
            away_team="Away",
            home_team_id=1,
            away_team_id=2,
            mu_margin=-0.5,
            sigma_margin=15.0,
            p_win_home=0.55,
            conviction_tier="toss_up",
            conviction_team=None,
            p_chart=0.55,
            abs_mu=0.5,
            is_toss_up=True,
            primary_team_id=1,
            secondary_team_id=2,
            primary_school="Home",
            secondary_school="Away",
        )
    ]
    _write_png(cached_path(logo_dir, 1, dark=True))
    _write_png(cached_path(logo_dir, 2, dark=True))
    games = [
        {
            "game_id": "1",
            "home_team": "Home",
            "away_team": "Away",
            "mu_margin": -0.5,
            "sigma_margin": 15.0,
            "p_win_home": 0.55,
            "conviction_tier": "toss_up",
        }
    ]
    assert find_sign_disagreements(games)
    result = render_slate_map(
        points=points,
        excluded=[],
        week=6,
        season=2026,
        refresh_kind="tuesday_primary",
        published_at="2026-10-06T16:51:04Z",
        sizes=[ChartSize("4x5", 400, 500)],
        out_dir=tmp_path,
        footer="the-cfb-model.vercel.app · Forecasts, not betting advice",
        logo_dir=logo_dir,
        source_games=games,
    )
    assert result.shipped is True
    assert result.paths
    assert any("lean opposite ways" in f for f in result.footnotes)
    assert result.notes.get("toss_up_sign_note")
    assert "lean opposite ways" in (result.notes["toss_up_sign_note"] or "")


def test_non_toss_conviction_team_p_side_assert() -> None:
    from ncaa_quant.social.charts.slate_map import SlateCoherenceError, extract_slate_points

    bad = {
        "games": [
            {
                "game_id": "9",
                "home_team": "Home",
                "away_team": "Away",
                "home_team_id": 1,
                "away_team_id": 2,
                "mu_margin": 3.0,
                "sigma_margin": 15.0,
                "p_win_home": 0.40,
                "sigma_margin_credible": True,
                "conviction_tier": "lean",
                "conviction_team": "Home",
            }
        ]
    }
    with pytest.raises(SlateCoherenceError, match="p_win_home"):
        extract_slate_points(bad)


def test_phi_check_approximate_switch() -> None:
    from ncaa_quant.social.charts.slate_map import SlatePoint

    def mk(p_fav: float, mu: float, sigma: float) -> SlatePoint:
        return SlatePoint(
            game_id="1",
            home_team="H",
            away_team="A",
            home_team_id=1,
            away_team_id=2,
            mu_margin=mu,
            sigma_margin=sigma,
            p_win_home=p_fav,
            conviction_tier="lean",
            conviction_team="H",
            p_chart=p_fav,
            abs_mu=abs(mu),
            is_toss_up=False,
            primary_team_id=1,
            secondary_team_id=2,
            primary_school="H",
            secondary_school="A",
        )

    exact = mk(float(norm.cdf(1.0)), 10.0, 10.0)
    assert phi_check([exact]).approximate is False
    bad = mk(0.90, 10.0, 10.0)  # Φ(1)≈0.841
    result = phi_check([bad])
    assert result.approximate is True
    assert result.max_abs_dev > PHI_CHECK_TOLERANCE


def test_interval_hit_headline_excludes_nulls() -> None:
    games = [
        {"margin_interval_hit": True},
        {"margin_interval_hit": False},
        {"margin_interval_hit": None},
        {"margin_interval_hit": True},
    ]
    h = interval_hit_headline(games)
    assert h.hits == 2
    assert h.denom == 3
    assert h.pct == pytest.approx(200.0 / 3.0)


def test_placement_overlap_leader_determinism() -> None:
    args = dict(
        team_ids=[3, 1, 2],
        xs=[0.0, 0.01, 0.02],
        ys=[0.0, 0.01, 0.0],
        widths=[0.2, 0.2, 0.2],
        heights=[0.2, 0.2, 0.2],
        x_min=-1.0,
        x_max=1.0,
        y_min=-1.0,
        y_max=1.0,
    )
    a = repel_logos(**args)
    b = repel_logos(**args)
    assert [(p.logo_x, p.logo_y, p.draw_leader) for p in a] == [
        (p.logo_x, p.logo_y, p.draw_leader) for p in b
    ]
    assert max_pairwise_overlap_fraction(a) <= 0.10 + 1e-9

    # Allowed overlap: no push when fraction ≤ tolerance.
    soft = repel_logos(
        **args,
        overlap_allow_fraction=0.15,
        leader_threshold_widths=0.5,
    )
    assert max_pairwise_overlap_fraction(soft) <= 0.15 + 1e-9

    # Leader when forced far
    far = repel_logos(
        team_ids=[1, 2],
        xs=[0.0, 0.0],
        ys=[0.0, 0.0],
        widths=[0.3, 0.3],
        heights=[0.3, 0.3],
        x_min=-2.0,
        x_max=2.0,
        y_min=-2.0,
        y_max=2.0,
        max_disp_widths=2.0,
        leader_threshold_widths=0.05,
    )
    assert any(p.draw_leader for p in far)

    ys = beeswarm_offsets(xs=[0.0, 0.05, 0.1], radii=[0.2, 0.2, 0.2], team_ids=[3, 1, 2])
    assert ys == beeswarm_offsets(xs=[0.0, 0.05, 0.1], radii=[0.2, 0.2, 0.2], team_ids=[3, 1, 2])


def test_logo_override_and_missing(logo_dir: Path) -> None:
    tid = 99
    # Missing → None
    assert resolve_logo_path(tid, logo_dir=logo_dir)[0] is None

    cached = cached_path(logo_dir, tid, dark=True)
    _write_png(cached, color=(10, 10, 10, 255))
    path, ov = resolve_logo_path(tid, logo_dir=logo_dir)
    assert path == cached
    assert ov is False

    # Override wins
    ov_path = override_path(logo_dir, tid, dark=True)
    _write_png(ov_path, color=(200, 200, 200, 255))
    path, is_ov = resolve_logo_path(tid, logo_dir=logo_dir)
    assert path == ov_path
    assert is_ov is True
    asset = load_logo_asset(tid, logo_dir=logo_dir)
    assert asset is not None
    assert asset.from_override is True


def test_banned_words_footer_exception() -> None:
    hits = find_banned_words("Forecasts, not betting advice · https://example.com")
    assert hits == []
    hits = find_banned_words("This is a lock and a great bet")
    assert {h.word.lower() for h in hits} >= {"lock", "bet"}
    with pytest.raises(ValueError, match="Banned"):
        assert_clean_texts(["our edge tonight"])
    footer = footer_line(site_host="the-cfb-model.vercel.app")
    assert footer == "the-cfb-model.vercel.app · Forecasts, not betting advice"
    assert_clean_texts([footer])
    excluded = footer_line(site_host="the-cfb-model.vercel.app", games_not_shown=3)
    assert excluded.endswith("· 3 games not shown")
    assert_clean_texts([excluded])


def test_brand_mark_required(tmp_path: Path, logo_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ncaa_quant.social.charts._frame import BrandMarkError, require_brand_mark

    missing = tmp_path / "nope.png"
    with pytest.raises(BrandMarkError, match="Missing brand mark"):
        require_brand_mark(missing)


def test_png_byte_identical(tmp_path: Path, logo_dir: Path) -> None:
    # Tiny synthetic ratings chart with two logos.
    for tid in (1, 2):
        _write_png(cached_path(logo_dir, tid, dark=True))
    points = extract_ridge_points(
        {
            "teams": {
                "1": {
                    "school": "Alpha",
                    "weeks": [
                        {
                            "week": 5,
                            "off_epa": 0.1,
                            "def_epa": 0.05,
                            "off_sd": 0.02,
                            "def_sd": 0.02,
                        }
                    ],
                },
                "2": {
                    "school": "Beta",
                    "weeks": [
                        {
                            "week": 5,
                            "off_epa": -0.05,
                            "def_epa": 0.12,
                            "off_sd": 0.03,
                            "def_sd": 0.03,
                        }
                    ],
                },
            }
        },
        week=5,
        fbs_team_ids={1, 2},
        abbrev_by_id={1: "ALP", 2: "BET"},
    )
    size = ChartSize(name="4x5", width=400, height=500)
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    footer = "the-cfb-model.vercel.app · Forecasts, not betting advice"
    r1 = render_ridge_map(
        points=points,
        week=5,
        season=2024,
        sizes=[size],
        out_dir=out1,
        footer=footer,
        logo_dir=logo_dir,
    )
    r2 = render_ridge_map(
        points=points,
        week=5,
        season=2024,
        sizes=[size],
        out_dir=out2,
        footer=footer,
        logo_dir=logo_dir,
    )
    b1 = r1.paths[0].read_bytes()
    b2 = r2.paths[0].read_bytes()
    assert b1 == b2


def test_missing_logo_strict_failure(tmp_path: Path, logo_dir: Path) -> None:
    points = extract_ridge_points(
        {
            "teams": {
                "7": {
                    "school": "Ghost",
                    "weeks": [
                        {
                            "week": 1,
                            "off_epa": 0.0,
                            "def_epa": 0.0,
                            "off_sd": 0.1,
                            "def_sd": 0.1,
                        }
                    ],
                }
            }
        },
        week=1,
        fbs_team_ids={7},
    )
    footer = "the-cfb-model.vercel.app · Forecasts, not betting advice"
    with pytest.raises(RuntimeError, match="Missing logos"):
        render_ridge_map(
            points=points,
            week=1,
            season=2024,
            sizes=[ChartSize("4x5", 400, 500)],
            out_dir=tmp_path,
            footer=footer,
            logo_dir=logo_dir,
            allow_missing_logos=False,
        )
    result = render_ridge_map(
        points=points,
        week=1,
        season=2024,
        sizes=[ChartSize("4x5", 400, 500)],
        out_dir=tmp_path / "ok",
        footer=footer,
        logo_dir=logo_dir,
        allow_missing_logos=True,
    )
    assert result.logo_fallbacks


def test_parse_sizes() -> None:
    sizes = parse_sizes("4x5,16x9")
    assert [(s.name, s.width, s.height) for s in sizes] == [
        ("4x5", 1600, 2000),
        ("16x9", 1920, 1080),
    ]


def test_normalize_logo_trim_and_resize() -> None:
    img = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 10, 60, 40), fill=(255, 0, 0, 255))
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format="PNG")
    out = normalize_logo(buf.getvalue())
    assert max(out.size) == 512


def test_fixture_load_and_render_end_to_end(tmp_path: Path, logo_dir: Path) -> None:
    """Load webapp fixtures (allow-fixture) and render all three charts."""
    loaded = load_artifacts(
        season=2024,
        source="local",
        artifact_dir=FIXTURES,
        allow_fixture=True,
        allow_stale=True,
        now=datetime(2024, 9, 24, 12, tzinfo=UTC),
    )
    assert loaded.fixture_watermark is True
    assert loaded.weeks.slate_week == 5
    assert len(loaded.fbs_team_ids) >= 100

    size = ChartSize(name="4x5", width=400, height=500)
    footer = "the-cfb-model.vercel.app · Forecasts, not betting advice"
    ridge_points = extract_ridge_points(
        loaded.team_ratings.data,
        week=loaded.weeks.map_week,
        fbs_team_ids=loaded.fbs_team_ids,
    )
    # Keep the render cheap: first 12 teams only for the map.
    ridge_points = ridge_points[:12]
    for p in ridge_points:
        _write_png(cached_path(logo_dir, p.team_id, dark=True))

    map_result = render_ridge_map(
        points=ridge_points,
        week=loaded.weeks.map_week,
        season=2024,
        sizes=[size],
        out_dir=tmp_path / "map",
        footer=footer,
        slate_week=loaded.weeks.slate_week,
        logo_dir=logo_dir,
        fixture_watermark=True,
    )
    assert map_result.paths[0].is_file()
    assert map_result.week_mode in {"entering", "through"}

    slate_points, excluded = extract_slate_points(loaded.week_predictions.data)
    slate_points = slate_points[:8]
    for p in slate_points:
        _write_png(cached_path(logo_dir, p.home_team_id, dark=True))
        _write_png(cached_path(logo_dir, p.away_team_id, dark=True))
    slate_result = render_slate_map(
        points=slate_points,
        excluded=excluded,
        week=loaded.weeks.slate_week,
        season=2024,
        refresh_kind="tuesday_primary",
        published_at="2024-09-24T10:00:00Z",
        sizes=[size],
        out_dir=tmp_path / "slate",
        footer=footer,
        logo_dir=logo_dir,
        fixture_watermark=True,
        source_games=list(loaded.week_predictions.data.get("games") or []),
    )
    assert slate_result.shipped is True
    assert slate_result.paths[0].is_file()
    assert all(0.5 <= p.p_chart <= 1.0 for p in slate_points)

    surprise_points, sexcl = extract_surprise_points(
        loaded.results.data,
        week=loaded.weeks.surprise_week,
        school_to_team_id=loaded.school_to_team_id,
    )
    surprise_points = surprise_points[:10]
    for p in surprise_points:
        if p.outperform_team_id is not None:
            _write_png(cached_path(logo_dir, p.outperform_team_id, dark=True))
    surprise_result = render_surprise_strip(
        points=surprise_points,
        excluded=sexcl,
        week=loaded.weeks.surprise_week,
        season=2024,
        sizes=[size],
        out_dir=tmp_path / "surprise",
        footer=footer,
        logo_dir=logo_dir,
        allow_missing_logos=True,
        fixture_watermark=True,
    )
    assert surprise_result.paths[0].is_file()
    assert surprise_result.headline.denom >= 1
