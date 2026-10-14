"""Net rating formulas and defense-sign convention for SOC-2 charts.

Defense orientation (2026-10-08):
  Export writes Kalman ``def_epa`` without a flip
  (``src/ncaa_quant/webapp/export.py`` ``build_team_ratings``).
  ``RatingTrajectoryChart`` plots ``p.def_epa`` directly and labels
  "Higher is better on defense… EPA suppressed".
  Therefore ``def_good = def_epa`` (higher = tougher defense).
"""

from __future__ import annotations

from dataclasses import dataclass

from ncaa_quant.ratings.state_space import StateSpaceConfig

#: Path recorded in the run report — production filter uses this default.
MARGIN_SCALE_SOURCE = "src/ncaa_quant/ratings/state_space.py:StateSpaceConfig.margin_scale"

UNCERTAINTY_FOOTNOTE = "Uncertainty is approximate (offense and defense treated as independent)."


@dataclass(frozen=True)
class MarginScaleInfo:
    """Resolved points-per-EPA scale from the production rating config."""

    value: float
    source: str


def resolve_margin_scale() -> MarginScaleInfo:
    """Read ``margin_scale`` from :class:`StateSpaceConfig` (not a literal)."""
    value = float(StateSpaceConfig().margin_scale)
    return MarginScaleInfo(value=value, source=MARGIN_SCALE_SOURCE)


def def_good(def_epa: float) -> float:
    """Defense on the higher=tougher scale. Currently identity with export."""
    return float(def_epa)


def net_epa(*, off_epa: float, def_epa: float) -> float:
    """EPA/play vs an average team: offense + tougher-defense rating."""
    return float(off_epa) + def_good(def_epa)


def net_pts(*, off_epa: float, def_epa: float, margin_scale: float) -> float:
    """Neutral-field projected margin vs an average FBS team (points)."""
    return float(margin_scale) * net_epa(off_epa=off_epa, def_epa=def_epa)


def net_sd_pts(*, off_sd: float, def_sd: float, margin_scale: float) -> float:
    """Approximate net-rating SD in points (independence assumption)."""
    import math

    return float(margin_scale) * math.sqrt(float(off_sd) ** 2 + float(def_sd) ** 2)
