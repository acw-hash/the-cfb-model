"""HPO framework tests (Task 18) — nested isolation, resume, seeds, tiebreak."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import optuna
import pandas as pd
import pytest

from ncaa_quant.models.hpo import (
    REGULARIZATION_TIEBREAK_ORDER,
    HPOConfig,
    HPOError,
    HPOTrainWindow,
    NestedIsolationError,
    SeasonSlice,
    WalkForwardObjective,
    WallClockCallback,
    apply_quarantine_tiebreak,
    create_study,
    crps_gaussian,
    default_storage_path,
    evaluate_default_params,
    loss_for_head_kind,
    mse_loss,
    params_to_head_train_config,
    pinball_loss,
    rankings_unstable,
    regularization_key,
    run_hpo,
    select_by_regularization,
    suggest_params,
    trial_seed,
)


def _synthetic_window_frames(
    seasons: Sequence[int] = (2016, 2017, 2018, 2019, 2021),
    *,
    games_per_season: int = 24,
    seed: int = 7,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Margin ≈ 0.8 * rating_diff + noise; enough seasons for last-3 WF."""
    rng = np.random.default_rng(seed)
    strength = {tid: float(rng.normal(0, 10)) for tid in range(1, 13)}
    feat_rows: list[dict[str, Any]] = []
    lab_rows: list[dict[str, Any]] = []
    gid = 1
    for season in seasons:
        for i in range(games_per_season):
            home = 1 + (i % 12)
            away = 1 + ((i + 5) % 12)
            if home == away:
                away = 1 + (away % 12)
            rd = strength[home] - strength[away]
            margin = 0.8 * rd + float(rng.normal(0, 5))
            total = 48.0 + 0.2 * (strength[home] + strength[away]) + float(rng.normal(0, 4))
            feat_rows.append(
                {
                    "game_id": gid,
                    "rating_diff": rd,
                    "home_form": float(strength[home]),
                    "away_form": float(strength[away]),
                    "rest_proxy": float(1 + (i % 8)),
                    "rating_uncertainty": float(1.0 / np.sqrt(i + 1)),
                }
            )
            lab_rows.append(
                {
                    "game_id": gid,
                    "season": int(season),
                    "week": 1 + (i % 8),
                    "realized_margin": margin,
                    "realized_total": total,
                    "abs_residual_margin": abs(margin - 0.5 * rd),
                }
            )
            gid += 1
    return pd.DataFrame(feat_rows), pd.DataFrame(lab_rows)


def test_nested_isolation_outer_test_raises() -> None:
    """Objective cannot read outer test seasons — API-enforced."""
    features, labels = _synthetic_window_frames()
    # Add an outer-test season's rows to the *full* frames; window must drop them.
    outer = 2022
    extra_f = features.head(8).copy()
    extra_f["game_id"] = extra_f["game_id"] + 50_000
    extra_l = labels.head(8).copy()
    extra_l["game_id"] = extra_l["game_id"] + 50_000
    extra_l["season"] = outer
    features_full = pd.concat([features, extra_f], ignore_index=True)
    labels_full = pd.concat([labels, extra_l], ignore_index=True)

    window = HPOTrainWindow.from_frames(
        features_full,
        labels_full,
        train_seasons=(2016, 2017, 2018, 2019, 2021),
        outer_test_seasons=(outer,),
    )

    assert outer not in window.seasons()
    assert outer in window.forbidden_seasons
    # Rows for 2022 were never stored.
    with pytest.raises(NestedIsolationError, match="outer test season"):
        window.get(outer)
    with pytest.raises(NestedIsolationError, match="outer test season"):
        window.require_season(outer)

    # Objective path: attempting access from inside WalkForwardObjective body.
    obj = WalkForwardObjective(
        window=window,
        config=HPOConfig(
            study_name="iso-test",
            head_kind="lgbm_mu",
            n_trials=1,
            n_jobs=1,
            fast=True,
            mlflow_experiment=None,
        ),
    )

    def _sneaky_objective(trial: optuna.Trial) -> float:
        # This is what leakage would look like — must raise.
        obj.window.get(2022)
        return 0.0

    study = optuna.create_study(direction="minimize")
    with pytest.raises(NestedIsolationError):
        study.optimize(_sneaky_objective, n_trials=1)


def test_nested_isolation_absent_season_raises() -> None:
    features, labels = _synthetic_window_frames()
    window = HPOTrainWindow.from_frames(
        features,
        labels,
        train_seasons=(2016, 2017, 2018, 2019, 2021),
        outer_test_seasons=(2022, 2023),
    )
    with pytest.raises(NestedIsolationError, match="outside the HPO training"):
        window.get(2015)


