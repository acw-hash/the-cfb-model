"""MKT-2019-FIX — snapshot feature path never uses CFBD; provenance recorded."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.d6_eval import load_cfbd_lines
from ncaa_quant.evaluation.production_stack import ProductionFeatureProvider
from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    WalkForwardError,
    resolve_lines_for_games,
    week_decision_as_of,
)
from ncaa_quant.features.market_lines import provenance_from_line_source

ROOT = Path(__file__).resolve().parents[2]
STAGED = ROOT / "data" / "staged"
PRED_TUE = (
    ROOT
    / "data"
    / "backtests"
    / "task23_market_aware_reduced_v2_tue"
    / "full"
    / "predictions.parquet"
)
PRED_ARCHIVED = (
    ROOT / "docs" / "notes" / "_artifacts" / "mkt_2019_fix" / "contaminated_tue_predictions.parquet"
)
EXPECTED_OLD_VIOLATIONS = 757
EXPECTED_PRED_ROWS_2019 = 763


def _kick(season: int, week: int) -> datetime:
    return datetime(season, 9, 7, 19, 30, tzinfo=UTC) + timedelta(days=7 * (week - 1))


def _game(game_id: int, season: int, week: int = 2) -> dict[str, object]:
    return {
        "game_id": game_id,
        "game_key": f"k{game_id}",
        "season": season,
        "week": week,
        "event_time": _kick(season, week),
        "home_team": "Home U",
        "away_team": "Away U",
        "home_team_id": 1,
        "away_team_id": 2,
    }


def _cfbd_close_only(
    game_id: int, *, spread: float = -7.5, total: float = 55.0, n_books: int = 4
) -> pd.DataFrame:
    rows = []
    for i, book in enumerate(("a", "b", "c", "d")[:n_books]):
        rows.append(
            {
                "game_id": game_id,
                "book": book,
                "line_type": "close",
                "spread": spread + 0.25 * i,
                "total": total,
            }
        )
    return pd.DataFrame(rows)


def _legacy_contaminated_relabel(resolved: pd.DataFrame) -> pd.DataFrame:
    """Pre-MKT-2019-FIX provider stamp: any non-null line_source → snapshots."""
    out = resolved.copy()
    src = out["line_source"].astype(str)
    out["market_provenance"] = np.where(src.eq("null"), "null", "snapshots")
    out["mkt_spread"] = pd.to_numeric(out["spread"], errors="coerce")
    out["mkt_total"] = pd.to_numeric(out["total"], errors="coerce")
    out["mkt_n_books"] = pd.to_numeric(out["n_books"], errors="coerce").fillna(0).astype(int)
    spread_null = ~np.isfinite(out["mkt_spread"].to_numpy(dtype=float))
    total_null = ~np.isfinite(out["mkt_total"].to_numpy(dtype=float))
    out["mkt_is_missing"] = np.where(spread_null & total_null, 1.0, 0.0)
    return out


def _count_close_as_snapshots_violations(frame: pd.DataFrame) -> int:
    src = frame["line_source"].astype(str)
    prov = frame["market_provenance"].astype(str)
    missing = pd.to_numeric(frame["mkt_is_missing"], errors="coerce").fillna(1.0)
    return int(((src == "cfbd_close") & (prov == "snapshots") & (missing == 0.0)).sum())


def test_provenance_from_line_source_never_inferred() -> None:
    assert provenance_from_line_source(None) == "null"
    assert provenance_from_line_source("null") == "null"
    assert provenance_from_line_source("") == "null"
    assert provenance_from_line_source("odds_api_snapshot") == "snapshots"
    assert provenance_from_line_source("odds_api_snapshot_fallback") == "snapshots"
    assert provenance_from_line_source("cfbd_close") == "cfbd"
    assert provenance_from_line_source("cfbd_open") == "cfbd"
    assert provenance_from_line_source("cfbd_close_eval") == "cfbd"
    # Non-null garbage must not become snapshots.
    assert provenance_from_line_source("something_else") == "null"


def test_feature_resolution_closing_true_is_hard_error() -> None:
    cfg = WalkForwardConfig(market_feature_source="snapshots")
    games = pd.DataFrame([_game(1, 2021)])
    with pytest.raises(WalkForwardError, match="closing=True is forbidden"):
        resolve_lines_for_games(
            games,
            week_decision_as_of(2021, 2, cfg),
            snapshots=pd.DataFrame(),
            cfbd_lines=_cfbd_close_only(1),
            config=cfg,
            closing=True,
            for_features=True,
        )


def test_2019_snapshots_feature_path_null_is_missing() -> None:
    """2019 + snapshots config + no Odds rows → every mkt_* null + is_missing."""
    cfg = WalkForwardConfig(market_feature_source="snapshots")
    games = pd.DataFrame([_game(401110773, 2019, 2)])
    cfbd = _cfbd_close_only(401110773, spread=-54.75, total=65.0, n_books=4)
    as_of = week_decision_as_of(2019, 2, cfg)
    provider = ProductionFeatureProvider(config=cfg, snapshots=pd.DataFrame(), cfbd_lines=cfbd)
    mkt = provider._resolve_market_lines(games, as_of)
    assert len(mkt) == 1
    row = mkt.iloc[0]
    assert not np.isfinite(float(row["mkt_spread"]))
    assert not np.isfinite(float(row["mkt_total"]))
    assert int(row["mkt_is_missing"]) == 1
    assert str(row["line_source"]) == "null"
    assert str(row["market_provenance"]) == "null"


def test_legacy_relabel_fails_where_fix_passes() -> None:
    """757-row mechanism on a one-row fixture: old stamp violates, new does not."""
    cfg = WalkForwardConfig(market_feature_source="snapshots")
    games = pd.DataFrame([_game(401110773, 2019, 2)])
    cfbd = _cfbd_close_only(401110773, spread=-54.75, total=65.0, n_books=4)
    as_of = week_decision_as_of(2019, 2, cfg)
    # Old feature path == evaluation as-of (CFBD for season<2021) + relabel.
    old_ladder = resolve_lines_for_games(
        games, as_of, snapshots=pd.DataFrame(), cfbd_lines=cfbd, config=cfg, closing=False
    )
    legacy = _legacy_contaminated_relabel(old_ladder)
    assert str(legacy.iloc[0]["line_source"]) == "cfbd_close"
    assert str(legacy.iloc[0]["market_provenance"]) == "snapshots"
    assert float(legacy.iloc[0]["mkt_is_missing"]) == 0.0
    assert _count_close_as_snapshots_violations(legacy) == 1

    provider = ProductionFeatureProvider(config=cfg, snapshots=pd.DataFrame(), cfbd_lines=cfbd)
    fixed = provider._resolve_market_lines(games, as_of)
    assert _count_close_as_snapshots_violations(fixed) == 0
    assert str(fixed.iloc[0]["market_provenance"]) == "null"
    assert str(fixed.iloc[0]["line_source"]) == "null"


def test_evaluation_asof_2019_still_uses_cfbd_open() -> None:
    """Harness as-of lines (not features) still record CFBD for 2019."""
    cfg = WalkForwardConfig(market_feature_source="snapshots")
    games = pd.DataFrame([_game(9, 2019, 1)])
    cfbd = pd.DataFrame(
        [
            {"game_id": 9, "book": "c", "line_type": "open", "spread": -3.5, "total": 52.0},
            {"game_id": 9, "book": "c", "line_type": "close", "spread": -4.0, "total": 53.0},
        ]
    )
    resolved = resolve_lines_for_games(
        games,
        week_decision_as_of(2019, 1, cfg),
        snapshots=None,
        cfbd_lines=cfbd,
        config=cfg,
        closing=False,
        for_features=False,
    )
    assert resolved.iloc[0]["line_source"] == "cfbd_open"
    assert resolved.iloc[0]["spread"] == pytest.approx(-3.5)


def test_a6_cfbd_open_close_still_works_2021_2024() -> None:
    cfg = WalkForwardConfig(
        test_seasons=(2021, 2022, 2023, 2024),
        continuity_seasons=(),
        market_feature_source="cfbd_open_close",
    )
    cfg.validate_ablations()
    games = pd.DataFrame([_game(2021, 2022, 3)])
    cfbd = pd.DataFrame(
        [
            {"game_id": 2021, "book": "a", "line_type": "close", "spread": -6.5, "total": 54.0},
            {"game_id": 2021, "book": "a", "line_type": "open", "spread": -6.0, "total": 53.5},
        ]
    )
    provider = ProductionFeatureProvider(config=cfg, snapshots=pd.DataFrame(), cfbd_lines=cfbd)
    mkt = provider._resolve_market_lines(games, week_decision_as_of(2022, 3, cfg))
    assert str(mkt.iloc[0]["market_provenance"]) == "cfbd"
    assert str(mkt.iloc[0]["line_source"]).startswith("cfbd")
    assert np.isfinite(float(mkt.iloc[0]["mkt_spread"]))
    assert int(mkt.iloc[0]["mkt_is_missing"]) == 0


def test_a6_outside_window_still_hard_errors() -> None:
    cfg = WalkForwardConfig(
        test_seasons=(2019,), continuity_seasons=(), market_feature_source="cfbd_open_close"
    )
    with pytest.raises(WalkForwardError, match="2021-2025"):
        cfg.validate_ablations()


def test_provenance_null_when_unresolved() -> None:
    cfg = WalkForwardConfig(market_feature_source="snapshots")
    games = pd.DataFrame([_game(50, 2023)])
    provider = ProductionFeatureProvider(config=cfg, snapshots=pd.DataFrame(), cfbd_lines=None)
    mkt = provider._resolve_market_lines(games, week_decision_as_of(2023, 2, cfg))
    assert str(mkt.iloc[0]["line_source"]) == "null"
    assert str(mkt.iloc[0]["market_provenance"]) == "null"
    assert int(mkt.iloc[0]["mkt_is_missing"]) == 1


def test_cfbd_sourced_row_never_reads_snapshots() -> None:
    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        market_feature_source="cfbd_open_close",
    )
    games = pd.DataFrame([_game(88, 2023)])
    cfbd = _cfbd_close_only(88)
    provider = ProductionFeatureProvider(
        config=cfg,
        snapshots=pd.DataFrame(
            [
                {
                    "game_id": 88,
                    "book": "dk",
                    "market": "spread",
                    "side": "Home U",
                    "line": -99.0,
                    "event_time": _kick(2023, 2) - timedelta(hours=12),
                    "snapshot_id": "snap-sentinel",
                }
            ]
        ),
        cfbd_lines=cfbd,
    )
    mkt = provider._resolve_market_lines(games, week_decision_as_of(2023, 2, cfg))
    assert str(mkt.iloc[0]["market_provenance"]) != "snapshots"
    assert str(mkt.iloc[0]["market_provenance"]) == "cfbd"
    assert str(mkt.iloc[0]["line_source"]).startswith("cfbd")


def _prediction_2019_ids() -> list[int] | None:
    for path in (PRED_ARCHIVED, PRED_TUE):
        if path.is_file():
            preds = pd.read_parquet(path, columns=["season", "game_id"])
            ids = preds.loc[preds["season"].astype(int) == 2019, "game_id"].astype(int).tolist()
            if ids:
                return ids
    return None


def _staged_2019_available() -> bool:
    return (STAGED / "games").exists() and (STAGED / "lines_historical").exists()


@pytest.mark.skipif(not _staged_2019_available(), reason="staged 2019 games/lines required")
def test_2019_old_behavior_reproduces_757_violation() -> None:
    """STOP finding: 757 prediction rows were cfbd_close relabeled snapshots."""
    ids = _prediction_2019_ids()
    if ids is None:
        pytest.skip("2019 Tuesday predictions not archived or present")
    assert len(ids) == EXPECTED_PRED_ROWS_2019
    games = load_staged_games(STAGED, [2019])
    games = games.loc[games["game_id"].astype(int).isin(ids)].copy()
    assert len(games) == EXPECTED_PRED_ROWS_2019
    cfbd = load_cfbd_lines(STAGED, seasons=[2019])
    cfg = WalkForwardConfig(market_feature_source="snapshots")
    as_of = week_decision_as_of(2019, 2, cfg)
    # Old path: per-week as_of is irrelevant for CFBD open/close (no event_time).
    old_ladder = resolve_lines_for_games(
        games, as_of, snapshots=pd.DataFrame(), cfbd_lines=cfbd, config=cfg, closing=False
    )
    legacy = _legacy_contaminated_relabel(old_ladder)
    n_viol = _count_close_as_snapshots_violations(legacy)
    assert n_viol == EXPECTED_OLD_VIOLATIONS, (
        f"old behavior violations={n_viol} expected={EXPECTED_OLD_VIOLATIONS}"
    )


@pytest.mark.skipif(not _staged_2019_available(), reason="staged 2019 games/lines required")
def test_2019_staged_snapshots_config_all_mkt_null_after_fix() -> None:
    ids = _prediction_2019_ids()
    if ids is None:
        pytest.skip("2019 Tuesday predictions not archived or present")
    games = load_staged_games(STAGED, [2019])
    games = games.loc[games["game_id"].astype(int).isin(ids)].copy()
    cfbd = load_cfbd_lines(STAGED, seasons=[2019])
    cfg = WalkForwardConfig(market_feature_source="snapshots")
    provider = ProductionFeatureProvider(config=cfg, snapshots=pd.DataFrame(), cfbd_lines=cfbd)
    as_of = week_decision_as_of(2019, 2, cfg)
    mkt = provider._resolve_market_lines(games, as_of)
    assert len(mkt) == EXPECTED_PRED_ROWS_2019
    assert (
        mkt["mkt_spread"].isna().all()
        or (~np.isfinite(mkt["mkt_spread"].to_numpy(dtype=float))).all()
    )
    assert (
        mkt["mkt_total"].isna().all()
        or (~np.isfinite(mkt["mkt_total"].to_numpy(dtype=float))).all()
    )
    assert (pd.to_numeric(mkt["mkt_is_missing"], errors="coerce") == 1.0).all()
    assert (mkt["line_source"].astype(str) == "null").all()
    assert (mkt["market_provenance"].astype(str) == "null").all()
    assert _count_close_as_snapshots_violations(mkt) == 0
