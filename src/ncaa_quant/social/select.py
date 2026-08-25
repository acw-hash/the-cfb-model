"""Public Best Bet selection for the social layer (S2).

Applies a stricter public edge bar on top of already-accepted §12 candidates.
Reusable by S3 calibration (override thresholds via ``SocialConfig`` copies).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ncaa_quant.config import BettingConfig, SocialConfig

# Public A-tier label threshold (playbook §1). Not a SocialConfig field —
# S3 calibrates the floor edges, not the A/B display cut.
A_TIER_EDGE = 0.060


@dataclass
class BestBet:
    """One publicly posted Best Bet (rank is 1-based display order)."""

    rank: int
    rating: str  # "A" | "B"
    game: dict[str, Any]
    cand: dict[str, Any]
    unit_fraction: float

    @property
    def units(self) -> float:
        """Displayed stake units: ``stake_fraction / unit_fraction``.

        ``unit_fraction`` is bankroll fraction per displayed unit (default 0.5%).
        Missing/zero stake falls back to 1.0u for copy safety.
        """
        sf = float(self.cand.get("stake_fraction") or 0.0)
        uf = float(self.unit_fraction)
        if sf > 0.0 and uf > 0.0:
            return round(sf / uf, 1)
        return 1.0

    @property
    def edge_pct(self) -> str:
        return f"{float(self.cand['edge']) * 100:.1f}"


def select_best_bets(
    games_by_id: Mapping[str, Mapping[str, Any]],
    accepted: Sequence[Mapping[str, Any]],
    now: datetime,
    *,
    social: SocialConfig,
    betting: BettingConfig,
) -> tuple[list[BestBet], list[tuple[dict[str, Any], str]]]:
    """Return ``(best_bets, publicly_skipped_with_reason)``.

    Public bar (on top of already-accepted §12 candidates):

    - edge ≥ ``social.public_min_edge_sides`` / ``public_min_edge_totals``
    - prediction row present, ``sigma_margin_credible`` is not False
    - not stale at post time (``is_stale``)
    - kickoff strictly after ``now``
    - sorted by edge descending, capped at ``betting.max_bets_per_week``

    Count is whatever qualifies — never padded. ``N=0`` yields an empty list
    (caller renders the No-Bet post).

    Parameters
    ----------
    now:
        Post-time clock (UTC-aware). Kickoffs at or before ``now`` are skipped.
    social:
        Thresholds and ``unit_fraction``. S3 overrides by passing a copy with
        different ``public_min_edge_*`` values.
    betting:
        Supplies ``max_bets_per_week`` (hard public card cap).
    """
    qualified: list[dict[str, Any]] = []
    skipped: list[tuple[dict[str, Any], str]] = []

    for raw in accepted:
        cand = dict(raw)
        gid = str(cand.get("game_id"))
        game = games_by_id.get(gid)
        if game is None:
            skipped.append((cand, "game not in published slate"))
            continue

        # Public suppressions mirror the site's honesty rules.
        if game.get("is_stale"):
            skipped.append((cand, "stale inputs at post time"))
            continue
        if game.get("sigma_margin_credible") is False:
            skipped.append((cand, "sigma refused (ADR 0014)"))
            continue
        kick = game.get("kickoff_utc")
        if kick:
            try:
                kdt = datetime.fromisoformat(str(kick).replace("Z", "+00:00"))
                if kdt <= now:
                    skipped.append((cand, "already kicked off"))
                    continue
            except ValueError:
                pass

        bar = (
            float(social.public_min_edge_totals)
            if cand.get("market") == "total"
            else float(social.public_min_edge_sides)
        )
        if float(cand.get("edge", 0.0)) < bar:
            skipped.append((cand, "below public edge bar"))
            continue
        qualified.append(cand)

    qualified.sort(key=lambda c: float(c.get("edge", 0.0)), reverse=True)
    qualified = qualified[: int(betting.max_bets_per_week)]

    unit_fraction = float(social.unit_fraction)
    bets = [
        BestBet(
            rank=i + 1,
            rating="A" if float(c["edge"]) >= A_TIER_EDGE else "B",
            game=dict(games_by_id[str(c["game_id"])]),
            cand=c,
            unit_fraction=unit_fraction,
        )
        for i, c in enumerate(qualified)
    ]
    return bets, skipped