def test_trial_seed_determinism() -> None:
    a = trial_seed("margin-mu", 0)
    b = trial_seed("margin-mu", 0)
    c = trial_seed("margin-mu", 1)
    d = trial_seed("other-study", 0)
    assert a == b
    assert a != c
    assert a != d
    assert 0 <= a < 2**31 - 1


def test_regularization_tiebreak_order() -> None:
    assert REGULARIZATION_TIEBREAK_ORDER == (
        ("min_child_samples", "higher"),
        ("num_leaves", "lower"),
        ("lambda_l2", "higher"),
    )
    more = {"min_child_samples": 50, "num_leaves": 16, "lambda_l2": 2.0}
    less = {"min_child_samples": 10, "num_leaves": 64, "lambda_l2": 0.1}
    assert regularization_key(more) < regularization_key(less)
    # Higher min_child_samples wins even if leaves are larger.
    a = {"min_child_samples": 40, "num_leaves": 64, "lambda_l2": 0.1}
    b = {"min_child_samples": 20, "num_leaves": 8, "lambda_l2": 10.0}
    assert regularization_key(a) < regularization_key(b)
    # Tie on min_child → lower num_leaves wins.
    c = {"min_child_samples": 20, "num_leaves": 8, "lambda_l2": 0.1}
    d = {"min_child_samples": 20, "num_leaves": 31, "lambda_l2": 10.0}
    assert regularization_key(c) < regularization_key(d)
    # Tie on first two → higher lambda_l2 wins (reg_lambda alias).
    e = {"min_child_samples": 20, "num_leaves": 31, "reg_lambda": 5.0}
    f = {"min_child_samples": 20, "num_leaves": 31, "lambda_l2": 1.0}
    assert regularization_key(e) < regularization_key(f)
    assert select_by_regularization([less, more]) == more


def test_apply_quarantine_tiebreak_stable_keeps_winner() -> None:
    t0 = MagicMock()
    t0.number = 0
    t0.params = {"min_child_samples": 10, "num_leaves": 64, "lambda_l2": 0.1}
    t0.value = 1.0
    t1 = MagicMock()
    t1.number = 1
    t1.params = {"min_child_samples": 40, "num_leaves": 16, "lambda_l2": 2.0}
    t1.value = 1.2
    # Quarantine ranking matches study order → keep study winner.
    q_losses = {0: 2.0, 1: 3.0}
    study_order = [0, 1]
    q_order = sorted(study_order, key=lambda n: q_losses[n])
    assert not rankings_unstable(study_order, q_order)
    selected = apply_quarantine_tiebreak([t0, t1], q_losses)
    assert selected.number == 0


def test_apply_quarantine_tiebreak_unstable_picks_regularized() -> None:
    """When quarantine ranking flips, prefer the more regularized config."""
    t0 = MagicMock()
    t0.number = 0
    t0.params = {"min_child_samples": 10, "num_leaves": 64, "lambda_l2": 0.1}
    t0.value = 1.0
    t1 = MagicMock()
    t1.number = 1
    t1.params = {"min_child_samples": 40, "num_leaves": 16, "lambda_l2": 2.0}
    t1.value = 1.1
    # Study order: t0 best; quarantine prefers t1 (flip → unstable).
    q_losses = {0: 5.0, 1: 1.0}
    selected = apply_quarantine_tiebreak([t0, t1], q_losses)
    assert selected.number == 1


def test_losses_hand_computed() -> None:
    y = np.array([0.0, 1.0, 2.0])
    pred = np.array([0.0, 0.0, 0.0])
    assert mse_loss(y, pred) == pytest.approx(5.0 / 3.0)
    # Pinball at q=0.5 equals 0.5 * MAE.
    assert pinball_loss(y, {0.5: pred}) == pytest.approx(0.5 * np.mean(np.abs(y - pred)))
    # CRPS of N(0,1) at y=0: 2φ(0) − 1/√π = √(2/π) − 1/√π ≈ 0.2337
    assert crps_gaussian(np.array([0.0]), np.array([0.0]), np.array([1.0])) == pytest.approx(
        math.sqrt(2.0 / math.pi) - 1.0 / math.sqrt(math.pi), rel=1e-5
    )


