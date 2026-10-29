"""SOC-2 on-image number / refresh-kind formatting (DESIGN §4.2 + polish)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
MINUS = "\u2212"  # U+2212 minus sign

HEADLINE_MAX = 48
DEK_MAX = 60


class RefreshKindError(RuntimeError):
    """Unknown ``refresh_kind`` — polish requires an explicit humanizer table."""


def format_signed(value: float, *, decimals: int = 1) -> str:
    """Signed figure with U+2212 for negatives and ``+`` for non-negatives."""
    mag = f"{abs(value):.{decimals}f}"
    if value > 0:
        return f"+{mag}"
    if value < 0:
        return f"{MINUS}{mag}"
    zero = f"{0:.{decimals}f}"
    return f"+{zero}"


def format_signed_whole(value: float) -> str:
    """Signed whole number (no trailing ``.0``)."""
    return format_signed(round(value), decimals=0)


def format_delta(value: float, *, decimals: int = 1) -> str:
    """Signed delta without a unit suffix (unit lives on the axis title)."""
    return format_signed(value, decimals=decimals)


def format_delta_pts(value: float, *, decimals: int = 1) -> str:
    """Delta label like ``−20.0 pts`` / ``+3.5 pts`` (alt/report use)."""
    return f"{format_signed(value, decimals=decimals)} pts"


def format_axis_tick(value: float, *, prefix: str = "") -> str:
    """Axis tick: whole numbers preferred (``±10``, ``+20``, ``0``)."""
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        if rounded == 0:
            return f"{prefix}0" if prefix else "0"
        body = format_signed(float(rounded), decimals=0)
        if prefix == "±":
            return f"±{abs(rounded)}"
        return body
    body = format_signed(value, decimals=1)
    if prefix == "±":
        return f"±{abs(value):.1f}"
    return body


def rounds_to_zero(value: float, *, decimals: int = 1) -> bool:
    """True when ``value`` formats to ``0.0`` (or ``0``) at ``decimals``."""
    return float(f"{abs(value):.{decimals}f}") == 0.0


def format_team_by(team: str, margin: float, *, decimals: int = 1) -> str:
    """``{TEAM} by {x}`` from the favored side; ``Even`` when margin rounds to 0."""
    if rounds_to_zero(margin, decimals=decimals):
        return "Even"
    return f"{team} by {abs(margin):.{decimals}f}"


def format_win_lose_by(mu_team: float, *, decimals: int = 1) -> str:
    """Team-perspective pregame: ``Win by X`` / ``Lose by X`` / ``Even``."""
    if rounds_to_zero(mu_team, decimals=decimals):
        return "Even"
    mag = f"{abs(mu_team):.{decimals}f}"
    if mu_team > 0:
        return f"Win by {mag}"
    return f"Lose by {mag}"


def format_rank_tick(rank: int) -> str:
    """Power-rank axis tick like ``#1`` / ``#25``."""
    return f"#{int(rank)}"


def clip_chars(text: str, limit: int) -> str:
    """Hard-clip to ``limit`` characters (already-short copy should fit)."""
    t = text.strip()
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 1)] + "…"


def margin_team_name(
    *,
    mu_home: float,
    home_team: str,
    away_team: str,
) -> tuple[str | None, float]:
    """Team the published home-perspective margin favors, and |μ|.

    ``mu_home > 0`` → home, ``< 0`` → away, ``== 0`` → ``(None, 0)``.
    """
    if mu_home > 0:
        return home_team, abs(mu_home)
    if mu_home < 0:
        return away_team, abs(mu_home)
    return None, 0.0


def prob_team_name(
    *,
    p_win_home: float,
    home_team: str,
    away_team: str,
) -> str:
    """Team the published win probability favors (ties at 0.5 → home)."""
    return home_team if p_win_home >= 0.5 else away_team


def short_name(team_id: int, school: str, abbrev_by_id: dict[int, str]) -> str:
    """Display short name (CSV override → CFBD abbreviation → school token)."""
    from ncaa_quant.social.display_names import label_name

    return label_name(team_id, school, abbrev_by_id, prefer="short")


def home_perspective_bound_label(
    *,
    bound: float,
    home_team: str,
    away_team: str,
    decimals: int = 1,
) -> str:
    """Name the team a home-perspective margin bound favors."""
    mag = f"{abs(bound):.{decimals}f}"
    if bound >= 0:
        return f"{home_team} by {mag}"
    return f"{away_team} by {mag}"


