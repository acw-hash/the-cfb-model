"""Well-posed leakage suite (DESIGN §14 as amended, audit A-8).

Replaces the deleted shifted-label null. That test asserted future features must
not predict past games at better than chance, which is false — strength persists,
so a November rating is legitimately informative about a September game, and a
leak-free system fails it.

These tests are falsifiable for the right reason, and each one is paired with a
positive control that proves it can actually fail.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.leakage import (
    LeakageError,
    assert_label_permutation_clean,
    assert_no_prophecy_features,
    audit_prophecy_features,
    chance_band_mae,
    compare_as_of_sensitivity,
    evaluate_label_permutation,
    permute_labels_within_week,
    plant_prophecy_feature,
)

N_WEEKS = 10
GAMES_PER_WEEK = 12
SEED = 4041


def _labels(*, seed: int = SEED) -> pd.DataFrame:
    """Synthetic labels with a real week trend, so within-week matters."""
    rng = np.random.default_rng(seed)
    rows: list[dict[str, float]] = []
    gid = 1
    for week in range(1, N_WEEKS + 1):
        for _ in range(GAMES_PER_WEEK):
            rows.append(
                {
                    "game_id": gid,
                    "season": 2023,
                    "week": week,
                    # Margins widen as the season goes on; totals climb too.
                    "realized_margin": float(rng.normal(loc=week * 0.5, scale=14.0)),
                    "realized_total": float(rng.normal(loc=48.0 + week, scale=9.0)),
                }
            )
            gid += 1
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# The permutation itself
# ---------------------------------------------------------------------------


def test_permutation_keeps_labels_within_their_own_week() -> None:
    """Week structure must survive, or the test would flag it as leakage."""
    labels = _labels()
    permuted = permute_labels_within_week(labels, seed=1)

    for week in range(1, N_WEEKS + 1):
        before = labels.loc[labels["week"] == week, "realized_margin"].sort_values().to_numpy()
        after = permuted.loc[permuted["week"] == week, "realized_margin"].sort_values().to_numpy()
        assert after == pytest.approx(before)


def test_permutation_actually_moves_labels() -> None:
    labels = _labels()
    permuted = permute_labels_within_week(labels, seed=2)

    moved = (
        labels["realized_margin"].to_numpy() != permuted["realized_margin"].to_numpy()
    ).sum()
    assert moved > 0.5 * len(labels)


def test_margin_and_total_move_together() -> None:
    """A shuffled game keeps one real game's whole outcome, not a chimera."""
    labels = _labels()
    permuted = permute_labels_within_week(labels, seed=3)

    original_pairs = set(
        zip(labels["realized_margin"], labels["realized_total"], strict=True)
    )
    for margin, total in zip(
        permuted["realized_margin"], permuted["realized_total"], strict=True
    ):
        assert (margin, total) in original_pairs


def test_permutation_is_reproducible_and_seed_dependent() -> None:
    labels = _labels()

    a = permute_labels_within_week(labels, seed=7)["realized_margin"].to_numpy()
    b = permute_labels_within_week(labels, seed=7)["realized_margin"].to_numpy()
    c = permute_labels_within_week(labels, seed=8)["realized_margin"].to_numpy()

    assert a == pytest.approx(b)
    assert not np.allclose(a, c)


def test_single_game_week_is_left_alone() -> None:
    labels = pd.DataFrame(
        {
            "game_id": [1],
            "season": [2023],
            "week": [1],
            "realized_margin": [7.0],
            "realized_total": [55.0],
        }
    )

    assert permute_labels_within_week(labels, seed=1)["realized_margin"].item() == 7.0


def test_missing_columns_are_reported() -> None:
    with pytest.raises(LeakageError, match="missing columns"):
        permute_labels_within_week(pd.DataFrame({"game_id": [1]}), seed=1)


# ---------------------------------------------------------------------------
# The gate, with a positive control
# ---------------------------------------------------------------------------


def test_a_model_with_no_signal_lands_at_chance() -> None:
    """A leak-free fit on permuted labels predicts the training mean and passes."""
    labels = _labels()
    permuted = permute_labels_within_week(labels, seed=11)
    y = permuted["realized_margin"].to_numpy(dtype=float)
    train_mean = float(y[:60].mean())
    y_eval = y[60:]

    result = evaluate_label_permutation(
        y_eval,
        np.full(y_eval.size, train_mean),
        train_mean_label=train_mean,
        seed=11,
    )

    assert result.passed
    assert not result.beats_chance
    assert_label_permutation_clean(result)


def test_a_leaking_model_is_caught() -> None:
    """Positive control: a model handed the labels must fail the gate.

    Without this the suite could pass simply by being unable to detect anything.
    """
    labels = _labels()
    permuted = permute_labels_within_week(labels, seed=12)
    y = permuted["realized_margin"].to_numpy(dtype=float)
    train_mean = float(y[:60].mean())
    y_eval = y[60:]

    # Predictions that are the labels plus a whisper of noise: a leak.
    leaked = y_eval + np.random.default_rng(12).normal(scale=0.5, size=y_eval.size)
    result = evaluate_label_permutation(
        y_eval, leaked, train_mean_label=train_mean, seed=12
    )

    assert result.beats_chance
    assert not result.passed
    with pytest.raises(LeakageError, match="beat chance"):
        assert_label_permutation_clean(result)


