"""MODEL-HFA-4 — explicit is_home_site + unpenalized ElasticNet term."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.backtest_runner import walkforward_config_from_mapping
from ncaa_quant.evaluation.production_stack import ProductionFeatureProvider
from ncaa_quant.evaluation.walkforward import WalkForwardConfig
from ncaa_quant.models.heads.elasticnet import ElasticNetMuHead


def test_explicit_home_site_feature_gated() -> None:
    cfg_off = WalkForwardConfig(explicit_home_site=False, market_features_available=False)
    cfg_on = WalkForwardConfig(explicit_home_site=True, market_features_available=False)
    games = pd.DataFrame(
        {
            "game_id": [1, 2],
            "home_team_id": [10, 11],
            "away_team_id": [20, 21],
            "season": [2023, 2023],
            "week": [1, 1],
            "neutral_site": [False, True],
        }
    )
    as_of = datetime(2023, 9, 1, tzinfo=UTC)
    state = {
        "10:off_epa": 0.1,
        "20:off_epa": -0.1,
        "11:off_epa": 0.0,
        "21:off_epa": 0.0,
    }
    off = ProductionFeatureProvider(config=cfg_off).compute_game_features(
        games, as_of, rating_state=state, market_features=False
    )
    on = ProductionFeatureProvider(config=cfg_on).compute_game_features(
        games, as_of, rating_state=state, market_features=False
    )
    assert "is_home_site" not in off.columns
    assert list(on["is_home_site"]) == [1.0, 0.0]


def test_enet_unpenalized_home_site_not_shrunk() -> None:
    rng = np.random.default_rng(7)
    n = 80
    strength = rng.normal(0, 1, size=n)
    is_home = rng.integers(0, 2, size=n).astype(float)
    true_home = 3.5
    y = 4.0 * strength + true_home * is_home + rng.normal(0, 0.5, size=n)
    features = pd.DataFrame(
        {
            "game_id": np.arange(1, n + 1),
            "rating_diff_off_epa": strength,
            "noise_a": rng.normal(0, 1, size=n),
            "noise_b": rng.normal(0, 1, size=n),
            "is_home_site": is_home,
        }
    )
    labels = pd.DataFrame(
        {
            "game_id": features["game_id"],
            "realized_margin": y,
            "season": np.full(n, 2022),
            "week": np.tile(np.arange(1, 5), n // 4 + 1)[:n],
        }
    )
    head = ElasticNetMuHead(
        target="margin",
        top_k=3,
        seed=7,
        fit_intercept=False,
        force_include_features=("is_home_site",),
        unpenalized_features=("is_home_site",),
        alpha=1.0,  # heavy penalty on other terms
        l1_ratio=0.5,
    )
    head.fit(features, labels)
    assert "is_home_site" in head._selected_features
    assert "is_home_site" in head.unpenalized_coef_
    coef = float(head.unpenalized_coef_["is_home_site"])
    assert abs(coef - true_home) < 0.75
    # Flip-only delta matches the unpenalized coefficient.
    row = features.iloc[[0]].copy()
    row_h = row.assign(is_home_site=1.0)
    row_n = row.assign(is_home_site=0.0)
    delta = float(head.predict(row_h)["pred_margin"].iloc[0] - head.predict(row_n)["pred_margin"].iloc[0])
    assert abs(delta - coef) < 1e-6


def test_v4_config_wiring() -> None:
    payload = {
        "run_id": "task23_fundamental_reduced_v4",
        "walkforward": {
            "test_seasons": [2019, 2021, 2022, 2023, 2024],
            "continuity_seasons": [2020, 2026],
            "explicit_home_site": True,
            "elasticnet_fit_intercept": False,
            "freeze_mapping_from_season": 2026,
            "market_features_available": False,
        },
    }
    cfg = walkforward_config_from_mapping(payload)
    assert cfg.explicit_home_site is True
    assert cfg.elasticnet_fit_intercept is False
    assert cfg.freeze_mapping_from_season == 2026
    assert 2026 in cfg.continuity_seasons
