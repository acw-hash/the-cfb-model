"""Slate ranges — Ridge vs. the market for featured games."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

from ncaa_quant.social.charts.slate_map import extract_slate_points
from ncaa_quant.social.copy import trim_caption
from ncaa_quant.social.display_names import label_name
from ncaa_quant.social.logos import logo_display_size, store_logo_scales_in_manifest
from ncaa_quant.social.specs.common import (
    LOGO_TARGET_OPAQUE_AREA,
    SLOT_PX,
    frame_dict,
    logo_path_for,
    size_pixels,
    week_point,
)
from ncaa_quant.social.specs.format_text import (
    format_axis_tick,
    format_team_by,
)
from ncaa_quant.social.specs.market import (
    MarketSnapshotError,
    load_market_by_game,
    market_as_of_et,
    require_week_market_snapshot,
    to_market_favorite_perspective,
)
from ncaa_quant.social.specs.net import net_pts, resolve_margin_scale
from ncaa_quant.social.theme import ChartSize

SlateScope = Literal["featured", "all"]
FEATURED_N = 16

_BAR_FILL = "#AEAEB2"
_GAP_STRONG = 5.0


def _combined_net(
    *,
    home_id: int,
    away_id: int,
    fbs_team_ids: set[int],
    team_ratings: dict[str, Any],
    ratings_week: int,
    margin_scale: float,
) -> float:
    """Combined net_pts of both teams; FCS opponents rank last (large negative)."""
    teams = team_ratings.get("teams") or {}
    total = 0.0
    for tid in (home_id, away_id):
        if tid not in fbs_team_ids:
            return -1e9
        entry = teams.get(str(tid))
        pt = week_point(entry, ratings_week) if entry else None
        if pt is None or pt.get("off_epa") is None or pt.get("def_epa") is None:
            return -1e9
        total += net_pts(
            off_epa=float(pt["off_epa"]),
            def_epa=float(pt["def_epa"]),
            margin_scale=margin_scale,
        )
    return total


def select_slate_games(
    *,
    week_predictions: dict[str, Any],
    team_ratings: dict[str, Any],
    ratings_week: int,
    fbs_team_ids: set[int],
    scope: SlateScope,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (ordered featured candidates, excluded from slate map, selection)."""
    points, excluded = extract_slate_points(week_predictions)
    by_id = {str(g.get("game_id")): g for g in (week_predictions.get("games") or [])}
    scale = resolve_margin_scale()

    scored: list[tuple[float, dict[str, Any]]] = []
    for p in points:
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
        scored.append((score, g))
    scored.sort(key=lambda t: (-t[0], str(t[1].get("game_id"))))

    chosen = scored[:FEATURED_N] if scope == "featured" else scored
    rows = [{"game": g, "combined_net": score} for score, g in chosen]
    selection = [
        {
            "game_id": str(r["game"]["game_id"]),
            "matchup": f"{r['game']['away_team']} @ {r['game']['home_team']}",
            "combined_net": r["combined_net"],
            "tier": r["game"].get("conviction_tier"),
        }
        for r in rows
    ]
    return rows, excluded, selection