def test_study_resumability(tmp_path: Path) -> None:
    features, labels = _synthetic_window_frames()
    window = HPOTrainWindow.from_frames(
        features,
        labels,
        train_seasons=(2016, 2017, 2018, 2019, 2021),
        outer_test_seasons=(2022,),
    )
    storage = default_storage_path("resume-margin-mu", tmp_path)
    cfg = HPOConfig(
        study_name="resume-margin-mu",
        head_kind="lgbm_mu",
        target="margin",
        storage=storage,
        n_trials=3,
        n_jobs=1,
        fast=True,
        early_stopping_rounds=20,
        mlflow_experiment=None,
        sampler_seed=0,
    )
    # First pass: 2 trials (mid-study "kill").
    cfg.n_trials = 2
    r1 = run_hpo(window, cfg, quarantine=None)
    n_after_first = len(r1.study.trials)
    assert n_after_first >= 2

    # Resume toward 5 total COMPLETE+PRUNED budget.
    cfg.n_trials = 5
    r2 = run_hpo(window, cfg, quarantine=None)
    n_after_second = len(
        [
            t
            for t in r2.study.trials
            if t.state
            in (
                optuna.trial.TrialState.COMPLETE,
                optuna.trial.TrialState.PRUNED,
                optuna.trial.TrialState.FAIL,
            )
        ]
    )
    assert n_after_second >= n_after_first
    assert n_after_second >= 5 or len(r2.study.trials) >= 5


def test_run_hpo_lgbm_mu_with_quarantine(tmp_path: Path) -> None:
    train_seasons = (2016, 2017, 2018, 2019, 2021)
    all_seasons = (*train_seasons, 2020)  # 2020 = quarantine (not in study, not outer test)
    features, labels = _synthetic_window_frames(all_seasons)
    window = HPOTrainWindow.from_frames(
        features,
        labels,
        train_seasons=train_seasons,
        outer_test_seasons=(2022,),
    )
    q_labels = labels.loc[labels["season"] == 2020]
    q_features = features.loc[features["game_id"].isin(q_labels["game_id"])]
    quarantine = SeasonSlice(season=2020, features=q_features, labels=q_labels)

    storage = default_storage_path("q-margin-mu", tmp_path)
    cfg = HPOConfig(
        study_name="q-margin-mu",
        head_kind="lgbm_mu",
        target="margin",
        storage=storage,
        n_trials=4,
        n_jobs=1,
        fast=True,
        early_stopping_rounds=15,
        top_k_tiebreak=3,
        mlflow_experiment=None,
        sampler_seed=1,
    )
    result = run_hpo(window, cfg, quarantine=quarantine)
    assert result.best_value < float("inf")
    assert "learning_rate" in result.best_params
    assert result.head_train_config.n_estimators >= 1
    assert len(result.quarantine_losses) == min(
        3, len([t for t in result.study.trials if t.state.name == "COMPLETE"])
    )


def test_create_study_tpe_multivariate(tmp_path: Path) -> None:
    cfg = HPOConfig(
        study_name="tpe-check",
        head_kind="lgbm_mu",
        storage=default_storage_path("tpe-check", tmp_path),
        mlflow_experiment=None,
    )
    study = create_study(cfg)
    assert isinstance(study.sampler, optuna.samplers.TPESampler)
    assert study.sampler._multivariate is True  # noqa: SLF001 — contract under test
    assert isinstance(study.pruner, optuna.pruners.HyperbandPruner)


def _window_and_quarantine(
    *,
    train: Sequence[int] = (2016, 2017, 2018, 2019, 2021),
    quarantine_season: int = 2020,
    outer: Sequence[int] = (2022,),
) -> tuple[HPOTrainWindow, SeasonSlice]:
    all_seasons = tuple(sorted({*train, quarantine_season}))
    features, labels = _synthetic_window_frames(all_seasons)
    window = HPOTrainWindow.from_frames(
        features,
        labels,
        train_seasons=train,
        outer_test_seasons=outer,
    )
    q_lab = labels.loc[labels["season"] == quarantine_season]
    q_feat = features.loc[features["game_id"].isin(q_lab["game_id"])]
    return window, SeasonSlice(season=quarantine_season, features=q_feat, labels=q_lab)


@pytest.mark.parametrize(
    "head_kind,target",
    [
        ("lgbm_quantile", "margin"),
        ("lgbm_sigma", "sigma_margin"),
        ("xgb_mu", "margin"),
        ("cat_mu", "margin"),
        ("enet_mu", "margin"),
        ("ngboost", "margin"),
    ],
)
def test_run_hpo_each_head_kind(tmp_path: Path, head_kind: str, target: str) -> None:
    window, quarantine = _window_and_quarantine()
    cfg = HPOConfig(
        study_name=f"kind-{head_kind}",
        head_kind=head_kind,  # type: ignore[arg-type]
        target=target,  # type: ignore[arg-type]
        storage=default_storage_path(f"kind-{head_kind}", tmp_path),
        n_trials=2,
        n_jobs=1,
        fast=True,
        early_stopping_rounds=10,
        top_k_tiebreak=2,
        mlflow_experiment=None,
        sampler_seed=3,
    )
    result = run_hpo(window, cfg, quarantine=quarantine)
    assert math.isfinite(result.best_value)
    assert result.selected_trial_number >= 0


