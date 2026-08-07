"""Walk-forward harness tests (Task 16)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.walkforward import (
    PREDICTION_COLUMNS,
    InformationSetAuditError,
    WalkForwardConfig,
    WalkForwardHarness,
    assert_information_set_clean,
    audit_information_set,
    predictions_bytes,
    resolve_lines_for_games,
    run_shifted_label_test,
    week_decision_as_of,
)
from tests.fixtures.walkforward_stubs import (
    LeagueAverageMarginPredictor,
    RunningMarginRatingEngine,
)

# ---------------------------------------------------------------------------
# Synthetic data + PIT feature provider
# ---------------------------------------------------------------------------


def _kickoff(season: int, week: int, game_slot: int = 0) -> datetime:
    """Saturday kickoff after the week's Tuesday decision."""
    tuesday = week_decision_as_of(
        season, week, WalkForwardConfig(as_of_weekday=1, as_of_hour=6, as_of_minute=0)
    )
    return tuesday + timedelta(days=4, hours=game_slot)


def build_multi_season_games(
    seasons: Sequence[int] = (2019, 2020, 2021, 2022, 2023, 2024),
    weeks: Sequence[int] = (1, 2, 3, 4, 5),
    games_per_week: int = 2,
) -> pd.DataFrame:
    """Deterministic FBS-toy schedule spanning the acceptance window."""
    rows: list[dict[str, Any]] = []
    game_id = 1000
    rng = np.random.default_rng(0)
    for season in seasons:
        for week in weeks:
            for slot in range(games_per_week):
                home = 10 + (slot * 2) % 8
                away = 11 + (slot * 2) % 8
                home_pts = int(24 + rng.integers(0, 21) + 3)
                away_pts = int(24 + rng.integers(0, 21))
                start = _kickoff(season, week, slot)
                rows.append(
                    {
                        "game_id": game_id,
                        "game_key": f"{season}:T{home}:T{away}:{start.date().isoformat()}",
                        "season": season,
                        "week": week,
                        "event_time": start,
                        "home_team_id": home,
                        "away_team_id": away,
                        "home_points": home_pts,
                        "away_points": away_pts,
                        "neutral_site": False,
                    }
                )
                game_id += 1
    return pd.DataFrame(rows)


def build_team_history(games: pd.DataFrame) -> pd.DataFrame:
    """Per-team postgame facts for PIT feature construction."""
    rows: list[dict[str, Any]] = []
    for r in games.itertuples(index=False):
        margin = float(r.home_points) - float(r.away_points)
        rows.append(
            {
                "team_id": int(r.home_team_id),
                "event_time": r.event_time,
                "game_id": int(r.game_id),
                "margin_for": margin,
                "points_for": float(r.home_points),
            }
        )
        rows.append(
            {
                "team_id": int(r.away_team_id),
                "event_time": r.event_time,
                "game_id": int(r.game_id),
                "margin_for": -margin,
                "points_for": float(r.away_points),
            }
        )
    return pd.DataFrame(rows)


class PitMeanMarginFeatureProvider:
    """Honest provider: rolling mean margin using only ``event_time < as_of``."""

    def __init__(self, history: pd.DataFrame) -> None:
        self.history = history.copy()
        self.history["event_time"] = pd.to_datetime(self.history["event_time"], utc=True)

    def compute_game_features(
        self,
        games: pd.DataFrame,
        as_of: datetime,
        *,
        rating_state: Mapping[str, Any],
        market_features: bool,
    ) -> pd.DataFrame:
        bound = pd.Timestamp(as_of)
        eligible = self.history.loc[self.history["event_time"] < bound]
        rows: list[dict[str, Any]] = []
        for g in games.itertuples(index=False):
            hid, aid = int(g.home_team_id), int(g.away_team_id)
            h = eligible.loc[eligible["team_id"] == hid, "margin_for"]
            a = eligible.loc[eligible["team_id"] == aid, "margin_for"]
            home_form = float(h.mean()) if not h.empty else 0.0
            away_form = float(a.mean()) if not a.empty else 0.0
            rating_diff = float(rating_state.get(str(hid), 0.0)) - float(
                rating_state.get(str(aid), 0.0)
            )
            same = eligible.loc[eligible["game_id"] == int(g.game_id)]
            rows.append(
                {
                    "game_id": int(g.game_id),
                    "home_form": home_form,
                    "away_form": away_form,
                    "form_diff": home_form - away_form,
                    "rating_diff": rating_diff,
                    "market_available": 1.0 if market_features else 0.0,
                    "leaked_margin": (float(same.iloc[0]["margin_for"]) if not same.empty else 0.0),
                }
            )
        return pd.DataFrame(rows)


