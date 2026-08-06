"""Margin-aware Elo baseline (DESIGN §9.1 / §15 item 13).

This is a **benchmark and sanity anchor**, not the production Stage-1 engine.
Ratings are a scalar team strength updated after every completed game.

Formula (FiveThirtyEight-style NFL Elo, adapted for CFB)
--------------------------------------------------------
Pregame home win probability (neutral site drops HFA)::

    elo_diff = elo_home - elo_away + (0 if neutral else hfa)
    P(home)  = 1 / (1 + 10^(-elo_diff / 400))

Result ``S_home`` is 1 (home win), 0 (away win), or 0.5 (tie).

Margin-of-victory multiplier with autocorrelation correction::

    MOV = ln(|PD| + 1) * (mov_factor / ((ELOW - ELOL) * mov_autocorr + mov_factor))

where ``PD`` is the point differential, and ``ELOW`` / ``ELOL`` are the
*pregame HFA-adjusted* Elo of the winner and loser (for a tie the autocorrelation
term is taken as 1). Favorites that win get a discounted MOV; underdogs that win
get an inflated MOV — the 538 autocorrelation fix.

Rating update (symmetric before HFA is applied only to the expectation)::

    shift     = K * MOV * (S_home - P(home))
    elo_home ← elo_home + shift
    elo_away ← elo_away - shift

Between seasons, every team's Elo regresses toward ``mean_rating``::

    elo ← (1 - season_regression) * elo + season_regression * mean_rating

Implied home margin for ATS evaluation uses ``elo_per_point``::

    pred_home_margin = elo_diff / elo_per_point

Time semantics
--------------
Each post-game rating row carries ``event_time`` equal to the game's
``event_time`` (kickoff / knowable-at of the result under the staged games
convention). Weekly history rows use the max game ``event_time`` for that
team-week. Consumers must join with ``event_time < as_of``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Defaults (untuned until :func:`tune_elo_params`; 538 NFL-ish starting point)
# ---------------------------------------------------------------------------

DEFAULT_MEAN_RATING = 1500.0
# Tuned by walk-forward one-step log-loss scan over 2014–2024 FBS games
# (see docs/notes/13.md). 538-ish starting point was K=20 / mov_factor=2.2.
DEFAULT_K_FACTOR = 30.0
DEFAULT_HFA = 55.0
DEFAULT_SEASON_REGRESSION = 1.0 / 3.0
DEFAULT_MOV_FACTOR = 2.8
DEFAULT_MOV_AUTOCORR = 0.001
DEFAULT_ELO_PER_POINT = 25.0

HISTORY_COLUMNS: tuple[str, ...] = (
    "team_id",
    "season",
    "week",
    "elo",
    "game_id",
    "event_time",
    "kind",
)


@dataclass(frozen=True)
class EloConfig:
    """Configurable Elo hyperparameters.

    Parameters
    ----------
    k_factor:
        Base update scale ``K``.
    hfa:
        Home-field advantage in Elo points added to the home rating for
        expectation (not applied on neutral sites).
    mean_rating:
        League-mean anchor for initialization and between-season regression.
    season_regression:
        Fraction of the gap to ``mean_rating`` closed between seasons in
        ``[0, 1]``. ``1/3`` matches the classic 538 revert.
    mov_factor:
        Numerator / denominator constant in the MOV multiplier (538: 2.2).
    mov_autocorr:
        Scale on ``(ELOW - ELOL)`` in the autocorrelation term (538: 0.001).
    elo_per_point:
        Elo points per point of predicted margin (ATS / spread mapping).
    """

    k_factor: float = DEFAULT_K_FACTOR
    hfa: float = DEFAULT_HFA
    mean_rating: float = DEFAULT_MEAN_RATING
    season_regression: float = DEFAULT_SEASON_REGRESSION
    mov_factor: float = DEFAULT_MOV_FACTOR
    mov_autocorr: float = DEFAULT_MOV_AUTOCORR
    elo_per_point: float = DEFAULT_ELO_PER_POINT


@dataclass(frozen=True)
class EloUpdateResult:
    """Result of a single game update (pre-HFA ratings in, post ratings out)."""

    elo_home_before: float
    elo_away_before: float
    elo_diff: float
    p_home: float
    mov_mult: float
    shift: float
    elo_home_after: float
    elo_away_after: float


# ---------------------------------------------------------------------------
# Core math
# ---------------------------------------------------------------------------


def expected_score(elo_a: float, elo_b: float) -> float:
    """P(A beats B) from Elo difference ``elo_a - elo_b`` (no HFA)."""
    return float(1.0 / (1.0 + 10.0 ** (-(elo_a - elo_b) / 400.0)))


def mov_multiplier(
    point_diff: float,
    elo_winner: float,
    elo_loser: float,
    *,
    mov_factor: float = DEFAULT_MOV_FACTOR,
    mov_autocorr: float = DEFAULT_MOV_AUTOCORR,
    is_tie: bool = False,
) -> float:
    """FiveThirtyEight MOV × autocorrelation multiplier.

    ``elo_winner`` / ``elo_loser`` must already include HFA on the home side
    (i.e. the same HFA-adjusted pregame Elos used for ``P(home)``).
    """
    pd_abs = abs(float(point_diff))
    ln_term = math.log(pd_abs + 1.0)
    if is_tie:
        return ln_term * (mov_factor / mov_factor)  # == ln_term
    denom = (elo_winner - elo_loser) * mov_autocorr + mov_factor
    if denom <= 0.0:
        # Pathological extreme underdog blowout; clamp to keep the update finite.
        denom = 1e-6
    return ln_term * (mov_factor / denom)


def apply_season_regression(
    elo: float,
    *,
    mean_rating: float = DEFAULT_MEAN_RATING,
    season_regression: float = DEFAULT_SEASON_REGRESSION,
) -> float:
    """Regress a single Elo toward ``mean_rating`` between seasons."""
    if not 0.0 <= season_regression <= 1.0:
        msg = f"season_regression must be in [0, 1], got {season_regression}"
        raise ValueError(msg)
    return (1.0 - season_regression) * elo + season_regression * mean_rating


def update_elo_game(
    elo_home: float,
    elo_away: float,
    home_points: int,
    away_points: int,
    *,
    neutral_site: bool = False,
    config: EloConfig | None = None,
) -> EloUpdateResult:
    """Apply one margin-aware Elo update; returns before/after and intermediates.

    Symmetry: with ``neutral_site=True`` (no HFA), a home win of margin ``M``
    moves the two ratings equal-and-opposite. With HFA the *shift* is still
    equal-and-opposite; only the expectation uses the home boost.
    """
    cfg = config or EloConfig()
    hfa = 0.0 if neutral_site else cfg.hfa
    elo_diff = elo_home - elo_away + hfa
    p_home = expected_score(elo_home + hfa, elo_away)

    if home_points > away_points:
        s_home = 1.0
        elo_w, elo_l = elo_home + hfa, elo_away
        is_tie = False
    elif away_points > home_points:
        s_home = 0.0
        elo_w, elo_l = elo_away, elo_home + hfa
        is_tie = False
    else:
        s_home = 0.5
        elo_w, elo_l = elo_home + hfa, elo_away
        is_tie = True

    mov = mov_multiplier(
        float(home_points - away_points),
        elo_w,
        elo_l,
        mov_factor=cfg.mov_factor,
        mov_autocorr=cfg.mov_autocorr,
        is_tie=is_tie,
    )
    shift = cfg.k_factor * mov * (s_home - p_home)
    return EloUpdateResult(
        elo_home_before=elo_home,
        elo_away_before=elo_away,
        elo_diff=elo_diff,
        p_home=p_home,
        mov_mult=mov,
        shift=shift,
        elo_home_after=elo_home + shift,
        elo_away_after=elo_away - shift,
    )


def predicted_home_margin(
    elo_home: float,
    elo_away: float,
    *,
    neutral_site: bool = False,
    config: EloConfig | None = None,
) -> float:
    """Map HFA-adjusted Elo difference to an expected home margin (points)."""
    cfg = config or EloConfig()
    hfa = 0.0 if neutral_site else cfg.hfa
    return (elo_home - elo_away + hfa) / cfg.elo_per_point


# ---------------------------------------------------------------------------
# Season / history runners
# ---------------------------------------------------------------------------


def _require_columns(df: pd.DataFrame, cols: Iterable[str], *, name: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        msg = f"{name} missing columns: {missing}"
        raise ValueError(msg)


def _fbs_ids_for_season(teams: pd.DataFrame | None, season: int) -> set[int] | None:
    if teams is None or teams.empty:
        return None
    sub = teams.loc[teams["season"] == season]
    if sub.empty:
        return None
    fbs = sub.loc[sub["classification"].astype(str).str.casefold() == "fbs", "team_id"]
    return {int(t) for t in fbs}


def _sort_games(games: pd.DataFrame) -> pd.DataFrame:
    frame = games.copy()
    frame["start_date"] = pd.to_datetime(frame["start_date"], utc=True)
    if "event_time" in frame.columns:
        frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    else:
        frame["event_time"] = frame["start_date"]
    return frame.sort_values(
        ["season", "start_date", "game_id"],
        kind="mergesort",
    ).reset_index(drop=True)


def run_elo(
    games: pd.DataFrame,
    *,
    config: EloConfig | None = None,
    teams: pd.DataFrame | None = None,
    initial_elos: Mapping[int, float] | None = None,
    fbs_only: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[int, float]]:
    """Run margin-aware Elo over ``games`` in chronological order.

    Parameters
    ----------
    games:
        Staged-schema (or compatible) game rows. Required columns:
        ``game_id, season, week, start_date, home_team_id, away_team_id,
        home_points, away_points, neutral_site, completed``. Optional
        ``event_time``.
    teams:
        Optional teams table with ``season, team_id, classification``. When
        ``fbs_only`` is True, only games with both sides FBS that season update
        ratings (FCS cupcakes do not inflate Elo).
    initial_elos:
        Optional preseason Elo map; missing teams start at ``mean_rating``.
    fbs_only:
        Drop non-FBS-vs-FBS games when ``teams`` is provided.

    Returns
    -------
    game_log:
        One row per processed game with pregame Elos, ``p_home``, MOV, shift,
        and postgame Elos — the evaluation / harness hook.
    weekly_history:
        Per-team, per-week rating table (``kind='weekly'``) plus preseason rows
        (``kind='preseason'``, ``week=0``) after between-season regression.
        Consumable as a feature via ``event_time < as_of``.
    final_elos:
        Team → Elo after the last processed game.
    """
    cfg = config or EloConfig()
    _require_columns(
        games,
        (
            "game_id",
            "season",
            "week",
            "start_date",
            "home_team_id",
            "away_team_id",
            "home_points",
            "away_points",
            "neutral_site",
            "completed",
        ),
        name="games",
    )
    ordered = _sort_games(games)
    completed = ordered.loc[
        ordered["completed"].astype(bool)
        & ordered["home_points"].notna()
        & ordered["away_points"].notna()
    ].copy()

    elos: dict[int, float] = {int(k): float(v) for k, v in (initial_elos or {}).items()}
    game_rows: list[dict[str, Any]] = []
    history_rows: list[dict[str, Any]] = []
    # Track last post-game state per (season, week, team) for weekly snapshots.
    week_last: dict[tuple[int, int, int], tuple[float, Any, int]] = {}
    current_season: int | None = None

    def _ensure_team(tid: int) -> None:
        if tid not in elos:
            elos[tid] = cfg.mean_rating

    def _regress_all() -> None:
        for tid in list(elos):
            elos[tid] = apply_season_regression(
                elos[tid],
                mean_rating=cfg.mean_rating,
                season_regression=cfg.season_regression,
            )

    for row in completed.itertuples(index=False):
        season = int(row.season)
        week = int(row.week)
        home_id = int(row.home_team_id)
        away_id = int(row.away_team_id)
        game_id = int(row.game_id)

        if current_season is None:
            current_season = season
        elif season != current_season:
            _regress_all()
            current_season = season
            # Preseason snapshot after regression (week 0).
            pre_time = pd.Timestamp(year=season, month=8, day=1, tz="UTC")
            for tid, elo in elos.items():
                history_rows.append(
                    {
                        "team_id": tid,
                        "season": season,
                        "week": 0,
                        "elo": elo,
                        "game_id": pd.NA,
                        "event_time": pre_time,
                        "kind": "preseason",
                    }
                )

        fbs_ids = _fbs_ids_for_season(teams, season)
        if fbs_only and fbs_ids is not None and (home_id not in fbs_ids or away_id not in fbs_ids):
            continue

        _ensure_team(home_id)
        _ensure_team(away_id)

        home_pts = int(row.home_points)
        away_pts = int(row.away_points)
        neutral = bool(row.neutral_site)
        before_h = elos[home_id]
        before_a = elos[away_id]
        result = update_elo_game(
            before_h,
            before_a,
            home_pts,
            away_pts,
            neutral_site=neutral,
            config=cfg,
        )
        elos[home_id] = result.elo_home_after
        elos[away_id] = result.elo_away_after
        event_time = row.event_time

        game_rows.append(
            {
                "game_id": game_id,
                "season": season,
                "week": week,
                "start_date": row.start_date,
                "event_time": event_time,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_points": home_pts,
                "away_points": away_pts,
                "neutral_site": neutral,
                "elo_home_pre": before_h,
                "elo_away_pre": before_a,
                "elo_diff": result.elo_diff,
                "p_home": result.p_home,
                "mov_mult": result.mov_mult,
                "shift": result.shift,
                "elo_home_post": result.elo_home_after,
                "elo_away_post": result.elo_away_after,
                "pred_home_margin": predicted_home_margin(
                    before_h, before_a, neutral_site=neutral, config=cfg
                ),
            }
        )
        for tid, elo_post in (
            (home_id, result.elo_home_after),
            (away_id, result.elo_away_after),
        ):
            week_last[(season, week, tid)] = (elo_post, event_time, game_id)
            history_rows.append(
                {
                    "team_id": tid,
                    "season": season,
                    "week": week,
                    "elo": elo_post,
                    "game_id": game_id,
                    "event_time": event_time,
                    "kind": "postgame",
                }
            )

    for (season, week, tid), (elo, event_time, game_id) in sorted(week_last.items()):
        history_rows.append(
            {
                "team_id": tid,
                "season": season,
                "week": week,
                "elo": elo,
                "game_id": game_id,
                "event_time": event_time,
                "kind": "weekly",
            }
        )

    game_log = pd.DataFrame(game_rows)
    weekly_history = pd.DataFrame(history_rows)
    if not weekly_history.empty:
        weekly_history = weekly_history.sort_values(
            ["season", "week", "team_id", "kind", "event_time"],
            kind="mergesort",
        ).reset_index(drop=True)
    return game_log, weekly_history, elos


def end_of_season_ratings(
    weekly_history: pd.DataFrame,
    *,
    season: int,
    teams: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Latest weekly Elo per team for ``season`` (FBS filter when ``teams`` given)."""
    if weekly_history.empty:
        return pd.DataFrame(columns=["team_id", "season", "week", "elo"])
    hist = weekly_history.loc[
        (weekly_history["season"] == season) & (weekly_history["kind"] == "weekly")
    ]
    if hist.empty:
        return pd.DataFrame(columns=["team_id", "season", "week", "elo"])
    idx = hist.groupby("team_id")["week"].idxmax()
    out = hist.loc[idx, ["team_id", "season", "week", "elo"]].copy()
    fbs = _fbs_ids_for_season(teams, season)
    if fbs is not None:
        out = out.loc[out["team_id"].isin(fbs)]
    return out.sort_values("elo", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Walk-forward tuning & evaluation baselines
# ---------------------------------------------------------------------------


def one_step_log_loss(game_log: pd.DataFrame) -> float:
    """Mean Bernoulli log-loss of pregame ``p_home`` vs home win (ties skipped)."""
    if game_log.empty:
        return float("nan")
    frame = game_log.copy()
    frame["home_win"] = np.where(
        frame["home_points"] > frame["away_points"],
        1.0,
        np.where(frame["home_points"] < frame["away_points"], 0.0, np.nan),
    )
    scored = frame.dropna(subset=["home_win"])
    if scored.empty:
        return float("nan")
    p = scored["p_home"].clip(1e-15, 1.0 - 1e-15).to_numpy(dtype=float)
    y = scored["home_win"].to_numpy(dtype=float)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def ats_accuracy_vs_closing(
    game_log: pd.DataFrame,
    lines: pd.DataFrame,
    *,
    book: str | None = None,
) -> dict[str, float]:
    """ATS hit rate of Elo-implied margin vs closing home spread.

    ``lines`` must have ``game_id, line_type, spread`` (and optionally ``book``).
    Closing spread is home-centric (negative ⇒ home favored). Pushes are excluded.
    """
    _require_columns(lines, ("game_id", "line_type", "spread"), name="lines")
    close = lines.loc[lines["line_type"].astype(str).str.casefold() == "close"].copy()
    if book is not None and "book" in close.columns:
        close = close.loc[close["book"] == book]
    close = close.dropna(subset=["spread"])
    # One close line per game: prefer median across books when multiple remain.
    close_spread = close.groupby("game_id", sort=False)["spread"].median().rename("close_spread")
    merged = game_log.merge(close_spread, on="game_id", how="inner")
    if merged.empty:
        return {"ats_accuracy": float("nan"), "n_ats": 0.0, "n_push": 0.0}

    # Home covers when home_score + spread > away_score.
    margin = merged["home_points"].astype(float) - merged["away_points"].astype(float)
    home_cover_margin = margin + merged["close_spread"].astype(float)
    push = home_cover_margin == 0.0
    decided = merged.loc[~push].copy()
    if decided.empty:
        return {
            "ats_accuracy": float("nan"),
            "n_ats": 0.0,
            "n_push": float(push.sum()),
        }

    # Model picks home cover when pred_margin + spread > 0.
    model_edge = decided["pred_home_margin"] + decided["close_spread"]
    # Skip model pushes too.
    model_decided = decided.loc[model_edge != 0.0]
    if model_decided.empty:
        return {
            "ats_accuracy": float("nan"),
            "n_ats": 0.0,
            "n_push": float(push.sum()),
        }
    actual_home_cover = (
        model_decided["home_points"].astype(float)
        - model_decided["away_points"].astype(float)
        + model_decided["close_spread"].astype(float)
    ) > 0.0
    model_home_cover = (model_decided["pred_home_margin"] + model_decided["close_spread"]) > 0.0
    hits = actual_home_cover == model_home_cover
    return {
        "ats_accuracy": float(hits.mean()),
        "n_ats": float(len(model_decided)),
        "n_push": float(int(push.sum())),
    }


def spearman_rank_corr(a: Mapping[int, float], b: Mapping[int, float]) -> float:
    """Spearman rank correlation over the intersection of team ids."""
    common = sorted(set(a) & set(b))
    if len(common) < 3:
        return float("nan")
    ra = pd.Series({t: a[t] for t in common}).rank(ascending=False)
    rb = pd.Series({t: b[t] for t in common}).rank(ascending=False)
    return float(ra.corr(rb, method="pearson"))


def tune_elo_params(
    games: pd.DataFrame,
    *,
    teams: pd.DataFrame | None = None,
    k_grid: Sequence[float] = (10.0, 15.0, 20.0, 25.0, 30.0),
    mov_factor_grid: Sequence[float] = (1.5, 2.2, 2.8),
    hfa: float = DEFAULT_HFA,
    season_regression: float = DEFAULT_SEASON_REGRESSION,
    mean_rating: float = DEFAULT_MEAN_RATING,
    mov_autocorr: float = DEFAULT_MOV_AUTOCORR,
    elo_per_point: float = DEFAULT_ELO_PER_POINT,
    fbs_only: bool = True,
) -> tuple[EloConfig, pd.DataFrame]:
    """Walk-forward grid scan minimizing one-step-ahead log-loss.

    Each ``(K, mov_factor)`` candidate is scored by a single chronological pass
    over ``games`` (one-step-ahead by construction: ``p_home`` is pregame).
    No Optuna — a simple product scan as specified.
    """
    records: list[dict[str, Any]] = []
    best_cfg = EloConfig(
        k_factor=float(k_grid[0]),
        hfa=hfa,
        mean_rating=mean_rating,
        season_regression=season_regression,
        mov_factor=float(mov_factor_grid[0]),
        mov_autocorr=mov_autocorr,
        elo_per_point=elo_per_point,
    )
    best_ll = float("inf")

    for k in k_grid:
        for mov_f in mov_factor_grid:
            cfg = EloConfig(
                k_factor=float(k),
                hfa=hfa,
                mean_rating=mean_rating,
                season_regression=season_regression,
                mov_factor=float(mov_f),
                mov_autocorr=mov_autocorr,
                elo_per_point=elo_per_point,
            )
            game_log, _, _ = run_elo(games, config=cfg, teams=teams, fbs_only=fbs_only)
            ll = one_step_log_loss(game_log)
            records.append(
                {
                    "k_factor": float(k),
                    "mov_factor": float(mov_f),
                    "log_loss": ll,
                    "n_games": float(len(game_log)),
                }
            )
            if ll < best_ll:
                best_ll = ll
                best_cfg = cfg

    scan = pd.DataFrame(records).sort_values("log_loss").reset_index(drop=True)
    return best_cfg, scan


def rating_history_asof(
    weekly_history: pd.DataFrame,
    as_of: Any,
    *,
    kind: str = "postgame",
) -> pd.DataFrame:
    """Point-in-time Elo snapshot: latest row per team with ``event_time < as_of``."""
    if weekly_history.empty:
        return pd.DataFrame(columns=["team_id", "elo", "event_time", "season", "week"])
    ts = pd.Timestamp(as_of)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    eligible = weekly_history.loc[
        (weekly_history["kind"] == kind) & (weekly_history["event_time"] < ts)
    ]
    if eligible.empty:
        return pd.DataFrame(columns=["team_id", "elo", "event_time", "season", "week"])
    idx = eligible.groupby("team_id")["event_time"].idxmax()
    return eligible.loc[idx, ["team_id", "elo", "event_time", "season", "week"]].reset_index(
        drop=True
    )
