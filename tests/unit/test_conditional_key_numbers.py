"""Conditional key-number kernel (DESIGN §2.3, audit A-7)."""

from __future__ import annotations

import numpy as np
import pytest

from ncaa_quant.distribution.key_numbers import (
    DEFAULT_MU_ABS_EDGES,
    ConditionalKeyNumberKernel,
    KeyNumberError,
    KeyNumberKernel,
    discrete_margin_pmf,
    fit_key_number_kernel,
    validate_key_number_kernel,
)


def _planted_world(*, n: int = 8000, seed: int = 17) -> tuple[np.ndarray, np.ndarray]:
    """Pick'em games pile on ±3; blowouts pile on ±14. Pooled kernel cannot fit both."""
    rng = np.random.default_rng(seed)
    # Half pick'em (|μ|~1), half blowouts (|μ|~20).
    is_pickem = rng.random(n) < 0.5
    mu = np.where(is_pickem, rng.normal(0.5, 1.0, n), rng.normal(20.0, 2.0, n))
    # Continuous residual noise, then plant key mass by regime.
    y = mu + rng.normal(0.0, 4.0, n)
    plant = rng.random(n) < 0.35
    y = np.where(plant & is_pickem, np.round(mu) + 3.0, y)
    y = np.where(plant & ~is_pickem, np.round(mu) + 14.0, y)
    return y, mu


def test_default_fit_is_conditional() -> None:
    y, mu = _planted_world(n=2000)
    kernel = fit_key_number_kernel(y, mu, min_count=5, min_bucket_n=30)
    assert isinstance(kernel, ConditionalKeyNumberKernel)
    assert kernel.edges == DEFAULT_MU_ABS_EDGES
    assert kernel.meta["n_buckets_fit"] >= 2


def test_pooled_opt_out_still_available() -> None:
    y, mu = _planted_world(n=500)
    kernel = fit_key_number_kernel(y, mu, mu_abs_edges=None)
    assert isinstance(kernel, KeyNumberKernel)
    assert not isinstance(kernel, ConditionalKeyNumberKernel)


def test_pickem_and_blowout_buckets_learn_different_offsets() -> None:
    """The whole point of A-7: regime-specific key mass."""
    y, mu = _planted_world()
    kernel = fit_key_number_kernel(y, mu, min_count=5, min_bucket_n=50)
    assert isinstance(kernel, ConditionalKeyNumberKernel)

    pickem = kernel.resolve(1.0)
    blowout = kernel.resolve(20.0)
    # Pick'em should overweight +3 relative to the blowout bucket.
    assert pickem.weight(3) > blowout.weight(3)
    # Blowout should overweight +14 relative to pick'em.
    assert blowout.weight(14) > pickem.weight(14)


def test_discrete_pmf_uses_the_mu_bucket() -> None:
    y, mu = _planted_world()
    kernel = fit_key_number_kernel(y, mu, min_count=5, min_bucket_n=50)

    _, pmf_pick = discrete_margin_pmf(1.0, 8.0, kernel)
    _, pmf_blow = discrete_margin_pmf(20.0, 8.0, kernel)
    integers = np.arange(-80, 81)
    # Mass on margin == round(μ)+3 for pick'em vs round(μ)+14 for blowout.
    idx3 = int(np.where(integers == 4)[0][0])  # round(1)+3
    idx14 = int(np.where(integers == 34)[0][0])  # round(20)+14
    # Not a strict ordering across different centers — just check each regime
    # puts relatively more mass on its planted offset than the other regime's
    # PMF does at the same offset index relative to its own center.
    _, pmf_pick_at3 = discrete_margin_pmf(0.0, 8.0, kernel)
    _, pmf_blow_at3 = discrete_margin_pmf(0.0, 8.0, kernel.resolve(20.0))
    # At μ=0, pick'em-resolved kernel should put more on +3 than blowout-resolved.
    i3 = int(np.where(np.arange(-80, 81) == 3)[0][0])
    assert float(pmf_pick_at3[i3]) > float(pmf_blow_at3[i3])
    del pmf_pick, pmf_blow, idx3, idx14


def test_thin_bucket_falls_back_to_pooled() -> None:
    rng = np.random.default_rng(3)
    mu = rng.normal(0, 1, 200)  # all pick'em — blowout buckets empty
    y = mu + rng.normal(0, 5, 200)
    kernel = fit_key_number_kernel(
        y, mu, mu_abs_edges=(0.0, 3.5, 50.0, float("inf")), min_bucket_n=80
    )
    assert isinstance(kernel, ConditionalKeyNumberKernel)
    # Far-blowout bucket was empty / thin → resolve returns pooled.
    assert kernel.resolve(60.0) is kernel.pooled


def test_validation_detects_pooled_failure_on_planted_world() -> None:
    """Pooled kernel cannot track both regimes; conditional can."""
    y, mu = _planted_world()
    pooled = fit_key_number_kernel(y, mu, mu_abs_edges=None, min_count=5)
    conditional = fit_key_number_kernel(y, mu, min_count=5, min_bucket_n=50)

    edges = (0.0, 3.5, 14.5, float("inf"))
    pooled_val = validate_key_number_kernel(
        y, mu, pooled, key_margins=(3, 14), mu_abs_edges=edges, sigma=5.0
    )
    cond_val = validate_key_number_kernel(
        y, mu, conditional, key_margins=(3, 14), mu_abs_edges=edges, sigma=5.0
    )

    # Conditional should be at least as close in L1 across reported cells.
    def l1(report: object) -> float:
        total = 0.0
        for row in report.rows:  # type: ignore[attr-defined]
            for key in report.key_margins:  # type: ignore[attr-defined]
                total += abs(float(row["empirical"][key]) - float(row["kernel"][key]))
        return total

    assert l1(cond_val) < l1(pooled_val)


def test_bad_edges_are_refused() -> None:
    with pytest.raises(KeyNumberError, match="strictly increasing"):
        fit_key_number_kernel([1.0], [0.0], mu_abs_edges=(0.0, 5.0, 3.0))