Side = Literal["home", "away"]


def gamecard_headline(
    *,
    home_team: str,
    away_team: str,
    mu_home: float,
    p_win_home: float,
    home_short: str | None = None,
    away_short: str | None = None,
    home_id: int | None = None,
    away_id: int | None = None,
    abbrev_by_id: dict[int, str] | None = None,
) -> str:
    """≤48-char headline; never mix margin team with probability team.

    When ``home_id`` / ``away_id`` are provided, names cascade
    full+full → short+short → abbr+abbr until the line fits (never mixed).
    """
    from ncaa_quant.social.display_names import (
        TeamDisplay,
        fit_headline,
        fit_single_name_headline,
        resolve_team_display,
    )

    abbr = abbrev_by_id or {}
    if home_id is not None and away_id is not None:
        home_d = resolve_team_display(home_id, home_team, abbr)
        away_d = resolve_team_display(away_id, away_team, abbr)
    else:
        home_d = TeamDisplay(
            team_id=0,
            school=home_team,
            abbreviation=home_short or home_team,
            display_name=home_team,
            display_short=home_short or home_team,
        )
        away_d = TeamDisplay(
            team_id=0,
            school=away_team,
            abbreviation=away_short or away_team,
            display_name=away_team,
            display_short=away_short or away_team,
        )

    coin = abs(p_win_home - 0.5) <= 0.03
    if coin:
        return fit_headline(
            lambda a, h: f"Ridge can't split {a} and {h}",
            away_d,
            home_d,
        )
    m_team, m_mag = margin_team_name(
        mu_home=mu_home, home_team=home_team, away_team=away_team
    )
    p_team = prob_team_name(
        p_win_home=p_win_home, home_team=home_team, away_team=away_team
    )
    if m_team is not None and m_team == p_team:
        marg_d = home_d if m_team == home_team else away_d
        return fit_single_name_headline(
            lambda n: f"Ridge has {n} by {m_mag:.1f}",
            marg_d,
        )
    return fit_headline(
        lambda a, h: f"{a}–{h}: a toss-up for Ridge",
        away_d,
        home_d,
    )


def ridge_had_team_perspective(
    *,
    mu_home: float,
    team_id: int,
    home_id: int | None,
    away_id: int | None,
) -> float | None:
    """Pregame margin from ``team_id``'s perspective (home μ flipped for away)."""
    if team_id == home_id:
        return mu_home
    if team_id == away_id:
        return -mu_home
    return None


def published_day_et(iso_utc: str | None) -> str:
    """``{Day Mon D}`` in ET, e.g. ``Tue Oct 6``."""
    if not iso_utc:
        return "—"
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(_ET)
    return f"{dt.strftime('%a')} {dt.strftime('%b')} {dt.day}"


def humanize_refresh_kind(refresh_kind: str | None, published_at: str | None) -> str:
    """Map ``refresh_kind`` to a short human label; unknown values fail the run.

    Explicit table:
    - ``tuesday_primary`` → ``Tuesday forecast``
    - ``postgame`` / ``postgame_ratings`` → ``Sunday ratings``
    - Thu–Sat (and other) ``daily_refresh`` / ``t_minus_*`` → ``{Day} update``
    """
    kind = (refresh_kind or "").strip()
    if kind == "tuesday_primary":
        return "Tuesday forecast"
    if kind in ("postgame", "postgame_ratings"):
        return "Sunday ratings"
    if kind in ("daily_refresh", "t_minus_6h", "t_minus_1h"):
        if not published_at:
            msg = f"refresh_kind={kind!r} requires published_at for day stamp"
            raise RefreshKindError(msg)
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00")).astimezone(_ET)
        return f"{dt.strftime('%a')} update"
    msg = f"Unknown refresh_kind {kind!r}; cannot humanize for on-image copy"
    raise RefreshKindError(msg)


def margin_sign_vs_prob_disagree(*, mu_margin: float, p_win_home: float) -> bool:
    """True when sign(mu) and (p_win_home >= 0.5) lean opposite ways.

    ``mu == 0`` never counts as a disagreement (no margin lean).
    """
    if mu_margin == 0.0:
        return False
    home_by_mu = mu_margin > 0.0
    home_by_p = p_win_home >= 0.5
    return home_by_mu != home_by_p
