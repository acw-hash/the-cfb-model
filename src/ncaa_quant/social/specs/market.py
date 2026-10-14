"""Market-favorite perspective for Ridge-vs-market social charts.

Market source (vig-free projection the site compares Ridge against):
``workers/odds-refresh/src/consensus.ts`` → ``homeSpreadToMarketMargin``
writes ``odds_snapshot.games[].market_home_margin``
(``market_home_margin = −median_home_spread``, same home-minus-away scale
as published ``mu_margin``). ADR-ODDS-SNAPSHOT §5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


class MarketSnapshotError(RuntimeError):
    """Missing or ambiguous market snapshot for the slate week."""


@dataclass(frozen=True)
class MarketFavoritePerspective:
    """Ridge margin/interval/win% in the market-favorite team's view.

    ``mkt_home`` is ``market_home_margin``: points by which the market
    expects home to win (positive → home favorite).
    """

    favored_side: Literal["home", "away"]
    mkt_home: float
    x_mkt: float
    x_ridge: float
    lo: float
    hi: float
    p_fav: float
    fav_team: str
    dog_team: str
    fav_team_id: int
    dog_team_id: int
    gap: float
    leans_team: str | None
    leans_team_id: int | None
    leans_mag: float
    leans_label: str


def load_market_by_game(
    odds_snapshot: dict[str, Any] | None,
) -> dict[str, float]:
    """Map ``game_id`` → ``market_home_margin`` (skip nulls)."""
    if not odds_snapshot:
        return {}
    out: dict[str, float] = {}
    for g in odds_snapshot.get("games") or []:
        gid = g.get("game_id")
        mkt = g.get("market_home_margin")
        if gid is None or mkt is None:
            continue
        try:
            out[str(gid)] = float(mkt)
        except (TypeError, ValueError):
            continue
    return out


def require_week_market_snapshot(
    odds_snapshot: dict[str, Any] | None,
    *,
    week: int,
) -> dict[str, Any]:
    """Fail closed when the snapshot is missing or for the wrong week."""
    if not odds_snapshot:
        msg = (
            f"No odds_snapshot.json for week {week} — "
            "cannot build Ridge-vs-market slate/game cards"
        )
        raise MarketSnapshotError(msg)
    snap_week = odds_snapshot.get("week")
    if snap_week is not None and int(snap_week) != int(week):
        msg = (
            f"odds_snapshot week={snap_week} != slate week={week}"
        )
        raise MarketSnapshotError(msg)
    if odds_snapshot.get("market_home_margin") is not None:
        msg = "odds_snapshot root has market_home_margin; expected per-game field only"
        raise MarketSnapshotError(msg)
    games = odds_snapshot.get("games")
    if not isinstance(games, list) or not games:
        msg = "odds_snapshot has no games[]"
        raise MarketSnapshotError(msg)
    sample = next(
        (g for g in games if g.get("market_home_margin") is not None),
        None,
    )
    if sample is None:
        msg = "odds_snapshot games lack market_home_margin"
        raise MarketSnapshotError(msg)
    return odds_snapshot


def market_favorite_side(mkt_home: float) -> Literal["home", "away"]:
    """Market favorite by sign of ``mkt_home`` (ties → home)."""
    return "home" if mkt_home >= 0.0 else "away"


def to_market_favorite_perspective(
    *,
    mkt_home: float,
    mu_home: float,
    lo_home: float,
    hi_home: float,
    p_win_home: float,
    home_team: str,
    away_team: str,
    home_team_id: int,
    away_team_id: int,
    gap_dash_threshold: float = 1.0,
) -> MarketFavoritePerspective:
    """Convert home-perspective Ridge fields to market-favorite perspective.

    ``x_mkt = |mkt_home|``. ``x_ridge`` / interval / ``p_fav`` are Ridge's
    numbers for the market favorite. Gap = ``x_ridge − x_mkt`` in that
    team's perspective: positive means Ridge likes the market favorite
    more than the market does.
    """
    side = market_favorite_side(mkt_home)
    x_mkt = abs(float(mkt_home))
    if side == "home":
        x_ridge = float(mu_home)
        lo = float(lo_home)
        hi = float(hi_home)
        p_fav = float(p_win_home)
        fav_team, dog_team = home_team, away_team
        fav_id, dog_id = home_team_id, away_team_id
    else:
        x_ridge = -float(mu_home)
        lo = -float(hi_home)
        hi = -float(lo_home)
        p_fav = 1.0 - float(p_win_home)
        fav_team, dog_team = away_team, home_team
        fav_id, dog_id = away_team_id, home_team_id

    gap = x_ridge - x_mkt
    # Ridge likes the market favorite more → gap > 0 → leans fav.
    # Ridge likes the dog more → gap < 0 → leans dog by |gap|.
    if abs(gap) < gap_dash_threshold:
        leans_team = None
        leans_id = None
        leans_mag = 0.0
        leans_label = "—"
    elif gap > 0:
        leans_team = fav_team
        leans_id = fav_id
        leans_mag = gap
        leans_label = f"{fav_team} +{gap:.1f}"
    else:
        leans_team = dog_team
        leans_id = dog_id
        leans_mag = abs(gap)
        leans_label = f"{dog_team} +{abs(gap):.1f}"

    return MarketFavoritePerspective(
        favored_side=side,
        mkt_home=float(mkt_home),
        x_mkt=x_mkt,
        x_ridge=x_ridge,
        lo=lo,
        hi=hi,
        p_fav=p_fav,
        fav_team=fav_team,
        dog_team=dog_team,
        fav_team_id=fav_id,
        dog_team_id=dog_id,
        gap=gap,
        leans_team=leans_team,
        leans_team_id=leans_id,
        leans_mag=leans_mag,
        leans_label=leans_label,
    )


def prove_market_sign_convention(
    *,
    spread_home_points: float,
    market_home_margin: float,
) -> None:
    """Assert ADR sign: ``market_home_margin == −spread_home_points``."""
    expected = -float(spread_home_points)
    if abs(float(market_home_margin) - expected) > 1e-9:
        msg = (
            f"market_home_margin={market_home_margin} != "
            f"-spread_home_points={expected}"
        )
        raise MarketSnapshotError(msg)


def market_as_of_et(snapshot_at: str) -> str:
    """``{Day h:mm AM/PM} ET`` for the market footer stamp."""
    if not snapshot_at:
        return ""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    try:
        dt = datetime.fromisoformat(snapshot_at.replace("Z", "+00:00")).astimezone(
            ZoneInfo("America/New_York")
        )
    except ValueError:
        return snapshot_at
    hour = dt.strftime("%I").lstrip("0") or "0"
    return f"{dt.strftime('%a')} {hour}:{dt.strftime('%M %p')} ET"
