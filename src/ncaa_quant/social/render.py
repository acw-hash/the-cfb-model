"""Thread + reply-bank rendering for RidgeCFB X copy (S2).

No network calls. Human reviews and posts. Character counts use Python
``len`` (Unicode code points), matching the draft script.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ncaa_quant.social.select import BestBet

POST_CHAR_LIMIT = 280

FIXTURE_BANNER = (
    "WARNING: FIXTURE ARTIFACT — DO NOT POST THESE NUMBERS. "
    "Fixture publish for testing / dry-run only.\n"
)

REASON_PLAIN: dict[str, str] = {
    "edge_too_small": "the market has this priced about right",
    "non_positive_ev": "no positive EV at current prices",
    "model_market_disagree": (
        "model and market are so far apart it usually means the market "
        "knows something we don't — auto no-play"
    ),
    "qb_status_unknown": "QB situation is unclear and we don't bet through that",
    "stale_inputs": "our odds feed was stale at decision time — no bet on stale data",
    "max_bets_per_week": "we hit our weekly bet cap",
    "max_weekly_exposure": "weekly bankroll exposure cap reached",
    "max_team_exposure": "already have max exposure on this team",
    "sigma_not_credible": "margin uncertainty isn't credible enough to bet",
    "kickoff_passed": "this game had already kicked off at decision time",
    "no_snapshot": "we didn't have a usable odds snapshot at the decision point",
    "line_quarantined": "the book line failed ingest sanity and was quarantined",
}


def fmt_line(x: Any) -> str:
    """Format a spread/total line with an explicit sign when numeric."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "?"
    return f"{v:+g}"


def kick_et_label(kick_utc: str | None) -> str:
    """Weekday label from an ISO kickoff (UTC). Empty on missing/invalid."""
    if not kick_utc:
        return ""
    try:
        dt = datetime.fromisoformat(kick_utc.replace("Z", "+00:00"))
        return dt.strftime("%a")
    except ValueError:
        return ""


def why_line(bet: BestBet) -> str:
    """One-liner gap explanation for a Best Bet post."""
    gap = abs(float(bet.cand.get("model_line", 0.0)) - float(bet.cand.get("market_line", 0.0)))
    if bet.cand.get("market") == "total":
        return f"Model's total is {gap:.1f} points off the market number."
    return f"Model has this {gap:.1f} points off the market and the inputs are clean."


def join_posts(posts: Sequence[str]) -> str:
    """Number posts with per-post character counts and an over-280 marker."""
    out: list[str] = []
    n_posts = len(posts)
    for i, p in enumerate(posts, 1):
        n = len(p)
        flag = "  ⚠️ OVER 280" if n > POST_CHAR_LIMIT else ""
        out.append(f"--- POST {i}/{n_posts} ({n} chars){flag} ---\n{p}\n")
    return "\n".join(out)


def extract_post_bodies(thread_md: str) -> list[str]:
    """Parse bodies from a ``join_posts`` / ``render_thread`` document."""
    bodies: list[str] = []
    chunks = thread_md.split("--- POST ")
    for chunk in chunks[1:]:
        # Drop the header line ("1/N (k chars) ---")
        nl = chunk.find("\n")
        if nl < 0:
            continue
        body = chunk[nl + 1 :]
        if body.endswith("\n"):
            body = body[:-1]
        bodies.append(body)
    return bodies