class LeakyMeanMarginFeatureProvider(PitMeanMarginFeatureProvider):
    """Ignores as_of — plants future rows into every feature vector."""

    def compute_game_features(
        self,
        games: pd.DataFrame,
        as_of: datetime,
        *,
        rating_state: Mapping[str, Any],
        market_features: bool,
    ) -> pd.DataFrame:
        del as_of
        far = datetime(2099, 1, 1, tzinfo=UTC)
        return super().compute_game_features(
            games,
            far,
            rating_state=rating_state,
            market_features=market_features,
        )


class CheatingPredictor:
    """Reads ``leaked_margin`` — beats chance on shifted-label data."""

    model_version = "cheat-v0"

    def fit(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame,
        *,
        sample_weight: pd.Series | None = None,
    ) -> None:
        del features, labels, sample_weight

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        if "leaked_margin" not in features.columns:
            margins = np.zeros(len(features))
        else:
            margins = features["leaked_margin"].astype(float).to_numpy()
        return pd.DataFrame(
            {
                "game_id": features["game_id"].to_numpy(),
                "pred_margin": margins,
                "pred_total": np.full(len(features), 55.0),
            }
        )


def _make_harness(
    games: pd.DataFrame,
    *,
    config: WalkForwardConfig | None = None,
    leaky: bool = False,
) -> tuple[WalkForwardHarness, PitMeanMarginFeatureProvider]:
    history = build_team_history(games)
    provider: PitMeanMarginFeatureProvider
    if leaky:
        provider = LeakyMeanMarginFeatureProvider(history)
    else:
        provider = PitMeanMarginFeatureProvider(history)
    cfg = config or WalkForwardConfig(
        test_seasons=(2019, 2021, 2022, 2023, 2024),
        continuity_seasons=(2020,),
        retrain_weeks=(5,),
        seed=42,
        model_version="placeholder-league-avg-v0",
    )
    predictor = LeagueAverageMarginPredictor(model_version=cfg.model_version)
    engine = RunningMarginRatingEngine()
    return WalkForwardHarness(cfg, predictor, provider, engine), provider


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_week_decision_as_of_tuesday_et() -> None:
    cfg = WalkForwardConfig()
    as_of = week_decision_as_of(2023, 1, cfg)
    assert as_of.tzinfo is not None
    # 2023 Labor Day Monday = Sept 4 → Tuesday Sept 5 06:00 ET = 10:00 UTC (EDT).
    assert as_of == datetime(2023, 9, 5, 10, 0, tzinfo=UTC)


def test_placeholder_predicts_fitted_league_average() -> None:
    pred = LeagueAverageMarginPredictor(default_margin=2.5)
    labels = pd.DataFrame(
        {
            "game_id": [1, 2, 3],
            "realized_margin": [10.0, -4.0, 6.0],
            "realized_total": [50.0, 60.0, 55.0],
        }
    )
    pred.fit(pd.DataFrame({"game_id": [1, 2, 3]}), labels)
    out = pred.predict(pd.DataFrame({"game_id": [10, 11]}))
    assert list(out["pred_margin"]) == pytest.approx([4.0, 4.0])
    assert list(out["pred_total"]) == pytest.approx([55.0, 55.0])


def test_determinism_byte_identical_prediction_tables() -> None:
    games = build_multi_season_games()
    cfg = WalkForwardConfig(
        test_seasons=(2019, 2021, 2022),
        continuity_seasons=(2020,),
        retrain_weeks=(3, 5),
        seed=7,
    )
    h1, _ = _make_harness(games, config=cfg)
    h2, _ = _make_harness(games, config=cfg)
    r1 = h1.run(games)
    r2 = h2.run(games)
    assert predictions_bytes(r1.predictions) == predictions_bytes(r2.predictions)
    assert not r1.predictions.empty


