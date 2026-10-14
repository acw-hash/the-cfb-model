"""Favored-side perspective transforms for slate ranges and game cards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ncaa_quant.social.specs.format_text import margin_sign_vs_prob_disagree


@dataclass(frozen=True)
class FavoredPerspective:
    """Margin interval and win probability from the favored team's view.

    Favored side is chosen by published win probability
    (``p_win_home >= 0.5`` → home). Margin fields are flipped to that side's
    perspective, so a probability/margin disagreement yields ``mu < 0``.
    """

    favored_side: Literal["home", "away"]
    mu: float
    lo: float
    hi: float
    p_fav: float
    fav_team: str
    dog_team: str
    fav_team_id: int
    dog_team_id: int
    mu_home: float
    p_win_home: float
    sign_prob_mismatch: bool


def favored_side_by_probability(p_win_home: float) -> Literal["home", "away"]:
    """Published win-probability side (ties at 0.5 go home)."""
    return "home" if p_win_home >= 0.5 else "away"


def favored_perspective(game: dict[str, Any]) -> FavoredPerspective:
    """Flip home-perspective fields to the probability-favored side."""
    mu = float(game["mu_margin"])
    lo = float(game["margin_interval_lo"])
    hi = float(game["margin_interval_hi"])
    p_home = float(game["p_win_home"])
    side = favored_side_by_probability(p_home)
    mismatch = margin_sign_vs_prob_disagree(mu_margin=mu, p_win_home=p_home)

    home = str(game["home_team"])
    away = str(game["away_team"])
    home_id = int(game["home_team_id"])
    away_id = int(game["away_team_id"])

    if side == "home":
        return FavoredPerspective(
            favored_side=side,
            mu=mu,
            lo=lo,
            hi=hi,
            p_fav=p_home,
            fav_team=home,
            dog_team=away,
            fav_team_id=home_id,
            dog_team_id=away_id,
            mu_home=mu,
            p_win_home=p_home,
            sign_prob_mismatch=mismatch,
        )
    return FavoredPerspective(
        favored_side=side,
        mu=-mu,
        lo=-hi,
        hi=-lo,
        p_fav=1.0 - p_home,
        fav_team=away,
        dog_team=home,
        fav_team_id=away_id,
        dog_team_id=home_id,
        mu_home=mu,
        p_win_home=p_home,
        sign_prob_mismatch=mismatch,
    )


def mismatch_report_row(game: dict[str, Any], fav: FavoredPerspective) -> dict[str, Any]:
    """One ``run_report.json`` row for a margin-sign vs win-probability mismatch."""
    return {
        "game_id": str(game.get("game_id")),
        "home_team": game.get("home_team"),
        "away_team": game.get("away_team"),
        "mu": fav.mu_home,
        "sigma": game.get("sigma_margin"),
        "p_win_home": fav.p_win_home,
        "interval": [game.get("margin_interval_lo"), game.get("margin_interval_hi")],
        "favored_by_probability": fav.favored_side,
    }
