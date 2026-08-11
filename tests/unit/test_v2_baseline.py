"""V2-BASELINE — equivalence + market-feature audit fixtures (no live backtest)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

from ncaa_quant.evaluation.walkforward import WalkForwardConfig, week_decision_as_of

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "_v2_baseline.py"
_SPEC = importlib.util.spec_from_file_location("v2_baseline_script", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_v2 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _v2
_SPEC.loader.exec_module(_v2)

audit_market_feature_ladders = _v2.audit_market_feature_ladders
compare_prediction_frames = _v2.compare_prediction_frames
config_diff_beyond_market = _v2.config_diff_beyond_market
sample_week_points = _v2.sample_week_points


def _games_frame() -> pd.DataFrame:
    """Synthetic games with kickoffs AFTER week decision as_of (honest PIT)."""
    cfg = WalkForwardConfig(seed=42)
    rows: list[dict] = []
    gid = 1000
    for season in (2019, 2021, 2022, 2023, 2024):
        for week in range(1, 8):
            as_of = pd.Timestamp(week_decision_as_of(season, week, cfg))
            # Three games per week, kicking off Sat–Sun after Tuesday as_of.
            for i in range(3):
                kick = as_of + timedelta(days=4, hours=6 + i)
                rows.append(
                    {
                        "game_id": gid,
                        "game_key": f"k{gid}",
                        "season": season,
                        "week": week,
                        "event_time": kick.to_pydatetime(),
                        "home_team": "Alabama",
                        "away_team": "Auburn",
                        "home_team_id": 1,
                        "away_team_id": 2,
                    }
                )
                gid += 1
    return pd.DataFrame(rows)


def _anchor_game(games: pd.DataFrame, season: int = 2023, week: int = 5) -> int:
    sub = games.loc[(games["season"] == season) & (games["week"] == week)]
    return int(sub.iloc[0]["game_id"])


def test_compare_prediction_frames_equivalent() -> None:
    base = pd.DataFrame(
        {
            "season": [2023, 2023],
            "week": [5, 6],
            "game_id": [1, 2],
            "pred_margin": [3.0, -1.5],
            "sigma_m": [12.0, 11.0],
            "p_ats_home": [0.55, 0.48],
            "spread_close": [-3.5, 2.0],
            "spread_asof": [-3.0, 1.5],
        }
    )
    out = compare_prediction_frames(base, base.copy())
    assert out["verdict"] == "EQUIVALENT"
    assert out["n_cell_disagreements"] == 0


def test_compare_prediction_frames_divergent() -> None:
    a = pd.DataFrame(
        {
            "season": [2023],
            "week": [5],
            "game_id": [1],
            "pred_margin": [3.0],
            "sigma_m": [12.0],
            "p_ats_home": [0.55],
            "spread_close": [-3.5],
            "spread_asof": [-3.0],
        }
    )
    b = a.copy()
    b.loc[0, "pred_margin"] = 9.0
    out = compare_prediction_frames(a, b)
    assert out["verdict"] == "DIVERGENT"
    assert out["columns"]["pred_margin"]["n_disagree"] == 1


def test_config_diff_marks_expected_market_flag() -> None:
    fund = {
        "stack": "fundamental",
        "run_id": "fund",
        "walkforward": {"market_features_available": False, "seed": 42},
    }
    a3 = {
        "stack": "market_aware",
        "run_id": "a3",
        "walkforward": {"market_features_available": False, "seed": 42},
    }
    diffs = config_diff_beyond_market(fund, a3)
    assert any(d.startswith("EXPECTED stack:") for d in diffs)
    unexpected = [d for d in diffs if not d.startswith("EXPECTED ")]
    assert unexpected == []


def test_sample_week_points_at_least_20() -> None:
    games = _games_frame()
    pts = sample_week_points(games, n_min=20, seed=42)
    assert len(pts) >= 20
    assert len(pts) == len(set(pts))


def test_audit_clean_when_feature_before_decision_and_distinct_close() -> None:
    """Feature uses Tuesday snap; grade uses later Friday snap — CLEAN."""
    cfg = WalkForwardConfig(seed=42)
    games = _games_frame()
    pts = sample_week_points(games, n_min=20, seed=0)
    if (2023, 5) not in pts:
        pts = [(2023, 5), *pts][:20]
    gid = _anchor_game(games)
    as_of = week_decision_as_of(2023, 5, cfg)
    tue = pd.Timestamp(as_of) - timedelta(hours=1)
    fri = pd.Timestamp(as_of) + timedelta(days=3)
    kick = pd.Timestamp(games.loc[games["game_id"] == gid, "event_time"].iloc[0])
    assert tue < pd.Timestamp(as_of) < fri < kick

    snaps = pd.DataFrame(
        [
            {
                "snapshot_id": "snap-tue",
                "game_id": gid,
                "event_time": tue,
                "market": "spread",
                "side": "Alabama",
                "line": -7.5,
                "book": "b1",
                "season": 2023,
            },
            {
                "snapshot_id": "snap-fri",
                "game_id": gid,
                "event_time": fri,
                "market": "spread",
                "side": "Alabama",
                "line": -6.5,
                "book": "b1",
                "season": 2023,
            },
            {
                "snapshot_id": "snap-tue-t",
                "game_id": gid,
                "event_time": tue,
                "market": "total",
                "side": "over",
                "line": 55.0,
                "book": "b1",
                "season": 2023,
            },
            {
                "snapshot_id": "snap-fri-t",
                "game_id": gid,
                "event_time": fri,
                "market": "total",
                "side": "over",
                "line": 54.0,
                "book": "b1",
                "season": 2023,
            },
        ]
    )
    result = audit_market_feature_ladders(
        games,
        snaps,
        pd.DataFrame(),
        config=cfg,
        week_points=pts,
        market_features=("mkt_spread",),
    )
    assert result["n_week_points"] >= 20
    assert result["verdict"] == "CLEAN"
    hit = [r for r in result["rows"] if r["game_id"] == gid and r["feature"] == "mkt_spread"]
    assert hit
    assert hit[0]["feature_source_row_id"] == "snap-tue"
    assert hit[0]["grade_source_row_id"] == "snap-fri"
    assert hit[0]["distinct_from_grade_row"] is True
    assert hit[0]["leak"] is False


def test_audit_correct_ladder_picks_distinct_rows_when_later_snap_exists() -> None:
    cfg = WalkForwardConfig(seed=42)
    games = _games_frame()
    pts = sample_week_points(games, n_min=20, seed=1)
    if (2023, 5) not in pts:
        pts = [(2023, 5), *pts][:20]
    gid = _anchor_game(games)
    as_of = week_decision_as_of(2023, 5, cfg)
    early = pd.Timestamp(as_of) - timedelta(hours=2)
    later = pd.Timestamp(as_of) + timedelta(days=2)
    snaps = pd.DataFrame(
        [
            {
                "snapshot_id": "early-row",
                "game_id": gid,
                "event_time": early,
                "market": "spread",
                "side": "Alabama",
                "line": -7.0,
                "book": "b1",
                "season": 2023,
            },
            {
                "snapshot_id": "later-row",
                "game_id": gid,
                "event_time": later,
                "market": "spread",
                "side": "Alabama",
                "line": -5.0,
                "book": "b1",
                "season": 2023,
            },
            {
                "snapshot_id": "early-row-t",
                "game_id": gid,
                "event_time": early,
                "market": "total",
                "side": "over",
                "line": 50.0,
                "book": "b1",
                "season": 2023,
            },
            {
                "snapshot_id": "later-row-t",
                "game_id": gid,
                "event_time": later,
                "market": "total",
                "side": "over",
                "line": 51.0,
                "book": "b1",
                "season": 2023,
            },
        ]
    )
    result = audit_market_feature_ladders(
        games,
        snaps,
        pd.DataFrame(),
        config=cfg,
        week_points=pts,
        market_features=("mkt_spread",),
    )
    assert result["verdict"] == "CLEAN"
    hit = [r for r in result["rows"] if r["game_id"] == gid][0]
    assert hit["feature_source_row_id"] != hit["grade_source_row_id"]


def test_audit_post_decision_snap_excluded_from_features() -> None:
    """Post-decision snaps must not enter the feature ladder (PIT)."""
    cfg = WalkForwardConfig(seed=42)
    games = _games_frame()
    pts = sample_week_points(games, n_min=20, seed=2)
    if (2023, 5) not in pts:
        pts = [(2023, 5), *pts][:20]
    gid = _anchor_game(games)
    as_of = week_decision_as_of(2023, 5, cfg)
    post = pd.Timestamp(as_of) + timedelta(minutes=1)
    kick = pd.Timestamp(games.loc[games["game_id"] == gid, "event_time"].iloc[0])
    assert pd.Timestamp(as_of) < post < kick
    snaps = pd.DataFrame(
        [
            {
                "snapshot_id": "post",
                "game_id": gid,
                "event_time": post,
                "market": "spread",
                "side": "Alabama",
                "line": -3.0,
                "book": "b1",
                "season": 2023,
            },
            {
                "snapshot_id": "post-t",
                "game_id": gid,
                "event_time": post,
                "market": "total",
                "side": "over",
                "line": 40.0,
                "book": "b1",
                "season": 2023,
            },
        ]
    )
    result = audit_market_feature_ladders(
        games,
        snaps,
        pd.DataFrame(),
        config=cfg,
        week_points=pts,
        market_features=("mkt_spread",),
    )
    hit = [r for r in result["rows"] if r["game_id"] == gid and r["feature"] == "mkt_spread"]
    assert hit
    assert hit[0]["feature_source_row_id"] is None
    assert hit[0]["leak"] is False
    assert result["verdict"] == "CLEAN"
    assert hit[0]["grade_source_row_id"] == "post"


def test_audit_flags_post_kickoff_feature_as_leak() -> None:
    """When as_of > kickoff, a post-kickoff feature snap is a LEAK."""
    cfg = WalkForwardConfig(seed=42)
    # Build one week where kickoff is BEFORE as_of (production misalignment case).
    as_of = pd.Timestamp(week_decision_as_of(2023, 5, cfg))
    kick = as_of - timedelta(days=2)
    games = pd.DataFrame(
        [
            {
                "game_id": 9999,
                "game_key": "k9999",
                "season": 2023,
                "week": 5,
                "event_time": kick.to_pydatetime(),
                "home_team": "Alabama",
                "away_team": "Auburn",
                "home_team_id": 1,
                "away_team_id": 2,
            }
        ]
    )
    # Pad to ≥20 week-points with clean future kickoffs.
    pad = _games_frame()
    pad = pad.loc[~((pad["season"] == 2023) & (pad["week"] == 5))]
    games = pd.concat([games, pad], ignore_index=True)
    pts = sample_week_points(games, n_min=20, seed=3)
    if (2023, 5) not in pts:
        pts = [(2023, 5), *pts][:20]
    post_kick = kick + timedelta(minutes=30)
    assert kick < post_kick < as_of
    snaps = pd.DataFrame(
        [
            {
                "snapshot_id": "post-kick",
                "game_id": 9999,
                "event_time": post_kick,
                "market": "spread",
                "side": "Alabama",
                "line": -1.0,
                "book": "b1",
                "season": 2023,
            },
            {
                "snapshot_id": "post-kick-t",
                "game_id": 9999,
                "event_time": post_kick,
                "market": "total",
                "side": "over",
                "line": 40.0,
                "book": "b1",
                "season": 2023,
            },
        ]
    )
    result = audit_market_feature_ladders(
        games,
        snaps,
        pd.DataFrame(),
        config=cfg,
        week_points=pts,
        market_features=("mkt_spread",),
    )
    assert result["verdict"] == "LEAK"
    hit = [r for r in result["rows"] if r["game_id"] == 9999 and r["feature"] == "mkt_spread"]
    assert hit and hit[0]["leak"] is True
    assert hit[0]["leak_reason"] == "feature_event_time_at_or_after_kickoff"
