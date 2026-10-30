"""SOC-3 market-map spec build coverage (no Playwright)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ncaa_quant.social.specs.market_map import build_market_map_specs
from ncaa_quant.social.specs.market_ratings import DualFitReport, ShrinkageFitResult
from ncaa_quant.social.theme import parse_sizes

REPO = Path(__file__).resolve().parents[2]


def _report() -> DualFitReport:
    ratings = {tid: float(tid % 17 - 8) for tid in range(1, 40)}
    market = {tid: float((tid * 3) % 17 - 8) for tid in range(1, 40)}
    y = {tid: ratings[tid] - market[tid] for tid in ratings}
    counts = {tid: 5 for tid in ratings}
    return DualFitReport(
        ridge=None,
        market=ShrinkageFitResult(
            ratings=market,
            h=2.5,
            lam=0.1,
            cv_error=2.0,
            in_sample_rmse=2.0,
            holdout_mae=2.0,
            centering_offset=0.1,
            n_games=100,
            n_teams=len(market),
            game_counts=counts,
        ),
        current_week=9,
        season=2026,
        n_joint_games=100,
        coverage_by_week={9: {"fbs_games": 50, "ridge": 50, "market": 50, "both": 50}},
        y_by_team=y,
        plot_team_ids=sorted(ratings),
        excluded_team_ids=[],
        contamination_ok=True,
        contamination_notes=["ok"],
        spot_check={},
        ridge_method="net_pts_rescale",
        ridge_ratings=ratings,
        ridge_holdout_failed=7.0,
    )


def test_build_market_map_specs_smoke() -> None:
    brand = REPO / "assets" / "brand" / "ridge_mark.png"
    if not brand.is_file():
        pytest.skip("brand mark missing")
    team_ratings = {
        "teams": {
            str(tid): {"school": f"Team {tid}", "weeks": []}
            for tid in range(1, 40)
        }
    }
    specs, report = build_market_map_specs(
        team_ratings=team_ratings,
        week_predictions={"games": [], "week": 9},
        odds_snapshot={
            "week": 9,
            "snapshot_at": "2026-10-29T13:01:04Z",
            "games": [],
        },
        week=9,
        slate_week=9,
        season=2026,
        fbs_team_ids=set(range(1, 40)),
        abbrev_by_id={tid: f"T{tid}" for tid in range(1, 40)},
        logo_dir=REPO / "data" / "social" / "logos",
        sizes=parse_sizes("4x5"),
        theme="light",
        model_version="test",
        vintage_label="test",
        published_at="2026-10-28T14:08:37Z",
        site_url="https://example.com",
        brand_mark=brand,
        fit_report=_report(),
    )
    assert len(specs) == 1
    spec = specs[0]
    assert spec["chart"] == "market_map"
    assert spec["frame"]["title"] == "Where Ridge and the market disagree"
    assert "market through" in spec["frame"]["footer"]
    assert spec["axes"]["y_zero_label"] == "Ridge = market"
    assert spec["axes"]["agreement_band"] == 2.0
    assert abs(spec["axes"]["y_min"] + spec["axes"]["y_max"]) < 1e-9
    ticks = spec["axes"]["y_ticks"]
    assert len(ticks) >= 3
    diffs = [ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)]
    assert all(abs(d - diffs[0]) < 1e-9 for d in diffs)
    assert report.ridge_method == "net_pts_rescale"
    assert "ridge regression" not in spec["caption"].lower()
    assert "ridge regression" not in spec["alt"].lower()