def apply_market_and_sort(
    rows: list[dict[str, Any]],
    *,
    market_by_game: dict[str, float],
    abbrev_by_id: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach market-favorite perspective; drop games without market; sort by |gap|.

    Returns (kept rows sorted, excluded-for-missing-market report rows).
    """
    kept: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for r in rows:
        g = r["game"]
        gid = str(g["game_id"])
        mkt = market_by_game.get(gid)
        if mkt is None:
            missing.append(
                {
                    "game_id": gid,
                    "matchup": f"{g['away_team']} @ {g['home_team']}",
                    "reason": "no_market_snapshot",
                }
            )
            continue
        if g.get("mu_margin") is None or g.get("margin_interval_lo") is None:
            missing.append(
                {
                    "game_id": gid,
                    "matchup": f"{g['away_team']} @ {g['home_team']}",
                    "reason": "missing_ridge_margin",
                }
            )
            continue
        persp = to_market_favorite_perspective(
            mkt_home=mkt,
            mu_home=float(g["mu_margin"]),
            lo_home=float(g["margin_interval_lo"]),
            hi_home=float(g["margin_interval_hi"]),
            p_win_home=float(g["p_win_home"]),
            home_team=str(g["home_team"]),
            away_team=str(g["away_team"]),
            home_team_id=int(g["home_team_id"]),
            away_team_id=int(g["away_team_id"]),
        )
        # Relabel leans with short display names.
        if persp.leans_team_id is not None:
            short = label_name(
                persp.leans_team_id,
                persp.leans_team or "",
                abbrev_by_id,
                prefer="short",
            )
            leans_label = f"{short} +{persp.leans_mag:.1f}"
        else:
            leans_label = "—"
        kept.append(
            {
                **r,
                "mkt": persp,
                "leans_label": leans_label,
                "abs_gap": abs(persp.gap),
            }
        )
    kept.sort(
        key=lambda r: (
            -r["abs_gap"],
            str(r["game"].get("kickoff_utc") or ""),
            str(r["game"].get("game_id")),
        )
    )
    return kept, missing


def _nice_ticks(lo: float, hi: float, *, target: int = 5) -> list[float]:
    if hi <= lo:
        return [lo, (lo + hi) / 2.0, hi]
    raw = (hi - lo) / max(target - 1, 1)
    exp = math.floor(math.log10(raw)) if raw > 0 else 0
    base = raw / (10**exp) if raw > 0 else 1.0
    if base <= 1:
        nice = 1.0
    elif base <= 2:
        nice = 2.0
    elif base <= 5:
        nice = 5.0
    else:
        nice = 10.0
    step = nice * (10**exp)
    start = math.floor(lo / step) * step
    end = math.ceil(hi / step) * step
    ticks: list[float] = []
    n = int(round((end - start) / step))
    for i in range(n + 1):
        v = start + i * step
        if v >= lo - 1e-9 and v <= hi + 1e-9:
            ticks.append(round(v, 6))
    if len(ticks) < 3:
        ticks = [lo, 0.0, hi]
    return ticks


def _axis_limits(values: list[float]) -> tuple[float, float, list[float]]:
    """Fit domain to marks/ranges + padding; always include 0; 4–5 nice ticks."""
    if not values:
        return -5.0, 40.0, [0.0, 10.0, 20.0, 30.0, 40.0]
    lo = min(values)
    hi = max(values)
    span = max(hi - lo, 5.0)
    pad = max(2.0, span * 0.08)
    x_min = lo - pad
    x_max = hi + pad
    if x_min > 0:
        x_min = 0.0
    if x_max < 0:
        x_max = 0.0
    # Round outward to nice whole numbers.
    step = 5.0
    x_min = math.floor(x_min / step) * step
    x_max = math.ceil(x_max / step) * step
    if x_min == x_max:
        x_min, x_max = x_min - step, x_max + step
    ticks = _nice_ticks(x_min, x_max, target=5)
    ticks = [float(round(t)) for t in ticks if abs(t - round(t)) < 1e-9]
    if 0.0 not in ticks:
        ticks = sorted({*ticks, 0.0})
    return float(x_min), float(x_max), ticks


def clip_bar(
    lo: float,
    hi: float,
    x_min: float,
    x_max: float,
) -> tuple[float, float, bool, bool]:
    """Clip a margin bar to the axis; return arrow flags when clipped."""
    arrow_lo = lo < x_min
    arrow_hi = hi > x_max
    return max(lo, x_min), min(hi, x_max), arrow_lo, arrow_hi


def _matchup_label(
    *,
    fav_id: int,
    dog_id: int,
    fav_team: str,
    dog_team: str,
    abbrev_by_id: dict[int, str],
) -> str:
    fav_s = label_name(fav_id, fav_team, abbrev_by_id, prefer="short")
    dog_s = label_name(dog_id, dog_team, abbrev_by_id, prefer="short")
    return f"{fav_s} vs {dog_s}"


def slate_column_labels(
    *,
    x_ridge: float,
    x_mkt: float,
    fav_short: str,
    dog_short: str,
) -> tuple[str, str, str]:
    """Ridge / Market / Gap cell text from the market-favorite perspective."""
    from ncaa_quant.social.specs.format_text import rounds_to_zero

    if rounds_to_zero(x_ridge):
        ridge_label = "Even"
    elif x_ridge > 0:
        ridge_label = format_team_by(fav_short, x_ridge)
    else:
        ridge_label = format_team_by(dog_short, abs(x_ridge))
    market_label = format_team_by(fav_short, x_mkt)
    gap_label = f"{abs(x_ridge - x_mkt):.1f}"
    return ridge_label, market_label, gap_label


def _caption_seed(
    rows: list[dict[str, Any]],
    *,
    abbrev_by_id: dict[int, str],
) -> str:
    """``Biggest gaps with the market: OSU–MD (Ridge OSU by 23.1, market 34.5), …``."""
    top = rows[:3]
    if not top:
        return "No featured games with a market snapshot this week."
    parts: list[str] = []
    for r in top:
        mkt = r["mkt"]
        fav_s = label_name(mkt.fav_team_id, mkt.fav_team, abbrev_by_id, prefer="short")
        dog_s = label_name(mkt.dog_team_id, mkt.dog_team, abbrev_by_id, prefer="short")
        ridge_lab, _mkt_lab, _gap = slate_column_labels(
            x_ridge=mkt.x_ridge,
            x_mkt=mkt.x_mkt,
            fav_short=fav_s,
            dog_short=dog_s,
        )
        # Caption quotes Ridge's favored-side wording + market magnitude only.
        ridge_bit = "Ridge Even" if ridge_lab == "Even" else f"Ridge {ridge_lab}"
        parts.append(f"{fav_s}\u2013{dog_s} ({ridge_bit}, market {mkt.x_mkt:.1f})")
    return trim_caption("Biggest gaps with the market: " + ", ".join(parts) + ".")


def build_slate_ranges_specs(
    *,
    week_predictions: dict[str, Any],
    team_ratings: dict[str, Any],
    ratings_week: int,
    week: int,
    season: int,
    fbs_team_ids: set[int],
    abbrev_by_id: dict[int, str],
    logo_dir: Path,
    sizes: list[ChartSize],
    theme: str,
    scope: SlateScope,
    model_version: str,
    vintage_label: str,
    published_at: str,
    site_url: str,
    brand_mark: Path,
    meta: dict[str, Any],
    odds_snapshot: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    del site_url
    snap = require_week_market_snapshot(odds_snapshot, week=week)
    market_by_game = load_market_by_game(snap)
    if not market_by_game:
        raise MarketSnapshotError("odds_snapshot has no usable market_home_margin values")

    rows0, excluded, selection = select_slate_games(
        week_predictions=week_predictions,
        team_ratings=team_ratings,
        ratings_week=ratings_week,
        fbs_team_ids=fbs_team_ids,
        scope=scope,
    )
    rows, missing_market = apply_market_and_sort(
        rows0, market_by_game=market_by_game, abbrev_by_id=abbrev_by_id
    )

    nominal = None
    nominal_field = "margin_interval_nominal"
    for r in rows:
        n = r["game"].get("margin_interval_nominal")
        if n is not None:
            nominal = float(n)
            break
    if nominal is None:
        nominal = 0.8
        nominal_field = "margin_interval_nominal (fallback 0.8)"

    refresh_kind = str(week_predictions.get("refresh_kind") or meta.get("refresh_kind") or "")
    snapshot_at = str(snap.get("snapshot_at") or "")
    market_as_of = market_as_of_et(snapshot_at)

    pages: list[list[dict[str, Any]]] = []
    if scope == "all":
        for i in range(0, max(len(rows), 1), FEATURED_N):
            pages.append(rows[i : i + FEATURED_N])
        if not pages:
            pages = [[]]
    else:
        pages = [rows]

    headline = f"Ridge vs. the market · week {week}"
    dek = "Dot = Ridge · ◇ = market · sorted by gap (pts)"
    caption = _caption_seed(rows, abbrev_by_id=abbrev_by_id)
    alt = (
        f"Week {week} Ridge vs market projected margins, {season}. "
        f"Market from odds_snapshot market_home_margin as of {snapshot_at}."
    )

    target_area = LOGO_TARGET_OPAQUE_AREA["slate_ranges"]
    logo_slot = SLOT_PX["slate_ranges"]
    scales: dict[int, float] = {}

    specs: list[dict[str, Any]] = []
    for page_idx, page in enumerate(pages, start=1):
        axis_vals: list[float] = []
        for r in page:
            m = r["mkt"]
            axis_vals.extend([m.lo, m.hi, m.x_mkt, m.x_ridge])
        x_min, x_max, x_ticks = _axis_limits(axis_vals)

        for size in sizes:
            name, width, height = size_pixels(size)
            out_rows: list[dict[str, Any]] = []
            missing_logos: list[dict[str, Any]] = []
            for r in page:
                m = r["mkt"]
                g = r["game"]
                fav_path = logo_path_for(
                    m.fav_team_id, logo_dir=logo_dir, theme=theme  # type: ignore[arg-type]
                )
                dog_path = logo_path_for(
                    m.dog_team_id, logo_dir=logo_dir, theme=theme  # type: ignore[arg-type]
                )
                fav_lw = fav_lh = dog_lw = dog_lh = None
                if fav_path is None:
                    missing_logos.append(
                        {"team_id": m.fav_team_id, "school": m.fav_team}
                    )
                else:
                    fl = logo_display_size(
                        fav_path,
                        team_id=m.fav_team_id,
                        target_opaque_area=target_area,
                        slot_px=logo_slot,
                    )
                    fav_lw, fav_lh = fl.width, fl.height
                    scales[m.fav_team_id] = fl.scale
                if dog_path is None:
                    missing_logos.append(
                        {"team_id": m.dog_team_id, "school": m.dog_team}
                    )
                else:
                    dl = logo_display_size(
                        dog_path,
                        team_id=m.dog_team_id,
                        target_opaque_area=target_area,
                        slot_px=logo_slot,
                    )
                    dog_lw, dog_lh = dl.width, dl.height
                    scales[m.dog_team_id] = dl.scale
                bar_lo, bar_hi, arrow_lo, arrow_hi = clip_bar(m.lo, m.hi, x_min, x_max)
                label = _matchup_label(
                    fav_id=m.fav_team_id,
                    dog_id=m.dog_team_id,
                    fav_team=m.fav_team,
                    dog_team=m.dog_team,
                    abbrev_by_id=abbrev_by_id,
                )
                gap_strong = r["abs_gap"] >= _GAP_STRONG
                fav_short = label_name(
                    m.fav_team_id, m.fav_team, abbrev_by_id, prefer="short"
                )
                dog_short = label_name(
                    m.dog_team_id, m.dog_team, abbrev_by_id, prefer="short"
                )
                ridge_col, market_col, gap_col = slate_column_labels(
                    x_ridge=m.x_ridge,
                    x_mkt=m.x_mkt,
                    fav_short=fav_short,
                    dog_short=dog_short,
                )
                out_rows.append(
                    {
                        "game_id": str(g["game_id"]),
                        "label": label,
                        "fav_team": m.fav_team,
                        "dog_team": m.dog_team,
                        "fav_logo": str(fav_path) if fav_path else None,
                        "dog_logo": str(dog_path) if dog_path else None,
                        "fav_logo_w": fav_lw,
                        "fav_logo_h": fav_lh,
                        "dog_logo_w": dog_lw,
                        "dog_logo_h": dog_lh,
                        "x_ridge": m.x_ridge,
                        "x_mkt": m.x_mkt,
                        "mu": m.x_ridge,
                        "lo": m.lo,
                        "hi": m.hi,
                        "p_fav": m.p_fav,
                        "ridge_col": ridge_col,
                        "market_col": market_col,
                        "gap_col": gap_col,
                        # Keep legacy keys empty so old mark labels are not drawn.
                        "ridge_label": "",
                        "market_label": "",
                        "mu_label": "",
                        "gap": m.gap,
                        "abs_gap": r["abs_gap"],
                        "gap_strong": gap_strong,
                        "bar_fill": _BAR_FILL,
                        "arrow_lo": arrow_lo,
                        "arrow_hi": arrow_hi,
                        "bar_lo": bar_lo,
                        "bar_hi": bar_hi,
                        "mkt_home": m.mkt_home,
                        "favored_side": m.favored_side,
                    }
                )

            page_suffix = f"_p{page_idx}" if scope == "all" and len(pages) > 1 else ""
            footer_extra = f"market as of {market_as_of}" if market_as_of else None
            specs.append(
                {
                    "chart": "slate_ranges",
                    "schema": "soc2-spec/1",
                    "page": page_idx,
                    "page_suffix": page_suffix,
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
                        footer_extra=footer_extra,
                    ),
                    "axes": {
                        "x_min": x_min,
                        "x_max": x_max,
                        "x_zero": 0.0,
                        "x_ticks": x_ticks,
                        "x_tick_labels": [format_axis_tick(t) for t in x_ticks],
                        "zero_label": "0",
                        "x_label": "Projected margin for market favorite (pts)",
                        "caption": "Projected margin for market favorite (pts)",
                    },
                    "layout": {
                        "matchup_frac": 0.28,
                        "bars_frac": 0.30,
                        "ridge_frac": 0.15,
                        "market_frac": 0.15,
                        "gap_frac": 0.12,
                    },
                    "columns": [
                        {"id": "matchup", "header": ""},
                        {"id": "plot", "header": ""},
                        {"id": "ridge", "header": "Ridge"},
                        {"id": "market", "header": "Market"},
                        {"id": "gap", "header": "Gap"},
                    ],
                    "rows": out_rows,
                    "footnotes": [],
                    "caption": caption,
                    "alt": alt,
                    "meta": {
                        "scope": scope,
                        "nominal": nominal,
                        "nominal_field": nominal_field,
                        "interval_fields": ["margin_interval_lo", "margin_interval_hi"],
                        "n_featured": len(rows),
                        "excluded": excluded,
                        "excluded_no_market": missing_market,
                        "selection": selection,
                        "missing_logos": missing_logos,
                        "refresh_kind": refresh_kind,
                        "market_field": "market_home_margin",
                        "market_module": (
                            "workers/odds-refresh/src/consensus.ts:"
                            "homeSpreadToMarketMargin"
                        ),
                        "market_snapshot_at": snapshot_at,
                        "consensus_method": snap.get("consensus_method"),
                    },
                }
            )

    if scales:
        store_logo_scales_in_manifest(logo_dir, scales)

    report = {
        "scope": scope,
        "nominal": nominal,
        "nominal_field": nominal_field,
        "selection": selection,
        "excluded": excluded,
        "excluded_no_market": missing_market,
        "n_rows": len(rows),
        "pages": len(pages),
        "market_snapshot_at": snapshot_at,
        "rows": [
            {
                "game_id": str(r["game"]["game_id"]),
                "matchup": f"{r['game']['away_team']} @ {r['game']['home_team']}",
                "x_ridge": r["mkt"].x_ridge,
                "x_mkt": r["mkt"].x_mkt,
                "gap": r["mkt"].gap,
                "abs_gap": r["abs_gap"],
                "leans_label": r["leans_label"],
                "p_fav": r["mkt"].p_fav,
            }
            for r in rows
        ],
    }
    return specs, report