def test_2020_excluded_from_headline_included_for_continuity() -> None:
    games = build_multi_season_games(seasons=(2019, 2020, 2021), weeks=(1, 2))
    cfg = WalkForwardConfig(
        test_seasons=(2019, 2021),
        continuity_seasons=(2020,),
        retrain_weeks=(),
        seed=1,
    )
    harness, _ = _make_harness(games, config=cfg)
    result = harness.run(games)
    assert set(result.predictions["season"].unique()) == {2019, 2020, 2021}
    covid = result.predictions.loc[result.predictions["season"] == 2020]
    assert covid["exclude_from_headline"].all()
    assert covid["continuity_season"].all()
    headline = result.headline_predictions()
    assert 2020 not in set(headline["season"].unique())

    # Sensitivity flag: include 2020 in headline.
    cfg2 = WalkForwardConfig(
        test_seasons=(2019, 2021),
        continuity_seasons=(2020,),
        retrain_weeks=(),
        seed=1,
        include_continuity_in_headline=True,
    )
    h2, _ = _make_harness(games, config=cfg2)
    r2 = h2.run(games)
    assert not r2.predictions.loc[r2.predictions["season"] == 2020, "exclude_from_headline"].any()


def test_information_set_audit_passes_on_20_plus_week_points() -> None:
    games = build_multi_season_games(
        seasons=(2019, 2020, 2021, 2022),
        weeks=(1, 2, 3, 4, 5, 6),
        games_per_week=2,
    )
    cfg = WalkForwardConfig(
        test_seasons=(2019, 2021, 2022),
        continuity_seasons=(2020,),
        retrain_weeks=(5,),
        seed=42,
    )
    harness, provider = _make_harness(games, config=cfg)
    result = harness.run(games)

    week_points = (
        result.feature_log[["season", "week"]]
        .drop_duplicates()
        .sort_values(["season", "week"], kind="mergesort")
    )
    points = [(int(r.season), int(r.week)) for r in week_points.itertuples(index=False)]
    assert len(points) >= 20

    # Rebuild rating snapshots by replaying (same deterministic engine).
    rating_snapshots: dict[tuple[int, int], dict[str, Any]] = {}
    engine = RunningMarginRatingEngine()
    for season in cfg.all_replay_seasons():
        sg = games.loc[games["season"] == season]
        weeks = sorted(int(w) for w in sg["week"].unique())
        if not weeks:
            continue
        first_as_of = week_decision_as_of(season, weeks[0], cfg)
        engine.initialize_season(season, first_as_of - timedelta(seconds=1))
        for week in weeks:
            rating_snapshots[(season, week)] = engine.state_snapshot()
            engine.update_after_games(sg.loc[sg["week"] == week])

    audit = audit_information_set(
        result.feature_log,
        provider,
        games,
        rating_snapshots=rating_snapshots,
        market_features=cfg.market_features_available,
        sample_week_points=points,
    )
    assert audit.n_week_points >= 20
    assert audit.passed, audit.mismatches[:3]
    assert_information_set_clean(audit)


def test_information_set_audit_catches_leaky_provider() -> None:
    games = build_multi_season_games(seasons=(2022,), weeks=(1, 2, 3), games_per_week=2)
    cfg = WalkForwardConfig(
        test_seasons=(2022,),
        continuity_seasons=(),
        retrain_weeks=(),
        seed=0,
    )
    # Run with honest provider to get a feature log, then audit with leaky
    # recompute that disagrees — OR run leaky harness and audit with honest.
    harness_leaky, leaky = _make_harness(games, config=cfg, leaky=True)
    result = harness_leaky.run(games)
    honest = PitMeanMarginFeatureProvider(build_team_history(games))

    engine = RunningMarginRatingEngine()
    snaps: dict[tuple[int, int], dict[str, Any]] = {}
    for week in (1, 2, 3):
        snaps[(2022, week)] = engine.state_snapshot()
        engine.update_after_games(games.loc[games["week"] == week])

    audit = audit_information_set(
        result.feature_log,
        honest,
        games,
        rating_snapshots=snaps,
        sample_week_points=[(2022, 1), (2022, 2), (2022, 3)],
    )
    # Leaky harness fed future-contaminated features; honest recompute differs.
    assert not audit.passed
    with pytest.raises(InformationSetAuditError):
        assert_information_set_clean(audit)
    del leaky