def test_loss_for_head_kind_mapping() -> None:
    assert loss_for_head_kind("lgbm_mu") == "mse"
    assert loss_for_head_kind("lgbm_quantile") == "pinball"
    assert loss_for_head_kind("ngboost") == "crps"
    with pytest.raises(HPOError):
        loss_for_head_kind("nope")  # type: ignore[arg-type]


def test_resolved_n_jobs_defaults() -> None:
    assert HPOConfig(study_name="a", head_kind="lgbm_mu").resolved_n_jobs() == 4
    assert HPOConfig(study_name="a", head_kind="xgb_mu", use_gpu=True).resolved_n_jobs() == 1
    assert HPOConfig(study_name="a", head_kind="ngboost").resolved_n_jobs() == 1
    assert HPOConfig(study_name="a", head_kind="lgbm_mu", n_jobs=2).resolved_n_jobs() == 2


def test_wall_clock_callback_stops_study(tmp_path: Path) -> None:
    window, _ = _window_and_quarantine()
    cfg = HPOConfig(
        study_name="wall-clock",
        head_kind="enet_mu",
        storage=default_storage_path("wall-clock", tmp_path),
        n_trials=50,
        n_jobs=1,
        fast=True,
        max_wall_clock_seconds=0.001,
        mlflow_experiment=None,
        sampler_seed=0,
    )
    with pytest.raises(HPOError):
        WallClockCallback(0.0)
    # Tiny budget: study should stop early; may or may not complete a trial.
    try:
        run_hpo(window, cfg)
    except HPOError as exc:
        assert "no COMPLETE" in str(exc)


def test_evaluate_default_params() -> None:
    window, _ = _window_and_quarantine()
    cfg = HPOConfig(
        study_name="defaults",
        head_kind="lgbm_mu",
        n_jobs=1,
        fast=True,
        early_stopping_rounds=10,
        mlflow_experiment=None,
    )
    mean, per = evaluate_default_params(window, cfg)
    assert math.isfinite(mean)
    assert set(per) == {2018, 2019, 2021}


def test_quarantine_must_not_be_in_train_or_outer(tmp_path: Path) -> None:
    window, quarantine = _window_and_quarantine()
    cfg = HPOConfig(
        study_name="bad-q",
        head_kind="enet_mu",
        storage=default_storage_path("bad-q", tmp_path),
        n_trials=1,
        n_jobs=1,
        fast=True,
        mlflow_experiment=None,
    )
    # Quarantine season colliding with train window.
    bad = SeasonSlice(
        season=2019,
        features=quarantine.features,
        labels=quarantine.labels.assign(season=2019),
    )
    with pytest.raises(HPOError, match="must not be in the HPO training"):
        run_hpo(window, cfg, quarantine=bad)
    # Quarantine colliding with outer test.
    outer_q = SeasonSlice(
        season=2022,
        features=quarantine.features,
        labels=quarantine.labels.assign(season=2022),
    )
    with pytest.raises(NestedIsolationError, match="collides with outer"):
        run_hpo(window, cfg, quarantine=outer_q)


def test_from_frames_rejects_overlap() -> None:
    features, labels = _synthetic_window_frames()
    with pytest.raises(HPOError, match="overlap"):
        HPOTrainWindow.from_frames(
            features,
            labels,
            train_seasons=(2019, 2021),
            outer_test_seasons=(2021, 2022),
        )


def test_mlflow_trial_logging(tmp_path: Path) -> None:
    window, _ = _window_and_quarantine()
    tracking = tmp_path / "mlruns"
    cfg = HPOConfig(
        study_name="mlflow-log",
        head_kind="enet_mu",
        storage=default_storage_path("mlflow-log", tmp_path),
        n_trials=1,
        n_jobs=1,
        fast=True,
        mlflow_experiment="task18-test",
        mlflow_tracking_uri=tracking.as_uri(),
        sampler_seed=0,
    )
    result = run_hpo(window, cfg)
    assert math.isfinite(result.best_value)
    # Tracking dir should have been created by mlflow.
    assert tracking.exists()


def test_params_to_head_train_config_mapping() -> None:
    cfg = params_to_head_train_config(
        {
            "n_estimators": 200,
            "learning_rate": 0.01,
            "max_depth": 5,
            "num_leaves": 40,
            "min_child_samples": 25,
            "subsample": 0.8,
            "colsample_bytree": 0.7,
            "lambda_l2": 2.5,
        }
    )
    assert cfg.n_estimators == 200
    assert cfg.reg_lambda == 2.5
    assert cfg.learning_rate == 0.01


def test_suggest_params_unknown_kind() -> None:
    study = optuna.create_study()
    trial = study.ask()
    bad = HPOConfig(study_name="x", head_kind="lgbm_mu")
    bad.head_kind = "nope"  # type: ignore[assignment]
    with pytest.raises(HPOError, match="unknown head kind"):
        suggest_params(trial, bad)