def test_a_broken_model_also_fails_rather_than_passing_quietly() -> None:
    """Scoring far worse than chance is a wiring fault, not proof of cleanliness."""
    labels = _labels()
    y = labels["realized_margin"].to_numpy(dtype=float)
    train_mean = float(y[:60].mean())
    y_eval = y[60:]

    result = evaluate_label_permutation(
        y_eval,
        np.full(y_eval.size, 500.0),  # nonsense predictions
        train_mean_label=train_mean,
        seed=13,
    )

    assert result.worse_than_chance
    with pytest.raises(LeakageError, match="wiring"):
        assert_label_permutation_clean(result)


def test_chance_band_is_derived_not_hardcoded() -> None:
    y = np.array([1.0, -3.0, 5.0, -2.0, 4.0, 0.0])
    band = chance_band_mae(y, 0.0)

    assert band.chance == pytest.approx(np.mean(np.abs(y)))
    assert band.half_width > 0.0
    assert band.low < band.chance < band.high
    assert band.n == 6


def test_chance_band_on_a_single_row_reports_no_width() -> None:
    band = chance_band_mae([3.0], 0.0)

    assert band.chance == pytest.approx(3.0)
    assert np.isnan(band.half_width)


def test_mismatched_shapes_are_refused() -> None:
    with pytest.raises(LeakageError, match="shape"):
        evaluate_label_permutation([1.0, 2.0], [1.0], train_mean_label=0.0, seed=1)


# ---------------------------------------------------------------------------
# Planted prophecy
# ---------------------------------------------------------------------------


def _features(labels: pd.DataFrame) -> pd.DataFrame:
    """Honest features: correlated with the outcome, but nowhere near perfectly."""
    rng = np.random.default_rng(SEED + 1)
    n = len(labels)
    return pd.DataFrame(
        {
            "game_id": labels["game_id"].to_numpy(),
            "feat__rating_diff": labels["realized_margin"].to_numpy() * 0.25
            + rng.normal(scale=10.0, size=n),
            "feat__pace": rng.normal(size=n),
        }
    )


def test_clean_features_pass_the_prophecy_audit() -> None:
    labels = _labels()
    result = audit_prophecy_features(_features(labels), labels)

    assert result.passed
    assert result.n_features_checked == 2
    assert "no prophecy" in result.describe()
    assert_no_prophecy_features(result)


def test_a_planted_prophecy_is_caught() -> None:
    """The §14 planted-prophecy requirement: plant a leak, confirm detection."""
    labels = _labels()
    poisoned = plant_prophecy_feature(_features(labels), labels)

    result = audit_prophecy_features(poisoned, labels)

    assert not result.passed
    assert [f.feature for f in result.findings] == ["feat__prophecy"]
    assert abs(result.findings[0].correlation) > 0.999
    with pytest.raises(LeakageError, match="prophecy audit failed"):
        assert_no_prophecy_features(result)


def test_a_noisy_prophecy_below_threshold_is_not_flagged() -> None:
    """The detector targets label copies, not merely strong honest predictors.

    A feature correlating 0.9 with the outcome would be extraordinary in this
    domain but is not by itself proof of leakage; the threshold is deliberately
    set where only a relabelled outcome can reach.
    """
    labels = _labels()
    noisy = plant_prophecy_feature(_features(labels), labels, noise_scale=8.0, seed=5)

    result = audit_prophecy_features(noisy, labels)

    assert result.passed


def test_prophecy_audit_ignores_constant_and_bookkeeping_columns() -> None:
    labels = _labels()
    frame = _features(labels)
    frame["feat__constant"] = 1.0
    frame["season"] = 2023

    result = audit_prophecy_features(frame, labels)

    assert result.passed
    assert result.n_features_checked == 2  # constant and season both skipped


def test_prophecy_audit_can_target_the_total() -> None:
    labels = _labels()
    poisoned = plant_prophecy_feature(
        _features(labels), labels, label_column="realized_total"
    )

    assert audit_prophecy_features(poisoned, labels, label_column="realized_total").findings
    # ...and the same column is innocent with respect to the margin.
    assert audit_prophecy_features(poisoned, labels, label_column="realized_margin").passed


# ---------------------------------------------------------------------------
# Feature-timestamp behaviour
# ---------------------------------------------------------------------------


def test_features_that_move_with_as_of_are_detected() -> None:
    early = pd.DataFrame({"game_id": [1, 2], "feat__rating": [0.1, 0.2], "feat__flat": [1.0, 1.0]})
    late = pd.DataFrame({"game_id": [1, 2], "feat__rating": [0.4, 0.3], "feat__flat": [1.0, 1.0]})

    result = compare_as_of_sensitivity(early, late)

    assert result.reads_the_clock
    assert result.changed_features == ("feat__rating",)
    assert result.constant_features == ("feat__flat",)


def test_a_provider_ignoring_as_of_is_visible() -> None:
    """The silent failure a single snapshot cannot reveal."""
    frame = pd.DataFrame({"game_id": [1, 2], "feat__rating": [0.1, 0.2]})

    result = compare_as_of_sensitivity(frame, frame.copy())

    assert not result.reads_the_clock
    assert result.changed_features == ()


def test_nan_in_both_frames_is_not_a_change() -> None:
    early = pd.DataFrame({"game_id": [1], "feat__x": [float("nan")]})
    late = pd.DataFrame({"game_id": [1], "feat__x": [float("nan")]})

    assert compare_as_of_sensitivity(early, late).changed_features == ()
