"""Extra condition columns for angles batch 5 (research only).

Attaches body-clock / service-academy / lookahead / revenge / season-record /
returning-production / weather columns that are knowable at the decision
as_of. Totals close lines are attached separately via :func:`attach_totals`.

No production feature or model path is touched.
"""

from __future__ import annotations

from typing import Any, Final
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.features.builders.situational import (
    team_home_venues,
    timezone_offset_hours,
    tz_crossed_signed,
)
from ncaa_quant.research.angles import (
    fbs_ids_by_season,
    game_end_utc,
    kickoff_utc,
)
from ncaa_quant.research.angles_context import net_epa_as_of, rank_fbs_ratings
from ncaa_quant.research.angles_rankings import attach_ap_ranks_at_kickoff

SERVICE_ACADEMY_SCHOOLS: Final[frozenset[str]] = frozenset({"Army", "Navy", "Air Force"})
WIND_MS_15MPH: Final[float] = 6.7


def service_academy_ids(teams: pd.DataFrame) -> set[int]:
    """Team ids whose school is Army / Navy / Air Force (any season row)."""
    if teams.empty or "school" not in teams.columns:
        return set()
    mask = teams["school"].astype(str).isin(SERVICE_ACADEMY_SCHOOLS)
    return {int(t) for t in teams.loc[mask, "team_id"].dropna().unique()}


def attach_totals_close(
    rows: pd.DataFrame,
    lines: pd.DataFrame,
) -> pd.DataFrame:
    """Attach CFBD close total (median across books) as ``total_close_cfbd``."""
    out = rows.copy()
    out["total_close_cfbd"] = np.nan
    if lines is None or lines.empty or "total" not in lines.columns:
        return out
    work = lines.copy()
    if "line_type" in work.columns:
        close = work.loc[work["line_type"].astype(str).str.casefold() == "close"]
        work = close if not close.empty else work
    work["game_id"] = pd.to_numeric(work["game_id"], errors="coerce")
    work = work.loc[work["game_id"].notna()].copy()
    if work.empty:
        return out
    work["game_id"] = work["game_id"].astype(int)
    work["total"] = pd.to_numeric(work["total"], errors="coerce")
    med = work.groupby("game_id")["total"].median()
    out["total_close_cfbd"] = out["game_id"].map(med).astype(float)
    return out


