"""Ridge social chart renderers (top25 · map · slate · surprise)."""

from ncaa_quant.social.charts.ridge_map import (
    ridge_week_labels,
    render_ridge_map,
)
from ncaa_quant.social.charts.slate_map import (
    PHI_CHECK_TOLERANCE,
    SlateCoherenceError,
    chart_p_max,
    favored_win_prob,
    find_sign_disagreements,
    phi_check,
    render_slate_map,
    tier_boundary_zs,
    tier_enter_thresholds,
)
from ncaa_quant.social.charts.surprise_strip import (
    favored_perspective_z,
    interval_hit_headline,
    render_surprise_strip,
)
from ncaa_quant.social.charts.top25 import (
    extract_top25,
    format_rank_change,
    render_top25,
)

__all__ = [
    "PHI_CHECK_TOLERANCE",
    "SlateCoherenceError",
    "chart_p_max",
    "extract_top25",
    "favored_perspective_z",
    "favored_win_prob",
    "find_sign_disagreements",
    "format_rank_change",
    "interval_hit_headline",
    "phi_check",
    "render_ridge_map",
    "render_slate_map",
    "render_surprise_strip",
    "render_top25",
    "ridge_week_labels",
    "tier_boundary_zs",
    "tier_enter_thresholds",
]