def test_shifted_label_hook_is_diagnostic_only() -> None:
    """A-8: scoring at chance on this hook is not a cleanliness gate.

    The null is invalid (strength persists), so ``passed`` is meaningless.
    The function survives only as a cheater detector — see the next test.
    """
    games = build_multi_season_games(seasons=(2022, 2023), weeks=(1, 2, 3), games_per_week=3)
    history = build_team_history(games)
    provider = PitMeanMarginFeatureProvider(history)
    predictor = LeagueAverageMarginPredictor()
    lab = games.loc[games["season"] == 2022].copy()
    lab["realized_margin"] = lab["home_points"].astype(float) - lab["away_points"].astype(float)
    lab["realized_total"] = lab["home_points"].astype(float) + lab["away_points"].astype(float)
    predictor.fit(pd.DataFrame({"game_id": lab["game_id"]}), lab)

    past = games.loc[games["season"] == 2022].copy()
    shifted_as_of = datetime(2023, 1, 15, tzinfo=UTC)
    result = run_shifted_label_test(
        predictor,
        past,
        provider,
        shifted_as_of,
        chance_constant=None,
        tolerance=0.05,
    )
    assert result.n > 0
    assert result.null_is_invalid
    # Do not assert result.passed — that is the retired gate.


def test_shifted_label_hook_detects_cheater() -> None:
    """Still useful: a model that reads leaked outcomes will beat chance here."""
    games = build_multi_season_games(seasons=(2022,), weeks=(1, 2, 3), games_per_week=4)
    provider = PitMeanMarginFeatureProvider(build_team_history(games))
    cheater = CheatingPredictor()
    shifted_as_of = datetime(2023, 1, 15, tzinfo=UTC)
    result = run_shifted_label_test(
        cheater,
        games,
        provider,
        shifted_as_of,
        chance_constant=None,
        tolerance=0.05,
    )
    assert result.n > 0
    assert result.null_is_invalid
    assert not result.passed, result.detail
    assert result.model_score < result.chance_score * 0.5


def test_placeholder_e2e_2019_2024_metrics_ready_table(tmp_path: Any) -> None:
    games = build_multi_season_games(
        seasons=(2019, 2020, 2021, 2022, 2023, 2024),
        weeks=(1, 2, 3, 4, 5),
        games_per_week=2,
    )
    cfg = WalkForwardConfig(
        test_seasons=(2019, 2021, 2022, 2023, 2024),
        continuity_seasons=(2020,),
        retrain_weeks=(5,),
        seed=42,
    )
    harness, _ = _make_harness(games, config=cfg)
    # Seed labels from a fictional 2018 so Week-1 2019 has a fitted mean.
    seed = games.loc[games["season"] == 2019].head(0).copy()
    result = harness.run(games, train_labels_seed=seed)

    assert set(result.predictions["season"].unique()) == {
        2019,
        2020,
        2021,
        2022,
        2023,
        2024,
    }
    for col in PREDICTION_COLUMNS:
        assert col in result.predictions.columns, col
    assert result.predictions["pred_margin"].notna().all()
    assert result.predictions["prediction_id"].is_unique
    assert result.predictions["feature_hash"].notna().all()

    path = result.store_predictions(tmp_path / "oof_predictions.parquet")
    loaded = pd.read_parquet(path)
    assert len(loaded) == len(result.predictions)
    assert predictions_bytes(loaded) == predictions_bytes(result.predictions)

    headline = result.headline_predictions()
    assert 2020 not in set(headline["season"])
    assert headline["realized_margin"].notna().all()


