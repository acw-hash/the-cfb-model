"""Market map — Ridge fitted rating vs Ridge−market disagreement."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ncaa_quant.social.charts.ridge_map import ridge_week_labels
from ncaa_quant.social.copy import trim_caption
from ncaa_quant.social.display_names import label_name
from ncaa_quant.social.logos import store_logo_scales_in_manifest
from ncaa_quant.social.specs.common import frame_dict, size_pixels
from ncaa_quant.social.specs.format_text import format_axis_tick, format_signed
from ncaa_quant.social.specs.market_ratings import (
    DualFitReport,
    fit_dual_ratings,
    snapshot_market_through_label,
)
from ncaa_quant.social.specs.readmap import (
    _place_all_logos,
    filled_plot_box,
    fitted_net_domain,
    logo_span_fractions,
    quadrant_obstacle_boxes,
)
from ncaa_quant.social.theme import ChartSize

TITLE = "Where Ridge and the market disagree"
DEK = "Right = Ridge rates them higher · up = Ridge higher than the market"
AGREEMENT_BAND = 2.0
Y_PAD_FRAC = 0.10
#: CSS px reserved on the right for the "roughly agree" label (outside logos).
AGREEMENT_LABEL_PAD_CSS = 88.0


def _symmetric_y_domain(values: list[float]) -> tuple[float, float, list[float], list[str]]:
    """Symmetric about 0; pad max|y| by 10%; nice ticks (2 or 5 multiples)."""
    if not values:
        ticks = [-10.0, -5.0, 0.0, 5.0, 10.0]
        return -10.0, 10.0, ticks, [format_axis_tick(v) for v in ticks]
    peak = max(abs(float(v)) for v in values)
    hi = peak * (1.0 + Y_PAD_FRAC)
    if hi < 1.0:
        hi = 1.0
    # Prefer step 2 or 5.
    for step in (2.0, 5.0, 1.0, 10.0):
        n = int(math.ceil(hi / step))
        if 2 <= n <= 6:
            bound = n * step
            ticks = [round(-bound + i * step, 6) for i in range(2 * n + 1)]
            # Thin to ~5 ticks if too many
            if len(ticks) > 7:
                continue
            return -bound, bound, ticks, [format_axis_tick(v) for v in ticks]
    bound = math.ceil(hi)
    ticks = [-bound, -bound / 2, 0.0, bound / 2, bound]
    return -float(bound), float(bound), ticks, [format_axis_tick(v) for v in ticks]


def market_map_caption(
    *,
    higher: list[dict[str, Any]],
    lower: list[dict[str, Any]],
) -> str:
    """Caption seed: top-3 higher and top-3 lower by y (display names, 1 decimal)."""

    def _fmt(rows: list[dict[str, Any]], *, sign: str) -> str:
        parts: list[str] = []
        for r in rows[:3]:
            y = float(r["y"])
            parts.append(f"{r['display']} ({format_signed(y, decimals=1)})")
        return ", ".join(parts)

    hi = _fmt(higher, sign="+")
    lo = _fmt(lower, sign="-")
    return (
        f"Ridge is higher than the market on {hi}. "
        f"Lower on {lo}."
    )


def market_map_alt(
    *,
    week: int,
    season: int,
    top: list[dict[str, Any]],
    bottom: list[dict[str, Any]],
    excluded_names: list[str],
) -> str:
    top_s = ", ".join(
        f"{r['display']} ({format_signed(r['y'], decimals=1)}, {r['n_games']} games)"
        for r in top[:5]
    )
    bot_s = ", ".join(
        f"{r['display']} ({format_signed(r['y'], decimals=1)}, {r['n_games']} games)"
        for r in bottom[:5]
    )
    excl = ", ".join(excluded_names) if excluded_names else "none"
    return (
        f"Where Ridge and the market disagree, entering Week {week} · {season}. "
        f"X is Ridge rating (pts vs average FBS team); "
        f"Y is Ridge minus market (pts). "
        f"Top by Y: {top_s}. Bottom by Y: {bot_s}. "
        f"Excluded under the 3-game floor: {excl}. "
        "Market ratings fitted from this season's market prices, "
        "weighted toward recent weeks."
    )


def _school_lookup(team_ratings: dict[str, Any]) -> dict[int, str]:
    out: dict[int, str] = {}
    for tid_s, entry in (team_ratings.get("teams") or {}).items():
        try:
            tid = int(tid_s)
        except (TypeError, ValueError):
            continue
        out[tid] = str(entry.get("school") or f"Team {tid}")
    return out


def run_spot_check(
    report: DualFitReport,
    *,
    schools: dict[int, str],
    week_predictions: dict[str, Any] | None,
    odds_snapshot: dict[str, Any] | None,
) -> dict[str, dict[str, float | str | None]]:
    """Ohio State, Notre Dame, Texas Tech, Arizona, Wake Forest vs slate gaps."""
    want = {
        "Ohio State",
        "Notre Dame",
        "Texas Tech",
        "Arizona",
        "Wake Forest",
    }
    by_school = {name: tid for tid, name in schools.items() if name in want}
    # Slate gap from current week: for each team, mean (mu - market) in fav view
    # Approximate: home-perspective ridge−market on that team's games this week.
    mkt_by = {}
    if odds_snapshot:
        for g in odds_snapshot.get("games") or []:
            if g.get("market_home_margin") is not None and g.get("game_id") is not None:
                mkt_by[str(g["game_id"])] = float(g["market_home_margin"])
    slate_gap: dict[int, float] = {}
    if week_predictions:
        for g in week_predictions.get("games") or []:
            gid = str(g.get("game_id"))
            mu = g.get("mu_margin")
            mkt = mkt_by.get(gid)
            if mu is None or mkt is None:
                continue
            gap_home = float(mu) - float(mkt)
            hid = int(g["home_team_id"])
            aid = int(g["away_team_id"])
            # Positive gap_home → Ridge higher on home than market.
            slate_gap[hid] = slate_gap.get(hid, 0.0) + gap_home
            slate_gap[aid] = slate_gap.get(aid, 0.0) - gap_home

    out: dict[str, dict[str, float | str | None]] = {}
    for name in sorted(want):
        tid = by_school.get(name)
        if tid is None or tid not in report.y_by_team:
            out[name] = {
                "team_id": None,
                "ridge": None,
                "market": None,
                "y": None,
                "slate_gap": None,
                "note": "not plotted or not found",
            }
            continue
        y = report.y_by_team[tid]
        sg = slate_gap.get(tid)
        note = "ok"
        if sg is not None and y * sg < 0 and abs(y) > 0.5 and abs(sg) > 0.5:
            note = "direction disagrees with this week's slate gap"
        out[name] = {
            "team_id": float(tid),
            "ridge": float(report.ridge_ratings[tid]),
            "market": float(report.market.ratings[tid]),
            "y": float(y),
            "slate_gap": float(sg) if sg is not None else None,
            "note": note,
        }
    return out


def build_market_map_specs(
    *,
    team_ratings: dict[str, Any],
    week_predictions: dict[str, Any],
    odds_snapshot: dict[str, Any] | None,
    week: int,
    slate_week: int,
    season: int,
    fbs_team_ids: set[int],
    abbrev_by_id: dict[int, str],
    logo_dir: Path,
    sizes: list[ChartSize],
    theme: str,
    model_version: str,
    vintage_label: str,
    published_at: str,
    site_url: str,
    brand_mark: Path,
    fit_report: DualFitReport | None = None,
) -> tuple[list[dict[str, Any]], DualFitReport]:
    """Build market-map specs (one per size) and return the fit report."""
    del site_url, fbs_team_ids
    report = fit_report or fit_dual_ratings(
        season=season,
        current_week=slate_week,
        odds_snapshot=odds_snapshot,
        week_predictions=week_predictions,
        team_ratings=team_ratings,
    )
    schools = _school_lookup(team_ratings)
    report.spot_check = run_spot_check(
        report,
        schools=schools,
        week_predictions=week_predictions,
        odds_snapshot=odds_snapshot,
    )

    rows: list[dict[str, Any]] = []
    for tid in report.plot_team_ids:
        school = schools.get(tid, f"Team {tid}")
        abbr = abbrev_by_id.get(tid, school[:4].upper())
        display = label_name(tid, school, abbrev_by_id, prefer="full")
        short = label_name(tid, school, abbrev_by_id, prefer="short")
        ridge = float(report.ridge_ratings[tid])
        market = float(report.market.ratings[tid])
        y = float(report.y_by_team[tid])
        rows.append(
            {
                "team_id": tid,
                "school": school,
                "abbreviation": abbr,
                "display": display,
                "short_name": short,
                "ridge": ridge,
                "market": market,
                "y": y,
                "x": ridge,
                "n_games": int(report.market.game_counts.get(tid, 0)),
            }
        )
    if not rows:
        msg = "No FBS teams cleared the market-map data floor"
        raise RuntimeError(msg)

    by_y = sorted(rows, key=lambda r: (-r["y"], r["team_id"]))
    higher = by_y[:3]
    lower = list(reversed(by_y[-3:]))
    top5 = by_y[:5]
    bot5 = list(reversed(by_y[-5:]))

    excluded_names = [
        label_name(
            tid,
            schools.get(tid, f"Team {tid}"),
            abbrev_by_id,
            prefer="full",
        )
        for tid in report.excluded_team_ids
    ]

    x_lo, x_hi, x_ticks, x_tick_labels = fitted_net_domain([r["x"] for r in rows])
    y_lo, y_hi, y_ticks, y_tick_labels = _symmetric_y_domain([r["y"] for r in rows])

    labels = ridge_week_labels(map_week=week, slate_week=slate_week, season=season)
    # Prompt: "Entering Week N · 2026"
    week_stamp = f"Entering Week {slate_week} · {season}"

    caption = trim_caption(market_map_caption(higher=higher, lower=lower))
    alt = market_map_alt(
        week=slate_week,
        season=season,
        top=top5,
        bottom=bot5,
        excluded_names=excluded_names,
    )

    market_through = snapshot_market_through_label(odds_snapshot)
    footer_extra = (
        f"market through {market_through} ET" if market_through else None
    )

    xs = [r["x"] for r in rows]
    ys = [r["y"] for r in rows]

    out: list[dict[str, Any]] = []
    for size in sizes:
        name, width, height = size_pixels(size)
        plot = filled_plot_box(canvas_w=width, canvas_h=height, size_name=name)
        # Keep packing domain aligned with the renderer's right-side label pad.
        plot = {
            **plot,
            "w": max(120.0, float(plot["w"]) - AGREEMENT_LABEL_PAD_CSS),
        }
        obstacles = quadrant_obstacle_boxes(
            x_lo=x_lo,
            x_hi=x_hi,
            y_lo=y_lo,
            y_hi=y_hi,
            plot_w=float(plot["w"]),
            plot_h=float(plot["h"]),
        )
        points_out, slot_px, max_disp, worst_ov, scales, missing = _place_all_logos(
            rows=rows,
            xs=xs,
            ys=ys,
            x_lo=x_lo,
            x_hi=x_hi,
            y_lo=y_lo,
            y_hi=y_hi,
            plot_w=float(plot["w"]),
            plot_h=float(plot["h"]),
            logo_dir=logo_dir,
            theme=theme,
            obstacles=obstacles,
        )
        span_w, span_h = logo_span_fractions(
            [
                {
                    "logo_x": p["logo_x"],
                    "logo_y": p["logo_y"],
                    "width": p["width"],
                    "height": p["height"],
                }
                for p in points_out
            ],
            x_lo=x_lo,
            x_hi=x_hi,
            y_lo=y_lo,
            y_hi=y_hi,
        )
        for p in points_out:
            p["logo_w_data"] = p.pop("width")
            p["logo_h_data"] = p.pop("height")
            p["true_x"] = p["x"]
            p["true_y"] = p["y"]

        if scales:
            store_logo_scales_in_manifest(logo_dir, scales)

        frame = frame_dict(
            season=season,
            week=slate_week,
            week_stamp=week_stamp,
            title=TITLE,
            subtitle=DEK,
            theme=theme,
            size_name=name,
            width=width,
            height=height,
            published_at=published_at,
            model_version=model_version,
            vintage_label=vintage_label,
            brand_mark=brand_mark,
            footer_extra=footer_extra,
        )

        out.append(
            {
                "chart": "market_map",
                "schema": "soc2-spec/1",
                "frame": frame,
                "plot": plot,
                "axes": {
                    "x_label": "Ridge rating (pts vs. average FBS team)",
                    "y_label": "Ridge minus market (pts)",
                    "x_min": x_lo,
                    "x_max": x_hi,
                    "y_min": y_lo,
                    "y_max": y_hi,
                    "y_inverted": False,
                    "x_zero": 0.0,
                    "y_mid": 0.0,
                    "zero_midline_label": "",
                    "y_zero_label": "Ridge = market",
                    "agreement_band": AGREEMENT_BAND,
                    "agreement_label": "roughly agree",
                    "x_ticks": x_ticks,
                    "x_tick_labels": x_tick_labels,
                    "y_ticks": y_ticks,
                    "y_tick_labels": y_tick_labels,
                    "y_top_lines": [],
                    "y_bottom_lines": [],
                    "zero_label": "0",
                },
                "quadrants": [
                    {
                        "id": "proven_good",
                        "label": "Good, and Ridge likes them more",
                        "x_sign": 1,
                        "y_side": "upper",
                        "placement": "outside",
                    },
                    {
                        "id": "good_proving",
                        "label": "Good, but the market likes them more",
                        "x_sign": 1,
                        "y_side": "lower",
                        "placement": "outside",
                    },
                    {
                        "id": "known_struggles",
                        "label": "Struggling, but Ridge is higher",
                        "x_sign": -1,
                        "y_side": "upper",
                        "placement": "outside",
                    },
                    {
                        "id": "hard_to_read",
                        "label": "Struggling, and Ridge is even lower",
                        "x_sign": -1,
                        "y_side": "lower",
                        "placement": "outside",
                    },
                ],
                "points": points_out,
                "highlights": {
                    "higher": [r["team_id"] for r in higher],
                    "lower": [r["team_id"] for r in lower],
                },
                "footnotes": [],
                "caption": caption,
                "alt": alt,
                "callout": None,
                "meta": {
                    "n_fbs": len(rows),
                    "excluded_team_ids": list(report.excluded_team_ids),
                    "excluded_names": excluded_names,
                    "missing_logos": missing,
                    "logo_slot_px": slot_px,
                    "max_displacement_css_px": max_disp,
                    "worst_logo_overlap_frac": worst_ov,
                    "logo_span_width_frac": span_w,
                    "logo_span_height_frac": span_h,
                    "ridge_method": report.ridge_method,
                    "ridge_lam": report.ridge.lam if report.ridge else None,
                    "ridge_cv_error": report.ridge.cv_error if report.ridge else None,
                    "ridge_h": (
                        report.ridge_rescale.h
                        if report.ridge_method == "net_pts_rescale"
                        and report.ridge_rescale
                        else (report.ridge.h if report.ridge else None)
                    ),
                    "ridge_rmse": (
                        report.ridge.in_sample_rmse if report.ridge else None
                    ),
                    "ridge_holdout_mae": (
                        report.ridge.holdout_mae if report.ridge else None
                    ),
                    "ridge_holdout_failed": report.ridge_holdout_failed,
                    "ridge_centering_offset": (
                        report.ridge_rescale.centering_offset
                        if report.ridge_method == "net_pts_rescale"
                        and report.ridge_rescale
                        else (
                            report.ridge.centering_offset if report.ridge else None
                        )
                    ),
                    "ridge_rescale": (
                        {
                            "slope": report.ridge_rescale.slope,
                            "intercept": report.ridge_rescale.intercept,
                            "h": report.ridge_rescale.h,
                            "residual_mad": report.ridge_rescale.residual_mad,
                            "centering_offset": report.ridge_rescale.centering_offset,
                        }
                        if report.ridge_rescale
                        else None
                    ),
                    "market_lam": report.market.lam,
                    "market_cv_error": report.market.cv_error,
                    "market_h": report.market.h,
                    "market_rmse": report.market.in_sample_rmse,
                    "market_holdout_mae": report.market.holdout_mae,
                    "market_centering_offset": report.market.centering_offset,
                    "n_joint_games": report.n_joint_games,
                    "spot_check": report.spot_check,
                    "contamination_ok": report.contamination_ok,
                    "week_mode": labels.mode,
                },
            }
        )
    return out, report
