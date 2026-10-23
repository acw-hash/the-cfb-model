"""Last-game / Ridge-rank / turnover columns for the angles harness.

All values are knowable at the previous game's completion (or this week's
Tuesday as-of for current Ridge ratings). This-game scores and ATS residuals
are not computed here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.research.angles import (
    fbs_ids_by_season,
    game_as_of,
    game_end_utc,
    kickoff_utc,
    tuesday_as_of_by_week,
)
from ncaa_quant.research.angles_rankings import attach_ap_ranks_at_kickoff

INT_PLAY_TYPES: Final[frozenset[str]] = frozenset(
    {"Pass Interception Return", "Interception Return Touchdown"}
)
FUMBLE_LOST_PLAY_TYPES: Final[frozenset[str]] = frozenset(
    {"Fumble Recovery (Opponent)", "Fumble Return Touchdown"}
)
TURNOVER_PLAY_TYPES: Final[frozenset[str]] = INT_PLAY_TYPES | FUMBLE_LOST_PLAY_TYPES

TURNOVER_PLAY_TYPE_NOTE: Final[str] = (
    "play_type values counted as turnovers: interceptions = "
    + ", ".join(sorted(INT_PLAY_TYPES))
    + "; fumbles lost = "
    + ", ".join(sorted(FUMBLE_LOST_PLAY_TYPES))
    + ". Fumble Recovery (Own) is not a turnover."
)


def turnover_margins_from_plays(plays: pd.DataFrame) -> pd.DataFrame:
    """Per-(game, team) INT / fumble / total turnover margins from play_type.

    INT: offense threw it, defense forced it. Fumble lost: offense lost it
    (opponent recovery / return TD), defense recovered it. Margin = forced
    minus committed. Units: turnovers (count).
    """
    cols = ["game_id", "team_id", "to_margin", "int_margin", "fumble_margin"]
    if plays is None or plays.empty or "play_type" not in plays.columns:
        return pd.DataFrame(columns=cols)
    work = plays.loc[plays["play_type"].astype(str).isin(TURNOVER_PLAY_TYPES)].copy()
    if work.empty:
        return pd.DataFrame(columns=cols)
    work["play_type"] = work["play_type"].astype(str)
    work["is_int"] = work["play_type"].isin(INT_PLAY_TYPES)
    work["is_fumble"] = work["play_type"].isin(FUMBLE_LOST_PLAY_TYPES)
    work["game_id"] = work["game_id"].astype(int)
    work["offense_id"] = pd.to_numeric(work["offense_id"], errors="coerce")
    work["defense_id"] = pd.to_numeric(work["defense_id"], errors="coerce")
    work = work.loc[work["offense_id"].notna() & work["defense_id"].notna()]
    if work.empty:
        return pd.DataFrame(columns=cols)
    work["offense_id"] = work["offense_id"].astype(int)
    work["defense_id"] = work["defense_id"].astype(int)

    committed = (
        work.groupby(["game_id", "offense_id"], sort=False)
        .agg(int_committed=("is_int", "sum"), fumble_lost=("is_fumble", "sum"))
        .reset_index()
        .rename(columns={"offense_id": "team_id"})
    )
    forced = (
        work.groupby(["game_id", "defense_id"], sort=False)
        .agg(int_forced=("is_int", "sum"), fumble_recovered=("is_fumble", "sum"))
        .reset_index()
        .rename(columns={"defense_id": "team_id"})
    )
    out = committed.merge(forced, on=["game_id", "team_id"], how="outer").fillna(0)
    out["int_margin"] = out["int_forced"] - out["int_committed"]
    out["fumble_margin"] = out["fumble_recovered"] - out["fumble_lost"]
    out["to_margin"] = out["int_margin"] + out["fumble_margin"]
    return out[cols]


def _game_as_of_frame(games: pd.DataFrame) -> pd.DataFrame:
    """``game_id, as_of`` for every schedule row (FCS included)."""
    tuesdays = tuesday_as_of_by_week(games)
    tue = [
        tuesdays.get((int(s), int(w))) for s, w in zip(games["season"], games["week"], strict=True)
    ]
    kick = kickoff_utc(games)
    as_of = game_as_of(kick, pd.to_datetime(pd.Series(tue, index=games.index), utc=True))
    return pd.DataFrame(
        {"game_id": games["game_id"].astype(int).to_numpy(), "as_of": as_of.to_numpy()}
    )


def net_epa_as_of(
    keys: pd.DataFrame,
    filter_history: pd.DataFrame,
) -> pd.DataFrame:
    """Latest filter_history net EPA (off − def) with ``event_time < as_of``.

    ``keys`` carries ``team_id, season, as_of``. Same-season only. Weekly rows
    whose event_time is at or after ``as_of`` are not used.
    """
    out_cols = ["team_id", "season", "as_of", "ridge"]
    if keys.empty:
        return pd.DataFrame(columns=out_cols)
    left = keys[["team_id", "season", "as_of"]].copy()
    left["team_id"] = left["team_id"].astype(int)
    left["season"] = left["season"].astype(int)
    left["as_of"] = pd.to_datetime(left["as_of"], utc=True)
    left = left.drop_duplicates(["team_id", "season", "as_of"])
    if filter_history is None or filter_history.empty:
        left["ridge"] = np.nan
        return left[out_cols]
    hist = filter_history.copy()
    hist["event_time"] = pd.to_datetime(hist["event_time"], utc=True)
    hist["team_id"] = hist["team_id"].astype(int)
    hist["rat_season"] = hist["season"].astype(int)
    hist["ridge"] = pd.to_numeric(hist["off_epa"], errors="coerce") - pd.to_numeric(
        hist["def_epa"], errors="coerce"
    )
    hist = hist.dropna(subset=["event_time", "ridge"])
    left = left.dropna(subset=["as_of"])
    hist_groups = {int(tid): grp for tid, grp in hist.groupby("team_id", sort=False)}
    pieces: list[pd.DataFrame] = []
    for tid, lgrp in left.groupby("team_id", sort=False):
        lsort = lgrp.sort_values("as_of", kind="mergesort")
        rgrp = hist_groups.get(int(tid))
        if rgrp is None or rgrp.empty:
            tmp = lsort.copy()
            tmp["ridge"] = np.nan
            pieces.append(tmp)
            continue
        rsort = rgrp.sort_values("event_time", kind="mergesort")
        merged_t = pd.merge_asof(
            lsort,
            rsort[["rat_season", "event_time", "ridge"]],
            left_on="as_of",
            right_on="event_time",
            allow_exact_matches=False,
            direction="backward",
        )
        pieces.append(merged_t)
    merged = pd.concat(pieces, ignore_index=True) if pieces else left.copy()
    if "ridge" not in merged.columns:
        merged["ridge"] = np.nan
    if "rat_season" in merged.columns:
        same = merged["rat_season"].notna() & (
            merged["rat_season"].astype("Int64") == merged["season"].astype("Int64")
        )
        merged["ridge"] = merged["ridge"].where(same)
    return merged[out_cols]


def rank_fbs_ratings(
    ratings: pd.DataFrame,
    fbs_by_season: Mapping[int, set[int]],
) -> pd.DataFrame:
    """Rank 1 = highest net EPA among FBS teams sharing ``(season, as_of)``."""
    if ratings.empty:
        out = ratings.copy()
        out["ridge_rank"] = pd.Series(dtype="Int64")
        return out
    work = ratings.copy()
    if fbs_by_season:
        fbs_mask = [
            int(tid) in fbs_by_season.get(int(season), set())
            for tid, season in zip(work["team_id"], work["season"], strict=True)
        ]
        work = work.loc[np.asarray(fbs_mask, dtype=bool) & work["ridge"].notna()].copy()
    else:
        work = work.loc[work["ridge"].notna()].copy()
    work["ridge_rank"] = (
        work.groupby(["season", "as_of"], sort=False)["ridge"]
        .rank(ascending=False, method="min")
        .astype("Int64")
    )
    return work


def build_side_game_stats(
    games: pd.DataFrame,
    close_spread_home: Mapping[int, float],
    teams: pd.DataFrame,
    *,
    plays: pd.DataFrame | None = None,
    rankings: pd.DataFrame | None = None,
    filter_history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per (game, team) on the full schedule (FCS games included)."""
    cols = [
        "game_id",
        "team_id",
        "season",
        "won",
        "lost",
        "margin",
        "spread",
        "was_home",
        "was_away",
        "neutral",
        "opp_id",
        "opp_fbs",
        "upset_win",
        "wounded",
        "to_margin",
        "int_margin",
        "fumble_margin",
        "ridge",
        "ridge_rank",
        "opp_ridge",
        "opp_ridge_rank",
        "opp_ap_rank",
        "opp_ap_unranked",
        "as_of",
    ]
    if games.empty:
        return pd.DataFrame(columns=cols)
    g = games.copy()
    g["kickoff"] = kickoff_utc(g).to_numpy()
    g["game_end"] = game_end_utc(g).to_numpy()
    hp = pd.to_numeric(g["home_points"], errors="coerce")
    ap = pd.to_numeric(g["away_points"], errors="coerce")
    g["margin_home"] = (hp - ap).to_numpy(dtype=float)
    spread_map = (
        close_spread_home
        if isinstance(close_spread_home, pd.Series)
        else pd.Series(dict(close_spread_home), dtype=float)
    )
    g["spread_close_home"] = g["game_id"].map(spread_map).astype(float)
    as_of = _game_as_of_frame(g)
    g = g.merge(as_of, on="game_id", how="left")
    fbs = fbs_ids_by_season(teams)
    ap_ranks = attach_ap_ranks_at_kickoff(g, rankings if rankings is not None else pd.DataFrame())
    g = g.merge(ap_ranks, on="game_id", how="left")
    tos = turnover_margins_from_plays(plays if plays is not None else pd.DataFrame())

    sides: list[pd.DataFrame] = []
    for is_home in (True, False):
        side = pd.DataFrame(
            {
                "game_id": g["game_id"].astype(int).to_numpy(),
                "season": g["season"].astype(int).to_numpy(),
                "team_id": (g["home_team_id"] if is_home else g["away_team_id"])
                .astype(int)
                .to_numpy(),
                "opp_id": (g["away_team_id"] if is_home else g["home_team_id"])
                .astype(int)
                .to_numpy(),
                "is_home_raw": is_home,
                "neutral": g["neutral_site"].fillna(False).astype(bool).to_numpy(),
                "margin": (g["margin_home"] if is_home else -g["margin_home"]).to_numpy(
                    dtype=float
                ),
                "spread": (g["spread_close_home"] if is_home else -g["spread_close_home"]).to_numpy(
                    dtype=float
                ),
                "as_of": g["as_of"].to_numpy(),
                "opp_ap_rank": pd.to_numeric(
                    g["away_ap_rank"] if is_home else g["home_ap_rank"], errors="coerce"
                ).to_numpy(dtype=float),
            }
        )
        sides.append(side)
    stats = pd.concat(sides, ignore_index=True)
    stats["was_home"] = stats["is_home_raw"] & ~stats["neutral"]
    stats["was_away"] = (~stats["is_home_raw"]) & ~stats["neutral"]
    stats["won"] = stats["margin"] > 0
    stats["lost"] = stats["margin"] < 0
    spread_ok = stats["spread"].notna()
    stats["upset_win"] = (stats["won"] & (stats["spread"] >= 7.0)).where(spread_ok)
    abs_spread = stats["spread"].abs()
    stats["wounded"] = (
        stats["lost"]
        & ((stats["spread"] <= -7.0) | ((stats["margin"] <= -21.0) & (abs_spread <= 7.0)))
    ).where(spread_ok)
    stats["opp_fbs"] = [
        int(oid) in fbs.get(int(season), set())
        for oid, season in zip(stats["opp_id"], stats["season"], strict=True)
    ]
    stats["opp_ap_unranked"] = stats["opp_fbs"] & stats["opp_ap_rank"].isna()

    if not tos.empty:
        stats = stats.merge(tos, on=["game_id", "team_id"], how="left")
    else:
        stats["to_margin"] = np.nan
        stats["int_margin"] = np.nan
        stats["fumble_margin"] = np.nan

    keys = pd.concat(
        [
            stats[["team_id", "season", "as_of"]],
            stats[["opp_id", "season", "as_of"]].rename(columns={"opp_id": "team_id"}),
        ],
        ignore_index=True,
    ).drop_duplicates()
    rated = rank_fbs_ratings(
        net_epa_as_of(keys, filter_history if filter_history is not None else pd.DataFrame()), fbs
    )
    team_r = rated.rename(columns={"ridge": "ridge", "ridge_rank": "ridge_rank"})
    stats = stats.merge(
        team_r[["team_id", "season", "as_of", "ridge", "ridge_rank"]],
        on=["team_id", "season", "as_of"],
        how="left",
    )
    opp_r = rated.rename(
        columns={"team_id": "opp_id", "ridge": "opp_ridge", "ridge_rank": "opp_ridge_rank"}
    )
    stats = stats.merge(
        opp_r[["opp_id", "season", "as_of", "opp_ridge", "opp_ridge_rank"]],
        on=["opp_id", "season", "as_of"],
        how="left",
    )
    return stats[cols]


