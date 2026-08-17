"""W9-G — ATS interval sample aligned with rate; no invented missing-σ p."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from ncaa_quant.evaluation.metrics import (
    ats_home_outcomes,
    attach_metric_cis,
    binary_accuracy,
    compute_metric_suite,
    log_loss,
    log_loss_per_row,
)

ROOT = Path(__file__).resolve().parents[2]


def _load_regrade() -> object:
    path = ROOT / "scripts" / "_ats_regrade.py"
    spec = importlib.util.spec_from_file_location("ats_regrade_w9g", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ats_regrade_w9g"] = mod
    spec.loader.exec_module(mod)
    return mod


def _frame(
    *,
    seasons: np.ndarray,
    weeks: np.ndarray,
    p: np.ndarray,
    y_cover: np.ndarray,
    spread: float = -3.0,
) -> pd.DataFrame:
    """Build a grade-shaped frame. ``y_cover`` 1=home covers, 0=away, nan=push."""
    y = np.asarray(y_cover, dtype=float)
    spread_arr = np.full(y.shape, spread, dtype=float)
    # margin + spread > 0 ⇒ cover. Use margin = 10 for cover, -10 for miss, -spread for push.
    margin = np.where(np.isnan(y), -spread, np.where(y >= 0.5, 10.0, -10.0))
    return pd.DataFrame(
        {
            "season": seasons.astype(int),
            "week": weeks.astype(int),
            "realized_margin": margin,
            "spread_close": spread_arr,
            "p_ats_home": np.asarray(p, dtype=float),
            "pred_margin": np.full(y.shape, 4.0),
            "sigma_m": np.full(y.shape, 14.0),
        }
    )


def test_nan_p_excluded_from_rate_and_interval_not_scored_as_away() -> None:
    """NaN p must drop from both accuracy and CI, not score as an away pick."""
    # All home-covered. Finite p are correct home picks; NaN p would have been
    # (nan >= 0.5) == False → away miss under the old CI mask, pulling n to 4
    # and the rate below 100%.
    p = np.array([0.8, 0.7, 0.6, np.nan])
    y = np.array([1.0, 1.0, 1.0, 1.0])
    frame = _frame(
        seasons=np.array([2019, 2019, 2019, 2019]),
        weeks=np.array([5, 6, 7, 8]),
        p=p,
        y_cover=y,
    )
    y_ats = ats_home_outcomes(frame["realized_margin"].to_numpy(), frame["spread_close"].to_numpy())
    rate = binary_accuracy(frame["p_ats_home"].to_numpy(), y_ats)
    assert rate == pytest.approx(1.0)
    n_rate = int(
        (np.isfinite(frame["p_ats_home"].to_numpy(dtype=float)) & np.isfinite(y_ats)).sum()
    )
    assert n_rate == 3

    suite = compute_metric_suite(frame)
    cis = attach_metric_cis(suite, frame, n_boot=32, seed=23)
    assert suite.ats_accuracy == pytest.approx(1.0)
    assert int(cis["ats_accuracy"].n) == 3
    assert int(cis["ats_accuracy_naive"].n) == 3
    assert cis["ats_accuracy"].rate == pytest.approx(1.0)
    assert cis["ats_accuracy_naive"].rate == pytest.approx(1.0)


def test_denominator_equality_per_regime() -> None:
    """Published-rate n equals interval n on every regime, including NaN-p rows."""
    rng = np.random.default_rng(7)
    n_2019_ok = 40
    n_2019_nan = 12
    n_snap = 36
    p_2019 = np.concatenate([rng.uniform(0.2, 0.8, n_2019_ok), np.full(n_2019_nan, np.nan)])
    y_2019 = np.concatenate(
        [rng.integers(0, 2, n_2019_ok).astype(float), rng.integers(0, 2, n_2019_nan).astype(float)]
    )
    p_snap = rng.uniform(0.25, 0.75, n_snap)
    y_snap = rng.integers(0, 2, n_snap).astype(float)
    frame = pd.concat(
        [
            _frame(
                seasons=np.full(n_2019_ok + n_2019_nan, 2019),
                weeks=np.arange(1, n_2019_ok + n_2019_nan + 1) % 8 + 1,
                p=p_2019,
                y_cover=y_2019,
            ),
            _frame(
                seasons=np.full(n_snap, 2022),
                weeks=np.arange(1, n_snap + 1) % 6 + 1,
                p=p_snap,
                y_cover=y_snap,
            ),
        ],
        ignore_index=True,
    )
    regrade = _load_regrade()
    for label, mask in [
        ("cfbd_2019", frame["season"] == 2019),
        ("snapshots_2021_2024", frame["season"] == 2022),
    ]:
        rec = regrade._regime_ats(frame, label, mask)
        assert rec is not None
        sub = frame.loc[mask]
        y = ats_home_outcomes(
            sub["realized_margin"].to_numpy(dtype=float),
            sub["spread_close"].to_numpy(dtype=float),
        )
        p = sub["p_ats_home"].to_numpy(dtype=float)
        n_rate = int((np.isfinite(p) & np.isfinite(y)).sum())
        suite = compute_metric_suite(sub)
        cis = attach_metric_cis(suite, sub, n_boot=40, seed=23)
        boot = cis["ats_accuracy"]
        naive = cis["ats_accuracy_naive"]
        assert rec.n == n_rate
        assert int(boot.n) == n_rate
        assert int(naive.n) == n_rate
        assert rec.n == int(boot.n) == int(naive.n)
    assert (frame.loc[frame["season"] == 2019, "p_ats_home"].isna()).sum() == n_2019_nan


def test_missing_sigma_does_not_invent_probability() -> None:
    """Rows the fit stored as missing p stay missing; they enter no ATS metric."""
    regrade = _load_regrade()
    mu = np.array([10.0, 10.0, 4.0])
    sigma = np.array([np.nan, 14.0, 12.0])
    spread = np.array([-3.0, -7.0, -3.0])
    p = regrade._p_ats_gaussian(mu, sigma, spread)
    assert not np.isfinite(p[0])
    for invented in (0.999, 0.001, 0.5):
        assert p[0] != pytest.approx(invented)
    expected_ok = float(stats.norm.cdf((10.0 + (-7.0)) / 14.0))
    assert p[1] == pytest.approx(expected_ok)
    assert np.isfinite(p[2])

    # Grade-shaped row: fit recorded p missing on the missing-σ game.
    y_cover = np.array([1.0, 1.0, 0.0])
    frame = _frame(
        seasons=np.array([2019, 2019, 2019]),
        weeks=np.array([2, 5, 6]),
        p=p,
        y_cover=y_cover,
    )
    frame.loc[0, "sigma_m"] = np.nan
    y = ats_home_outcomes(
        frame["realized_margin"].to_numpy(dtype=float),
        frame["spread_close"].to_numpy(dtype=float),
    )
    mask = np.isfinite(p) & np.isfinite(y)
    assert int(mask.sum()) == 2
    assert not np.isfinite(log_loss_per_row(p, y)[0])
    rate = binary_accuracy(p, y)
    ll = log_loss(p[mask], y[mask])
    suite = compute_metric_suite(frame)
    cis = attach_metric_cis(suite, frame, n_boot=32, seed=23)
    assert suite.ats_accuracy == pytest.approx(rate)
    assert int(cis["ats_accuracy"].n) == 2
    assert int(cis["ats_accuracy_naive"].n) == 2
    assert np.isfinite(ll)
    # Invented hard-edge p on the missing-σ row would have entered log-loss.
    p_invented = p.copy()
    p_invented[0] = 0.001
    assert np.isfinite(log_loss_per_row(p_invented, y)[0])
    assert log_loss(p_invented, y) != pytest.approx(ll)
