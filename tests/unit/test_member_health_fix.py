"""MEMBER-HEALTH-FIX / ADR 0014 — credibility contract, ENet NaN, gate addenda."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.production_stack import (
    NULL_REASON_COLD_START,
    MemberStatus,
    ProductionEnsemblePredictor,
)
from ncaa_quant.evaluation.walkforward import (
    PredictionQualityGateError,
    WalkForwardConfig,
    assert_prediction_quality_gate,
)
from ncaa_quant.models.ensemble import FittedEnsemble, NNLSStackResult, single_lgbm_stack
from ncaa_quant.models.heads.base import HeadTrainConfig, PredictorError
from ncaa_quant.models.heads.elasticnet import (
    ElasticNetMuHead,
    drop_high_null_columns,
    impute_with_medians,
    training_window_medians,
)
from ncaa_quant.models.heads.margin import LightGBMMuHead

_FAST = HeadTrainConfig(n_estimators=20, learning_rate=0.1, num_leaves=8, max_depth=3)


def _base_xy(n: int = 40, *, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    gids = np.arange(1, n + 1)
    x1 = rng.normal(0, 1, size=n)
    features = pd.DataFrame(
        {
            "game_id": gids,
            "rating_diff_off_epa": x1,
            "rating_uncertainty": np.abs(rng.normal(0.5, 0.1, size=n)),
            "home_pace": rng.normal(70, 3, size=n),
            "mkt_spread": x1 * 3 + rng.normal(0, 0.5, size=n),
            "mkt_total": rng.normal(55, 5, size=n),
            "mkt_n_books": np.full(n, 5.0),
            "mkt_is_missing": np.zeros(n),
        }
    )
    labels = pd.DataFrame(
        {
            "game_id": gids,
            "realized_margin": x1 * 4 + rng.normal(0, 2, size=n),
            "realized_total": rng.normal(52, 8, size=n),
            "season": np.full(n, 2022),
            "week": np.tile(np.arange(1, 5), n // 4 + 1)[:n],
        }
    )
    return features, labels


# ---------------------------------------------------------------------------
# Mechanism B — ENet NaN policy + no constant fill
# ---------------------------------------------------------------------------


def test_enet_nan_policy_fits_after_drop_and_impute() -> None:
    """Mechanism B root: NaN in X used to abort fit; policy must make fit succeed."""
    features, labels = _base_xy(60, seed=1)
    features.loc[:40, "mkt_spread"] = np.nan  # null share > 0.5 → drop
    features.loc[5:8, "home_pace"] = np.nan  # remaining → train median
    head = ElasticNetMuHead(target="margin", top_k=5, seed=3)
    head.fit(features, labels)
    assert head.is_fitted
    assert "mkt_spread" not in head._selected_features
    assert "mkt_spread" not in head._kept_columns
    preds = head.predict(features)
    assert np.isfinite(preds["pred_margin"].to_numpy()).all()
    assert bool(head._selected_features) == (head._model is not None)


def test_enet_clears_selection_on_fit_failure() -> None:
    """Selection state must not outlive a failed estimator (SDMU-DIAG ambiguity)."""
    features, labels = _base_xy(20, seed=2)
    head = ElasticNetMuHead(target="margin", top_k=5, seed=4)
    poisoned = features.copy()
    for c in poisoned.columns:
        if c != "game_id":
            poisoned[c] = np.nan
    with pytest.raises((PredictorError, ValueError)):
        head.fit(poisoned, labels)
    assert head._selected_features == []
    assert head._model is None
    assert not head.is_fitted


def test_predict_point_never_fills_constant_2_5() -> None:
    """Mechanism B surface: dead ENet must not become block-wide 2.5."""
    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        mapping_layer="ensemble",
        market_features_available=True,
        nnls_equal_weight_fallback=True,
        member_fit_failure_mode="exclude",
    )
    pred = ProductionEnsemblePredictor(
        config=cfg,
        seed=0,
        margin_head=LightGBMMuHead(target="margin", train=_FAST, seed=0),
        total_head=LightGBMMuHead(target="total", train=_FAST, seed=1),
        n_mc_draws=8,
        n_epistemic_draws=0,
    )
    features, labels = _base_xy(40, seed=5)
    pred.fit(features, labels)
    pred._member_status = [
        MemberStatus(
            name="lgbm_mu_margin",
            fitted=True,
            selection_consistent=True,
            non_degenerate=True,
            credible=True,
        ),
        MemberStatus(
            name="enet_mu_margin",
            fitted=False,
            selection_consistent=True,
            non_degenerate=False,
            credible=False,
            exclude_reason="fit_exception:ValueError",
        ),
    ]
    pred._ensemble = FittedEnsemble(
        margin=NNLSStackResult(
            target="margin",
            member_columns=("lgbm_mu_margin",),
            weights=(1.0,),
        ),
        total=single_lgbm_stack(target="total", lgbm_column="lgbm_mu_total"),
    )
    pred._null_reason = None
    pred._fitted = True
    out = pred._predict_point(features)
    assert not np.any(np.isclose(out["pred_margin"].to_numpy(dtype=float), 2.5))
    assert bool(out["enet_credible"].iloc[0]) is False


# ---------------------------------------------------------------------------
# Mechanism A — degeneracy → exclude → null, not constant
# ---------------------------------------------------------------------------


def test_degenerate_member_excluded_emits_null_not_constant() -> None:
    """Mechanism A: constant-on-train member is not credible; zero credible → null."""
    cfg = WalkForwardConfig(
        test_seasons=(2019,),
        continuity_seasons=(),
        mapping_layer="ensemble",
        market_features_available=False,
        nnls_equal_weight_fallback=True,
        member_fit_failure_mode="exclude",
    )
    pred = ProductionEnsemblePredictor(
        config=cfg,
        seed=0,
        n_mc_draws=8,
        n_epistemic_draws=0,
        total_head=LightGBMMuHead(target="total", train=_FAST, seed=1),
    )
    features, labels = _base_xy(24, seed=6)

    const_head = MagicMock(spec=LightGBMMuHead)
    const_head.is_fitted = True
    const_head.train = _FAST
    const_head.predict.return_value = pd.DataFrame(
        {
            "game_id": features["game_id"].to_numpy(),
            "pred_margin": np.full(len(features), 11.816),
            "pred_total": np.full(len(features), np.nan),
        }
    )
    const_head.fit = MagicMock()
    pred.margin_head = const_head

    enet = MagicMock(spec=ElasticNetMuHead)
    enet.is_fitted = False
    enet._selected_features = []
    enet._model = None
    enet.fit = MagicMock(side_effect=ValueError("Input X contains NaN"))
    enet._clear_estimator_state = MagicMock()
    pred.enet_margin = enet

    pred.fit(features, labels)
    assert pred.null_reason in {NULL_REASON_COLD_START, "no_credible_members"}
    assert pred.is_fitted
    statuses = {s.name: s for s in pred.member_status}
    assert statuses["lgbm_mu_margin"].credible is False
    assert statuses["lgbm_mu_margin"].exclude_reason == "degenerate_constant_on_train"

    out = pred.predict(features)
    assert out["pred_margin"].isna().all()
    assert (out["null_reason"] == pred.null_reason).all()


# ---------------------------------------------------------------------------
# Gate addenda
# ---------------------------------------------------------------------------


def test_gate_reports_absent_scheduled_block() -> None:
    """Vacuous pass: scheduled week with zero rows → ABSENT in gate output."""
    frame = pd.DataFrame(
        {
            "season": [2019, 2019],
            "week": [2, 2],
            "pred_margin": [3.0, -1.0],
            "realized_margin": [7.0, -2.0],
            "exclude_from_headline": [False, False],
            "n_train_games": [125, 125],
        }
    )
    result = assert_prediction_quality_gate(
        frame,
        raise_on_fail=False,
        scheduled_blocks=((2019, 1), (2019, 2)),
    )
    assert (2019, 1) in result.absent_blocks
    assert (2019, 2) not in result.absent_blocks
    assert result.passed is True


def test_gate_fails_partial_death_weight_on_noncredible() -> None:
    """(2023, w5)-style: positive NNLS weight on non-credible member → fail."""
    n = 20
    frame = pd.DataFrame(
        {
            "season": np.full(n, 2023),
            "week": np.full(n, 5),
            "pred_margin": np.linspace(-5, 5, n),
            "realized_margin": np.linspace(-4, 6, n),
            "exclude_from_headline": False,
            "n_train_games": 3600,
            "lgbm_credible": False,
            "enet_credible": False,
            "w_lgbm_mu_margin": 0.128,
            "w_enet_mu_margin": 0.872,
        }
    )
    with pytest.raises(PredictionQualityGateError, match="non-credible"):
        assert_prediction_quality_gate(frame, raise_on_fail=True)


def test_gate_ungradable_null_reason_excluded_from_scored() -> None:
    """Intentional nulls with reason are ungradable — not accidental null-μ fails."""
    rows = []
    for i in range(10):
        rows.append(
            {
                "season": 2019,
                "week": 2,
                "pred_margin": np.nan,
                "realized_margin": float(i),
                "exclude_from_headline": False,
                "n_train_games": 125,
                "null_reason": "cold_start_insufficient",
            }
        )
    for i in range(10):
        rows.append(
            {
                "season": 2019,
                "week": 5,
                "pred_margin": float(i) - 4.5,
                "realized_margin": float(i),
                "exclude_from_headline": False,
                "n_train_games": 200,
                "null_reason": None,
            }
        )
    frame = pd.DataFrame(rows)
    result = assert_prediction_quality_gate(frame, raise_on_fail=False)
    assert result.n_ungradable == 10
    assert result.n_null_mu == 0
    assert ("cold_start_insufficient", 10) in result.null_reason_counts
    assert result.passed is True


# ---------------------------------------------------------------------------
# PIT — training medians only
# ---------------------------------------------------------------------------


def test_predict_preserves_finite_mu_when_sigma_missing() -> None:
    """Honest μ survives constant-σ refusal — never erase with null_reason."""
    cfg = WalkForwardConfig(
        test_seasons=(2019,),
        continuity_seasons=(),
        mapping_layer="ensemble",
        nnls_equal_weight_fallback=True,
        member_fit_failure_mode="exclude",
    )
    pred = ProductionEnsemblePredictor(
        config=cfg,
        seed=0,
        n_mc_draws=8,
        n_epistemic_draws=0,
        margin_head=LightGBMMuHead(target="margin", train=_FAST, seed=0),
        total_head=LightGBMMuHead(target="total", train=_FAST, seed=1),
    )
    features, labels = _base_xy(40, seed=11)
    pred.fit(features, labels)
    mus = np.linspace(-5.0, 5.0, len(features))
    mus[0] = np.nan  # one accidental non-finite member stack row

    def _point(feats: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "game_id": feats["game_id"].to_numpy(),
                "pred_margin": mus,
                "pred_total": np.full(len(feats), 50.0),
                "null_reason": None,
                "lgbm_credible": True,
                "enet_credible": True,
                "w_lgbm_mu_margin": 0.0,
                "w_enet_mu_margin": 1.0,
            }
        )

    pred._predict_point = _point  # type: ignore[method-assign]
    # Post-refusal σ: block-constant floor already wiped to NaN (ADR 0014).
    pred._predict_sigma_heads = (  # type: ignore[method-assign]
        lambda feats, gids: (
            np.full(len(gids), np.nan),
            np.full(len(gids), np.nan),
        )
    )
    out = pred.predict(features)
    assert int(out["pred_margin"].notna().sum()) == len(features) - 1
    assert bool(np.isfinite(out["pred_margin"].iloc[1]))
    # Accidental nan μ must carry null_reason so the gate does not see scored nulls.
    assert str(out.loc[out["pred_margin"].isna(), "null_reason"].iloc[0]) == "no_credible_members"
    assert out["sigma_m"].isna().all()
    assert out["p_ats_home"].isna().all()


def test_predict_does_not_mutate_fit_null_reason() -> None:
    """Predict-time all-NaN must not poison later weeks' fit-time null_reason."""
    cfg = WalkForwardConfig(
        test_seasons=(2019,),
        continuity_seasons=(),
        mapping_layer="ensemble",
        nnls_equal_weight_fallback=True,
        member_fit_failure_mode="exclude",
    )
    pred = ProductionEnsemblePredictor(
        config=cfg,
        seed=0,
        n_mc_draws=8,
        n_epistemic_draws=0,
        margin_head=LightGBMMuHead(target="margin", train=_FAST, seed=0),
        total_head=LightGBMMuHead(target="total", train=_FAST, seed=1),
    )
    features, labels = _base_xy(40, seed=9)
    pred.fit(features, labels)
    assert pred.null_reason is None
    # Force a one-shot all-NaN point path without clearing fit state.
    pred._predict_point = lambda feats: pd.DataFrame(  # type: ignore[method-assign]
        {
            "game_id": feats["game_id"].to_numpy(),
            "pred_margin": np.full(len(feats), np.nan),
            "pred_total": np.full(len(feats), np.nan),
            "null_reason": None,
            "lgbm_credible": True,
            "enet_credible": True,
            "w_lgbm_mu_margin": 0.5,
            "w_enet_mu_margin": 0.5,
        }
    )
    out = pred.predict(features)
    assert out["pred_margin"].isna().all()
    assert pred.null_reason is None


def test_enet_imputation_medians_are_training_window_only() -> None:
    """PIT: medians come from training rows; future-only values must not leak in."""
    train = pd.DataFrame(
        {
            "a": [1.0, 2.0, 3.0, np.nan],
            "b": [10.0, 10.0, 10.0, 10.0],
        }
    )
    future = pd.DataFrame(
        {
            "a": [100.0, 200.0],
            "b": [999.0, 999.0],
        }
    )
    kept, cols = drop_high_null_columns(train, threshold=0.5)
    assert cols == ["a", "b"]
    medians = training_window_medians(kept)
    assert medians["a"] == pytest.approx(2.0)
    assert medians["b"] == pytest.approx(10.0)
    mixed = pd.concat([train, future], ignore_index=True)
    imputed = impute_with_medians(mixed, medians)
    assert imputed.loc[3, "a"] == pytest.approx(2.0)
    assert imputed.loc[4, "a"] == pytest.approx(100.0)