def attach_last_game_context(
    rows: pd.DataFrame,
    stats: pd.DataFrame,
) -> pd.DataFrame:
    """Join previous-game stats onto team-game rows as ``team_last_*`` / ``opp_last_*``."""
    out = rows.copy()
    if stats.empty:
        return out
    rename_team = {
        "game_id": "team_prev_game_id",
        "team_id": "team_id",
        "won": "team_last_won",
        "lost": "team_last_lost",
        "margin": "team_last_margin",
        "spread": "team_last_spread",
        "was_home": "team_last_was_home",
        "was_away": "team_last_was_away",
        "neutral": "team_last_neutral",
        "upset_win": "team_last_upset_win",
        "wounded": "team_last_wounded",
        "to_margin": "team_last_to_margin",
        "int_margin": "team_last_int_margin",
        "fumble_margin": "team_last_fumble_margin",
        "ridge_rank": "team_last_ridge_rank",
        "opp_ridge_rank": "team_last_opp_ridge_rank",
        "opp_ap_rank": "team_last_opp_ap_rank",
        "opp_ap_unranked": "team_last_opp_ap_unranked",
        "opp_fbs": "team_last_opp_fbs",
    }
    rename_opp = {
        "game_id": "opp_prev_game_id",
        "team_id": "opp_id",
        "won": "opp_last_won",
        "lost": "opp_last_lost",
        "margin": "opp_last_margin",
        "spread": "opp_last_spread",
        "was_home": "opp_last_was_home",
        "was_away": "opp_last_was_away",
        "neutral": "opp_last_neutral",
        "upset_win": "opp_last_upset_win",
        "wounded": "opp_last_wounded",
        "to_margin": "opp_last_to_margin",
        "int_margin": "opp_last_int_margin",
        "fumble_margin": "opp_last_fumble_margin",
        "ridge_rank": "opp_last_ridge_rank",
        "opp_ridge_rank": "opp_last_opp_ridge_rank",
        "opp_ap_rank": "opp_last_opp_ap_rank",
        "opp_ap_unranked": "opp_last_opp_ap_unranked",
        "opp_fbs": "opp_last_opp_fbs",
    }
    team_part = stats[list(rename_team.keys())].rename(columns=rename_team)
    opp_part = stats[list(rename_opp.keys())].rename(columns=rename_opp)
    out = out.merge(team_part, on=["team_prev_game_id", "team_id"], how="left")
    out = out.merge(opp_part, on=["opp_prev_game_id", "opp_id"], how="left")
    return out


