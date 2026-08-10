"""Adaptive Conformal Inference (DESIGN §2.6, audit A-9)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.models.conformal import (
    AdaptiveCQR,
    fit_adaptive_cqr,
    fit_cqr,
    run_aci_stream,
)
from ncaa_quant.models.heads.quantile import quantile_column


def _calib_frame(*, n_per_season: int = 200, seed: int = 11) -> pd.DataFrame:
    """Well-specified Gaussian world with honest quantile columns."""
    from scipy import stats

    rng = np.random.default_rng(seed)
    rows: list[dict[str, float]] = []
    gid = 0
    for season in (2022, 2023, 2024):
        for _ in range(n_per_season):
            mu = float(rng.normal(0, 8))
            y = float(mu + rng.normal(0, 12))
            row: dict[str, float] = {
                "game_id": gid,
                "season": season,
                "realized_margin": y,
            }
            for q in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95):
                row[quantile_column("margin", q)] = float(stats.norm.ppf(q, loc=mu, scale=12))
            rows.append(row)
            gid += 1
    return pd.DataFrame(rows)


def test_fit_adaptive_starts_at_nominal_alpha() -> None:
    frame = _calib_frame()
    aci = fit_adaptive_cqr(frame, target="margin", nominal=0.8, calibration_seasons=(2022, 2023))
    assert isinstance(aci, AdaptiveCQR)
    assert aci.target_alpha == pytest.approx(0.2)
    assert aci.alpha_t == pytest.approx(0.2)
    assert aci.meta["initializer"] == "split_cqr"
    assert aci.threshold() == pytest.approx(
        fit_cqr(frame, target="margin", calibration_seasons=(2022, 2023)).score_thresholds[0.8]
    )


def test_persistent_undercoverage_lowers_alpha_to_widen() -> None:
    """Misses drive α_t down: smaller α → higher score quantile → wider intervals.

    ACI update: α ← α + γ(α_target − err). err=1 pulls α down.
    """
    frame = _calib_frame()
    aci = fit_adaptive_cqr(
        frame,
        target="margin",
        nominal=0.8,
        calibration_seasons=(2022, 2023),
        gamma=0.05,
    )
    thr0 = aci.threshold()
    alpha0 = aci.alpha_t
    for _ in range(40):
        aci.update(y=500.0, q_lo=-5.0, q_hi=5.0)
    assert aci.alpha_t < alpha0
    assert aci.threshold() >= thr0
    assert aci.n_misses == 40
    assert aci.empirical_coverage == pytest.approx(0.0)


def test_persistent_overcoverage_raises_alpha_to_tighten() -> None:
    """Always-covered stream drives α_t up: tighter intervals."""
    frame = _calib_frame()
    aci = fit_adaptive_cqr(
        frame,
        target="margin",
        nominal=0.8,
        calibration_seasons=(2022, 2023),
        gamma=0.05,
    )
    thr0 = aci.threshold()
    alpha0 = aci.alpha_t
    for _ in range(40):
        aci.update(y=0.0, q_lo=-100.0, q_hi=100.0)
    assert aci.alpha_t > alpha0
    assert aci.threshold() <= thr0
    assert aci.n_misses == 0
    assert aci.empirical_coverage == pytest.approx(1.0)


def test_aci_stream_tracks_well_specified_coverage() -> None:
    """On a well-specified holdout, online coverage should sit near nominal."""
    frame = _calib_frame(n_per_season=300, seed=3)
    aci = fit_adaptive_cqr(
        frame,
        target="margin",
        nominal=0.8,
        calibration_seasons=(2022, 2023),
        gamma=0.01,
    )
    held = frame.loc[frame["season"] == 2024]
    lo_col = quantile_column("margin", 0.10)
    hi_col = quantile_column("margin", 0.90)
    diag = run_aci_stream(
        aci,
        held["realized_margin"].to_numpy(),
        held[lo_col].to_numpy(),
        held[hi_col].to_numpy(),
    )
    assert len(diag) == len(held)
    # After adaptation, empirical coverage on the stream should be within a
    # few points of 80%. ACI is approximate under any residual misspecification.
    assert 0.70 <= aci.empirical_coverage <= 0.90


def test_module_docstring_no_longer_claims_distribution_free_guarantee() -> None:
    import ncaa_quant.models.conformal as mod

    assert "distribution-free guarantee" not in (mod.__doc__ or "").lower()
    assert "approximate" in (mod.__doc__ or "").lower()
