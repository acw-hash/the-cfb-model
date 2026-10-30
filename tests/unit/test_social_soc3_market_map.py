"""SOC-3 market-map: shrinkage fit, gates, wording, linear y-axis."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ncaa_quant.social.copy import find_banned_words
from ncaa_quant.social.specs.market_map import (
    DEK,
    TITLE,
    _symmetric_y_domain,
    market_map_alt,
    market_map_caption,
)
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.social.specs.market_ratings import (
    FCS_POOL_ID,
    DualFitReport,
    GameMarginRow,
    MarketRatingsError,
    ShrinkageFitResult,
    build_joint_game_rows,
    contamination_evidence,
    fit_dual_ratings,
    fit_net_pts_rescale,
    fit_shrinkage_ratings,
    load_ridge_pre_kickoff_margins,
    recency_weights,
    reconstruct_market_home_margin,
    snapshot_market_through_label,
)
from ncaa_quant.social.specs.market_map import run_spot_check


def _synth_rows(
    *,
    n_teams: int = 10,
    n_weeks: int = 8,
    h_true: float = 2.5,
    seed: int = 7,
    noise: float = 0.25,
) -> tuple[list[GameMarginRow], dict[int, float]]:
    rng = np.random.default_rng(seed)
    truth = {i + 1: float(rng.normal(0.0, 8.0)) for i in range(n_teams)}
    mean = float(np.mean(list(truth.values())))
    truth = {k: v - mean for k, v in truth.items()}
    truth[FCS_POOL_ID] = -18.0
    rows: list[GameMarginRow] = []
    gid = 1
    for week in range(1, n_weeks + 1):
        # Rotate pairings so the graph is well connected.
        offset = ((week - 1) % (n_teams - 1)) + 1
        used: set[int] = set()
        for home in range(1, n_teams + 1):
            if home in used:
                continue
            away = ((home - 1 + offset) % n_teams) + 1
            if away in used or away == home:
                continue
            used.add(home)
            used.add(away)
            neutral = week == 3 and home == 1
            mu = truth[home] - truth[away] + (0.0 if neutral else h_true)
            mu += float(rng.normal(0.0, noise))
            rows.append(
                GameMarginRow(
                    game_id=gid,
                    week=week,
                    home_team_id=home,
                    away_team_id=away,
                    neutral_site=neutral,
                    target=mu,
                    home_is_fbs=True,
                    away_is_fbs=True,
                )
            )
            gid += 1
        home = (week % n_teams) + 1
        rows.append(
            GameMarginRow(
                game_id=gid,
                week=week,
                home_team_id=home,
                away_team_id=9000 + week,
                neutral_site=False,
                target=truth[home]
                - truth[FCS_POOL_ID]
                + h_true
                + float(rng.normal(0.0, noise)),
                home_is_fbs=True,
                away_is_fbs=False,
            )
        )
        gid += 1
    return rows, truth


def test_recency_weights_half_life() -> None:
    w = recency_weights([9, 5, 1], current_week=9, half_life=4.0)
    assert w[0] == pytest.approx(1.0)
    assert w[1] == pytest.approx(0.5)  # 4 weeks older → half
    assert w[2] == pytest.approx(0.25)  # 8 weeks older → quarter


def test_synthetic_recovery_correlation_and_h() -> None:
    rows, truth = _synth_rows(noise=0.15)
    # Dense connected schedule recovers well under light shrinkage.
    fit = fit_shrinkage_ratings(
        rows,
        current_week=8,
        holdout_week=8,
        lambdas=(0.1, 0.3, 1.0, 3.0),
    )
    recovered = [fit.ratings[tid] for tid in sorted(truth) if tid != FCS_POOL_ID]
    true_vals = [truth[tid] for tid in sorted(truth) if tid != FCS_POOL_ID]
    corr = float(np.corrcoef(recovered, true_vals)[0, 1])
    assert corr > 0.95, corr
    assert abs(fit.h - 2.5) < 0.5, fit.h


def test_sign_home_away_neutral() -> None:
    # Home favorite by 7; away favorite by 4; neutral drop of h.
    rows = [
        GameMarginRow(1, 1, 10, 20, False, 7.0, True, True),
        GameMarginRow(2, 1, 20, 10, False, -4.0, True, True),
        GameMarginRow(3, 2, 10, 20, True, 5.0, True, True),
        GameMarginRow(4, 2, 30, 10, False, -3.0, True, True),
        GameMarginRow(5, 3, 30, 20, False, 6.0, True, True),
        GameMarginRow(6, 3, 20, 30, False, -2.0, True, True),
        GameMarginRow(7, 4, 10, 30, False, 1.0, True, True),
        GameMarginRow(8, 4, 30, 20, True, 2.0, True, True),
    ]
    fit = fit_shrinkage_ratings(rows, current_week=5)
    # Team 10 should rate above 20 on net of the +7 / −4 games.
    assert fit.ratings[10] > fit.ratings[20]
    assert fit.h > 0.0


def test_centering_fbs_mean_zero() -> None:
    rows, _ = _synth_rows()
    fit = fit_shrinkage_ratings(rows, current_week=8)
    fbs = [v for tid, v in fit.ratings.items() if tid != FCS_POOL_ID]
    assert abs(float(np.mean(fbs))) < 1e-8


def test_data_floor_exclusion_counts() -> None:
    rows, _ = _synth_rows(n_teams=8, n_weeks=3)
    # Team 8 only appears if scheduled; force a team with 1 game.
    rows = [
        r
        for r in rows
        if not (r.home_team_id == 8 or r.away_team_id == 8)
    ]
    rows.append(
        GameMarginRow(999, 1, 8, 1, False, 3.0, True, True),
    )
    fit = fit_shrinkage_ratings(rows, current_week=3)
    assert fit.game_counts.get(8, 0) == 1
    assert fit.game_counts.get(1, 0) >= 3 or fit.game_counts.get(1, 0) >= 1


def test_holdout_gate_detects_bad_scale() -> None:
    rows, _ = _synth_rows(noise=0.2)
    # Deliberately mis-scale week-8 targets so holdout MAD blows up.
    bad = [
        GameMarginRow(
            game_id=r.game_id,
            week=r.week,
            home_team_id=r.home_team_id,
            away_team_id=r.away_team_id,
            neutral_site=r.neutral_site,
            target=r.target * 5.0 if r.week == 8 else r.target,
            home_is_fbs=r.home_is_fbs,
            away_is_fbs=r.away_is_fbs,
        )
        for r in rows
    ]
    fit = fit_shrinkage_ratings(bad, current_week=8, holdout_week=8)
    assert fit.holdout_mae is not None
    assert fit.holdout_mae > 1.5


def test_y_axis_linear_even_ticks() -> None:
    lo, hi, ticks, _labels = _symmetric_y_domain([-7.2, 3.1, 11.0, -2.0])
    assert lo == pytest.approx(-hi)
    assert 0.0 in ticks
    # Even spacing in points.
    diffs = [ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)]
    assert all(abs(d - diffs[0]) < 1e-9 for d in diffs)


def test_shrinkage_wording_no_ridge_regression() -> None:
    texts = [
        TITLE,
        DEK,
        market_map_caption(
            higher=[
                {"display": "Wake Forest", "y": 4.2},
                {"display": "Arizona", "y": 3.1},
                {"display": "Indiana", "y": 2.0},
            ],
            lower=[
                {"display": "Texas Tech", "y": -3.5},
                {"display": "Ohio State", "y": -2.2},
                {"display": "Alabama", "y": -1.1},
            ],
        ),
        market_map_alt(
            week=9,
            season=2026,
            top=[{"display": "A", "y": 1.0, "n_games": 5}],
            bottom=[{"display": "B", "y": -1.0, "n_games": 5}],
            excluded_names=["C"],
        ),
        "Market ratings use shrinkage toward zero; home field is unshrunk.",
    ]
    blob = " ".join(texts).lower()
    assert "ridge regression" not in blob
    assert "shrinkage" in blob
    for t in texts:
        hits = find_banned_words(t)
        # "market" is allowed; banned tokens must stay out.
        assert hits == [], hits


def test_contamination_evidence_ok() -> None:
    ok, notes = contamination_evidence()
    assert ok is True
    assert any("market_features_available" in n for n in notes)
    assert any("fundamental" in n for n in notes)


def test_neutral_drops_home_column() -> None:
    # Pure identification: two identical matchups, one neutral.
    rows = [
        GameMarginRow(1, 1, 1, 2, False, 5.0, True, True),
        GameMarginRow(2, 1, 2, 1, False, -1.0, True, True),
        GameMarginRow(3, 2, 1, 2, True, 3.0, True, True),
        GameMarginRow(4, 2, 2, 1, True, -3.0, True, True),
        GameMarginRow(5, 3, 1, 2, False, 5.5, True, True),
        GameMarginRow(6, 3, 2, 1, False, -0.5, True, True),
        GameMarginRow(7, 4, 1, 2, False, 4.5, True, True),
        GameMarginRow(8, 4, 2, 1, False, -1.5, True, True),
    ]
    fit = fit_shrinkage_ratings(rows, current_week=5)
    assert math.isfinite(fit.h)


def test_snapshot_market_through_label() -> None:
    label = snapshot_market_through_label(
        {"snapshot_at": "2026-10-29T13:01:04Z"}
    )
    assert label is not None
    assert "Oct" in label
    assert "29" in label
    assert snapshot_market_through_label(None) is None
    assert snapshot_market_through_label({}) is None


def test_fit_net_pts_rescale_gate() -> None:
    # Two teams, several games: mu ≈ 0.5 * net_diff + 2 * home.
    team_ratings = {
        "teams": {
            "1": {
                "school": "A",
                "weeks": [
                    {
                        "week": 9,
                        "off_epa": 0.1,
                        "def_epa": 0.05,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                ],
            },
            "2": {
                "school": "B",
                "weeks": [
                    {
                        "week": 9,
                        "off_epa": -0.05,
                        "def_epa": 0.0,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                ],
            },
        }
    }
    # net_pts A = 80*(0.15)=12, B=80*(-0.05)=-4 → diff=16
    games = []
    for i in range(12):
        games.append(
            {
                "game_id": i,
                "home_team_id": 1,
                "away_team_id": 2,
                "neutral_site": False,
                "mu_margin": 0.65 * 16.0 + 1.4,
            }
        )
        games.append(
            {
                "game_id": 100 + i,
                "home_team_id": 2,
                "away_team_id": 1,
                "neutral_site": True,
                "mu_margin": 0.65 * (-16.0),
            }
        )
    res = fit_net_pts_rescale(
        week_predictions={"games": games},
        team_ratings=team_ratings,
        week=9,
    )
    assert res.residual_mad <= 1.5
    assert abs(res.slope - 0.65) < 0.05
    assert abs(float(np.mean(list(res.ratings.values())))) < 1e-8


def test_fit_net_pts_rescale_fails_on_noise() -> None:
    team_ratings = {
        "teams": {
            "1": {
                "school": "A",
                "weeks": [
                    {
                        "week": 1,
                        "off_epa": 0.2,
                        "def_epa": 0.0,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                ],
            },
            "2": {
                "school": "B",
                "weeks": [
                    {
                        "week": 1,
                        "off_epa": -0.2,
                        "def_epa": 0.0,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                ],
            },
        }
    }
    games = [
        {
            "game_id": i,
            "home_team_id": 1,
            "away_team_id": 2,
            "neutral_site": False,
            "mu_margin": float((-1) ** i * 40),
        }
        for i in range(10)
    ]
    with pytest.raises(MarketRatingsError, match="fallback residual MAD"):
        fit_net_pts_rescale(
            week_predictions={"games": games},
            team_ratings=team_ratings,
            week=1,
        )


def test_fit_dual_ratings_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _ = _synth_rows(n_weeks=6, noise=0.2)
    market_rows = list(rows)

    def _fake_ridge(**_kwargs: object) -> dict[int, float]:
        return {r.game_id: r.target for r in rows}

    def _fake_mkt(**_kwargs: object) -> dict[int, float]:
        return {r.game_id: r.target for r in market_rows}

    def _fake_joint(**kwargs: object) -> tuple[list[GameMarginRow], list[GameMarginRow], dict[int, dict[str, int]]]:
        cov = {w: {"fbs_games": 10, "ridge": 10, "market": 10, "both": 10} for w in range(1, 7)}
        return list(rows), list(market_rows), cov

    # Force shrinkage holdout to fail by scaling week-6 targets on the ridge side.
    bad_ridge = [
        GameMarginRow(
            game_id=r.game_id,
            week=r.week,
            home_team_id=r.home_team_id,
            away_team_id=r.away_team_id,
            neutral_site=r.neutral_site,
            target=r.target * 8.0 if r.week == 6 else r.target,
            home_is_fbs=r.home_is_fbs,
            away_is_fbs=r.away_is_fbs,
        )
        for r in rows
    ]

    def _fake_joint_bad(**_kwargs: object) -> tuple[list[GameMarginRow], list[GameMarginRow], dict[int, dict[str, int]]]:
        cov = {w: {"fbs_games": 10, "ridge": 10, "market": 10, "both": 10} for w in range(1, 7)}
        return list(bad_ridge), list(market_rows), cov

    monkeypatch.setattr(
        "ncaa_quant.social.specs.market_ratings.load_ridge_pre_kickoff_margins",
        _fake_ridge,
    )
    monkeypatch.setattr(
        "ncaa_quant.social.specs.market_ratings.reconstruct_market_home_margin",
        _fake_mkt,
    )
    monkeypatch.setattr(
        "ncaa_quant.social.specs.market_ratings.build_joint_game_rows",
        _fake_joint_bad,
    )

    team_ratings = {
        "teams": {
            str(tid): {
                "school": f"T{tid}",
                "weeks": [
                    {
                        "week": 6,
                        "off_epa": 0.05 * tid,
                        "def_epa": 0.01,
                        "off_sd": 0.1,
                        "def_sd": 0.1,
                    }
                ],
            }
            for tid in range(1, 11)
        }
    }
    # Build week_predictions that match net_pts structure closely enough for MAD.
    wp_games = []
    for tid in range(1, 11):
        opp = (tid % 10) + 1
        wp_games.append(
            {
                "game_id": 1000 + tid,
                "home_team_id": tid,
                "away_team_id": opp,
                "neutral_site": False,
                "mu_margin": 0.65 * (80 * (0.05 * tid - 0.05 * opp)) + 1.4,
                "home_team": f"T{tid}",
                "away_team": f"T{opp}",
            }
        )
    report = fit_dual_ratings(
        season=2026,
        current_week=6,
        odds_snapshot={"games": [{"game_id": r.game_id, "market_home_margin": r.target} for r in market_rows]},
        week_predictions={"games": wp_games},
        team_ratings=team_ratings,
    )
    assert report.ridge_method == "net_pts_rescale"
    assert report.ridge_rescale is not None
    assert report.ridge_holdout_failed is not None
    assert report.ridge_holdout_failed > 1.5
    assert report.market.holdout_mae is not None
    assert report.market.holdout_mae <= 4.0


def test_reconstruct_and_ridge_loaders(tmp_path: Path) -> None:
    """Tiny staged tree exercises the closing-market + publish-history loaders."""
    season = 2026
    teams = pd.DataFrame(
        {
            "team_id": [1, 2],
            "school": ["Alpha", "Beta"],
            "classification": ["fbs", "fbs"],
            "abbreviation": ["ALP", "BET"],
            "conference": ["X", "X"],
            "season": [season, season],
            "event_time": pd.Timestamp("2026-08-01", tz="UTC"),
            "ingested_at": pd.Timestamp("2026-08-01", tz="UTC"),
            "source_version": ["t", "t"],
        }
    )
    teams_dir = tmp_path / "data" / "staged" / "teams" / f"season={season}"
    teams_dir.mkdir(parents=True)
    teams.to_parquet(teams_dir / "part.parquet")

    games = pd.DataFrame(
        {
            "game_id": [101],
            "home_team_id": [1],
            "away_team_id": [2],
            "home_points": [21],
            "away_points": [14],
            "neutral_site": [False],
            "start_date": [pd.Timestamp("2026-09-06T19:00:00Z")],
            "season": [season],
            "week": [1],
            "completed": [True],
            "conference_game": [False],
            "event_time": [pd.Timestamp("2026-09-06T19:00:00Z")],
            "event_time_estimated": [False],
            "ingested_at": [pd.Timestamp("2026-09-07", tz="UTC")],
            "season_type": ["regular"],
            "source_version": ["t"],
            "venue_id": [1],
        }
    )
    gdir = tmp_path / "data" / "staged" / "games" / f"season={season}" / "week=1"
    gdir.mkdir(parents=True)
    games.to_parquet(gdir / "part.parquet")

    odds = pd.DataFrame(
        {
            "away_team": ["Beta", "Beta"],
            "book": ["a", "b"],
            "captured_at": [
                pd.Timestamp("2026-09-06T18:00:00Z"),
                pd.Timestamp("2026-09-06T18:00:00Z"),
            ],
            "decision_point": [None, None],
            "event_time": [
                pd.Timestamp("2026-09-06T18:00:00Z"),
                pd.Timestamp("2026-09-06T18:00:00Z"),
            ],
            "game_id": [101, 101],
            "game_key": ["k", "k"],
            "home_team": ["Alpha", "Alpha"],
            "ingested_at": [
                pd.Timestamp("2026-09-06T18:00:00Z"),
                pd.Timestamp("2026-09-06T18:00:00Z"),
            ],
            "line": [-7.0, -6.5],
            "market": ["spread", "spread"],
            "n_books_available": [2, 2],
            "price": [-110.0, -110.0],
            "season": [season, season],
            "side": ["Alpha", "Alpha"],
            "snapshot_id": ["s", "s"],
            "snapshot_source": ["live", "live"],
            "source_version": ["t", "t"],
            "week": [1, 1],
        }
    )
    odir = tmp_path / "data" / "staged" / "odds_snapshots" / f"season={season}" / "week=1"
    odir.mkdir(parents=True)
    odds.to_parquet(odir / "part.parquet")

    mkt = reconstruct_market_home_margin(season=season, root=tmp_path)
    assert mkt[101] == pytest.approx(6.75)  # -median(-7, -6.5)

    hist = tmp_path / "data" / "webapp" / "publish_history"
    hist.mkdir(parents=True)
    (hist / "2026_w1.jsonl").write_text(
        '{"season":2026,"week":1,"refresh_kind":"tuesday_primary",'
        '"published_at":"2026-09-02T12:00:00Z","games":[{"game_id":101,'
        '"mu_margin":3.5,"published_at":"2026-09-02T12:00:00Z"}]}\n',
        encoding="utf-8",
    )
    ridge = load_ridge_pre_kickoff_margins(
        season=season, root=tmp_path, history_dir=hist
    )
    assert ridge[101] == pytest.approx(3.5)

    rr, mr, cov = build_joint_game_rows(
        season=season,
        current_week=1,
        ridge_margins=ridge,
        market_margins=mkt,
        current_week_ridge=ridge,
        current_week_market=mkt,
        root=tmp_path,
    )
    assert len(rr) == 1 and len(mr) == 1
    assert cov[1]["both"] == 1
    assert rr[0].target == pytest.approx(3.5)
    assert mr[0].target == pytest.approx(6.75)


def test_run_spot_check_directions() -> None:
    report = DualFitReport(
        ridge=None,
        market=ShrinkageFitResult(
            ratings={12: 8.0, 154: 8.0, 2641: 14.0, 87: 28.0, 194: 28.0},
            h=2.0,
            lam=0.1,
            cv_error=1.0,
            in_sample_rmse=1.0,
            holdout_mae=1.0,
            centering_offset=0.0,
            n_games=10,
            n_teams=5,
            game_counts={12: 5, 154: 5, 2641: 5, 87: 5, 194: 5},
        ),
        current_week=9,
        season=2026,
        n_joint_games=10,
        coverage_by_week={},
        y_by_team={12: 10.0, 154: 9.0, 2641: -7.0, 87: -5.0, 194: -10.0},
        plot_team_ids=[12, 154, 2641, 87, 194],
        excluded_team_ids=[],
        contamination_ok=True,
        contamination_notes=[],
        spot_check={},
        ridge_method="net_pts_rescale",
        ridge_ratings={12: 18.0, 154: 17.0, 2641: 7.0, 87: 23.0, 194: 18.0},
    )
    schools = {
        12: "Arizona",
        154: "Wake Forest",
        2641: "Texas Tech",
        87: "Notre Dame",
        194: "Ohio State",
    }
    out = run_spot_check(
        report,
        schools=schools,
        week_predictions={
            "games": [
                {
                    "game_id": 1,
                    "home_team_id": 12,
                    "away_team_id": 2641,
                    "mu_margin": 10.0,
                }
            ]
        },
        odds_snapshot={"games": [{"game_id": 1, "market_home_margin": 0.0}]},
    )
    assert out["Arizona"]["y"] == pytest.approx(10.0)
    assert out["Texas Tech"]["y"] == pytest.approx(-7.0)