def attach_current_ridge(
    rows: pd.DataFrame,
    filter_history: pd.DataFrame,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    """Ridge net EPA and FBS rank as of this row's Tuesday ``as_of``."""
    out = rows.copy()
    if out.empty:
        out["team_ridge"] = np.nan
        out["opp_ridge"] = np.nan
        out["team_ridge_rank"] = pd.Series(dtype="Int64")
        out["opp_ridge_rank"] = pd.Series(dtype="Int64")
        out["team_ridge_lt_opp"] = pd.Series(dtype="boolean")
        return out
    fbs = fbs_ids_by_season(teams)
    keys = pd.concat(
        [
            out[["team_id", "season", "as_of"]],
            out[["opp_id", "season", "as_of"]].rename(columns={"opp_id": "team_id"}),
        ],
        ignore_index=True,
    )
    rated = rank_fbs_ratings(net_epa_as_of(keys, filter_history), fbs)
    team_r = rated.rename(columns={"ridge": "team_ridge", "ridge_rank": "team_ridge_rank"})
    out = out.merge(
        team_r[["team_id", "season", "as_of", "team_ridge", "team_ridge_rank"]],
        on=["team_id", "season", "as_of"],
        how="left",
    )
    opp_r = rated.rename(
        columns={"team_id": "opp_id", "ridge": "opp_ridge", "ridge_rank": "opp_ridge_rank"}
    )
    out = out.merge(
        opp_r[["opp_id", "season", "as_of", "opp_ridge", "opp_ridge_rank"]],
        on=["opp_id", "season", "as_of"],
        how="left",
    )
    both = out["team_ridge"].notna() & out["opp_ridge"].notna()
    out["team_ridge_lt_opp"] = pd.NA
    out.loc[both, "team_ridge_lt_opp"] = out.loc[both, "team_ridge"] < out.loc[both, "opp_ridge"]
    return out


def attach_ats_momentum(rows: pd.DataFrame) -> pd.DataFrame:
    """Last-game ATS residual (team side) and home-minus-away momentum.

    ``team_last_ats_residual = team_last_margin + team_last_spread`` (points;
    last game's CFBD close from that team's side). ``ats_momentum`` is
    ``team_last_ats_residual − opp_last_ats_residual``. Trailing quantile of
    ``|ats_momentum|`` uses home rows whose kickoff is strictly before this
    row's ``as_of`` (point-in-time). Units: points / empirical CDF.
    """
    out = rows.copy()

    def _col(name: str) -> pd.Series:
        if name not in out.columns:
            return pd.Series(np.nan, index=out.index)
        return pd.to_numeric(out[name], errors="coerce")

    team_m = _col("team_last_margin")
    team_s = _col("team_last_spread")
    opp_m = _col("opp_last_margin")
    opp_s = _col("opp_last_spread")
    out["team_last_ats_residual"] = team_m + team_s
    out["opp_last_ats_residual"] = opp_m + opp_s
    out["ats_momentum"] = out["team_last_ats_residual"] - out["opp_last_ats_residual"]
    out["ats_momentum_abs"] = out["ats_momentum"].abs()
    both = out["team_last_ats_residual"].notna() & out["opp_last_ats_residual"].notna()
    out["team_last_ats_gt_opp"] = pd.NA
    out.loc[both, "team_last_ats_gt_opp"] = (
        out.loc[both, "team_last_ats_residual"] > out.loc[both, "opp_last_ats_residual"]
    )
    out["ats_momentum_abs_q"] = np.nan
    out["momentum_window_n"] = 0
    if out.empty or "as_of" not in out.columns:
        return out
    kick_col = "kickoff" if "kickoff" in out.columns else None
    if kick_col is None:
        return out
    home = out["venue"].astype(str).eq("home") if "venue" in out.columns else out["is_home"]
    pool_src = out.loc[home & out["ats_momentum_abs"].notna(), ["kickoff", "ats_momentum_abs"]]
    if pool_src.empty:
        return out
    pool_src = pool_src.sort_values("kickoff", kind="mergesort")
    kick_arr = pd.to_datetime(pool_src["kickoff"], utc=True).to_numpy()
    abs_arr = pool_src["ats_momentum_abs"].to_numpy(dtype=float)
    as_of_arr = pd.to_datetime(out["as_of"], utc=True).to_numpy()
    x = out["ats_momentum_abs"].to_numpy(dtype=float)
    q = np.full(len(out), np.nan)
    n_win = np.zeros(len(out), dtype=int)
    for as_of in np.unique(as_of_arr):
        k = int(np.searchsorted(kick_arr, as_of, side="left"))
        pool = np.sort(abs_arr[:k])
        pool = pool[np.isfinite(pool)]
        n = int(pool.size)
        mask = as_of_arr == as_of
        n_win[mask] = n
        if n == 0:
            continue
        xi = x[mask]
        qi = np.searchsorted(pool, xi, side="right") / float(n)
        q[mask] = np.where(np.isfinite(xi), qi, np.nan)
    out["ats_momentum_abs_q"] = q
    out["momentum_window_n"] = n_win
    return out


def attach_condition_context(
    rows: pd.DataFrame,
    games: pd.DataFrame,
    teams: pd.DataFrame,
    *,
    close_spread_home: Mapping[int, float],
    filter_history: pd.DataFrame | None = None,
    plays: pd.DataFrame | None = None,
    rankings: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Attach last-game, Ridge, turnover, and AP columns used by the vocabulary."""
    stats = build_side_game_stats(
        games,
        close_spread_home,
        teams,
        plays=plays,
        rankings=rankings,
        filter_history=filter_history,
    )
    out = attach_last_game_context(rows, stats)
    out = attach_current_ridge(
        out, filter_history if filter_history is not None else pd.DataFrame(), teams
    )
    if "team_prev_game_end" in out.columns and "as_of" in out.columns:
        out["team_last_game_completed_before_as_of"] = out["team_prev_game_end"].notna() & (
            out["team_prev_game_end"] < out["as_of"]
        )
    return attach_ats_momentum(out)


__all__ = [
    "FUMBLE_LOST_PLAY_TYPES",
    "INT_PLAY_TYPES",
    "TURNOVER_PLAY_TYPES",
    "TURNOVER_PLAY_TYPE_NOTE",
    "attach_ats_momentum",
    "attach_condition_context",
    "attach_current_ridge",
    "attach_last_game_context",
    "build_side_game_stats",
    "net_epa_as_of",
    "rank_fbs_ratings",
    "turnover_margins_from_plays",
]
