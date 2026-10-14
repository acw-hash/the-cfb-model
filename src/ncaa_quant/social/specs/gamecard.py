"""Game cards — simplified Ridge vs market matchup post."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from ncaa_quant.social.charts._frame import published_at_et
from ncaa_quant.social.charts.slate_map import extract_slate_points
from ncaa_quant.social.copy import trim_caption
from ncaa_quant.social.display_names import label_name
from ncaa_quant.social.logos import logo_display_size, store_logo_scales_in_manifest
from ncaa_quant.social.specs.common import (
    LOGO_TARGET_OPAQUE_AREA,
    SLOT_PX,
    TIER_CHIP_LABELS,
    frame_dict,
    logo_path_for,
    size_pixels,
    week_point,
)
from ncaa_quant.social.specs.format_text import (
    gamecard_headline,
    margin_sign_vs_prob_disagree,
    margin_team_name,
    prob_team_name,
)
from ncaa_quant.social.specs.market import (
    MarketSnapshotError,
    load_market_by_game,
    market_as_of_et,
    require_week_market_snapshot,
)
from ncaa_quant.social.specs.net import def_good, resolve_margin_scale
from ncaa_quant.social.specs.slate_ranges import _combined_net
from ncaa_quant.social.theme import ChartSize

_DEFAULT_TIERS = frozenset({"toss_up", "lean"})
_DEFAULT_N = 3

UnitKind = Literal["offense", "defense"]


def margin_and_prob_sides(game: dict[str, Any]) -> dict[str, Any]:
    """Published margin vs win-probability sides (home-perspective μ)."""
    mu_home = float(game["mu_margin"])
    p_home = float(game["p_win_home"])
    home_team = str(game["home_team"])
    away_team = str(game["away_team"])
    m_team, m_mag = margin_team_name(
        mu_home=mu_home, home_team=home_team, away_team=away_team
    )
    p_team = prob_team_name(
        p_win_home=p_home, home_team=home_team, away_team=away_team
    )
    p_pct = round(p_home * 100) if p_team == home_team else round((1.0 - p_home) * 100)
    return {
        "mu_home": mu_home,
        "p_win_home": p_home,
        "margin_team": m_team,
        "margin_mag": m_mag,
        "prob_team": p_team,
        "prob_pct": p_pct,
        "mismatch": margin_sign_vs_prob_disagree(mu_margin=mu_home, p_win_home=p_home),
    }


def unit_ranks(
    *,
    team_ratings: dict[str, Any],
    ratings_week: int,
    fbs_team_ids: set[int],
) -> dict[int, dict[str, int]]:
    """FBS unit ranks at ``ratings_week`` (1 = best). Ties broken by ``team_id``."""
    teams = team_ratings.get("teams") or {}
    off_scores: list[tuple[float, int]] = []
    def_scores: list[tuple[float, int]] = []
    for tid in sorted(fbs_team_ids):
        entry = teams.get(str(tid))
        pt = week_point(entry, ratings_week) if entry else None
        if pt is None or pt.get("off_epa") is None or pt.get("def_epa") is None:
            continue
        off_scores.append((float(pt["off_epa"]), tid))
        def_scores.append((def_good(float(pt["def_epa"])), tid))

    def _rank(scores: list[tuple[float, int]]) -> dict[int, int]:
        ordered = sorted(scores, key=lambda t: (-t[0], t[1]))
        return {tid: i for i, (_val, tid) in enumerate(ordered, start=1)}

    off_r = _rank(off_scores)
    def_r = _rank(def_scores)
    all_ids = set(off_r) | set(def_r)
    return {tid: {"off_rank": off_r[tid], "def_rank": def_r[tid]} for tid in all_ids}


def select_default_gamecards(
    *,
    week_predictions: dict[str, Any],
    team_ratings: dict[str, Any],
    ratings_week: int,
    fbs_team_ids: set[int],
) -> list[str]:
    """Top 3 eligible toss-up/lean games by combined net_pts."""
    points, _excl = extract_slate_points(week_predictions)
    by_id = {str(g.get("game_id")): g for g in (week_predictions.get("games") or [])}
    scale = resolve_margin_scale()
    scored: list[tuple[float, str]] = []
    for p in points:
        if p.conviction_tier not in _DEFAULT_TIERS:
            continue
        g = by_id.get(str(p.game_id))
        if g is None:
            continue
        score = _combined_net(
            home_id=p.home_team_id,
            away_id=p.away_team_id,
            fbs_team_ids=fbs_team_ids,
            team_ratings=team_ratings,
            ratings_week=ratings_week,
            margin_scale=scale.value,
        )
        scored.append((score, str(p.game_id)))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [gid for _, gid in scored[:_DEFAULT_N]]


def _fbs_ids_from_ratings(team_ratings: dict[str, Any], ratings_week: int) -> set[int]:
    teams = team_ratings.get("teams") or {}
    out: set[int] = set()
    for tid_str, entry in teams.items():
        if week_point(entry, ratings_week) is not None:
            out.add(int(tid_str))
    return out


def _dumbbell_rows(
    *,
    away_team: str,
    home_team: str,
    away_id: int,
    home_id: int,
    ranks: dict[int, dict[str, int]],
    abbrev_by_id: dict[int, str],
) -> list[dict[str, Any]]:
    """Two percentile dumbbell rows: A-off vs H-def, then H-off vs A-def."""
    away_s = label_name(away_id, away_team, abbrev_by_id, prefer="short")
    home_s = label_name(home_id, home_team, abbrev_by_id, prefer="short")
    ar = ranks.get(away_id, {})
    hr = ranks.get(home_id, {})
    n_fbs = max(len(ranks), 1)

    def _pct(rank: int) -> float:
        # Better rank (1) → 100; worst → ~0. Ties already broken in unit_ranks.
        if n_fbs <= 1:
            return 100.0
        return 100.0 * (n_fbs - rank) / (n_fbs - 1)

    rows_spec = [
        {
            "top": f"{away_s} offense #{ar.get('off_rank', '?')}",
            "bottom": f"vs {home_s} defense #{hr.get('def_rank', '?')}",
            "left_rank": ar.get("off_rank"),
            "right_rank": hr.get("def_rank"),
            "left_pct": _pct(int(ar["off_rank"])) if ar.get("off_rank") else 50.0,
            "right_pct": _pct(int(hr["def_rank"])) if hr.get("def_rank") else 50.0,
            "left_side": "away",
            "right_side": "home",
        },
        {
            "top": f"{home_s} offense #{hr.get('off_rank', '?')}",
            "bottom": f"vs {away_s} defense #{ar.get('def_rank', '?')}",
            "left_rank": hr.get("off_rank"),
            "right_rank": ar.get("def_rank"),
            "left_pct": _pct(int(hr["off_rank"])) if hr.get("off_rank") else 50.0,
            "right_pct": _pct(int(ar["def_rank"])) if ar.get("def_rank") else 50.0,
            "left_side": "home",
            "right_side": "away",
        },
    ]
    for row in rows_spec:
        lr = row["left_rank"]
        rr = row["right_rank"]
        if lr is not None and rr is not None:
            if lr < rr:
                row["better"] = "left"
            elif rr < lr:
                row["better"] = "right"
            else:
                row["better"] = "tie"
        else:
            row["better"] = "tie"
    return rows_spec


def build_gamecard_specs(
    *,
    game_ids: list[str],
    week_predictions: dict[str, Any],
    team_ratings: dict[str, Any],
    ratings_week: int,
    week: int,
    season: int,
    logo_dir: Path,
    sizes: list[ChartSize],
    theme: str,
    model_version: str,
    vintage_label: str,
    published_at: str,
    site_url: str,
    brand_mark: Path,
    fbs_team_ids: set[int] | None = None,
    abbrev_by_id: dict[int, str] | None = None,
    odds_snapshot: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    del site_url
    snap = require_week_market_snapshot(odds_snapshot, week=week)
    market_by_game = load_market_by_game(snap)
    snapshot_at = str(snap.get("snapshot_at") or "")
    market_as_of = market_as_of_et(snapshot_at)

    games_by_id = {str(g.get("game_id")): g for g in (week_predictions.get("games") or [])}
    if fbs_team_ids is None:
        fbs_team_ids = _fbs_ids_from_ratings(team_ratings, ratings_week)
    ranks = unit_ranks(
        team_ratings=team_ratings,
        ratings_week=ratings_week,
        fbs_team_ids=fbs_team_ids,
    )

    abbr = abbrev_by_id or {}
    header_area = LOGO_TARGET_OPAQUE_AREA["gamecard"]
    header_slot = SLOT_PX["gamecard"]
    scales: dict[int, float] = {}

    specs: list[dict[str, Any]] = []
    selection: list[dict[str, Any]] = []
    for gid in game_ids:
        g = games_by_id.get(str(gid))
        if g is None:
            continue
        mkt_home = market_by_game.get(str(gid))
        if mkt_home is None:
            raise MarketSnapshotError(
                f"game_id={gid} missing market_home_margin in odds_snapshot"
            )

        home_id = int(g["home_team_id"])
        away_id = int(g["away_team_id"])
        home_team = str(g["home_team"])
        away_team = str(g["away_team"])
        home_short = label_name(home_id, home_team, abbr, prefer="short")
        away_short = label_name(away_id, away_team, abbr, prefer="short")
        home_disp = label_name(home_id, home_team, abbr, prefer="full")
        away_disp = label_name(away_id, away_team, abbr, prefer="full")
        # Prefer full under logos when short enough.
        if len(home_disp) > 14:
            home_disp = home_short
        if len(away_disp) > 14:
            away_disp = away_short

        home_logo = logo_path_for(home_id, logo_dir=logo_dir, theme=theme)  # type: ignore[arg-type]
        away_logo = logo_path_for(away_id, logo_dir=logo_dir, theme=theme)  # type: ignore[arg-type]
        home_lw = home_lh = away_lw = away_lh = None
        if home_logo is not None:
            hl = logo_display_size(
                home_logo,
                team_id=home_id,
                target_opaque_area=header_area,
                slot_px=header_slot,
            )
            home_lw, home_lh = hl.width, hl.height
            scales[home_id] = hl.scale
        if away_logo is not None:
            al = logo_display_size(
                away_logo,
                team_id=away_id,
                target_opaque_area=header_area,
                slot_px=header_slot,
            )
            away_lw, away_lh = al.width, al.height
            scales[away_id] = al.scale

        sigma_ok = bool(g.get("sigma_margin_credible"))
        p_home = g.get("p_win_home")
        tier = str(g.get("conviction_tier") or "")
        tier_label = TIER_CHIP_LABELS.get(tier, tier.replace("_", " ").title())
        kickoff_et = published_at_et(str(g.get("kickoff_utc") or ""))
        # "{Day h:mm} ET · {Tier}" — strip trailing ET from published_at_et if present.
        kick_short = kickoff_et.replace(" ET", "").strip()
        dek = f"{kick_short} ET · {tier_label}"

        mu_home = float(g["mu_margin"]) if g.get("mu_margin") is not None else None
        lo_raw = (
            float(g["margin_interval_lo"])
            if g.get("margin_interval_lo") is not None
            else None
        )
        hi_raw = (
            float(g["margin_interval_hi"])
            if g.get("margin_interval_hi") is not None
            else None
        )

        headline = f"{away_short} @ {home_short}"
        mismatch_alt = ""
        p_away = None
        p_home_f = None
        if mu_home is not None and p_home is not None and sigma_ok:
            p_home_f = float(p_home)
            p_away = 1.0 - p_home_f
            sides = margin_and_prob_sides(g)
            headline = gamecard_headline(
                home_team=home_team,
                away_team=away_team,
                mu_home=mu_home,
                p_win_home=p_home_f,
                home_short=home_short,
                away_short=away_short,
                home_id=home_id,
                away_id=away_id,
                abbrev_by_id=abbr,
            )
            if sides["mismatch"]:
                mismatch_alt = (
                    " Projected margin and win chance point different ways; "
                    "both are Ridge's published numbers."
                )

        # Margin bar: left = away wins by, right = home wins by (home-perspective μ).
        # So plot x = mu_home directly (positive → home / right).
        m_team, m_mag = (
            margin_team_name(mu_home=mu_home, home_team=home_team, away_team=away_team)
            if mu_home is not None
            else (None, 0.0)
        )
        m_short = (
            home_short
            if m_team == home_team
            else away_short
            if m_team == away_team
            else ""
        )
        mu_label = f"{m_short} by {m_mag:.1f}" if m_team else "Even"

        # Market label in home-perspective plot coords.
        mkt_team = home_team if mkt_home >= 0 else away_team
        mkt_short = home_short if mkt_home >= 0 else away_short
        mkt_mag = abs(mkt_home)
        market_label = f"Market: {mkt_short} by {mkt_mag:.1f}"

        # Range end labels (whole numbers), home-perspective.
        lo_lab = hi_lab = None
        if lo_raw is not None and hi_raw is not None:
            # Left end of bar (more away): if lo < 0 → away by |lo|, else home by lo
            if lo_raw < 0:
                lo_lab = f"{away_short} by {abs(lo_raw):.0f}"
            else:
                lo_lab = f"{home_short} by {lo_raw:.0f}"
            if hi_raw < 0:
                hi_lab = f"{away_short} by {abs(hi_raw):.0f}"
            else:
                hi_lab = f"{home_short} by {hi_raw:.0f}"

        # Axis domain for home-perspective margin.
        axis_vals = [0.0, float(mkt_home)]
        if mu_home is not None:
            axis_vals.append(mu_home)
        if lo_raw is not None:
            axis_vals.append(lo_raw)
        if hi_raw is not None:
            axis_vals.append(hi_raw)
        pad = max(2.0, (max(axis_vals) - min(axis_vals)) * 0.12)
        x_min = min(axis_vals) - pad
        x_max = max(axis_vals) + pad

        dumbbells = _dumbbell_rows(
            away_team=away_team,
            home_team=home_team,
            away_id=away_id,
            home_id=home_id,
            ranks=ranks,
            abbrev_by_id=abbr,
        )

        caption = trim_caption(
            f"Ridge's view of {away_short} @ {home_short}: {mu_label}, "
            f"market {mkt_short} by {mkt_mag:.1f}. Tier: {tier_label}."
        )
        alt = (
            f"Game card for {away_team} @ {home_team}, week {week} {season}."
            f"{mismatch_alt}"
        )

        selection.append(
            {
                "game_id": str(gid),
                "matchup": f"{away_team} @ {home_team}",
                "tier": g.get("conviction_tier"),
                "mkt_home": mkt_home,
                "mu_home": mu_home,
            }
        )

        for size in sizes:
            name, width, height = size_pixels(size)
            specs.append(
                {
                    "chart": "gamecard",
                    "schema": "soc2-spec/1",
                    "game_id": str(gid),
                    "frame": frame_dict(
                        season=season,
                        week=week,
                        week_stamp=f"Week {week} · {season}",
                        title=headline,
                        subtitle=dek,
                        theme=theme,
                        size_name=name,
                        width=width,
                        height=height,
                        published_at=published_at,
                        model_version=model_version,
                        vintage_label=vintage_label,
                        brand_mark=brand_mark,
                        footer_extra=f"market as of {market_as_of}" if market_as_of else None,
                    ),
                    "header": {
                        "away_team": away_team,
                        "home_team": home_team,
                        "away_display": away_disp,
                        "home_display": home_disp,
                        "away_logo": str(away_logo) if away_logo else None,
                        "home_logo": str(home_logo) if home_logo else None,
                        "away_logo_w": away_lw,
                        "away_logo_h": away_lh,
                        "home_logo_w": home_lw,
                        "home_logo_h": home_lh,
                        "kickoff_et": kickoff_et,
                        "tier": tier,
                        "tier_label": tier_label,
                        "p_win_away": p_away,
                        "p_win_home": p_home_f,
                        "p_away_label": f"{round(p_away * 100)}%" if p_away is not None else None,
                        "p_home_label": (
                            f"{round(p_home_f * 100)}%" if p_home_f is not None else None
                        ),
                    },
                    "margin_bar": {
                        "label": "Projected margin · 80% range",
                        "x_min": x_min,
                        "x_max": x_max,
                        "mu": mu_home,
                        "mu_label": mu_label,
                        "lo": lo_raw,
                        "hi": hi_raw,
                        "lo_label": lo_lab,
                        "hi_label": hi_lab,
                        "market": mkt_home,
                        "market_label": market_label,
                        "orientation": "away_left_home_right",
                    },
                    "dumbbells": {
                        "title": "Where it's decided · FBS rank, better →",
                        "rows": dumbbells,
                    },
                    "caption": caption,
                    "alt": alt,
                    "footnotes": [],
                    "meta": {
                        "interval_fields": [
                            "margin_interval_lo",
                            "margin_interval_hi",
                        ],
                        "unit_ranks_week": ratings_week,
                        "market_field": "market_home_margin",
                        "market_snapshot_at": snapshot_at,
                    },
                }
            )

    if scales:
        store_logo_scales_in_manifest(logo_dir, scales)

    report = {
        "game_ids": game_ids,
        "selection": selection,
        "market_snapshot_at": snapshot_at,
    }
    return specs, report
