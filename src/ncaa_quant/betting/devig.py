"""De-vig methods for converting book implied probabilities to fair probs.

Default method is **proportional** (normalize by the overround): DESIGN §2.7
defines CLV in proportionally de-vigged probability space, and two-way markets
(ML / ATS / OU) are well-served by it. Multiplicative (power) and Shin are
implemented for comparison and are selectable via the ``method`` argument.

Wiring ``method`` into ``configs/betting.yaml`` / ``BettingConfig`` would require
editing ``config.py`` (out of Task 20 scope); callers pass the method explicitly.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

import numpy as np
from scipy import optimize  # type: ignore[import-untyped]

from ncaa_quant.distribution.simulate import SimulateError, american_to_implied

DevigMethod = Literal["proportional", "multiplicative", "shin"]

DEFAULT_DEVIG_METHOD: DevigMethod = "proportional"


class DevigMethodName(StrEnum):
    """Selectable de-vig algorithms."""

    PROPORTIONAL = "proportional"
    MULTIPLICATIVE = "multiplicative"
    SHIN = "shin"


class DevigError(ValueError):
    """Invalid odds or de-vig inputs."""


def american_to_raw_implied(american: float) -> float:
    """American odds → raw (vigged) implied probability.

    Units: American odds integer/float → probability in (0, 1].
    """
    try:
        return float(american_to_implied(american))
    except (SimulateError, ValueError, ZeroDivisionError) as exc:
        raise DevigError(str(exc)) from exc


def decimal_to_raw_implied(decimal_odds: float) -> float:
    """Decimal odds → raw implied probability."""
    d = float(decimal_odds)
    if d <= 1.0:
        raise DevigError(f"decimal odds must be > 1, got {d}")
    return 1.0 / d


def american_to_decimal(american: float) -> float:
    """Convert American odds to decimal odds."""
    a = float(american)
    if a == 0:
        raise DevigError("american odds cannot be 0")
    if a > 0:
        return 1.0 + a / 100.0
    return 1.0 + 100.0 / (-a)


def _validate_raw_probs(raw: Sequence[float] | np.ndarray) -> np.ndarray:
    q = np.asarray(raw, dtype=float).ravel()
    if q.size < 2:
        raise DevigError("need at least two outcomes to de-vig")
    if not np.all(np.isfinite(q)) or np.any(q <= 0.0):
        raise DevigError("raw implied probabilities must be finite and positive")
    return q


def proportional_devig(raw_implied: Sequence[float] | np.ndarray) -> np.ndarray:
    """Proportional (overround-normalize): ``p_i = q_i / sum(q)``.

    This is the DESIGN §2.7 / §12 default for CLV and market baselines.
    """
    q = _validate_raw_probs(raw_implied)
    s = float(q.sum())
    if s <= 0.0:
        raise DevigError("sum of raw implied probabilities must be positive")
    out: np.ndarray = q / s
    return out


def multiplicative_devig(raw_implied: Sequence[float] | np.ndarray) -> np.ndarray:
    """Power / multiplicative method: find ``c`` s.t. ``sum(q_i^c) = 1``.

    Distinct from proportional normalization; removes margin by compressing
    the implied-probability vector toward uniformity via a single exponent.
    """
    q = _validate_raw_probs(raw_implied)
    overround = float(q.sum())
    if abs(overround - 1.0) < 1e-12:
        return q.copy()

    def objective(c: float) -> float:
        return float(np.sum(np.power(q, c)) - 1.0)

    # Overround > 1 → c > 1; underround (rare) → c < 1.
    lo, hi = (1.0, 10.0) if overround > 1.0 else (0.05, 1.0)
    if objective(lo) * objective(hi) > 0:
        # Fall back to proportional if bracket fails.
        return proportional_devig(q)

    c_star = float(optimize.brentq(objective, lo, hi))
    p = np.power(q, c_star)
    out: np.ndarray = np.asarray(p / p.sum(), dtype=float)
    return out


def shin_devig(raw_implied: Sequence[float] | np.ndarray) -> np.ndarray:
    """Shin (1993) de-vig: bookmaker knows the outcome with probability ``z``.

    Solves for ``z ∈ [0, 1)`` such that the Shin-implied probabilities sum to 1.
    Uses the standard inversion with ``q_i² / Σq`` normalization (as in common
    sports-betting implementations); distinct from proportional on asymmetric
    markets.
    """
    q = _validate_raw_probs(raw_implied)
    s = float(np.sum(q))
    if s <= 0.0:
        raise DevigError("Shin de-vig requires positive sum of implied probs")

    def shin_probs(z: float) -> np.ndarray:
        # p_i = (sqrt(z^2 + 4(1-z) q_i^2 / Σq) - z) / (2(1-z))
        if z >= 1.0 - 1e-15:
            out = np.zeros_like(q)
            out[int(np.argmax(q))] = 1.0
            return out
        disc = z**2 + 4.0 * (1.0 - z) * (q**2) / s
        result: np.ndarray = (np.sqrt(disc) - z) / (2.0 * (1.0 - z))
        return result

    def objective(z: float) -> float:
        return float(np.sum(shin_probs(z)) - 1.0)

    if abs(objective(0.0)) < 1e-12:
        p0 = shin_probs(0.0)
        out0: np.ndarray = np.asarray(p0 / p0.sum(), dtype=float)
        return out0

    try:
        z_star = float(optimize.brentq(objective, 0.0, 0.9999))
    except ValueError:
        return proportional_devig(q)

    p = shin_probs(z_star)
    total = float(p.sum())
    if total <= 0.0:
        raise DevigError("Shin de-vig produced non-positive mass")
    out: np.ndarray = np.asarray(p / total, dtype=float)
    return out


def devig(
    raw_implied: Sequence[float] | np.ndarray,
    *,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> np.ndarray:
    """De-vig a vector of raw implied probabilities.

    Parameters
    ----------
    raw_implied:
        Vigged implied probabilities (typically from American/decimal odds).
    method:
        ``proportional`` (default), ``multiplicative``, or ``shin``.
    """
    if method == "proportional":
        return proportional_devig(raw_implied)
    if method == "multiplicative":
        return multiplicative_devig(raw_implied)
    if method == "shin":
        return shin_devig(raw_implied)
    raise DevigError(f"unknown de-vig method: {method!r}")


def devig_two_way(
    american_a: float,
    american_b: float,
    *,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> tuple[float, float]:
    """De-vig a two-way American-odds market; returns fair ``(p_a, p_b)``."""
    q_a = american_to_raw_implied(american_a)
    q_b = american_to_raw_implied(american_b)
    p = devig([q_a, q_b], method=method)
    return float(p[0]), float(p[1])


def fair_prob_on_side(
    american_side: float,
    american_other: float,
    *,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> float:
    """Fair probability of ``side`` given the two-way American market."""
    p_side, _p_other = devig_two_way(american_side, american_other, method=method)
    return p_side
