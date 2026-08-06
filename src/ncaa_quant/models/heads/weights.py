"""Sample-weight helpers for mapping-layer heads.

Time-decay across seasons: a game from ``age`` seasons before the newest
training season receives weight ``0.5 ** (age / half_life)``.

Default half-life
-----------------
``DEFAULT_SEASON_HALF_LIFE = 2.0`` seasons. Two seasons back weigh 0.5; four
seasons back weigh 0.25. Chosen as a small, explicit default so recent seasons
dominate without zeroing out the 2014–era history the rating engine still uses
for continuity. Override per-head via ``season_half_life`` or pass an explicit
``sample_weight`` series to ``fit``.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

DEFAULT_SEASON_HALF_LIFE: float = 2.0


def time_decay_weights(
    seasons: pd.Series | np.ndarray | Sequence[int],
    *,
    half_life: float = DEFAULT_SEASON_HALF_LIFE,
    reference_season: int | None = None,
) -> np.ndarray:
    """Exponential time-decay weights keyed by season.

    Parameters
    ----------
    seasons:
        Season for each training row.
    half_life:
        Seasons until weight halves. Must be > 0.
    reference_season:
        Season treated as age 0. Defaults to ``max(seasons)``.
    """
    if half_life <= 0:
        msg = f"half_life must be > 0, got {half_life}"
        raise ValueError(msg)

    arr = np.asarray(seasons, dtype=float)
    if arr.size == 0:
        return np.asarray([], dtype=float)
    ref = float(reference_season) if reference_season is not None else float(np.nanmax(arr))
    age = np.maximum(ref - arr, 0.0)
    return np.power(0.5, age / float(half_life))


def resolve_sample_weight(
    *,
    n: int,
    seasons: pd.Series | np.ndarray | None,
    sample_weight: pd.Series | np.ndarray | None,
    season_half_life: float = DEFAULT_SEASON_HALF_LIFE,
) -> np.ndarray:
    """Return explicit weights, else time-decay from ``seasons``, else ones."""
    if sample_weight is not None:
        w = np.asarray(sample_weight, dtype=float)
        if w.shape != (n,):
            msg = f"sample_weight length {w.shape} != n={n}"
            raise ValueError(msg)
        return w
    if seasons is not None:
        s = np.asarray(seasons)
        if s.shape != (n,):
            msg = f"seasons length {s.shape} != n={n}"
            raise ValueError(msg)
        return time_decay_weights(s, half_life=season_half_life)
    return np.ones(n, dtype=float)
