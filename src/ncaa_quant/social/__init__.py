"""Private local-only social layer (S-series). Never pushed to R2."""

from ncaa_quant.social.candidates import (
    CandidateRecord,
    export_social_candidates,
    orient_bet_lines,
    records_from_filter_result,
)
from ncaa_quant.social.render import (
    POST_CHAR_LIMIT,
    REASON_PLAIN,
    dominant_rejection_reason,
    extract_post_bodies,
    render_no_bet_post,
    render_replies,
    render_thread,
)
from ncaa_quant.social.select import A_TIER_EDGE, BestBet, select_best_bets

__all__ = [
    "A_TIER_EDGE",
    "BestBet",
    "CandidateRecord",
    "POST_CHAR_LIMIT",
    "REASON_PLAIN",
    "dominant_rejection_reason",
    "export_social_candidates",
    "extract_post_bodies",
    "orient_bet_lines",
    "records_from_filter_result",
    "render_no_bet_post",
    "render_replies",
    "render_thread",
    "select_best_bets",
]
