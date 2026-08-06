"""Toy walk-forward stubs relocated from ``evaluation.walkforward`` (Task 22B).

These exist only for Task 16 placeholder tests. Production code must not import
them — use :mod:`ncaa_quant.evaluation.production_stack` instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.evaluation.walkforward import WalkForwardError


@dataclass
class LeagueAverageMarginPredictor:
    """Always predicts the league-average home margin from fit history.

    Fit uses revealed ``realized_margin`` (home − away). Before any fit the
    prior is ``default_margin`` (home-field-ish ~2.5 points). ``pred_total``
    is the fitted mean total when totals are available, else null.
    """

    default_margin: float = 2.5
    default_total: float = 55.0
    model_version: str = "placeholder-league-avg-v0"
    _mean_margin: float = field(default=2.5, init=False, repr=False)
    _mean_total: float = field(default=55.0, init=False, repr=False)
    _fitted: bool = field(default=False, init=False, repr=False)

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def fit(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame,
        *,
        sample_weight: pd.Series | None = None,
    ) -> None:
        del features, sample_weight  # placeholder ignores features / weights
        if labels.empty or "realized_margin" not in labels.columns:
            self._mean_margin = self.default_margin
            self._mean_total = self.default_total
            self._fitted = True
            return
        margins = labels["realized_margin"].astype(float).dropna()
        self._mean_margin = float(margins.mean()) if not margins.empty else self.default_margin
        if "realized_total" in labels.columns:
            totals = labels["realized_total"].astype(float).dropna()
            self._mean_total = float(totals.mean()) if not totals.empty else self.default_total
        else:
            self._mean_total = self.default_total
        self._fitted = True

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        if features.empty:
            return pd.DataFrame(columns=["game_id", "pred_margin", "pred_total"])
        if "game_id" not in features.columns:
            msg = "features must include game_id"
            raise WalkForwardError(msg)
        margin = self._mean_margin if self._fitted else self.default_margin
        total = self._mean_total if self._fitted else self.default_total
        return pd.DataFrame(
            {
                "game_id": features["game_id"].to_numpy(),
                "pred_margin": np.full(len(features), margin, dtype=float),
                "pred_total": np.full(len(features), total, dtype=float),
            }
        )


@dataclass
class RunningMarginRatingEngine:
    """Minimal rating state for harness tests / placeholder E2E runs.

    Tracks per-team cumulative point differential. ``initialize_season``
    applies a soft regression toward 0 (prior). Real Stage-1 wiring injects
    a different RatingEngine that wraps ``state_space`` / ``priors``.
    """

    regression: float = 0.30
    ratings: dict[int, float] = field(default_factory=dict)

    def initialize_season(self, season: int, as_of: datetime) -> None:
        del season, as_of
        for tid in list(self.ratings):
            self.ratings[tid] = (1.0 - self.regression) * self.ratings[tid]

    def update_after_games(self, games: pd.DataFrame) -> None:
        for row in games.itertuples(index=False):
            hp = getattr(row, "home_points", None)
            ap = getattr(row, "away_points", None)
            if hp is None or ap is None or (isinstance(hp, float) and math.isnan(hp)):
                continue
            hid = int(row.home_team_id)
            aid = int(row.away_team_id)
            margin = float(hp) - float(ap)
            self.ratings[hid] = self.ratings.get(hid, 0.0) + margin
            self.ratings[aid] = self.ratings.get(aid, 0.0) - margin

    def state_snapshot(self) -> dict[str, Any]:
        return {str(k): float(v) for k, v in sorted(self.ratings.items())}
