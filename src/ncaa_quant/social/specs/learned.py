"""What Ridge learned — biggest posterior moves vs last week's uncertainty."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from ncaa_quant.social.charts.ridge_map import ridge_week_labels
from ncaa_quant.social.copy import trim_caption
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
    format_delta,
    format_delta_pts,
    format_win_lose_by,
    ridge_had_team_perspective,
    short_name,
)
from ncaa_quant.social.specs.net import (
    UNCERTAINTY_FOOTNOTE,
    net_pts,
    net_sd_pts,
    resolve_margin_scale,
)
from ncaa_quant.social.theme import ChartSize

logger = logging.getLogger(__name__)

_FOOTNOTE_MAX = 110


def resolve_learned_week(*, map_week: int, slate_week: int) -> int:
    """Week N of games for \"What Ridge learned in week N\".

    Title contract: after week N's games versus before them.

    Export rows (``build_team_ratings``): one point per ``(team, week)``,
    preferring ``kind == \"postgame\"`` over ``weekly``
    (``export.py`` sorts ``_kind_rank`` then ``tail(1)``). Postgame rows are
    written with ``week=week`` of the observed game
    (``state_space.run_filter`` → ``_state_to_row(..., kind=\"postgame\")``).
    The live open circle is a ``kind=\"weekly\"`` row for the *current*
    slate week (``predict._append_current_week_rating_points``), so when
    ``map_week >= slate_week`` the latest ratings week duplicates the prior
    postgame means — chart week N is then ``map_week - 1``.
    """
    labels = ridge_week_labels(map_week=map_week, slate_week=slate_week, season=0)
    if labels.mode == "entering":
        return map_week - 1
    return map_week


def prior_week_point(entry: dict[str, Any], week: int) -> dict[str, Any] | None:
    """State before week ``week``'s games: row for week−1, else latest week < N.

    A bye the week before leaves no week−1 row; the last earlier postgame
    row is the entering-week-N state.
    """
    prev = week_point(entry, week - 1)
    if prev is not None:
        return prev
    best: dict[str, Any] | None = None
    best_w = -1
    for row in entry.get("weeks") or []:
        w = int(row.get("week") or -1)
        if w < week and w > best_w:
            best = dict(row)
            best_w = w
    return best


def teams_that_played(
    *,
    results: dict[str, Any] | None,
    week: int,
    fbs_team_ids: set[int],
    team_ratings: dict[str, Any],
    school_to_team_id: dict[str, int] | None = None,
) -> tuple[set[int], str]:
    """FBS teams that played in ``week`` (exclude byes).

    Bye representation in ``team_ratings``: **missing week row** (not a carried
    row). Cross-check against ``results`` week games when available.

    ``results_*.json`` often omits ``home_team_id`` / ``away_team_id``; resolve
    schools via ``school_to_team_id`` in that case.
    """
    school_index = school_to_team_id or {}
    if results is not None:
        played: set[int] = set()
        for g in results.get("games") or []:
            if int(g.get("week") or -1) != week:
                continue
            for id_key, name_key in (
                ("home_team_id", "home_team"),
                ("away_team_id", "away_team"),
            ):
                tid: int | None = None
                if g.get(id_key) is not None:
                    tid = int(g[id_key])
                else:
                    school = g.get(name_key)
                    if school is not None and str(school) in school_index:
                        tid = int(school_index[str(school)])
                if tid is not None and tid in fbs_team_ids:
                    played.add(tid)
        if played:
            return played, "results_week_games"

    teams = team_ratings.get("teams") or {}
    played = set()
    for tid in fbs_team_ids:
        entry = teams.get(str(tid))
        if entry and week_point(entry, week) is not None:
            played.add(tid)
    return played, "ratings_week_row_present"


def _resolve_team_id(
    g: dict[str, Any],
    *,
    side: str,
    school_to_team_id: dict[str, int],
) -> int | None:
    id_key = f"{side}_team_id"
    if g.get(id_key) is not None:
        return int(g[id_key])
    school = g.get(f"{side}_team")
    if school is not None and str(school) in school_to_team_id:
        return int(school_to_team_id[str(school)])
    return None


def _games_for_team(
    *,
    results: dict[str, Any] | None,
    week: int,
    team_id: int,
    school_to_team_id: dict[str, int],
) -> list[dict[str, Any]]:
    if results is None:
        return []
    out: list[dict[str, Any]] = []
    for g in results.get("games") or []:
        if int(g.get("week") or -1) != week:
            continue
        home_id = _resolve_team_id(g, side="home", school_to_team_id=school_to_team_id)
        away_id = _resolve_team_id(g, side="away", school_to_team_id=school_to_team_id)
        if team_id in (home_id, away_id):
            out.append(g)
    return out


def _result_labels(
    *,
    g: dict[str, Any],
    team_id: int,
    home_id: int | None,
    away_id: int | None,
    abbrev_by_id: dict[int, str],
) -> tuple[str, str, float | None, int | None, str] | None:
    hp = g.get("home_points")
    ap = g.get("away_points")
    mu = g.get("mu_margin")
    if hp is None or ap is None or mu is None:
        return None
    home_pts = int(hp)
    away_pts = int(ap)
    home_name = str(g.get("home_team") or "Home")
    away_name = str(g.get("away_team") or "Away")
    mu_home = float(mu)
    mu_team = ridge_had_team_perspective(
        mu_home=mu_home, team_id=team_id, home_id=home_id, away_id=away_id
    )
    opp_id: int | None
    opp_school: str
    if team_id == home_id:
        won = home_pts > away_pts
        wl = "W" if won else "L"
        opp_id = int(away_id) if away_id is not None else None
        opp_school = away_name
        opp_s = short_name(int(away_id or 0), away_name, abbrev_by_id)
        result = f"{wl} {home_pts}\u2013{away_pts} vs {opp_s}"
    elif team_id == away_id:
        won = away_pts > home_pts
        wl = "W" if won else "L"
        prep = "at" if not g.get("neutral_site") else "vs"
        opp_id = int(home_id) if home_id is not None else None
        opp_school = home_name
        opp_s = short_name(int(home_id or 0), home_name, abbrev_by_id)
        result = f"{wl} {away_pts}\u2013{home_pts} {prep} {opp_s}"
    else:
        return None
    pregame = (
        "—" if mu_team is None else format_win_lose_by(mu_team, decimals=1)
    )
    return result, pregame, mu_team, opp_id, opp_school


def _nice_tick_step(span: float, *, target: int = 5) -> float:
    """d3-style 1/2/5 step for roughly ``target`` ticks across ``span``."""
    raw = span / max(target - 1, 1)
    if raw <= 0:
        return 1.0
    exp = math.floor(math.log10(raw))
    base = raw / (10**exp)
    if base <= 1:
        nice = 1.0
    elif base <= 2:
        nice = 2.0
    elif base <= 5:
        nice = 5.0
    else:
        nice = 10.0
    return nice * (10**exp)


def _fit_axis_limits(vals: list[float]) -> tuple[float, float, list[float]]:
    """Fit domain to plotted values + 10% padding (include 0); prefer 4–5 ticks."""
    lo = min(vals + [0.0])
    hi = max(vals + [0.0])
    span = max(hi - lo, 5.0)
    pad = max(2.0, span * 0.10)
    x_min = lo - pad
    x_max = hi + pad

    def _ticks_for(step: float) -> tuple[float, float, list[float]]:
        x0 = math.floor(x_min / step) * step
        x1 = math.ceil(x_max / step) * step
        ticks: list[float] = []
        v = x0
        while v <= x1 + 1e-9:
            ticks.append(round(v, 6))
            v += step
        if 0.0 not in ticks:
            ticks = sorted({*ticks, 0.0})
        return float(x0), float(x1), ticks

    best: tuple[float, float, list[float]] | None = None
    for target in (5, 4, 3):
        step = _nice_tick_step(x_max - x_min, target=target)
        # Grow the step until we land in the 4–5 tick window.
        for _ in range(4):
            x0, x1, ticks = _ticks_for(step)
            n = len(ticks)
            if 4 <= n <= 5:
                return x0, x1, ticks
            if n > 5:
                step *= 2.0
                continue
            if best is None or abs(len(best[2]) - 4.5) > abs(n - 4.5):
                best = (x0, x1, ticks)
            break
    if best is not None and 3 <= len(best[2]) <= 6:
        return best
    return _ticks_for(25.0)


def _learned_headline(
    row: dict[str, Any],
    *,
    abbrev_by_id: dict[int, str] | None = None,
) -> str:
    """Win: ``{T}'s win {over|at} {OPP}…``; loss: ``{T}'s loss to {OPP}…``."""
    from ncaa_quant.social.display_names import (
        TeamDisplay,
        fit_headline,
        fit_single_name_headline,
        resolve_team_display,
    )

    result = row.get("result_label") or ""
    parts = result.split()
    abbr = abbrev_by_id or {}
    tid = int(row.get("team_id") or 0)
    school = str(row.get("school") or "")
    team_d = resolve_team_display(tid, school, abbr)
    if len(parts) >= 2 and parts[0] in ("W", "L"):
        wl = parts[0]
        body = result.split(" · Ridge", 1)[0]
        was_away = " at " in body
        if " vs " in body:
            opp_label = body.split(" vs ", 1)[1].strip()
        elif " at " in body:
            opp_label = body.split(" at ", 1)[1].strip()
        else:
            opp_label = ""
        opp_id = int(row.get("opp_team_id") or 0)
        opp_school = str(row.get("opp_school") or opp_label)
        if opp_id:
            opp_d = resolve_team_display(opp_id, opp_school, abbr)
        else:
            opp_d = TeamDisplay(
                team_id=0,
                school=opp_school,
                abbreviation=opp_label,
                display_name=opp_school,
                display_short=opp_label,
            )
        if opp_label or opp_id:
            if wl == "W":
                prep = "at" if was_away else "over"

                def _win(t: str, o: str) -> str:
                    return f"{t}'s win {prep} {o} moved Ridge most"

                return fit_headline(_win, team_d, opp_d)
            return fit_headline(
                lambda t, o: f"{t}'s loss to {o} moved Ridge most",
                team_d,
                opp_d,
            )
    return fit_single_name_headline(
        lambda t: f"{t} moved Ridge most",
        team_d,
    )


def build_learned_specs(
    *,
    team_ratings: dict[str, Any],
    results: dict[str, Any] | None,
    map_week: int,
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
    school_to_team_id: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """Return specs (or None if skipped) and a report section."""
    del site_url
    learned_week = resolve_learned_week(map_week=map_week, slate_week=slate_week)
    school_index = school_to_team_id or {}
    report: dict[str, Any] = {
        "learned_week": learned_week,
        "movers_week": learned_week,
        "filename_alias": {
            "current": "movers_w{N}_*",
            "previous": "learned_w{N}_*",
            "cli_alias": "learned → movers (one-run alias)",
        },
        "compare": {
            "after_week": learned_week,
            "after_meaning": "state after week N games (preferred postgame row)",
            "before_meaning": (
                "state before week N games (week N−1 row, or latest week < N "
                "when N−1 is a bye / missing)"
            ),
        },
        "map_week": map_week,
        "bye_representation": "missing_week_row",
        "skipped": False,
        "result_join_excluded": [],
    }
    if learned_week <= 1:
        report["skipped"] = True
        report["skip_reason"] = f"week {learned_week} has no prior week in-season"
        return None, report

    played, play_source = teams_that_played(
        results=results,
        week=learned_week,
        fbs_team_ids=fbs_team_ids,
        team_ratings=team_ratings,
        school_to_team_id=school_to_team_id,
    )
    report["played_source"] = play_source
    report["n_played"] = len(played)

    scale = resolve_margin_scale()
    teams = team_ratings.get("teams") or {}
    moves: list[dict[str, Any]] = []
    excluded_bye = 0
    excluded_missing = 0
    for tid in sorted(fbs_team_ids):
        if tid not in played:
            excluded_bye += 1
            continue
        entry = teams.get(str(tid))
        if not entry:
            excluded_missing += 1
            continue
        cur = week_point(entry, learned_week)
        prev = prior_week_point(entry, learned_week)
        if cur is None or prev is None:
            excluded_missing += 1
            continue
        need = ("off_epa", "def_epa", "off_sd", "def_sd")
        if any(cur.get(k) is None or prev.get(k) is None for k in need):
            excluded_missing += 1
            continue
        if int(prev["week"]) < 1:
            excluded_missing += 1
            continue
        net_n = net_pts(
            off_epa=float(cur["off_epa"]),
            def_epa=float(cur["def_epa"]),
            margin_scale=scale.value,
        )
        net_p = net_pts(
            off_epa=float(prev["off_epa"]),
            def_epa=float(prev["def_epa"]),
            margin_scale=scale.value,
        )
        sd_n = net_sd_pts(
            off_sd=float(cur["off_sd"]),
            def_sd=float(cur["def_sd"]),
            margin_scale=scale.value,
        )
        sd_p = net_sd_pts(
            off_sd=float(prev["off_sd"]),
            def_sd=float(prev["def_sd"]),
            margin_scale=scale.value,
        )
        delta = net_n - net_p
        move_sd = abs(delta) / sd_p if sd_p > 0 else 0.0
        school = str(entry.get("school") or f"Team {tid}")
        moves.append(
            {
                "team_id": tid,
                "school": school,
                "abbreviation": abbrev_by_id.get(tid, school[:4].upper()),
                "net_prev": net_p,
                "net_cur": net_n,
                "sd_prev": sd_p,
                "sd_cur": sd_n,
                "delta_net": delta,
                "move_sd": move_sd,
                "delta_sd": sd_n - sd_p,
                "band_prev_lo": net_p - sd_p,
                "band_prev_hi": net_p + sd_p,
                "band_cur_lo": net_n - sd_n,
                "band_cur_hi": net_n + sd_n,
                "after_week": learned_week,
                "before_week": int(prev["week"]),
            }
        )

    report["excluded_bye"] = excluded_bye
    report["excluded_missing"] = excluded_missing
    if not moves:
        report["skipped"] = True
        report["skip_reason"] = "no eligible teams with prior-week ratings"
        return None, report

    eligible: list[dict[str, Any]] = []
    for r in moves:
        games = _games_for_team(
            results=results,
            week=learned_week,
            team_id=r["team_id"],
            school_to_team_id=school_index,
        )
        if len(games) != 1:
            reason = "no_game" if not games else "multiple_games"
            report["result_join_excluded"].append(
                {"team_id": r["team_id"], "school": r["school"], "reason": reason}
            )
            logger.info(
                "learned chart: exclude team %s from chart (%s)",
                r["team_id"],
                reason,
            )
            continue
        g = games[0]
        home_id = _resolve_team_id(g, side="home", school_to_team_id=school_index)
        away_id = _resolve_team_id(g, side="away", school_to_team_id=school_index)
        labels = _result_labels(
            g=g,
            team_id=r["team_id"],
            home_id=home_id,
            away_id=away_id,
            abbrev_by_id=abbrev_by_id,
        )
        if labels is None:
            report["result_join_excluded"].append(
                {
                    "team_id": r["team_id"],
                    "school": r["school"],
                    "reason": "missing_scores_or_mu",
                }
            )
            continue
        result, pregame, mu_team, opp_id, opp_school = labels
        r["result_label"] = result
        r["pregame_label"] = pregame
        r["mu_team"] = mu_team
        r["opp_team_id"] = opp_id
        r["opp_school"] = opp_school
        body = result.split(" · Ridge", 1)[0]
        r["opp"] = body.split(" vs ", 1)[-1] if " vs " in body else (
            body.split(" at ", 1)[-1] if " at " in body else ""
        )
        r["school_short"] = short_name(r["team_id"], r["school"], abbrev_by_id)
        eligible.append(r)

    if not eligible:
        report["skipped"] = True
        report["skip_reason"] = "no eligible teams after results join"
        return None, report

    eligible.sort(key=lambda r: (-r["move_sd"], r["team_id"]))
    top10 = eligible[:10]
    clarity = sorted(eligible, key=lambda r: (r["delta_sd"], r["team_id"]))[:3]

    report["top10"] = [
        {
            "team_id": r["team_id"],
            "school": r["school"],
            "move_sd": r["move_sd"],
            "delta_net": r["delta_net"],
        }
        for r in top10
    ]
    report["clearest"] = [{"team_id": r["team_id"], "delta_sd": r["delta_sd"]} for r in clarity]
    ridge_had_audit: list[dict[str, Any]] = []
    for r in top10:
        games = _games_for_team(
            results=results,
            week=learned_week,
            team_id=r["team_id"],
            school_to_team_id=school_index,
        )
        g0 = games[0] if len(games) == 1 else {}
        home_id = _resolve_team_id(g0, side="home", school_to_team_id=school_index)
        away_id = _resolve_team_id(g0, side="away", school_to_team_id=school_index)
        mu_home = g0.get("mu_margin")
        ridge_had_audit.append(
            {
                "team_id": r["team_id"],
                "school": r["school"],
                "home_team": g0.get("home_team"),
                "away_team": g0.get("away_team"),
                "home_points": g0.get("home_points"),
                "away_points": g0.get("away_points"),
                "mu_margin_home": mu_home,
                "mu_team_perspective": r.get("mu_team"),
            }
        )
    report["ridge_had_audit"] = ridge_had_audit

    # Fit domain to plotted before/after dots only (no ±1 SD bars).
    vals: list[float] = []
    for r in top10:
        vals.extend([r["net_prev"], r["net_cur"]])
    x_min, x_max, x_ticks = _fit_axis_limits(vals)

    top = top10[0]
    clear0 = clarity[0]
    headline = f"Biggest movers after week {learned_week}"
    dek = (
        f"Teams whose Ridge rating changed most after week {learned_week} games"
    )
    caption = trim_caption(
        f"Ridge changed its mind most on {top['abbreviation']} "
        f"({format_delta_pts(top['delta_net'])} move, {top['move_sd']:.1f} SD). "
        f"Biggest new clarity: {clear0['abbreviation']}."
    )
    alt = (
        f"Biggest Ridge rating moves in week {learned_week} {season}, "
        f"sized against last week's uncertainty. {UNCERTAINTY_FOOTNOTE}"
    )
    # Footnote lives in alt — 10 dense rows need the canvas for the chart body.
    footnotes: list[str] = []

    moved_up = sorted(
        [r for r in top10 if r["delta_net"] > 0],
        key=lambda r: (-r["move_sd"], r["team_id"]),
    )
    moved_down = sorted(
        [r for r in top10 if r["delta_net"] < 0],
        key=lambda r: (-r["move_sd"], r["team_id"]),
    )
    groups: list[dict[str, Any]] = []
    if moved_up:
        groups.append({"title": "Up", "rows": moved_up})
    if moved_down:
        groups.append({"title": "Down", "rows": moved_down})
    flat_rows = moved_up + moved_down
    if not groups:
        groups = [{"title": None, "rows": top10}]
        flat_rows = top10

    target_area = LOGO_TARGET_OPAQUE_AREA.get(
        "movers", LOGO_TARGET_OPAQUE_AREA["learned"]
    )
    logo_slot = SLOT_PX.get("movers", SLOT_PX["learned"])
    scales: dict[int, float] = {}

    specs: list[dict[str, Any]] = []
    for size in sizes:
        name, width, height = size_pixels(size)

        def _row_out(r: dict[str, Any]) -> dict[str, Any]:
            path = logo_path_for(r["team_id"], logo_dir=logo_dir, theme=theme)  # type: ignore[arg-type]
            disp_w = disp_h = None
            if path is not None:
                ld = logo_display_size(
                    path,
                    team_id=r["team_id"],
                    target_opaque_area=target_area,
                    slot_px=logo_slot,
                )
                disp_w, disp_h = ld.width, ld.height
                scales[r["team_id"]] = ld.scale
            short = r.get("school_short") or r["abbreviation"]
            expected = r.get("pregame_label") or "—"
            return {
                **r,
                "delta_net_label": format_delta(r["delta_net"]),
                "result_col": r.get("result_label") or "",
                "expected_col": expected,
                "school_short": short,
                "short_name": short,
                "logo_path": str(path) if path else None,
                "logo_display_w": disp_w,
                "logo_display_h": disp_h,
            }

        groups_out = [
            {"title": g["title"], "rows": [_row_out(r) for r in g["rows"]]} for g in groups
        ]
        rows_out = [_row_out(r) for r in flat_rows]
        missing = [
            {"team_id": r["team_id"], "school": r["school"]}
            for r in rows_out
            if r.get("logo_path") is None
        ]

        specs.append(
            {
                "chart": "movers",
                "schema": "soc2-spec/1",
                "frame": frame_dict(
                    season=season,
                    week=learned_week,
                    week_stamp=f"Week {learned_week} · {season}",
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
                ),
                "axes": {
                    "x_label": "Ridge rating (pts vs. average FBS team)",
                    "x_min": x_min,
                    "x_max": x_max,
                    "x_zero": 0.0,
                    "x_ticks": x_ticks,
                    "zero_midline_label": "avg",
                },
                "columns": [
                    {"id": "team", "header": "Team", "width_frac": 0.18},
                    {
                        "id": "plot",
                        "header": "Ridge rating: before ○ → after ●",
                        "width_frac": 0.32,
                    },
                    {"id": "delta", "header": "Rating change", "width_frac": 0.14},
                    {"id": "result", "header": "Result", "width_frac": 0.20},
                    {
                        "id": "expected",
                        "header": "Ridge expected",
                        "width_frac": 0.16,
                    },
                ],
                "groups": groups_out,
                "rows": rows_out,
                "footnotes": footnotes,
                "caption": caption,
                "alt": alt,
                "meta": {
                    "margin_scale": scale.value,
                    "margin_scale_source": scale.source,
                    "missing_logos": missing,
                    "bye_representation": "missing_week_row",
                    "played_source": play_source,
                    "result_join_excluded": report["result_join_excluded"],
                    "filename_alias": {
                        "current": "movers_w{N}_*",
                        "previous": "learned_w{N}_*",
                        "cli_alias": "learned → movers",
                    },
                },
            }
        )

    if scales:
        store_logo_scales_in_manifest(logo_dir, scales)
    return specs, report