def attach_body_clock(
    rows: pd.DataFrame,
    games: pd.DataFrame,
    venues: pd.DataFrame,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    """Travel / kickoff-local columns for H5.

    ``team_tz_crossed`` is signed hours (destination − origin home TZ) for the
    named team traveling to this game's venue (0 if home / neutral / unknown).
    ``team_home_kickoff_hour`` is kickoff clock hour in the team's modal home TZ.
    """
    out = rows.copy()
    out["team_tz_crossed"] = np.nan
    out["team_home_kickoff_hour"] = np.nan
    out["opp_tz_crossed"] = np.nan
    out["opp_home_kickoff_hour"] = np.nan
    out["opp_body_clock_east_early"] = False
    out["opp_eastern_pacific_late"] = False
    out["game_venue_timezone"] = pd.NA
    out["game_dome"] = False
    if games.empty or venues.empty:
        return out

    home_venues = team_home_venues(games, venues)
    home_tz = {
        int(r.team_id): str(r.timezone)
        for r in home_venues.itertuples(index=False)
        if getattr(r, "timezone", None) not in (None, "")
        and not (isinstance(r.timezone, float) and np.isnan(r.timezone))
    }
    venue_tz: dict[int, str] = {}
    venue_dome: dict[int, bool] = {}
    for r in venues.drop_duplicates("venue_id").itertuples(index=False):
        vid = int(r.venue_id)
        tz = getattr(r, "timezone", None)
        if tz not in (None, "") and not (isinstance(tz, float) and np.isnan(tz)):
            venue_tz[vid] = str(tz)
        dome = getattr(r, "dome", False)
        if dome is None or (isinstance(dome, float) and np.isnan(dome)) or pd.isna(dome):
            venue_dome[vid] = False
        else:
            venue_dome[vid] = bool(dome)

    gmap = games.drop_duplicates("game_id").set_index("game_id")
    kick = kickoff_utc(games.drop_duplicates("game_id")).to_numpy()
    gid_to_kick = dict(zip(games["game_id"].astype(int), kick, strict=False))
    gid_to_venue = {}
    if "venue_id" in gmap.columns:
        for gid, vid in gmap["venue_id"].items():
            if pd.notna(vid):
                gid_to_venue[int(gid)] = int(vid)

    team_tz_c: list[float] = []
    team_hour: list[float] = []
    opp_tz_c: list[float] = []
    opp_hour: list[float] = []
    opp_east: list[bool] = []
    opp_late: list[bool] = []
    g_tzs: list[Any] = []
    g_domes: list[bool] = []

    for r in out.itertuples(index=False):
        gid = int(r.game_id)
        tid = int(r.team_id)
        oid = int(r.opp_id)
        kick_ts: Any = gid_to_kick.get(gid)
        game_vid = gid_to_venue.get(gid)
        gtz: str | None = venue_tz.get(game_vid) if game_vid is not None else None
        g_tzs.append(gtz if gtz else pd.NA)
        g_domes.append(venue_dome.get(game_vid, False) if game_vid is not None else False)

        def _side(
            team: int,
            is_home_side: bool,
            *,
            kick: Any = kick_ts,
            venue_tz_name: str | None = gtz,
            neutral: bool = bool(getattr(r, "neutral_site", False)),
        ) -> tuple[float, float]:
            origin = home_tz.get(team)
            hour = float("nan")
            crossed = float("nan")
            if kick is None or origin is None:
                return crossed, hour
            try:
                local = pd.Timestamp(kick).tz_convert(ZoneInfo(origin))
                hour = float(local.hour) + float(local.minute) / 60.0
            except Exception:
                hour = float("nan")
            if venue_tz_name is None or is_home_side or neutral:
                crossed = 0.0
            else:
                try:
                    crossed = float(
                        tz_crossed_signed(
                            origin, venue_tz_name, at=pd.Timestamp(kick).to_pydatetime()
                        )
                    )
                except Exception:
                    crossed = float("nan")
            return crossed, hour

        is_home = bool(getattr(r, "is_home", False)) and not bool(
            getattr(r, "neutral_site", False)
        )
        tc, th = _side(tid, is_home)
        oc, oh = _side(oid, (not is_home) and not bool(getattr(r, "neutral_site", False)))
        team_tz_c.append(tc)
        team_hour.append(th)
        opp_tz_c.append(oc)
        opp_hour.append(oh)
        east_early = bool(np.isfinite(oc) and oc >= 2.0 and np.isfinite(oh) and oh < 13.0)
        opp_east.append(east_early)
        # Eastern team (UTC offset around -5/-4) playing in Pacific (around -8/-7)
        # with kickoff >= 22:30 ET.
        late = False
        if gtz and oid in home_tz and kick_ts is not None:
            try:
                o_off = timezone_offset_hours(home_tz[oid], pd.Timestamp(kick_ts).to_pydatetime())
                g_off = timezone_offset_hours(gtz, pd.Timestamp(kick_ts).to_pydatetime())
                et_local = pd.Timestamp(kick_ts).tz_convert(ZoneInfo("America/New_York"))
                et_hour = float(et_local.hour) + float(et_local.minute) / 60.0
                # Origin eastern-ish (offset > -6), destination pacific-ish (offset <= -7)
                late = bool(o_off > -6.0 and g_off <= -7.0 and et_hour >= 22.5)
            except Exception:
                late = False
        opp_late.append(late)

    out["team_tz_crossed"] = team_tz_c
    out["team_home_kickoff_hour"] = team_hour
    out["opp_tz_crossed"] = opp_tz_c
    out["opp_home_kickoff_hour"] = opp_hour
    out["opp_body_clock_east_early"] = opp_east
    out["opp_eastern_pacific_late"] = opp_late
    out["game_venue_timezone"] = g_tzs
    out["game_dome"] = g_domes
    return out


def attach_service_academy_flags(
    rows: pd.DataFrame,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    """``team_last_opp_service_academy`` / ``opp_is_service_academy``."""
    out = rows.copy()
    ids = service_academy_ids(teams)
    out["opp_is_service_academy"] = out["opp_id"].astype(int).isin(ids)
    # last opponent id may already be on rows as team_last from context; else derive
    if "team_last_opp_id" in out.columns:
        last_opp = pd.to_numeric(out["team_last_opp_id"], errors="coerce")
    elif "opp_id" in out.columns and "team_prev_game_id" in out.columns:
        # rebuild from prev game if side stats attached later
        last_opp = pd.Series(np.nan, index=out.index)
    else:
        last_opp = pd.Series(np.nan, index=out.index)
    out["team_last_opp_service_academy"] = last_opp.isin(ids) & last_opp.notna()
    if "opp_last_opp_id" in out.columns:
        opp_last = pd.to_numeric(out["opp_last_opp_id"], errors="coerce")
        out["opp_last_opp_service_academy"] = opp_last.isin(ids) & opp_last.notna()
    else:
        out["opp_last_opp_service_academy"] = False
    return out


def attach_lookahead(
    rows: pd.DataFrame,
    games: pd.DataFrame,
    teams: pd.DataFrame,
    filter_history: pd.DataFrame | None,
) -> pd.DataFrame:
    """Next-opponent Ridge rank and lookahead flag (top-25 or rivalry)."""
    out = rows.copy()
    out["team_next_opp_id"] = pd.array([pd.NA] * len(out), dtype="Int64")
    out["team_next_ridge_rank"] = np.nan
    out["team_lookahead"] = False
    out["team_last_lookahead"] = False
    if games.empty:
        return out

    work = games.copy()
    work["kickoff"] = kickoff_utc(work)
    work = work.sort_values(["season", "kickoff", "game_id"], kind="mergesort")
    home_side = work.rename(columns={"home_team_id": "team_id"})[
        ["game_id", "season", "kickoff", "team_id", "away_team_id"]
    ].rename(columns={"away_team_id": "next_opp_candidate"})
    away_side = work.rename(columns={"away_team_id": "team_id"})[
        ["game_id", "season", "kickoff", "team_id", "home_team_id"]
    ].rename(columns={"home_team_id": "next_opp_candidate"})
    long = pd.concat([home_side, away_side], ignore_index=True)
    long = long.sort_values(["team_id", "season", "kickoff", "game_id"], kind="mergesort")
    long["next_opp_id"] = long.groupby(["team_id", "season"], sort=False)[
        "next_opp_candidate"
    ].shift(-1)
    next_map = long.drop_duplicates(["game_id", "team_id"])[
        ["game_id", "team_id", "next_opp_id"]
    ]
    out = out.merge(next_map, on=["game_id", "team_id"], how="left")
    out["team_next_opp_id"] = pd.to_numeric(out["next_opp_id"], errors="coerce").astype("Int64")
    out = out.drop(columns=["next_opp_id"], errors="ignore")

    # Ridge ranks at this game's as_of for next opponent
    if filter_history is not None and not filter_history.empty and "as_of" in out.columns:
        keys = out.loc[
            out["team_next_opp_id"].notna(), ["team_next_opp_id", "season", "as_of"]
        ].rename(columns={"team_next_opp_id": "team_id"})
        keys = keys.drop_duplicates()
        if not keys.empty:
            fbs = fbs_ids_by_season(teams)
            rated = rank_fbs_ratings(net_epa_as_of(keys, filter_history), fbs)
            rated = rated.rename(
                columns={"team_id": "team_next_opp_id", "ridge_rank": "team_next_ridge_rank"}
            )
            out = out.drop(columns=["team_next_ridge_rank"], errors="ignore")
            out = out.merge(
                rated[["team_next_opp_id", "season", "as_of", "team_next_ridge_rank"]],
                on=["team_next_opp_id", "season", "as_of"],
                how="left",
            )

    rivalry = (
        out["rivalry_flag"].fillna(False).astype(bool)
        if "rivalry_flag" in out.columns
        else pd.Series(False, index=out.index)
    )
    next_top = pd.to_numeric(out["team_next_ridge_rank"], errors="coerce").le(25.0)
    out["team_lookahead"] = rivalry | (next_top.fillna(False) & out["team_next_opp_id"].notna())

    # Opponent's lookahead = the other side's team_lookahead on the same game.
    flip = out[["game_id", "team_id", "team_lookahead"]].rename(
        columns={"team_id": "opp_id", "team_lookahead": "opp_lookahead"}
    )
    out = out.drop(columns=["opp_lookahead"], errors="ignore")
    out = out.merge(flip, on=["game_id", "opp_id"], how="left")
    out["opp_lookahead"] = out["opp_lookahead"].fillna(False).astype(bool)

    # Sandwich: previous same-season game also had lookahead into its then-next.
    out = out.sort_values(["team_id", "season", "kickoff", "game_id"], kind="mergesort")
    out["team_last_lookahead"] = (
        out.groupby(["team_id", "season"], sort=False)["team_lookahead"].shift(1).fillna(False)
    )
    last_flip = out[["game_id", "team_id", "team_last_lookahead"]].rename(
        columns={"team_id": "opp_id", "team_last_lookahead": "opp_last_lookahead"}
    )
    out = out.drop(columns=["opp_last_lookahead"], errors="ignore")
    out = out.merge(last_flip, on=["game_id", "opp_id"], how="left")
    out["opp_last_lookahead"] = out["opp_last_lookahead"].fillna(False).astype(bool)

    # Opponent next-opponent Ridge rank (for conditions that filter on opp).
    nr = out[["game_id", "team_id", "team_next_ridge_rank"]].rename(
        columns={"team_id": "opp_id", "team_next_ridge_rank": "opp_next_ridge_rank"}
    )
    out = out.drop(columns=["opp_next_ridge_rank"], errors="ignore")
    out = out.merge(nr, on=["game_id", "opp_id"], how="left")
    return out


def attach_revenge(
    rows: pd.DataFrame,
    games: pd.DataFrame,
) -> pd.DataFrame:
    """Prior-season rematch margin vs this opponent (named team's side)."""
    out = rows.copy()
    out["team_prior_season_vs_opp_margin"] = np.nan
    out["team_revenge_loss_14"] = False
    out["team_rematch_prior_season"] = False
    if games.empty:
        return out
    g = games.copy()
    g["kickoff"] = kickoff_utc(g)
    hp = pd.to_numeric(g["home_points"], errors="coerce")
    ap = pd.to_numeric(g["away_points"], errors="coerce")
    g["margin_home"] = hp - ap
    # one row per (season, team, opp) taking last meeting that season
    sides = []
    for is_home in (True, False):
        side = pd.DataFrame(
            {
                "season": g["season"].astype(int),
                "team_id": (g["home_team_id"] if is_home else g["away_team_id"]).astype(int),
                "opp_id": (g["away_team_id"] if is_home else g["home_team_id"]).astype(int),
                "margin": (g["margin_home"] if is_home else -g["margin_home"]).to_numpy(
                    dtype=float
                ),
                "kickoff": g["kickoff"].to_numpy(),
            }
        )
        sides.append(side)
    hist = pd.concat(sides, ignore_index=True)
    hist = hist.sort_values(["team_id", "opp_id", "season", "kickoff"], kind="mergesort")
    last = hist.drop_duplicates(["team_id", "opp_id", "season"], keep="last")
    last = last.rename(
        columns={"season": "prior_season", "margin": "team_prior_season_vs_opp_margin"}
    )
    last["prior_season"] = last["prior_season"].astype(int)
    work = out.drop(
        columns=[
            "team_prior_season_vs_opp_margin",
            "team_revenge_loss_14",
            "team_rematch_prior_season",
            "prior_season",
        ],
        errors="ignore",
    ).copy()
    work["prior_season"] = work["season"].astype(int) - 1
    work = work.merge(
        last[["team_id", "opp_id", "prior_season", "team_prior_season_vs_opp_margin"]],
        on=["team_id", "opp_id", "prior_season"],
        how="left",
    )
    m = pd.to_numeric(work["team_prior_season_vs_opp_margin"], errors="coerce")
    work["team_rematch_prior_season"] = m.notna()
    work["team_revenge_loss_14"] = m.le(-14.0).fillna(False)
    return work.drop(columns=["prior_season"], errors="ignore")


def attach_season_records(
    rows: pd.DataFrame,
    games: pd.DataFrame,
) -> pd.DataFrame:
    """Season wins/losses entering this game (completed games with end < as_of)."""
    out = rows.copy()
    out["team_season_wins"] = np.nan
    out["team_season_losses"] = np.nan
    out["opp_season_wins"] = np.nan
    out["opp_season_losses"] = np.nan
    out["season_max_week"] = np.nan
    out["weeks_from_season_end"] = np.nan
    out["opp_bowl_or_spoiler"] = False
    if games.empty or "as_of" not in out.columns:
        return out
    g = games.copy()
    g["kickoff"] = kickoff_utc(g)
    g["game_end"] = game_end_utc(g)
    hp = pd.to_numeric(g["home_points"], errors="coerce")
    ap = pd.to_numeric(g["away_points"], errors="coerce")
    g["margin_home"] = hp - ap
    max_week = g.groupby("season")["week"].max().astype(int)
    out["season_max_week"] = out["season"].map(max_week).astype(float)
    out["weeks_from_season_end"] = out["season_max_week"] - out["week"].astype(float)

    long = pd.concat(
        [
            pd.DataFrame(
                {
                    "game_id": g["game_id"].astype(int),
                    "season": g["season"].astype(int),
                    "team_id": g["home_team_id"].astype(int),
                    "won": (g["margin_home"] > 0).to_numpy(),
                    "lost": (g["margin_home"] < 0).to_numpy(),
                    "game_end": g["game_end"].to_numpy(),
                    "completed": g["completed"].fillna(False).astype(bool).to_numpy(),
                }
            ),
            pd.DataFrame(
                {
                    "game_id": g["game_id"].astype(int),
                    "season": g["season"].astype(int),
                    "team_id": g["away_team_id"].astype(int),
                    "won": (g["margin_home"] < 0).to_numpy(),
                    "lost": (g["margin_home"] > 0).to_numpy(),
                    "game_end": g["game_end"].to_numpy(),
                    "completed": g["completed"].fillna(False).astype(bool).to_numpy(),
                }
            ),
        ],
        ignore_index=True,
    )
    long = long.loc[long["completed"]].copy()

    tw: list[float] = []
    tl: list[float] = []
    ow: list[float] = []
    ol: list[float] = []
    for r in out.itertuples(index=False):
        as_of = pd.Timestamp(r.as_of)
        season = int(r.season)
        tid = int(r.team_id)
        oid = int(r.opp_id)
        prior = long.loc[
            (long["season"] == season) & (pd.to_datetime(long["game_end"], utc=True) < as_of)
        ]
        tsub = prior.loc[prior["team_id"] == tid]
        osub = prior.loc[prior["team_id"] == oid]
        tw.append(float(tsub["won"].sum()) if not tsub.empty else 0.0)
        tl.append(float(tsub["lost"].sum()) if not tsub.empty else 0.0)
        ow.append(float(osub["won"].sum()) if not osub.empty else 0.0)
        ol.append(float(osub["lost"].sum()) if not osub.empty else 0.0)
    out["team_season_wins"] = tw
    out["team_season_losses"] = tl
    out["opp_season_wins"] = ow
    out["opp_season_losses"] = ol
    out["opp_bowl_or_spoiler"] = (
        pd.Series(ol).ge(7.0) | pd.Series(ow).ge(6.0)
    ).to_numpy()
    return out


def attach_returning_production(
    rows: pd.DataFrame,
    returning: pd.DataFrame,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    """Preseason returning-production overall_pct and FBS percentile."""
    out = rows.copy()
    out["team_returning_pct"] = np.nan
    out["team_returning_pctile"] = np.nan
    if returning is None or returning.empty:
        return out
    work = returning.copy()
    work["team_id"] = pd.to_numeric(work["team_id"], errors="coerce")
    work["season"] = pd.to_numeric(work["season"], errors="coerce")
    work["overall_pct"] = pd.to_numeric(work.get("overall_pct"), errors="coerce")
    work = work.loc[work["team_id"].notna() & work["season"].notna()].copy()
    work["team_id"] = work["team_id"].astype(int)
    work["season"] = work["season"].astype(int)
    fbs = fbs_ids_by_season(teams)
    pctiles: list[dict[str, Any]] = []
    for season, grp in work.groupby("season", sort=False):
        fbs_ids = fbs.get(int(season), set())
        sub = grp.loc[grp["team_id"].isin(fbs_ids) & grp["overall_pct"].notna()].copy()
        if sub.empty:
            continue
        sub["team_returning_pctile"] = sub["overall_pct"].rank(pct=True, method="average")
        sub["team_returning_pct"] = sub["overall_pct"]
        pctiles.append(sub[["team_id", "season", "team_returning_pct", "team_returning_pctile"]])
    if not pctiles:
        return out
    ranked = pd.concat(pctiles, ignore_index=True)
    out = out.drop(columns=["team_returning_pct", "team_returning_pctile"], errors="ignore")
    out = out.merge(ranked, on=["team_id", "season"], how="left")
    return out


def attach_weather_wind(
    rows: pd.DataFrame,
    weather: pd.DataFrame,
) -> pd.DataFrame:
    """Attach wind_speed_ms; prefer forecast as_of, else actual (labeled)."""
    out = rows.copy()
    out["wind_speed_ms"] = np.nan
    out["wind_obs_kind"] = pd.NA
    out["wind_is_forecast"] = False
    out["outdoor"] = ~out.get("game_dome", pd.Series(False, index=out.index)).fillna(False)
    if weather is None or weather.empty:
        return out
    w = weather.copy()
    w["game_id"] = pd.to_numeric(w["game_id"], errors="coerce")
    w = w.loc[w["game_id"].notna()].copy()
    w["game_id"] = w["game_id"].astype(int)
    w["wind_speed_ms"] = pd.to_numeric(w["wind_speed_ms"], errors="coerce")
    w["obs_kind"] = w["obs_kind"].astype(str) if "obs_kind" in w.columns else "actual"
    # Prefer forecast rows when present; else actual.
    chosen: dict[int, tuple[float, str]] = {}
    for gid, grp in w.groupby("game_id", sort=False):
        pref = grp.loc[grp["obs_kind"].str.casefold().isin({"forecast", "predicted", "model"})]
        use = pref if not pref.empty else grp
        use = use.loc[use["wind_speed_ms"].notna()]
        if use.empty:
            continue
        # latest captured_at if available
        if "captured_at" in use.columns:
            use = use.sort_values("captured_at")
        row = use.iloc[-1]
        chosen[int(gid)] = (float(row["wind_speed_ms"]), str(row["obs_kind"]))
    out["wind_speed_ms"] = out["game_id"].map(lambda g: chosen.get(int(g), (np.nan, None))[0])
    out["wind_obs_kind"] = out["game_id"].map(lambda g: chosen.get(int(g), (np.nan, None))[1])
    out["wind_is_forecast"] = (
        out["wind_obs_kind"].astype(str).str.casefold().isin({"forecast", "predicted", "model"})
    )
    if "game_dome" in out.columns:
        out["outdoor"] = ~out["game_dome"].fillna(False).astype(bool)
    return out


def attach_this_game_ap(
    rows: pd.DataFrame,
    games: pd.DataFrame,
    rankings: pd.DataFrame | None,
) -> pd.DataFrame:
    """AP ranks at kickoff for this game's teams."""
    out = rows.copy()
    out["team_ap_rank"] = np.nan
    out["opp_ap_rank"] = np.nan
    out["team_ap_ranked"] = False
    out["opp_ap_unranked"] = False
    if games.empty:
        return out
    ap = attach_ap_ranks_at_kickoff(games, rankings if rankings is not None else pd.DataFrame())
    g = games.drop_duplicates("game_id").merge(ap, on="game_id", how="left")
    home_rank = g.set_index("game_id")["home_ap_rank"] if "home_ap_rank" in g.columns else None
    away_rank = g.set_index("game_id")["away_ap_rank"] if "away_ap_rank" in g.columns else None
    if home_rank is None:
        return out
    team_ranks: list[float] = []
    opp_ranks: list[float] = []
    for r in out.itertuples(index=False):
        gid = int(r.game_id)
        if bool(getattr(r, "is_home", False)):
            tr = home_rank.get(gid, np.nan)
            or_ = away_rank.get(gid, np.nan) if away_rank is not None else np.nan
        else:
            tr = away_rank.get(gid, np.nan) if away_rank is not None else np.nan
            or_ = home_rank.get(gid, np.nan)
        team_ranks.append(float(tr) if pd.notna(tr) else float("nan"))
        opp_ranks.append(float(or_) if pd.notna(or_) else float("nan"))
    out["team_ap_rank"] = team_ranks
    out["opp_ap_rank"] = opp_ranks
    tr = pd.Series(team_ranks)
    orank = pd.Series(opp_ranks)
    out["team_ap_ranked"] = tr.notna().to_numpy()
    out["team_ap_unranked"] = tr.isna().to_numpy()
    out["opp_ap_ranked"] = orank.notna().to_numpy()
    out["opp_ap_unranked"] = orank.isna().to_numpy()
    return out


def attach_last_total_over(
    rows: pd.DataFrame,
    games: pd.DataFrame,
    lines: pd.DataFrame,
) -> pd.DataFrame:
    """Last-game total overshoot (actual − close) for team and opp."""
    out = rows.copy()
    out["team_last_total_over"] = np.nan
    out["opp_last_total_over"] = np.nan
    out["either_last_total_over_21"] = False
    if games.empty or lines is None or lines.empty or "total" not in lines.columns:
        return out
    work = lines.copy()
    if "line_type" in work.columns:
        close = work.loc[work["line_type"].astype(str).str.casefold() == "close"]
        work = close if not close.empty else work
    work["game_id"] = pd.to_numeric(work["game_id"], errors="coerce")
    work = work.loc[work["game_id"].notna()].copy()
    work["game_id"] = work["game_id"].astype(int)
    med = (
        work.assign(total=pd.to_numeric(work["total"], errors="coerce"))
        .groupby("game_id")["total"]
        .median()
    )
    g = games.drop_duplicates("game_id").copy()
    g["total_close"] = g["game_id"].map(med)
    hp = pd.to_numeric(g["home_points"], errors="coerce")
    ap = pd.to_numeric(g["away_points"], errors="coerce")
    g["actual_total"] = hp + ap
    g["total_over"] = g["actual_total"] - g["total_close"]
    over_map = g.set_index("game_id")["total_over"]

    if "team_prev_game_id" in out.columns:
        out["team_last_total_over"] = (
            pd.to_numeric(out["team_prev_game_id"], errors="coerce").map(over_map).astype(float)
        )
    if "opp_prev_game_id" in out.columns:
        out["opp_last_total_over"] = (
            pd.to_numeric(out["opp_prev_game_id"], errors="coerce").map(over_map).astype(float)
        )
    t = pd.to_numeric(out["team_last_total_over"], errors="coerce")
    o = pd.to_numeric(out["opp_last_total_over"], errors="coerce")
    either = t.ge(21.0).fillna(False) | o.ge(21.0).fillna(False)
    out["either_last_total_over_21"] = either.to_numpy()
    return out


def attach_efficiency_quantiles(
    rows: pd.DataFrame,
    *,
    min_n: int = 20,
) -> pd.DataFrame:
    """Trailing same-week FBS quantile of opp net adj EPA L3D (off − def).

    Requires ``opp_adj_off_epa_l3d`` / ``opp_adj_def_epa_l3d`` already joined.
    """
    out = rows.copy()
    out["opp_net_epa_l3d"] = np.nan
    out["opp_net_epa_l3d_q"] = np.nan
    out["opp_off_epa_l3d_q"] = np.nan
    out["opp_def_epa_l3d_q"] = np.nan
    if "opp_adj_off_epa_l3d" not in out.columns or "opp_adj_def_epa_l3d" not in out.columns:
        return out
    off = pd.to_numeric(out["opp_adj_off_epa_l3d"], errors="coerce")
    deff = pd.to_numeric(out["opp_adj_def_epa_l3d"], errors="coerce")
    out["opp_net_epa_l3d"] = off - deff
    # Quantile among home rows that week (one per game) with kickoff < as_of pool:
    # use same-season week cross-section of home sides.
    home = out.loc[out.get("is_home", pd.Series(False, index=out.index)).astype(bool)].copy()
    qnet = pd.Series(np.nan, index=out.index)
    qoff = pd.Series(np.nan, index=out.index)
    qdef = pd.Series(np.nan, index=out.index)
    for (_season, _week), grp in home.groupby(["season", "week"], sort=False):
        if len(grp) < min_n:
            continue
        idx = grp.index
        qnet.loc[idx] = grp["opp_net_epa_l3d"].rank(pct=True, method="average")
        qoff.loc[idx] = pd.to_numeric(grp["opp_adj_off_epa_l3d"], errors="coerce").rank(
            pct=True, method="average"
        )
        qdef.loc[idx] = pd.to_numeric(grp["opp_adj_def_epa_l3d"], errors="coerce").rank(
            pct=True, method="average"
        )
    # Broadcast home-computed quantiles to both sides via game_id merge
    game_q = pd.DataFrame(
        {
            "game_id": home["game_id"].to_numpy(),
            "opp_net_epa_l3d_q": qnet.loc[home.index].to_numpy(),
            "opp_off_epa_l3d_q": qoff.loc[home.index].to_numpy(),
            "opp_def_epa_l3d_q": qdef.loc[home.index].to_numpy(),
        }
    ).drop_duplicates("game_id")
    out = out.drop(
        columns=["opp_net_epa_l3d_q", "opp_off_epa_l3d_q", "opp_def_epa_l3d_q"], errors="ignore"
    )
    out = out.merge(game_q, on="game_id", how="left")
    return out


def attach_last_opp_ids_from_context(rows: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    """Ensure ``team_last_opp_id`` / ``opp_last_opp_id`` from prev-game joins."""
    out = rows.copy()
    if stats is None or stats.empty:
        if "team_last_opp_id" not in out.columns:
            out["team_last_opp_id"] = pd.array([pd.NA] * len(out), dtype="Int64")
        if "opp_last_opp_id" not in out.columns:
            out["opp_last_opp_id"] = pd.array([pd.NA] * len(out), dtype="Int64")
        return out
    key = stats[["game_id", "team_id", "opp_id"]].drop_duplicates(["game_id", "team_id"])
    if "team_prev_game_id" in out.columns:
        team_key = key.rename(
            columns={"game_id": "team_prev_game_id", "opp_id": "team_last_opp_id"}
        )
        out = out.drop(columns=["team_last_opp_id"], errors="ignore")
        out = out.merge(team_key, on=["team_prev_game_id", "team_id"], how="left")
    if "opp_prev_game_id" in out.columns:
        opp_key = key.rename(
            columns={
                "game_id": "opp_prev_game_id",
                "team_id": "opp_id",
                "opp_id": "opp_last_opp_id",
            }
        )
        out = out.drop(columns=["opp_last_opp_id"], errors="ignore")
        out = out.merge(opp_key, on=["opp_prev_game_id", "opp_id"], how="left")
    return out


__all__ = [
    "SERVICE_ACADEMY_SCHOOLS",
    "WIND_MS_15MPH",
    "attach_body_clock",
    "attach_efficiency_quantiles",
    "attach_last_opp_ids_from_context",
    "attach_last_total_over",
    "attach_lookahead",
    "attach_returning_production",
    "attach_revenge",
    "attach_season_records",
    "attach_service_academy_flags",
    "attach_this_game_ap",
    "attach_totals_close",
    "attach_weather_wind",
    "service_academy_ids",
]
