"""Mapping-layer model heads tests (Task 17)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    WalkForwardHarness,
    week_decision_as_of,
)
from ncaa_quant.models.heads import (
    DEFAULT_SEASON_HALF_LIFE,
    CatBoostMuHead,
    ElasticNetMuHead,
    FeatureSignatureError,
    HeadTrainConfig,
    LightGBMMarginMuHead,
    LightGBMQuantileHead,
    LightGBMSigmaHead,
    LightGBMTotalMuHead,
    NGBoostNormalHead,
    XGBoostMuHead,
    abs_residual_labels,
    enforce_quantile_order,
    monotone_constraints_for,
    resolve_sample_weight,
    time_decay_weights,
)
from ncaa_quant.ratings.elo_baseline import EloConfig, run_elo
from tests.fixtures.walkforward_stubs import RunningMarginRatingEngine

# Keep GBDT fits cheap in CI.
_FAST = HeadTrainConfig(n_estimators=40, learning_rate=0.1, max_depth=3, num_leaves=8)
_FAST_NG = HeadTrainConfig(n_estimators=25, learning_rate=0.05, max_depth=2)


def _kickoff(season: int, week: int, game_slot: int = 0) -> datetime:
    tuesday = week_decision_as_of(
        season, week, WalkForwardConfig(as_of_weekday=1, as_of_hour=6, as_of_minute=0)
    )
    return tuesday + timedelta(days=4, hours=game_slot)


def build_games(
    seasons: Sequence[int] = (2019, 2020, 2021, 2022),
    weeks: Sequence[int] = (1, 2, 3, 4, 5),
    games_per_week: int = 4,
) -> pd.DataFrame:
    """Deterministic schedule with margin ≈ rating_diff + noise."""
    rows: list[dict[str, Any]] = []
    game_id = 5000
    rng = np.random.default_rng(17)
    strength = {tid: float(rng.normal(0, 12)) for tid in range(10, 26)}
    for season in seasons:
        for week in weeks:
            for slot in range(games_per_week):
                home = 10 + (slot * 2 + week) % 16
                away = 11 + (slot * 2 + week) % 16
                if home == away:
                    away = 10 + (away + 3) % 16
                rd = strength[home] - strength[away] + 2.5
                margin = rd + float(rng.normal(0, 6))
                total = 50.0 + 0.3 * (strength[home] + strength[away]) + float(rng.normal(0, 5))
                home_pts = max(0, int(round((total + margin) / 2)))
                away_pts = max(0, int(round((total - margin) / 2)))
                start = _kickoff(season, week, slot)
                rows.append(
                    {
                        "game_id": game_id,
                        "game_key": f"{season}:T{home}:T{away}:{start.date().isoformat()}",
                        "season": season,
                        "week": week,
                        "event_time": start,
                        "start_date": start,
                        "home_team_id": home,
                        "away_team_id": away,
                        "home_points": home_pts,
                        "away_points": away_pts,
                        "neutral_site": False,
                        "completed": True,
                    }
                )
                game_id += 1
    return pd.DataFrame(rows)


def build_team_history(games: pd.DataFrame) -> pd.DataFrame:
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
                "points_against": float(r.away_points),
            }
        )
        rows.append(
            {
                "team_id": int(r.away_team_id),
                "event_time": r.event_time,
                "game_id": int(r.game_id),
                "margin_for": -margin,
                "points_for": float(r.away_points),
                "points_against": float(r.home_points),
            }
        )
    return pd.DataFrame(rows)


class RichPitFeatureProvider:
    """PIT features including rating_diff for monotone tests / harness."""

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
            hp = eligible.loc[eligible["team_id"] == hid, "points_for"]
            ap = eligible.loc[eligible["team_id"] == aid, "points_for"]
            rating_diff = float(rating_state.get(str(hid), 0.0)) - float(
                rating_state.get(str(aid), 0.0)
            )
            n_home = float(len(h))
            n_away = float(len(a))
            rating_uncertainty = 1.0 / np.sqrt(max(n_home + n_away, 1.0))
            rows.append(
                {
                    "game_id": int(g.game_id),
                    "home_form": home_form,
                    "away_form": away_form,
                    "form_diff": home_form - away_form,
                    "rating_diff": rating_diff,
                    "home_ppg": float(hp.mean()) if not hp.empty else 24.0,
                    "away_ppg": float(ap.mean()) if not ap.empty else 24.0,
                    "rating_uncertainty": rating_uncertainty,
                    "rest_proxy": float(g.week),
                    "market_available": 1.0 if market_features else 0.0,
                }
            )
        return pd.DataFrame(rows)


def _xy_from_games(games: pd.DataFrame, history: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    provider = RichPitFeatureProvider(history)
    feat_rows: list[pd.DataFrame] = []
    for r in games.itertuples(index=False):
        as_of = pd.Timestamp(r.event_time).to_pydatetime().replace(tzinfo=UTC) - timedelta(hours=1)
        g = games.loc[games["game_id"] == r.game_id]
        state = {
            str(int(tid)): float(
                history.loc[
                    (history["team_id"] == tid) & (history["event_time"] < as_of),
                    "margin_for",
                ].sum()
            )
            for tid in history["team_id"].unique()
        }
        feat_rows.append(
            provider.compute_game_features(g, as_of, rating_state=state, market_features=False)
        )
    features = pd.concat(feat_rows, ignore_index=True)
    labels = games[["game_id", "season", "week", "home_points", "away_points"]].copy()
    labels["realized_margin"] = labels["home_points"].astype(float) - labels["away_points"].astype(
        float
    )
    labels["realized_total"] = labels["home_points"].astype(float) + labels["away_points"].astype(
        float
    )
    return features, labels


def test_time_decay_default_half_life_documented() -> None:
    assert DEFAULT_SEASON_HALF_LIFE == 2.0
    w = time_decay_weights([2020, 2021, 2022], half_life=2.0, reference_season=2022)
    assert w[-1] == pytest.approx(1.0)
    assert w[0] == pytest.approx(0.5)
    assert time_decay_weights([]).shape == (0,)
    with pytest.raises(ValueError):
        time_decay_weights([2020], half_life=0.0)
    ones = resolve_sample_weight(n=3, seasons=None, sample_weight=None)
    assert ones.tolist() == [1.0, 1.0, 1.0]
    explicit = resolve_sample_weight(n=2, seasons=None, sample_weight=pd.Series([0.5, 1.5]))
    assert explicit.tolist() == [0.5, 1.5]


def test_monotone_constraints_vector_for_margin() -> None:
    names = ["home_form", "rating_diff", "noise", "elo_diff"]
    assert monotone_constraints_for(names, target="margin") == [0, 1, 0, 1]
    assert monotone_constraints_for(names, target="total") == [0, 0, 0, 0]


def test_monotone_constraint_respected_on_constructed_inputs() -> None:
    rng = np.random.default_rng(0)
    n = 300
    rating_diff = rng.normal(0, 12, n)
    noise_feat = rng.normal(0, 1, n)
    y = 0.9 * rating_diff + rng.normal(0, 4, n)
    features = pd.DataFrame(
        {
            "game_id": np.arange(n),
            "rating_diff": rating_diff,
            "noise_feat": noise_feat,
        }
    )
    labels = pd.DataFrame(
        {
            "game_id": np.arange(n),
            "season": np.full(n, 2022),
            "realized_margin": y,
        }
    )
    head = LightGBMMarginMuHead(train=_FAST, seed=0, model_version="mono-test")
    head.fit(features, labels)
    assert head.monotone_constraints == [1, 0]

    base = features.iloc[[0]].copy()
    preds: list[float] = []
    for v in (-25.0, -10.0, 0.0, 10.0, 25.0):
        row = base.copy()
        row["rating_diff"] = v
        pred = head.predict(row)
        preds.append(float(pred["pred_margin"].iloc[0]))
    for i in range(len(preds) - 1):
        assert preds[i + 1] >= preds[i] - 1e-9, preds


def test_feature_signature_mismatch_raises() -> None:
    games = build_games(seasons=(2021,), weeks=(1, 2, 3), games_per_week=3)
    history = build_team_history(games)
    features, labels = _xy_from_games(games, history)
    head = LightGBMMarginMuHead(train=_FAST, seed=1)
    head.fit(features, labels)

    bad = features.rename(columns={"rating_diff": "rating_diff_renamed"})
    with pytest.raises(FeatureSignatureError):
        head.predict(bad)

    with pytest.raises(FeatureSignatureError):
        head.predict(features.drop(columns=["rating_diff"]))


def test_quantile_crossing_sort_and_warn() -> None:
    mat = np.array([[10.0, 5.0, 1.0], [1.0, 2.0, 3.0]])
    ordered, crossed = enforce_quantile_order(mat, quantiles=(0.05, 0.5, 0.95))
    assert crossed is True
    assert ordered[0].tolist() == [1.0, 5.0, 10.0]
    assert ordered[1].tolist() == [1.0, 2.0, 3.0]


def test_quantile_head_crossing_emits_warning() -> None:
    games = build_games(seasons=(2021,), weeks=(1, 2, 3, 4), games_per_week=3)
    history = build_team_history(games)
    features, labels = _xy_from_games(games, history)
    head = LightGBMQuantileHead(
        target="margin",
        train=_FAST,
        seed=2,
        quantiles=(0.05, 0.5, 0.95),
    )
    head.fit(features, labels)
    n = len(features)
    head._boosters = {
        0.05: type("B", (), {"predict": lambda self, x: np.full(len(x), 10.0)})(),
        0.5: type("B", (), {"predict": lambda self, x: np.full(len(x), 0.0)})(),
        0.95: type("B", (), {"predict": lambda self, x: np.full(len(x), -5.0)})(),
    }
    with pytest.warns(UserWarning, match="quantile crossing"):
        out = head.predict(features)
    assert (out["pred_margin_q05"] <= out["pred_margin_q50"]).all()
    assert (out["pred_margin_q50"] <= out["pred_margin_q95"]).all()
    assert len(out) == n


def test_save_load_round_trip(tmp_path: Path) -> None:
    games = build_games(seasons=(2020, 2021), weeks=(1, 2, 3), games_per_week=3)
    history = build_team_history(games)
    features, labels = _xy_from_games(games, history)
    head = LightGBMMarginMuHead(train=_FAST, seed=3, model_version="rt")
    head.fit(features, labels)
    before = head.predict(features)

    path = head.save(tmp_path / "lgbm_margin.pkl")
    loaded = LightGBMMarginMuHead.load(path)
    after = loaded.predict(features)
    np.testing.assert_allclose(
        before["pred_margin"].to_numpy(),
        after["pred_margin"].to_numpy(),
        rtol=0,
        atol=0,
    )

    enet = ElasticNetMuHead(target="margin", top_k=5, seed=3)
    enet.fit(features, labels)
    before_e = enet.predict(features)
    loaded_e = ElasticNetMuHead.load(enet.save(tmp_path / "enet.pkl"))
    after_e = loaded_e.predict(features)
    np.testing.assert_allclose(
        before_e["pred_margin"].to_numpy(),
        after_e["pred_margin"].to_numpy(),
        rtol=0,
        atol=0,
    )

    xgb = XGBoostMuHead(target="margin", train=_FAST, seed=3)
    xgb.fit(features, labels)
    before_x = xgb.predict(features)
    loaded_x = XGBoostMuHead.load(xgb.save(tmp_path / "xgb.pkl"))
    after_x = loaded_x.predict(features)
    np.testing.assert_allclose(
        before_x["pred_margin"].to_numpy(),
        after_x["pred_margin"].to_numpy(),
        rtol=0,
        atol=0,
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: LightGBMMarginMuHead(train=_FAST, seed=4),
        lambda: LightGBMTotalMuHead(train=_FAST, seed=4),
        lambda: LightGBMQuantileHead(target="margin", train=_FAST, seed=4),
        lambda: LightGBMQuantileHead(target="total", train=_FAST, seed=4),
        lambda: XGBoostMuHead(target="margin", train=_FAST, seed=4),
        lambda: XGBoostMuHead(target="total", train=_FAST, seed=4),
        lambda: CatBoostMuHead(target="margin", train=_FAST, seed=4),
        lambda: CatBoostMuHead(target="total", train=_FAST, seed=4),
        lambda: ElasticNetMuHead(target="margin", top_k=5, seed=4),
        lambda: ElasticNetMuHead(target="total", top_k=5, seed=4),
        lambda: NGBoostNormalHead(target="margin", train=_FAST_NG, seed=4),
        lambda: NGBoostNormalHead(target="total", train=_FAST_NG, seed=4),
    ],
)
def test_each_member_trains_on_three_season_fixture(factory: Any) -> None:
    games = build_games(seasons=(2019, 2020, 2021), weeks=(1, 2, 3, 4), games_per_week=3)
    history = build_team_history(games)
    features, labels = _xy_from_games(games, history)
    head = factory()
    head.fit(features, labels)
    assert head.is_fitted
    preds = head.predict(features.head(5))
    assert "game_id" in preds.columns
    assert len(preds) == 5


def test_sigma_head_trains_on_abs_oof_residuals() -> None:
    games = build_games(seasons=(2019, 2020, 2021), weeks=(1, 2, 3, 4), games_per_week=3)
    history = build_team_history(games)
    features, labels = _xy_from_games(games, history)
    mu = LightGBMMarginMuHead(train=_FAST, seed=5)
    mu.fit(features, labels)
    mu_pred = mu.predict(features)
    sigma_labels = abs_residual_labels(labels, mu_pred, target="margin")
    assert "rating_uncertainty" in features.columns
    sigma = LightGBMSigmaHead(target="sigma_margin", train=_FAST, seed=5)
    sigma.fit(features, sigma_labels)
    assert sigma.is_fitted
    out = sigma.predict(features.head(3))
    assert (out["pred_sigma_margin"] > 0).all()


MEMBER_FACTORIES: list[tuple[str, Any]] = [
    ("lgbm_mu_margin", lambda: LightGBMMarginMuHead(train=_FAST, seed=7, model_version="lgbm-m")),
    ("lgbm_mu_total", lambda: LightGBMTotalMuHead(train=_FAST, seed=7, model_version="lgbm-t")),
    (
        "lgbm_quantile_margin",
        lambda: LightGBMQuantileHead(
            target="margin", train=_FAST, seed=7, model_version="lgbm-q-m"
        ),
    ),
    (
        "lgbm_quantile_total",
        lambda: LightGBMQuantileHead(target="total", train=_FAST, seed=7, model_version="lgbm-q-t"),
    ),
    ("xgb_mu_margin", lambda: XGBoostMuHead(target="margin", train=_FAST, seed=7)),
    ("xgb_mu_total", lambda: XGBoostMuHead(target="total", train=_FAST, seed=7)),
    ("cat_mu_margin", lambda: CatBoostMuHead(target="margin", train=_FAST, seed=7)),
    ("cat_mu_total", lambda: CatBoostMuHead(target="total", train=_FAST, seed=7)),
    ("enet_mu_margin", lambda: ElasticNetMuHead(target="margin", top_k=6, seed=7)),
    ("enet_mu_total", lambda: ElasticNetMuHead(target="total", top_k=6, seed=7)),
    ("ngb_margin", lambda: NGBoostNormalHead(target="margin", train=_FAST_NG, seed=7)),
    ("ngb_total", lambda: NGBoostNormalHead(target="total", train=_FAST_NG, seed=7)),
]


def _mae_rmse(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[float, float]:
    err = y_true - y_pred
    return float(np.mean(np.abs(err))), float(np.sqrt(np.mean(err**2)))


def test_all_members_train_via_harness_and_report_vs_elo() -> None:
    """Acceptance: 3 train seasons through Task 16 harness; metrics vs Elo."""
    seasons = (2019, 2020, 2021, 2022)
    holdout = 2022
    games = build_games(seasons=seasons, weeks=(1, 2, 3, 4, 5), games_per_week=4)
    history = build_team_history(games)
    provider = RichPitFeatureProvider(history)
    config = WalkForwardConfig(
        test_seasons=(2019, 2021, 2022),
        continuity_seasons=(2020,),
        retrain_weeks=(3,),
        seed=7,
        market_features_available=False,
    )

    elo_cfg = EloConfig()
    elo_log, _weekly, _final = run_elo(games, config=elo_cfg, fbs_only=False)
    elo_hold = elo_log.loc[elo_log["season"] == holdout].copy()
    y_elo = (
        elo_hold["home_points"].astype(float) - elo_hold["away_points"].astype(float)
    ).to_numpy()
    elo_mae, elo_rmse = _mae_rmse(y_elo, elo_hold["pred_home_margin"].to_numpy())

    report: list[dict[str, Any]] = []
    for name, factory in MEMBER_FACTORIES:
        predictor = factory()
        harness = WalkForwardHarness(
            config=config,
            predictor=predictor,
            feature_provider=provider,
            rating_engine=RunningMarginRatingEngine(),
        )
        result = harness.run(games)
        preds = result.predictions
        assert not preds.empty, name
        assert set(preds["season"].unique()) >= {2019, 2020, 2021, 2022}

        hold = preds.loc[preds["season"] == holdout].copy()
        assert not hold.empty, name

        if name.endswith("total"):
            y = hold["realized_total"].astype(float).to_numpy()
            yhat = hold["pred_total"].astype(float).to_numpy()
            metric_target = "total"
        else:
            y = hold["realized_margin"].astype(float).to_numpy()
            yhat = hold["pred_margin"].astype(float).to_numpy()
            metric_target = "margin"

        mask = np.isfinite(y) & np.isfinite(yhat)
        mae, rmse = _mae_rmse(y[mask], yhat[mask])
        beats_elo: bool | None = None
        flag = ""
        if metric_target == "margin":
            beats_elo = mae < elo_mae
            if not beats_elo:
                flag = "FLAG: does not beat Elo MAE"
        report.append(
            {
                "member": name,
                "target": metric_target,
                "n": int(mask.sum()),
                "mae": mae,
                "rmse": rmse,
                "beats_elo_mae": beats_elo,
                "flag": flag,
            }
        )

    mu_predictor = LightGBMMarginMuHead(train=_FAST, seed=7, model_version="lgbm-m-sigma-src")
    mu_harness = WalkForwardHarness(
        config=config,
        predictor=mu_predictor,
        feature_provider=provider,
        rating_engine=RunningMarginRatingEngine(),
    )
    mu_result = mu_harness.run(games)
    oof = mu_result.predictions
    train_oof = oof.loc[oof["season"] < holdout]
    feat_log = mu_result.feature_log
    feat_cols = [c for c in feat_log.columns if c.startswith("feat__")]
    rename = {c: c.removeprefix("feat__") for c in feat_cols}
    feat_frame = feat_log.rename(columns=rename)[["game_id", *list(rename.values())]]
    sigma_labels = abs_residual_labels(
        train_oof[["game_id", "season", "realized_margin", "realized_total"]],
        train_oof[["game_id", "pred_margin", "pred_total"]],
        target="margin",
    )
    sigma = LightGBMSigmaHead(target="sigma_margin", train=_FAST, seed=7)
    sigma_features = feat_frame.loc[feat_frame["game_id"].isin(sigma_labels["game_id"])]
    sigma.fit(sigma_features, sigma_labels)
    assert sigma.is_fitted
    hold_feat = feat_frame.loc[
        feat_frame["game_id"].isin(oof.loc[oof["season"] == holdout, "game_id"])
    ]
    sigma_pred = sigma.predict(hold_feat)
    assert (sigma_pred["pred_sigma_margin"] > 0).all()
    report.append(
        {
            "member": "lgbm_sigma_margin",
            "target": "sigma_margin",
            "n": len(sigma_pred),
            "mae": float("nan"),
            "rmse": float("nan"),
            "beats_elo_mae": None,
            "flag": "",
        }
    )

    print("\nTASK17_ACCEPTANCE_METRICS")
    print(f"ELO_HOLDOUT_MAE {elo_mae:.4f} RMSE {elo_rmse:.4f} season={holdout}")
    for row in report:
        print(
            f"{row['member']:24s} target={row['target']:12s} "
            f"n={row['n']:3d} MAE={row['mae']:.4f} RMSE={row['rmse']:.4f} "
            f"beats_elo={row['beats_elo_mae']} {row['flag']}"
        )

    assert len(report) == len(MEMBER_FACTORIES) + 1
    assert all(r["n"] > 0 for r in report)