def render_thread(
    week: int,
    bets: Sequence[BestBet],
    record: Mapping[str, Any] | None,
    site_url: str,
    *,
    fixture: bool = False,
) -> str:
    """Render the Tuesday anchor thread (one X post per block).

    ``N`` qualifying bets → ``N`` bet posts plus hook / methodology / CTA.
    ``N=0`` → single No-Bet post only (never padded).
    """
    posts: list[str] = []
    rec = dict(record or {})
    szn = rec.get("season_record", "first card of the season")
    clv = rec.get("season_clv")
    clv_bit = f", {clv:+.2f} avg CLV" if isinstance(clv, (int, float)) else ""

    if not bets:
        posts.append(
            f"Week {week}: the model found ZERO bets that clear our bar.\n\n"
            "Yes, really. The market priced this slate tight.\n\n"
            "Most accounts would post 10 picks anyway. We'd rather be flat "
            f"than wrong on purpose. Forecasts for every game: {site_url}"
        )
        body = join_posts(posts)
        return (FIXTURE_BANNER + "\n" + body) if fixture else body

    posts.append(
        f"Week {week} Best Bets are in. \U0001f3c8\n\n"
        f"{len(bets)} play{'s' if len(bets) != 1 else ''} cleared the model's "
        f"bar this week ({szn}{clv_bit}).\n\n"
        "Every pick, the number, and why — thread \U0001f9f5\U0001f447"
    )

    for b in bets:
        g, c = b.game, b.cand
        away, home = g.get("away_team", "?"), g.get("home_team", "?")
        badge = "\U0001f170\ufe0f" if b.rating == "A" else "\U0001f171\ufe0f"
        posts.append(
            f"Best Bet #{b.rank} — {badge}\n\n"
            f"{away} @ {home} ({kick_et_label(g.get('kickoff_utc'))})\n"
            f"\u25b8 Market: {c.get('side_team', '?')} {fmt_line(c.get('market_line'))}\n"
            f"\u25b8 Ridge: {c.get('side_team', '?')} {fmt_line(c.get('model_line'))}\n"
            f"\u25b8 Edge: {b.edge_pct}% | {b.units}u\n\n"
            f"{why_line(b)}"
        )

    posts.append(
        "How picks get made, in plain English:\n\n"
        "\u25b8 Model projects every game (margin + uncertainty)\n"
        "\u25b8 Compare to the market, vig removed\n"
        "\u25b8 Only real gaps with sane inputs qualify\n"
        "\u25b8 Some weeks that's 3. Some weeks 10. Never forced.\n\n"
        f"Full methodology \u2192 {site_url}/about"
    )
    posts.append(
        "That's the card. Tail, fade, or tell me I'm wrong. \U0001f447\n\n"
        "Want the model's read on a game NOT listed? Drop the matchup below "
        "— I'll reply with the number.\n\n"
        f"Forecasts, intervals + full track record: {site_url}"
    )
    body = join_posts(posts)
    return (FIXTURE_BANNER + "\n" + body) if fixture else body


def _is_sigma_refused(game: Mapping[str, Any]) -> bool:
    if game.get("sigma_margin_credible") is False:
        return True
    return game.get("mu_margin") is None


def render_replies(
    games: Sequence[Mapping[str, Any]],
    bets: Sequence[BestBet],
    rejected: Sequence[Mapping[str, Any]] | None,
    site_url: str,
    *,
    fixture: bool = False,
) -> str:
    """Reply bank for every game on the published slate.

    Three branches per game: on-card / forecast-only (FilterReason → plain
    English) / σ-refused.
    """
    bet_by_gid = {str(b.game["game_id"]): b for b in bets}
    rej_by_gid: dict[str, list[str]] = {}
    for r in rejected or []:
        rej_by_gid.setdefault(str(r.get("game_id")), []).extend(list(r.get("reasons") or []))

    blocks: list[str] = []
    if fixture:
        blocks.append(FIXTURE_BANNER.rstrip() + "\n")
    blocks.append("# Reply bank — paste-ready answers per game\n")

    for g in sorted(games, key=lambda x: str(x.get("kickoff_utc") or "")):
        gid = str(g.get("game_id"))
        away, home = g.get("away_team", "?"), g.get("home_team", "?")
        header = f"## {away} @ {home}"

        if gid in bet_by_gid:
            b = bet_by_gid[gid]
            c = b.cand
            body = (
                f"It's on the card \U0001f440 — Best Bet #{b.rank}: "
                f"{c.get('side_team')} {fmt_line(c.get('market_line'))} "
                f"(model: {fmt_line(c.get('model_line'))}, {b.edge_pct}% edge). "
                "Full thread pinned."
            )
        elif _is_sigma_refused(g):
            body = (
                "Model won't give a confident number on this one "
                "(not enough signal) — that's a feature, not a bug. "
                f"Details: {site_url}"
            )
        else:
            mu = g.get("mu_margin")
            assert mu is not None  # narrowed by _is_sigma_refused
            lo, hi = g.get("margin_interval_lo"), g.get("margin_interval_hi")
            fav = home if float(mu) >= 0 else away
            amt = abs(float(mu))
            rng = (
                f" (80% range: {fmt_line(lo)} to {fmt_line(hi)})"
                if lo is not None and hi is not None
                else ""
            )
            reasons = rej_by_gid.get(gid, [])
            why = next(
                (REASON_PLAIN[r] for r in reasons if r in REASON_PLAIN),
                "the market has this priced about right",
            )
            body = (
                f"Model: {fav} by {amt:.1f}{rng}.\n\nNo bet though — {why}. Forecast \u2260 edge."
            )
        blocks.append(f"{header}\n\n{body}\n")

    blocks.append(
        "## Game not in the slate\n\n"
        "That one's outside this week's publish (kicked off before our "
        "Tuesday decision point / not covered). Everything we forecast: "
        f"{site_url}\n"
    )
    return "\n".join(blocks)
