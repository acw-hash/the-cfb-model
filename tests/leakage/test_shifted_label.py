"""Shifted-label leakage test against the production stack (DESIGN §14).

A model given *future* features to predict *past* labels must score near
chance. Task 22B reported MAE≈15348 vs chance=6.75 as a pass under a
one-sided \"does not beat chance\" gate — that gate would also pass a
predictor returning infinity. This module replaces that with:

1. Chance = MAE of predicting the **training-set mean margin** on the exact
   evaluation games (computed in-test; never hardcoded).
2. A **two-sided** tolerance band around that baseline derived from the
   sampling noise of MAE on the evaluated game count:

       residuals_i = |y_i − μ_train|
       chance       = mean(residuals)
       se           = sample_std(residuals, ddof=1) / sqrt(n)
       band         = chance ± Z_SE * se    (Z_SE = 2 ≈ 95%)

   - model MAE materially **better** than chance → FAIL (leakage)
   - model MAE materially **worse** than chance → FAIL (wiring / degenerate
     features — a broken pipeline cannot certify a working one)
   - within the band → PASS

Band numbers for the fixture used below are printed on failure and asserted
as finite positive quantities so the derivation stays inspectable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.production_stack import build_production_stack
from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    WalkForwardHarness,
    build_shifted_feature_frame,
    week_decision_as_of,
)

# Two-sided band: ≈95% under a normal approximation for the MAE mean.
Z_SE: float = 2.0


def _kickoff(season: int, week: int, slot: int = 0) -> datetime:
    tuesday = week_decision_as_of(season, week, WalkForwardConfig())
    return tuesday + timedelta(days=4, hours=slot)


def _synth_games(
    *,
    weeks: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8),
    games_per_week: int = 4,
) -> pd.DataFrame:
    """Larger than the Task 22B 4-game toy so chance MAE is estimable."""
    rows: list[dict[str, Any]] = []
    gid = 5000
    rng = np.random.default_rng(7)
    for week in weeks:
        for slot in range(games_per_week):
            home = 10 + (slot % 8)
            away = 20 + (slot % 8)
            start = _kickoff(2023, week, slot)
            rows.append(
                {
                    "game_id": gid,
                    "game_key": f"2023:{home}:{away}:{start.date()}:{slot}",
                    "season": 2023,
                    "week": week,
                    "event_time": start,
                    "home_team_id": home,
                    "away_team_id": away,
                    "home_points": int(24 + rng.integers(0, 21)),
                    "away_points": int(21 + rng.integers(0, 21)),
                    "neutral_site": False,
                }
            )
            gid += 1
    return pd.DataFrame(rows)


def _synth_observations(games: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for g in games.itertuples(index=False):
        rows.append(
            {
                "game_id": int(g.game_id),
                "season": int(g.season),
                "week": int(g.week),
                "event_time": g.event_time,
                "home_team_id": int(g.home_team_id),
                "away_team_id": int(g.away_team_id),
                "home_epa": 0.05,
                "away_epa": -0.02,
                "home_plays": 70.0,
                "away_plays": 68.0,
                "margin": float(g.home_points) - float(g.away_points),
                "neutral_site": False,
            }
        )
    return pd.DataFrame(rows)


def _chance_mae_train_mean(y_eval: np.ndarray, mu_train: float) -> tuple[float, float, float]:
    """Return (chance_mae, se, half_width) for the training-mean predictor."""
    residuals = np.abs(y_eval - mu_train)
    n = int(residuals.size)
    chance = float(np.mean(residuals))
    if n < 2:
        return chance, float("nan"), float("nan")
    se = float(np.std(residuals, ddof=1) / np.sqrt(n))
    half_width = Z_SE * se
    return chance, se, half_width


def test_shifted_label_production_stack_two_sided_band() -> None:
    """Future features → past labels must land inside the chance band."""
    games = _synth_games()
    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(5,),
        market_features_available=False,
        seed=11,
        run_id="leak_22b_fix",
        ablation_id="full",
        nnls_equal_weight_fallback=True,
    )
    obs = _synth_observations(games)
    stack = build_production_stack(
        cfg,
        kind="fundamental",
        observations=obs,
        play_counts=(80, 100),
        n_mc_draws=400,
        n_epistemic_draws=2,
    )
    harness = WalkForwardHarness(
        config=stack.config,
        predictor=stack.predictor,
        feature_provider=stack.feature_provider,
        rating_engine=stack.rating_engine,
    )
    harness.run(games)

    # Rating snapshots for as-of feature computation.
    engine = build_production_stack(
        cfg,
        kind="fundamental",
        observations=obs,
        play_counts=(80, 100),
        n_mc_draws=400,
        n_epistemic_draws=2,
    ).rating_engine
    rating_snapshots: dict[tuple[int, int], dict[str, Any]] = {}
    weeks = sorted(int(w) for w in games["week"].unique())
    first_as_of = week_decision_as_of(2023, weeks[0], cfg)
    engine.initialize_season(2023, first_as_of - timedelta(seconds=1))
    for week in weeks:
        rating_snapshots[(2023, week)] = engine.state_snapshot()
        engine.update_after_games(games.loc[games["week"] == week])

    train = games.loc[games["week"] < 5].copy()
    train_labels = train.copy()
    train_labels["realized_margin"] = train_labels["home_points"].astype(float) - train_labels[
        "away_points"
    ].astype(float)
    train_labels["realized_total"] = train_labels["home_points"].astype(float) + train_labels[
        "away_points"
    ].astype(float)
    mu_train = float(train_labels["realized_margin"].mean())
    as_of = week_decision_as_of(2023, 4, cfg)
    feats = stack.feature_provider.compute_game_features(
        train,
        as_of,
        rating_state=rating_snapshots[(2023, 4)],
        market_features=False,
    )
    stack.predictor.fit(feats, train_labels)

    past = games.loc[games["week"] <= 2].copy()
    shifted_as_of = datetime(2024, 1, 15, tzinfo=UTC)
    shifted = build_shifted_feature_frame(
        past,
        stack.feature_provider,
        shifted_as_of,
        rating_state=rating_snapshots[(2023, 2)],
        market_features=False,
    )
    # Sanity: features must be usable (not the Task 22B all-degenerate path).
    feat_cols = [c for c in shifted.columns if c not in {"game_id", "realized_margin"}]
    assert not shifted[feat_cols].isna().all().all(), "shifted feature matrix is all-NaN"
    assert not (shifted[feat_cols].fillna(0) == 0).all().all(), "shifted features all-zero"

    preds = stack.predictor.predict(shifted)
    merged = shifted[["game_id", "realized_margin"]].merge(preds, on="game_id", how="inner")
    assert len(merged) >= 8, f"need enough games for a stable band, got n={len(merged)}"

    y = merged["realized_margin"].astype(float).to_numpy()
    yhat = merged["pred_margin"].astype(float).to_numpy()
    assert np.all(np.isfinite(yhat)), f"non-finite predictions: {yhat}"
    assert np.all(np.abs(yhat) <= 80.0), f"non-credible margin preds (wiring): {yhat}"

    model_mae = float(np.mean(np.abs(y - yhat)))
    chance_mae, se, half_width = _chance_mae_train_mean(y, mu_train)
    lo = chance_mae - half_width
    hi = chance_mae + half_width

    print(
        f"SHIFTED_LABEL model_mae={model_mae:.4f} chance_mae={chance_mae:.4f} "
        f"mu_train={mu_train:.4f} n={len(merged)} se={se:.4f} "
        f"band=[{lo:.4f}, {hi:.4f}] (chance ± {Z_SE}*SE)"
    )

    assert np.isfinite(chance_mae) and np.isfinite(half_width) and half_width > 0
    assert lo <= model_mae <= hi, (
        f"shifted-label outside two-sided band: model_mae={model_mae:.4f} "
        f"chance={chance_mae:.4f} band=[{lo:.4f}, {hi:.4f}] "
        f"(better→leakage, worse→wiring). preds sample={yhat[:4]}"
    )


def test_one_sided_worse_is_ok_audit_catalog() -> None:
    """Catalog one-sided 'worse is acceptable' assertions in the suite.

    This test documents the audit required by Task 22B-FIX item 5; it does not
    modify assertions outside ``tests/``. Findings that need two-sided treatment
    are listed in the assertion message / notes amendment.
    """
    # Kept as an executable checklist so the audit cannot silently vanish.
    audit: list[tuple[str, str, str]] = [
        (
            "tests/unit/test_task22b.py::test_production_infoset_determinism_shifted_label",
            "assert shifted.passed via walkforward.run_shifted_label_test one-sided gate",
            "needs two-sided — superseded by tests/leakage/test_shifted_label.py; "
            "legacy helper in walkforward.py still one-sided (src not edited here)",
        ),
        (
            "tests/unit/test_walkforward.py::test_shifted_label_hook_placeholder_at_chance",
            "run_shifted_label_test(...); assert result.passed (one-sided)",
            "needs two-sided when production predictor is under test; placeholder "
            "sits at chance so currently benign",
        ),
        (
            "tests/unit/test_walkforward.py::test_shifted_label_hook_detects_cheater",
            "assert model_score < chance * 0.5 (one-sided: only checks 'beats chance')",
            "intentional cheater detector — keep one-sided; not a wiring cert",
        ),
        (
            "tests/unit/test_ensemble_distribution.py::test_cqr_coverage_near_nominal_on_held_out",
            "empirical >= nominal - tolerance (one-sided: undercoverage fails, "
            "overcoverage passes)",
            "coverage: overcoverage is conservative — two-sided optional; report only",
        ),
        (
            "tests/unit/test_state_space.py (coverage band)",
            "assert 0.93 <= coverage <= 0.97",
            "already two-sided — OK",
        ),
        (
            "tests/unit/test_state_space.py (parameter recovery MAE)",
            "assert mean_abs_error_off/def < 0.20",
            "one-sided upper bound on error — appropriate for recovery; not "
            "'worse is OK' in the leakage sense",
        ),
        (
            "tests/unit/test_model_heads.py (quantile monotonicity)",
            "q05 <= q50 <= q95",
            "ordering constraint — N/A for two-sided chance bands",
        ),
        (
            "tests/unit/test_model_heads.py (Elo bake-off flag)",
            "FLAG: does not beat Elo MAE (print-only, not assert)",
            "not an assertion — OK",
        ),
    ]
    needs_fix = [row for row in audit if row[2].startswith("needs two-sided")]
    assert needs_fix, "audit catalog unexpectedly empty"
    # The leakage replacement in this module is the required fix inside tests/.
    assert any("test_shifted_label.py" in row[2] for row in needs_fix)
