"""Leakage detection with well-posed nulls (DESIGN §14, audit A-8).

The retired test and why it was wrong
-------------------------------------
The old suite asserted that "a model given future features to predict PAST games
must score approximately at chance". That null is false. Team strength is
persistent, so a rating computed in November is legitimately informative about a
September game — a completely leak-free system fails the test. The only ways to
make it pass are to loosen the threshold until it means nothing, or to break the
feature pipeline until the model is useless. Both are worse than having no test,
because they manufacture a green check.

§14 as amended deletes that null and replaces it with three tests that are
falsifiable for the right reason:

* **Within-week label permutation.** Shuffle outcomes among games in the same
  week and retrain. There is now no relationship between features and labels, so
  any out-of-sample skill above chance means the label reached the model through
  something other than honest prediction. Permuting *within* week rather than
  globally holds the week-level structure (schedule strength, weather regime,
  scoring environment) fixed, so a model cannot score above chance just by
  knowing which week it is.

* **Planted prophecy.** Deliberately add a feature derived from the outcome and
  confirm the detectors fire. A leakage suite that has never caught anything is
  not evidence of cleanliness; it may simply be blind.

* **Feature-timestamp behaviour.** Confirm features actually change when ``as_of``
  moves, which is what proves the provider reads the clock at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

#: Two-sided band half-width in standard errors (~95% under a normal approximation).
Z_SE: float = 2.0
#: |correlation| with the label above which a feature is treated as a prophecy.
PROPHECY_CORR_THRESHOLD: float = 0.99


class LeakageError(AssertionError):
    """Raised when a leakage check fails."""


# ---------------------------------------------------------------------------
# Within-week label permutation
# ---------------------------------------------------------------------------


def permute_labels_within_week(
    labels: pd.DataFrame,
    *,
    seed: int,
    label_columns: Sequence[str] = ("realized_margin", "realized_total"),
    week_columns: Sequence[str] = ("season", "week"),
) -> pd.DataFrame:
    """Shuffle label columns among the games within each ``(season, week)``.

    Label columns are permuted **together** by row, so margin and total stay
    mutually consistent — a shuffled game keeps some real game's whole outcome
    rather than acquiring an impossible margin/total pair.

    Permuting within week rather than across the season deliberately preserves
    week-level structure. If labels were shuffled globally, a model could beat
    chance simply by learning that later weeks score differently, and the test
    would flag that as leakage.
    """
    missing = [c for c in (*week_columns, *label_columns) if c not in labels.columns]
    if missing:
        msg = f"labels frame missing columns {missing}"
        raise LeakageError(msg)

    rng = np.random.default_rng(int(seed))
    out = labels.copy()
    cols = list(label_columns)
    for _, idx in out.groupby(list(week_columns), sort=False).groups.items():
        positions = np.asarray(idx)
        if positions.size < 2:
            continue
        order = rng.permutation(positions.size)
        out.loc[positions, cols] = out.loc[positions[order], cols].to_numpy()
    return out


@dataclass(frozen=True)
class ChanceBand:
    """Two-sided tolerance band around the chance-level score."""

    chance: float
    se: float
    half_width: float
    n: int

    @property
    def low(self) -> float:
        return self.chance - self.half_width

    @property
    def high(self) -> float:
        return self.chance + self.half_width

    def contains(self, score: float) -> bool:
        return bool(self.low <= score <= self.high)


def chance_band_mae(
    y: np.ndarray | Sequence[float],
    baseline_prediction: float,
    *,
    z_se: float = Z_SE,
) -> ChanceBand:
    """Band for mean absolute error against a constant baseline.

    ``baseline_prediction`` must come from the training set only — usually the
    training-set mean label. Deriving it from the evaluation labels would make
    chance itself a function of the answers.
    """
    resid = np.abs(np.asarray(y, dtype=float) - float(baseline_prediction))
    resid = resid[np.isfinite(resid)]
    n = int(resid.size)
    chance = float(np.mean(resid)) if n else float("nan")
    if n < 2:
        return ChanceBand(chance=chance, se=float("nan"), half_width=float("nan"), n=n)
    se = float(np.std(resid, ddof=1) / np.sqrt(n))
    return ChanceBand(chance=chance, se=se, half_width=float(z_se) * se, n=n)


@dataclass(frozen=True)
class LabelPermutationResult:
    """Outcome of a within-week label-permutation run."""

    model_mae: float
    band: ChanceBand
    n_eval: int
    seed: int

    @property
    def beats_chance(self) -> bool:
        """Scored materially better than chance — the leakage signature."""
        return bool(np.isfinite(self.model_mae) and self.model_mae < self.band.low)

    @property
    def worse_than_chance(self) -> bool:
        """Scored materially worse than chance — a wiring fault, not cleanliness."""
        return bool(np.isfinite(self.model_mae) and self.model_mae > self.band.high)

    @property
    def passed(self) -> bool:
        return not self.beats_chance and not self.worse_than_chance

    def describe(self) -> str:
        return (
            f"label_permutation seed={self.seed} model_mae={self.model_mae:.4f} "
            f"chance={self.band.chance:.4f} band=[{self.band.low:.4f}, {self.band.high:.4f}] "
            f"n_eval={self.n_eval}"
        )


def evaluate_label_permutation(
    y_eval: np.ndarray | Sequence[float],
    y_pred: np.ndarray | Sequence[float],
    *,
    train_mean_label: float,
    seed: int,
    z_se: float = Z_SE,
) -> LabelPermutationResult:
    """Score predictions from a permuted-label fit against the chance band.

    The gate is two-sided on purpose. Beating chance means the label leaked.
    Scoring far *worse* than chance is not a pass either: it usually means the
    features or the fit are broken, and a broken pipeline cannot certify a
    working one.
    """
    y = np.asarray(y_eval, dtype=float)
    yhat = np.asarray(y_pred, dtype=float)
    if y.shape != yhat.shape:
        msg = f"y_eval shape {y.shape} != y_pred shape {yhat.shape}"
        raise LeakageError(msg)
    ok = np.isfinite(y) & np.isfinite(yhat)
    mae = float(np.mean(np.abs(y[ok] - yhat[ok]))) if np.any(ok) else float("nan")
    band = chance_band_mae(y[ok], train_mean_label, z_se=z_se)
    return LabelPermutationResult(
        model_mae=mae,
        band=band,
        n_eval=int(np.count_nonzero(ok)),
        seed=int(seed),
    )


def assert_label_permutation_clean(result: LabelPermutationResult) -> None:
    """Raise when a permuted-label fit did not land at chance."""
    if result.passed:
        return
    reason = "beat chance (label leakage)" if result.beats_chance else "worse than chance (wiring)"
    msg = f"label-permutation test failed — {reason}: {result.describe()}"
    raise LeakageError(msg)


# ---------------------------------------------------------------------------
# Planted prophecy
# ---------------------------------------------------------------------------


def plant_prophecy_feature(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    feature_name: str = "feat__prophecy",
    label_column: str = "realized_margin",
    noise_scale: float = 0.0,
    seed: int = 0,
) -> pd.DataFrame:
    """Add a feature derived from the outcome, for testing the detectors.

    Test-fixture helper only, never a production path. ``noise_scale`` blunts the
    correlation so the detector can be probed near its threshold rather than only
    on a trivially perfect copy of the label.
    """
    if label_column not in labels.columns:
        msg = f"labels frame has no column {label_column!r}"
        raise LeakageError(msg)
    rng = np.random.default_rng(int(seed))
    joined = features.merge(labels[["game_id", label_column]], on="game_id", how="left")
    values = joined[label_column].to_numpy(dtype=float)
    if noise_scale > 0.0:
        values = values + rng.normal(scale=float(noise_scale), size=values.size)
    out = features.copy()
    out[feature_name] = values
    return out


@dataclass(frozen=True)
class ProphecyFinding:
    """A feature suspiciously correlated with the label it should be predicting."""

    feature: str
    correlation: float
    n: int


@dataclass(frozen=True)
class ProphecyAuditResult:
    """Result of scanning a feature frame for outcome-derived columns."""

    findings: tuple[ProphecyFinding, ...] = ()
    n_features_checked: int = 0
    threshold: float = PROPHECY_CORR_THRESHOLD

    @property
    def passed(self) -> bool:
        return not self.findings

    def describe(self) -> str:
        if self.passed:
            return f"no prophecy features among {self.n_features_checked} checked"
        worst = max(self.findings, key=lambda f: abs(f.correlation))
        return (
            f"{len(self.findings)} prophecy feature(s); worst={worst.feature} "
            f"|corr|={abs(worst.correlation):.4f} > {self.threshold}"
        )


def audit_prophecy_features(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    label_column: str = "realized_margin",
    threshold: float = PROPHECY_CORR_THRESHOLD,
    exclude: Sequence[str] = (),
) -> ProphecyAuditResult:
    """Flag features whose correlation with the label is implausibly high.

    A genuine predictor of college football margins does not correlate 0.99 with
    the outcome; nothing in this domain is that good. Such a column is the label
    wearing a different name.

    This complements the information-set audit rather than duplicating it: the
    audit catches features that *change* when recomputed at the logged ``as_of``,
    while this catches a leak that is stable under recomputation because the
    provider is consistently reading the future.
    """
    if label_column not in labels.columns:
        msg = f"labels frame has no column {label_column!r}"
        raise LeakageError(msg)

    joined = features.merge(labels[["game_id", label_column]], on="game_id", how="inner")
    y = joined[label_column].to_numpy(dtype=float)
    skip = {"game_id", "season", "week", "as_of", label_column, *exclude}

    findings: list[ProphecyFinding] = []
    checked = 0
    for col in joined.columns:
        if col in skip:
            continue
        values = pd.to_numeric(joined[col], errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(values) & np.isfinite(y)
        if int(ok.sum()) < 3:
            continue
        if float(np.std(values[ok])) <= 0.0 or float(np.std(y[ok])) <= 0.0:
            continue
        checked += 1
        corr = float(np.corrcoef(values[ok], y[ok])[0, 1])
        if np.isfinite(corr) and abs(corr) >= float(threshold):
            findings.append(ProphecyFinding(feature=col, correlation=corr, n=int(ok.sum())))
    return ProphecyAuditResult(
        findings=tuple(findings),
        n_features_checked=checked,
        threshold=float(threshold),
    )


def assert_no_prophecy_features(result: ProphecyAuditResult) -> None:
    """Raise when a feature is too correlated with the outcome to be honest."""
    if result.passed:
        return
    raise LeakageError(f"prophecy audit failed: {result.describe()}")


# ---------------------------------------------------------------------------
# Feature-timestamp behaviour
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AsOfSensitivityResult:
    """Whether a feature frame responds to moving ``as_of``."""

    changed_features: tuple[str, ...] = ()
    constant_features: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def reads_the_clock(self) -> bool:
        return bool(self.changed_features)


def compare_as_of_sensitivity(
    early: pd.DataFrame,
    late: pd.DataFrame,
    *,
    key: str = "game_id",
    atol: float = 1e-12,
) -> AsOfSensitivityResult:
    """Compare two feature frames for the same games at different ``as_of``.

    A provider that ignores ``as_of`` returns byte-identical features however
    much later you ask, which is the silent failure mode this catches: point-in-time
    correctness cannot be verified by inspecting a single snapshot.
    """
    shared = sorted(set(early.columns).intersection(late.columns) - {key})
    merged = early.merge(late, on=key, how="inner", suffixes=("__early", "__late"))
    changed: list[str] = []
    constant: list[str] = []
    for col in shared:
        a = pd.to_numeric(merged[f"{col}__early"], errors="coerce").to_numpy(dtype=float)
        b = pd.to_numeric(merged[f"{col}__late"], errors="coerce").to_numpy(dtype=float)
        both_nan = np.isnan(a) & np.isnan(b)
        diff = np.where(both_nan, 0.0, np.abs(a - b))
        if np.nanmax(diff, initial=0.0) > atol:
            changed.append(col)
        else:
            constant.append(col)
    return AsOfSensitivityResult(
        changed_features=tuple(changed),
        constant_features=tuple(constant),
        meta={"n_rows": int(len(merged)), "n_features": len(shared)},
    )


__all__ = [
    "PROPHECY_CORR_THRESHOLD",
    "Z_SE",
    "AsOfSensitivityResult",
    "ChanceBand",
    "LabelPermutationResult",
    "LeakageError",
    "ProphecyAuditResult",
    "ProphecyFinding",
    "assert_label_permutation_clean",
    "assert_no_prophecy_features",
    "audit_prophecy_features",
    "chance_band_mae",
    "compare_as_of_sensitivity",
    "evaluate_label_permutation",
    "permute_labels_within_week",
    "plant_prophecy_feature",
]