def test_line_resolution_cfbd_vs_snapshot_regimes() -> None:
    games = build_multi_season_games(seasons=(2019, 2021), weeks=(1,), games_per_week=1)
    cfg = WalkForwardConfig()
    g2019 = games.loc[games["season"] == 2019]
    g2021 = games.loc[games["season"] == 2021]
    as_of_2019 = week_decision_as_of(2019, 1, cfg)
    as_of_2021 = week_decision_as_of(2021, 1, cfg)

    cfbd = pd.DataFrame(
        [
            {
                "game_id": int(g2019.iloc[0]["game_id"]),
                "book": "consensus",
                "line_type": "open",
                "spread": -3.5,
                "total": 52.0,
            },
            {
                "game_id": int(g2019.iloc[0]["game_id"]),
                "book": "consensus",
                "line_type": "close",
                "spread": -4.0,
                "total": 53.0,
            },
        ]
    )
    snap_gid = int(g2021.iloc[0]["game_id"])
    snapshots = pd.DataFrame(
        [
            {
                "game_id": snap_gid,
                "game_key": str(g2021.iloc[0]["game_key"]),
                "book": "draftkings",
                "market": "spread",
                "line": -7.0,
                "event_time": as_of_2021 - timedelta(minutes=2),
                "n_books_available": 3,
            },
            {
                "game_id": snap_gid,
                "game_key": str(g2021.iloc[0]["game_key"]),
                "book": "draftkings",
                "market": "total",
                "line": 55.5,
                "event_time": as_of_2021 - timedelta(minutes=2),
                "n_books_available": 3,
            },
        ]
    )

    r2019 = resolve_lines_for_games(
        g2019, as_of_2019, snapshots=None, cfbd_lines=cfbd, config=cfg, closing=False
    )
    assert r2019.iloc[0]["line_source"] == "cfbd_open"
    assert r2019.iloc[0]["spread"] == pytest.approx(-3.5)

    r2021 = resolve_lines_for_games(
        g2021,
        as_of_2021,
        snapshots=snapshots,
        cfbd_lines=cfbd,
        config=cfg,
        closing=False,
    )
    assert r2021.iloc[0]["line_source"].startswith("odds_api_snapshot")
    assert r2021.iloc[0]["spread"] == pytest.approx(-7.0)
    # CFBD must not leak into snapshot-backed seasons even if present.
    assert "cfbd" not in r2021.iloc[0]["line_source"]

    # Closing evaluation: when snapshots are empty, fall back to CFBD close.
    cfbd_2021 = pd.DataFrame(
        [
            {
                "game_id": snap_gid,
                "book": "consensus",
                "line_type": "close",
                "spread": -6.5,
                "total": 54.0,
            }
        ]
    )
    r_close = resolve_lines_for_games(
        g2021,
        as_of_2021,
        snapshots=None,
        cfbd_lines=cfbd_2021,
        config=cfg,
        closing=True,
    )
    assert r_close.iloc[0]["line_source"] == "cfbd_close_eval"
    assert r_close.iloc[0]["spread"] == pytest.approx(-6.5)
    # Bet-time as-of still null without snapshots (no CFBD feature leak).
    r_asof_null = resolve_lines_for_games(
        g2021,
        as_of_2021,
        snapshots=None,
        cfbd_lines=cfbd_2021,
        config=cfg,
        closing=False,
    )
    assert r_asof_null.iloc[0]["line_source"] == "null"


def test_retrain_events_fire_at_configured_weeks() -> None:
    games = build_multi_season_games(seasons=(2022,), weeks=(1, 2, 3, 4, 5), games_per_week=1)
    cfg = WalkForwardConfig(
        test_seasons=(2022,),
        continuity_seasons=(),
        retrain_weeks=(3, 5),
        seed=0,
    )
    harness, _ = _make_harness(games, config=cfg)
    result = harness.run(games)
    weeks_hit = {(e["season"], e["week"]) for e in result.retrain_events}
    assert (2022, 0) in weeks_hit  # offseason
    assert (2022, 3) in weeks_hit
    assert (2022, 5) in weeks_hit
