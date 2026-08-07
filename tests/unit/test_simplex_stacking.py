"""Simplex-constrained Level-1 stacking (DESIGN §5, audit A-10).

The defect: the stack ran plain NNLS over the non-negative cone and then divided
the weights by their sum. That is not the same problem. NNLS minimizes over the
cone; rescaling its answer slides along a ray and generally lands somewhere other
than the constrained minimizer. These tests price the gap rather than asserting it
in the abstract.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.models.ensemble import (
    OOF_FLAG_COLUMN,
    EnsembleError,
    fit_nnls_stack,
    renormalized_nnls_weights,
    solve_simplex_least_squares,
    stack_weights_valid,
)


def _sse(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    resid = x @ w - y
    return float(resid @ resid)


# ---------------------------------------------------------------------------
# The demonstration the audit asked for
# ---------------------------------------------------------------------------


def test_renormalized_nnls_misses_the_constrained_optimum() -> None:
    """A case with a hand-computable answer, so the gap is not a matter of opinion.

    Two orthogonal members and `y = [0.2, 0.6]`. The unconstrained non-negative
    solution is `(0.2, 0.6)`, which sums to 0.8 and so lies *off* the simplex.

    Renormalizing gives `(0.25, 0.75)`. The true constrained optimum, from the
    Lagrange condition `w1 − 0.2 = w2 − 0.6` with `w1 + w2 = 1`, is `(0.3, 0.7)`.
    Those are different points, and the renormalized one is strictly worse.
    """
    x = np.array([[1.0, 0.0], [0.0, 1.0]])
    y = np.array([0.2, 0.6])

    simplex = solve_simplex_least_squares(x, y)
    renormalized = renormalized_nnls_weights(x, y)

    assert simplex == pytest.approx([0.3, 0.7], abs=1e-6)
    assert renormalized == pytest.approx([0.25, 0.75], abs=1e-9)

    # 0.02 versus 0.025 — the rejected approach gives away 25% more squared error.
    assert _sse(x, y, simplex) == pytest.approx(0.02, abs=1e-9)
    assert _sse(x, y, renormalized) == pytest.approx(0.025, abs=1e-9)
    assert _sse(x, y, simplex) < _sse(x, y, renormalized)


def test_simplex_solution_is_never_worse_than_renormalizing() -> None:
    """Over many random member matrices, the constrained solve must dominate."""
    rng = np.random.default_rng(510)
    strictly_better = 0
    for _ in range(60):
        n, k = 80, 4
        x = rng.normal(size=(n, k))
        y = x @ rng.dirichlet(np.ones(k)) + rng.normal(scale=0.4, size=n)

        simplex = solve_simplex_least_squares(x, y)
        renorm = renormalized_nnls_weights(x, y)

        assert _sse(x, y, simplex) <= _sse(x, y, renorm) + 1e-8
        if _sse(x, y, simplex) < _sse(x, y, renorm) - 1e-8:
            strictly_better += 1

    # Not a tie in general: the two approaches genuinely differ on real geometry.
    assert strictly_better > 0


def test_the_two_agree_when_nnls_already_lands_on_the_simplex() -> None:
    """No gap to close when the unconstrained optimum already sums to 1."""
    x = np.array([[1.0, 0.0], [0.0, 1.0]])
    y = np.array([0.4, 0.6])

    assert solve_simplex_least_squares(x, y) == pytest.approx([0.4, 0.6], abs=1e-6)
    assert renormalized_nnls_weights(x, y) == pytest.approx([0.4, 0.6], abs=1e-9)


# ---------------------------------------------------------------------------
# Constraint properties
# ---------------------------------------------------------------------------


def test_weights_are_on_the_simplex_by_construction() -> None:
    rng = np.random.default_rng(511)
    for _ in range(25):
        k = int(rng.integers(2, 7))
        x = rng.normal(size=(50, k))
        y = rng.normal(size=50)

        w = solve_simplex_least_squares(x, y)

        assert np.all(w >= -1e-12)
        assert float(w.sum()) == pytest.approx(1.0, abs=1e-9)


def test_a_single_member_takes_all_the_weight() -> None:
    x = np.array([[1.0], [2.0], [3.0]])
    assert solve_simplex_least_squares(x, np.array([1.0, 2.0, 3.0])) == pytest.approx([1.0])


def test_a_useless_member_is_driven_to_a_corner() -> None:
    """Soft model selection: pure noise should get essentially no weight."""
    rng = np.random.default_rng(512)
    signal = rng.normal(size=300)
    noise = rng.normal(size=300)
    y = signal.copy()

    w = solve_simplex_least_squares(np.column_stack([signal, noise]), y)

    assert w[0] > 0.97
    assert w[1] < 0.03


def test_no_intercept_is_introduced() -> None:
    """A convex combination cannot shift the level away from every member.

    With both members biased low, the stack must stay biased low: correcting the
    level is Level-2's job, not something the weights may invent.
    """
    rng = np.random.default_rng(513)
    truth = rng.normal(loc=20.0, scale=6.0, size=200)
    m1 = truth - 10.0
    m2 = truth - 12.0

    w = solve_simplex_least_squares(np.column_stack([m1, m2]), truth)
    fitted = np.column_stack([m1, m2]) @ w

    assert fitted.mean() < truth.mean() - 9.0


def test_solver_is_deterministic() -> None:
    rng = np.random.default_rng(514)
    x = rng.normal(size=(60, 5))
    y = rng.normal(size=60)

    first = solve_simplex_least_squares(x, y)
    for _ in range(3):
        assert solve_simplex_least_squares(x, y) == pytest.approx(first, abs=1e-12)


# ---------------------------------------------------------------------------
# Wiring into the fitted stack
# ---------------------------------------------------------------------------


def _oof_frame(x: np.ndarray, y: np.ndarray) -> pd.DataFrame:
    frame = pd.DataFrame({f"mu_{i}": x[:, i] for i in range(x.shape[1])})
    frame["realized_margin"] = y
    frame[OOF_FLAG_COLUMN] = True
    return frame


def test_fitted_stack_uses_the_constrained_solve() -> None:
    x = np.array([[1.0, 0.0], [0.0, 1.0]])
    y = np.array([0.2, 0.6])

    stack = fit_nnls_stack(
        _oof_frame(x, y),
        target="margin",
        member_columns=("mu_0", "mu_1"),
    )

    assert stack.weights == pytest.approx((0.3, 0.7), abs=1e-6)
    assert stack_weights_valid(stack)
    assert stack.fallback is None


def test_degenerate_members_still_raise_rather_than_being_hidden() -> None:
    """The simplex constraint must not paper over 'no member carries signal'.

    Forcing the weights to sum to 1 would always yield a plausible-looking answer,
    so degeneracy is still judged on the unconstrained cone solve.
    """
    frame = _oof_frame(np.zeros((10, 2)), np.ones(10))

    with pytest.raises(EnsembleError, match="all-zero weights"):
        fit_nnls_stack(frame, target="margin", member_columns=("mu_0", "mu_1"))

    explicit = fit_nnls_stack(
        frame,
        target="margin",
        member_columns=("mu_0", "mu_1"),
        allow_equal_weight_fallback=True,
    )
    assert explicit.fallback == "equal_weight"
    assert explicit.weights == pytest.approx((0.5, 0.5))
