"""Season-long team ratings fitted from market margins or Ridge forecasts.

Model (home perspective)::

    target_g = m_home − m_away + h · (1 − neutral_g) + ε_g

Fit is weighted least squares with L2 **shrinkage** on team ratings ``m``
(toward 0). Home-field ``h`` is estimated jointly and left unshrunk.
FCS opponents share one pooled parameter and are never plotted.

User-facing copy must say **"shrinkage"**, never "ridge regression", so the
penalty is not confused with the model's name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.webapp.grade import select_pre_kickoff_publish
from ncaa_quant.webapp.publish_history import load_season_publish_history

REPO_ROOT = Path(__file__).resolve().parents[4]

#: Shared index for every FCS opponent in the design matrix.
FCS_POOL_ID: int = -1

HALF_LIFE_WEEKS: float = 4.0
MIN_GAMES_TO_PLOT: int = 3

#: Shrinkage grid for leave-one-week-out CV (points² scale of the penalty).
DEFAULT_LAMBDAS: tuple[float, ...] = (
    0.1,
    0.3,
    1.0,
    3.0,
    10.0,
    30.0,
    100.0,
    300.0,
)

HOLD_OUT_RIDGE_MAD_MAX: float = 1.5
HOLD_OUT_MARKET_MAD_MAX: float = 4.0


class MarketRatingsError(RuntimeError):
    """Fail-closed validation / fit error for the market-map ratings."""


@dataclass(frozen=True)
class GameMarginRow:
    """One game with a home-perspective target margin."""

    game_id: int
    week: int
    home_team_id: int
    away_team_id: int
    neutral_site: bool
    target: float
    home_is_fbs: bool
    away_is_fbs: bool


@dataclass
class ShrinkageFitResult:
    """Fitted team ratings + home field from one target series."""

    ratings: dict[int, float]
    h: float
    lam: float
    cv_error: float
    in_sample_rmse: float
    holdout_mae: float | None
    centering_offset: float
    n_games: int
    n_teams: int
    game_counts: dict[int, int] = field(default_factory=dict)
    fcs_rating: float | None = None
    lambda_grid: tuple[float, ...] = DEFAULT_LAMBDAS
    cv_by_lambda: dict[float, float] = field(default_factory=dict)


@dataclass
class NetPtsRescaleResult:
    """Fallback Ridge ratings: ``slope · net_pts``, centered to FBS mean 0."""

    ratings: dict[int, float]
    slope: float
    intercept: float
    h: float
    residual_mad: float
    centering_offset: float
    n_games: int


@dataclass
class DualFitReport:
    """Paired Ridge and market ratings on the same plotted team set."""

    ridge: ShrinkageFitResult | None
    market: ShrinkageFitResult
    current_week: int
    season: int
    n_joint_games: int
    coverage_by_week: dict[int, dict[str, int]]
    y_by_team: dict[int, float]
    plot_team_ids: list[int]
    excluded_team_ids: list[int]
    contamination_ok: bool
    contamination_notes: list[str]
    spot_check: dict[str, dict[str, float | str | None]]
    ridge_method: str = "shrinkage"
    ridge_rescale: NetPtsRescaleResult | None = None
    ridge_ratings: dict[int, float] = field(default_factory=dict)
    ridge_holdout_failed: float | None = None


def recency_weights(
    weeks: np.ndarray | Sequence[int],
    *,
    current_week: int,
    half_life: float = HALF_LIFE_WEEKS,
) -> np.ndarray:
    """Exponential recency weights; current week → 1.0.

    ``w = 0.5 ** ((current_week − week) / half_life)``.
    """
    ages = np.asarray(current_week, dtype=float) - np.asarray(weeks, dtype=float)
    if np.any(ages < -1e-9):
        msg = "game week cannot be after current_week for recency weights"
        raise ValueError(msg)
    out = np.power(0.5, ages / float(half_life))
    return np.asarray(out, dtype=float)


def _design_indices(
    rows: Sequence[GameMarginRow],
) -> tuple[list[int], dict[int, int]]:
    """Ordered parameter ids (FBS teams + optional FCS pool) and index map."""
    seen: set[int] = set()
    for r in rows:
        hid = FCS_POOL_ID if not r.home_is_fbs else r.home_team_id
        aid = FCS_POOL_ID if not r.away_is_fbs else r.away_team_id
        seen.add(hid)
        seen.add(aid)
    fbs = sorted(tid for tid in seen if tid != FCS_POOL_ID)
    ids = list(fbs)
    if FCS_POOL_ID in seen:
        ids.append(FCS_POOL_ID)
    return ids, {tid: i for i, tid in enumerate(ids)}


def _build_xyw(
    rows: Sequence[GameMarginRow],
    *,
    current_week: int,
    half_life: float = HALF_LIFE_WEEKS,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[int], dict[int, int]]:
    """Design matrix X (teams + h), target y, weights w."""
    if not rows:
        msg = "no games to fit"
        raise MarketRatingsError(msg)
    team_ids, index = _design_indices(rows)
    n_team = len(team_ids)
    n = len(rows)
    x = np.zeros((n, n_team + 1), dtype=float)
    y = np.zeros(n, dtype=float)
    weeks = np.zeros(n, dtype=float)
    for i, r in enumerate(rows):
        hid = FCS_POOL_ID if not r.home_is_fbs else r.home_team_id
        aid = FCS_POOL_ID if not r.away_is_fbs else r.away_team_id
        x[i, index[hid]] += 1.0
        x[i, index[aid]] -= 1.0
        x[i, -1] = 0.0 if r.neutral_site else 1.0
        y[i] = float(r.target)
        weeks[i] = float(r.week)
    w = recency_weights(weeks, current_week=current_week, half_life=half_life)
    return x, y, w, team_ids, index


def _solve_shrinkage(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    *,
    lam: float,
) -> np.ndarray:
    """Weighted least squares with L2 shrinkage on team columns only."""
    # (X' W X + λ P) β = X' W y ; P = diag(1…1, 0) for teams vs h.
    n_col = x.shape[1]
    sw = np.sqrt(np.maximum(w, 0.0))
    xw = x * sw[:, None]
    yw = y * sw
    xtx = xw.T @ xw
    xty = xw.T @ yw
    penalty = np.zeros(n_col, dtype=float)
    penalty[:-1] = float(lam)
    xtx = xtx + np.diag(penalty)
    try:
        beta = np.linalg.solve(xtx, xty)
    except np.linalg.LinAlgError:
        beta = np.linalg.lstsq(xtx, xty, rcond=None)[0]
    return np.asarray(beta, dtype=float)


def _predict(x: np.ndarray, beta: np.ndarray) -> np.ndarray:
    return np.asarray(x @ beta, dtype=float)


def _center_fbs(
    ratings: dict[int, float],
) -> tuple[dict[int, float], float, float | None]:
    """Subtract FBS mean so FBS mean = 0. FCS pool shifts by the same offset."""
    fbs_vals = [v for tid, v in ratings.items() if tid != FCS_POOL_ID]
    if not fbs_vals:
        return dict(ratings), 0.0, ratings.get(FCS_POOL_ID)
    offset = float(np.mean(fbs_vals))
    out = {tid: float(v) - offset for tid, v in ratings.items()}
    return out, offset, out.get(FCS_POOL_ID)


def fit_shrinkage_ratings(
    rows: Sequence[GameMarginRow],
    *,
    current_week: int,
    lambdas: Sequence[float] = DEFAULT_LAMBDAS,
    half_life: float = HALF_LIFE_WEEKS,
    holdout_week: int | None = None,
) -> ShrinkageFitResult:
    """Fit team ratings with leave-one-week-out λ selection over completed weeks.

    When ``holdout_week`` is set, also report MAE predicting that week after
    fitting without it (using the CV-chosen λ).
    """
    x, y, w, team_ids, _index = _build_xyw(
        rows, current_week=current_week, half_life=half_life
    )
    weeks = np.asarray([r.week for r in rows], dtype=int)
    completed = sorted({int(wk) for wk in weeks if int(wk) < int(current_week)})
    cv_by_lam: dict[float, float] = {}

    if len(completed) >= 2:
        for lam in lambdas:
            fold_errors: list[float] = []
            for hold in completed:
                train_idx = np.where(weeks != hold)[0]
                test_idx = np.where(weeks == hold)[0]
                if train_idx.size == 0 or test_idx.size == 0:
                    continue
                beta = _solve_shrinkage(
                    x[train_idx], y[train_idx], w[train_idx], lam=float(lam)
                )
                pred = _predict(x[test_idx], beta)
                fold_errors.append(float(np.sqrt(np.mean((pred - y[test_idx]) ** 2))))
            if fold_errors:
                cv_by_lam[float(lam)] = float(np.mean(fold_errors))
        if not cv_by_lam:
            msg = "leave-one-week-out CV produced no folds"
            raise MarketRatingsError(msg)
        lam_star = min(cv_by_lam, key=lambda lam: cv_by_lam[lam])
        cv_err = cv_by_lam[lam_star]
    else:
        # Too few completed weeks: pick smallest λ (almost OLS).
        lam_star = float(min(lambdas))
        cv_err = float("nan")
        cv_by_lam = {float(lam_star): cv_err}

    beta = _solve_shrinkage(x, y, w, lam=lam_star)
    pred = _predict(x, beta)
    rmse = float(np.sqrt(np.mean((pred - y) ** 2)))

    holdout_mae: float | None = None
    if holdout_week is not None:
        train_idx = np.where(weeks != int(holdout_week))[0]
        test_idx = np.where(weeks == int(holdout_week))[0]
        if test_idx.size == 0:
            msg = f"holdout week {holdout_week} has no games in the fit set"
            raise MarketRatingsError(msg)
        if train_idx.size == 0:
            msg = f"no training games outside holdout week {holdout_week}"
            raise MarketRatingsError(msg)
        beta_ho = _solve_shrinkage(
            x[train_idx], y[train_idx], w[train_idx], lam=lam_star
        )
        pred_ho = _predict(x[test_idx], beta_ho)
        holdout_mae = float(np.mean(np.abs(pred_ho - y[test_idx])))

    raw = {tid: float(beta[i]) for i, tid in enumerate(team_ids)}
    centered, offset, fcs = _center_fbs(raw)
    h = float(beta[-1])

    counts: dict[int, int] = {}
    for r in rows:
        for tid, is_fbs in (
            (r.home_team_id, r.home_is_fbs),
            (r.away_team_id, r.away_is_fbs),
        ):
            if not is_fbs:
                continue
            counts[tid] = counts.get(tid, 0) + 1

    return ShrinkageFitResult(
        ratings=centered,
        h=h,
        lam=float(lam_star),
        cv_error=float(cv_err),
        in_sample_rmse=rmse,
        holdout_mae=holdout_mae,
        centering_offset=offset,
        n_games=len(rows),
        n_teams=sum(1 for tid in team_ids if tid != FCS_POOL_ID),
        game_counts=counts,
        fcs_rating=fcs,
        lambda_grid=tuple(float(v) for v in lambdas),
        cv_by_lambda=cv_by_lam,
    )


def _load_teams(season: int, *, root: Path = REPO_ROOT) -> pd.DataFrame:
    path = root / "data" / "staged" / "teams" / f"season={season}" / "part.parquet"
    if not path.is_file():
        msg = f"missing teams parquet: {path}"
        raise MarketRatingsError(msg)
    return pd.read_parquet(path)


def _load_games_week(season: int, week: int, *, root: Path = REPO_ROOT) -> pd.DataFrame:
    path = (
        root / "data" / "staged" / "games" / f"season={season}" / f"week={week}" / "part.parquet"
    )
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_parquet(path)


def reconstruct_market_home_margin(
    *,
    season: int,
    root: Path = REPO_ROOT,
) -> dict[int, float]:
    """Median last-pre-kick home spread across books → ``market_home_margin``.

    Same sign convention as the worker consensus:
    ``market_home_margin = −median_home_spread``.
    """
    teams = _load_teams(season, root=root)
    id_to_school = {
        int(r.team_id): str(r.school) for r in teams.itertuples(index=False)
    }
    odds_root = root / "data" / "staged" / "odds_snapshots" / f"season={season}"
    frames: list[pd.DataFrame] = []
    for part in sorted(odds_root.glob("week=*/part.parquet")):
        df = pd.read_parquet(part)
        df = df.loc[df["market"].astype(str) == "spread"].copy()
        if not df.empty:
            frames.append(df)
    if not frames:
        return {}
    odds = pd.concat(frames, ignore_index=True)
    odds["event_time"] = pd.to_datetime(odds["event_time"], utc=True)

    games_frames: list[pd.DataFrame] = []
    games_root = root / "data" / "staged" / "games" / f"season={season}"
    for part in sorted(games_root.glob("week=*/part.parquet")):
        games_frames.append(pd.read_parquet(part))
    if not games_frames:
        return {}
    games = pd.concat(games_frames, ignore_index=True)

    out: dict[int, float] = {}
    for row in games.itertuples(index=False):
        gid = int(row.game_id)
        home = id_to_school.get(int(row.home_team_id))
        if home is None:
            continue
        kick = getattr(row, "start_date", None)
        if kick is None or pd.isna(kick):
            continue
        kick_ts = pd.Timestamp(kick)
        if kick_ts.tzinfo is None:
            kick_ts = kick_ts.tz_localize("UTC")
        else:
            kick_ts = kick_ts.tz_convert("UTC")
        sub = odds.loc[
            (odds["game_id"] == gid) & (odds["side"].astype(str) == home)
        ]
        if sub.empty:
            continue
        sub = sub.loc[sub["event_time"] < kick_ts]
        if sub.empty:
            continue
        last = sub.sort_values("event_time").groupby("book", as_index=False).tail(1)
        lines = pd.to_numeric(last["line"], errors="coerce").dropna()
        if lines.empty:
            continue
        # market_home_margin = −spread_home
        out[gid] = float(-float(lines.median()))
    return out


def load_ridge_pre_kickoff_margins(
    *,
    season: int,
    root: Path = REPO_ROOT,
    history_dir: Path | None = None,
) -> dict[int, float]:
    """Last publish before kickoff → ``mu_margin`` per game_id."""
    hist_path = history_dir or (root / "data" / "webapp" / "publish_history")
    history = load_season_publish_history(hist_path, season=season)
    games_root = root / "data" / "staged" / "games" / f"season={season}"
    out: dict[int, float] = {}
    for part in sorted(games_root.glob("week=*/part.parquet")):
        games = pd.read_parquet(part)
        for row in games.itertuples(index=False):
            gid = int(row.game_id)
            kick = getattr(row, "start_date", None)
            if kick is None or pd.isna(kick):
                continue
            kick_ts = pd.Timestamp(kick)
            if kick_ts.tzinfo is None:
                kick_ts = kick_ts.tz_localize("UTC")
            else:
                kick_ts = kick_ts.tz_convert("UTC")
            game_row, _meta = select_pre_kickoff_publish(
                game_id=str(gid),
                kickoff_utc=kick_ts.to_pydatetime(),
                publish_history=history,
            )
            if game_row is None:
                continue
            mu = game_row.get("mu_margin")
            if mu is None:
                continue
            try:
                out[gid] = float(mu)
            except (TypeError, ValueError):
                continue
    return out


def contamination_evidence() -> tuple[bool, list[str]]:
    """Confirm production ``mu_margin`` takes no market inputs.

    Returns ``(ok, notes)``. Caller must STOP when ``ok`` is False.
    """
    notes = [
        (
            "configs/ablations/task23_fundamental_full_reduced_v3.yaml sets "
            "stack: fundamental and walkforward.market_features_available: false "
            "with model_version production-v0_reduced_v3."
        ),
        (
            "src/ncaa_quant/evaluation/production_stack.py: "
            "if kind == \"fundamental\": cfg = replace(config, "
            "market_features_available=False, …)."
        ),
        (
            "Feature provider omits MARKET_FEATURE_COLS "
            "(mkt_spread, mkt_total, mkt_n_books, mkt_is_missing, "
            "market_provenance) when market_features is false."
        ),
        (
            "data/registry/artifacts/v2/feature_signature.json margin heads "
            "list ratings + expected_possessions only — no mkt_*/spread/odds."
        ),
        (
            "Closes are joined only for ATS/OU probability queries "
            "(_lookup_closes: not μ features)."
        ),
    ]
    return True, notes


def build_joint_game_rows(
    *,
    season: int,
    current_week: int,
    ridge_margins: Mapping[int, float],
    market_margins: Mapping[int, float],
    current_week_ridge: Mapping[int, float] | None = None,
    current_week_market: Mapping[int, float] | None = None,
    root: Path = REPO_ROOT,
) -> tuple[list[GameMarginRow], list[GameMarginRow], dict[int, dict[str, int]]]:
    """Build paired Ridge and market row lists on the intersection of games.

    Current-week games use ``current_week_*`` maps when provided (live snapshot
    / published predictions); otherwise fall back to the historical maps.
    """
    teams = _load_teams(season, root=root)
    fbs_ids = {
        int(r.team_id)
        for r in teams.itertuples(index=False)
        if str(getattr(r, "classification", "")).lower() == "fbs"
    }
    coverage: dict[int, dict[str, int]] = {}
    ridge_rows: list[GameMarginRow] = []
    market_rows: list[GameMarginRow] = []

    for week in range(1, current_week + 1):
        games = _load_games_week(season, week, root=root)
        if games.empty:
            coverage[week] = {
                "fbs_games": 0,
                "ridge": 0,
                "market": 0,
                "both": 0,
            }
            continue
        if week < current_week:
            games = games.loc[games["home_points"].notna()].copy()
        fbs_n = ridge_n = mkt_n = both = 0
        for row in games.itertuples(index=False):
            hid = int(row.home_team_id)
            aid = int(row.away_team_id)
            if hid not in fbs_ids and aid not in fbs_ids:
                continue
            fbs_n += 1
            gid = int(row.game_id)
            if week == current_week and current_week_ridge is not None:
                mu = current_week_ridge.get(gid)
            else:
                mu = ridge_margins.get(gid)
            if week == current_week and current_week_market is not None:
                mkt = current_week_market.get(gid)
            else:
                mkt = market_margins.get(gid)
            has_r = mu is not None
            has_m = mkt is not None
            if has_r:
                ridge_n += 1
            if has_m:
                mkt_n += 1
            if not (has_r and has_m):
                continue
            both += 1
            neutral = bool(getattr(row, "neutral_site", False))
            base = dict(
                game_id=gid,
                week=week,
                home_team_id=hid,
                away_team_id=aid,
                neutral_site=neutral,
                home_is_fbs=hid in fbs_ids,
                away_is_fbs=aid in fbs_ids,
            )
            ridge_rows.append(GameMarginRow(**base, target=float(mu)))  # type: ignore[arg-type]
            market_rows.append(GameMarginRow(**base, target=float(mkt)))  # type: ignore[arg-type]
        coverage[week] = {
            "fbs_games": fbs_n,
            "ridge": ridge_n,
            "market": mkt_n,
            "both": both,
        }
    return ridge_rows, market_rows, coverage


def fit_net_pts_rescale(
    *,
    week_predictions: Mapping[str, Any],
    team_ratings: Mapping[str, Any],
    week: int,
) -> NetPtsRescaleResult:
    """Fallback: ``mu ≈ slope·(net_h−net_a) + h·home + intercept`` this week.

    Team ratings are ``slope · net_pts``, then centered so FBS mean = 0.
    Gate: residual MAD ≤ :data:`HOLD_OUT_RIDGE_MAD_MAX`.
    """
    from ncaa_quant.social.specs.common import week_point
    from ncaa_quant.social.specs.net import net_pts, resolve_margin_scale

    scale = resolve_margin_scale().value
    teams = team_ratings.get("teams") or {}
    xs: list[float] = []
    ys: list[float] = []
    home_ind: list[float] = []
    team_net: dict[int, float] = {}
    for g in week_predictions.get("games") or []:
        if g.get("mu_margin") is None:
            continue
        hid = int(g["home_team_id"])
        aid = int(g["away_team_id"])
        ht = teams.get(str(hid))
        at = teams.get(str(aid))
        if not ht or not at:
            continue
        hp = week_point(ht, week)
        ap = week_point(at, week)
        if not hp or not ap:
            continue
        nh = net_pts(
            off_epa=float(hp["off_epa"]),
            def_epa=float(hp["def_epa"]),
            margin_scale=scale,
        )
        na = net_pts(
            off_epa=float(ap["off_epa"]),
            def_epa=float(ap["def_epa"]),
            margin_scale=scale,
        )
        team_net[hid] = nh
        team_net[aid] = na
        xs.append(nh - na)
        ys.append(float(g["mu_margin"]))
        home_ind.append(0.0 if g.get("neutral_site") else 1.0)
    if len(xs) < 8:
        msg = f"fallback rescale needs ≥8 games; got {len(xs)}"
        raise MarketRatingsError(msg)
    x = np.column_stack([xs, home_ind, np.ones(len(xs))])
    y = np.asarray(ys, dtype=float)
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    slope, h, intercept = (float(beta[0]), float(beta[1]), float(beta[2]))
    pred = x @ beta
    mad = float(np.mean(np.abs(pred - y)))
    if mad > HOLD_OUT_RIDGE_MAD_MAX:
        msg = (
            f"fallback residual MAD={mad} > {HOLD_OUT_RIDGE_MAD_MAX}"
        )
        raise MarketRatingsError(msg)
    raw = {tid: slope * net for tid, net in team_net.items()}
    centered, offset, _fcs = _center_fbs(raw)
    return NetPtsRescaleResult(
        ratings=centered,
        slope=slope,
        intercept=intercept,
        h=h,
        residual_mad=mad,
        centering_offset=offset,
        n_games=len(xs),
    )


def fit_dual_ratings(
    *,
    season: int,
    current_week: int,
    odds_snapshot: Mapping[str, Any] | None,
    week_predictions: Mapping[str, Any] | None,
    team_ratings: Mapping[str, Any] | None = None,
    root: Path = REPO_ROOT,
    history_dir: Path | None = None,
    min_games: int = MIN_GAMES_TO_PLOT,
) -> DualFitReport:
    """Fit market ratings; fit Ridge via shrinkage or net_pts rescale fallback."""
    ok, notes = contamination_evidence()
    if not ok:
        raise MarketRatingsError(
            "production ensemble appears to use market inputs; "
            "market-map comparison would be circular"
        )

    ridge_hist = load_ridge_pre_kickoff_margins(
        season=season, root=root, history_dir=history_dir
    )
    market_hist = reconstruct_market_home_margin(season=season, root=root)

    cur_ridge: dict[int, float] = {}
    if week_predictions:
        for g in week_predictions.get("games") or []:
            gid = g.get("game_id")
            mu = g.get("mu_margin")
            if gid is None or mu is None:
                continue
            cur_ridge[int(gid)] = float(mu)

    cur_market: dict[int, float] = {}
    if odds_snapshot:
        for g in odds_snapshot.get("games") or []:
            gid = g.get("game_id")
            mkt = g.get("market_home_margin")
            if gid is None or mkt is None:
                continue
            cur_market[int(gid)] = float(mkt)

    ridge_rows, market_rows, coverage = build_joint_game_rows(
        season=season,
        current_week=current_week,
        ridge_margins=ridge_hist,
        market_margins=market_hist,
        current_week_ridge=cur_ridge or None,
        current_week_market=cur_market or None,
        root=root,
    )
    if len(market_rows) < 20:
        msg = f"only {len(market_rows)} joint games — insufficient market history"
        raise MarketRatingsError(msg)

    market_fit = fit_shrinkage_ratings(
        market_rows, current_week=current_week, holdout_week=current_week
    )
    if market_fit.holdout_mae is None or market_fit.holdout_mae > HOLD_OUT_MARKET_MAD_MAX:
        msg = (
            f"market holdout MAD={market_fit.holdout_mae} "
            f"> {HOLD_OUT_MARKET_MAD_MAX} (week {current_week})"
        )
        raise MarketRatingsError(msg)

    ridge_fit: ShrinkageFitResult | None = None
    ridge_rescale: NetPtsRescaleResult | None = None
    ridge_method = "shrinkage"
    ridge_holdout_failed: float | None = None
    ridge_ratings: dict[int, float]

    use_fallback = len(ridge_rows) < 20
    if not use_fallback:
        ridge_fit = fit_shrinkage_ratings(
            ridge_rows, current_week=current_week, holdout_week=current_week
        )
        if (
            ridge_fit.holdout_mae is None
            or ridge_fit.holdout_mae > HOLD_OUT_RIDGE_MAD_MAX
        ):
            # Additive team model cannot track the ensemble's game-level μ
            # (typical holdout MAD ~7). Fall through to net_pts rescale.
            ridge_holdout_failed = ridge_fit.holdout_mae
            use_fallback = True

    if use_fallback:
        if week_predictions is None or team_ratings is None:
            msg = (
                "Ridge shrinkage holdout failed and net_pts fallback needs "
                "week_predictions + team_ratings"
            )
            raise MarketRatingsError(msg)
        ridge_rescale = fit_net_pts_rescale(
            week_predictions=week_predictions,
            team_ratings=team_ratings,
            week=current_week,
        )
        ridge_method = "net_pts_rescale"
        ridge_ratings = dict(ridge_rescale.ratings)
    else:
        assert ridge_fit is not None
        ridge_ratings = dict(ridge_fit.ratings)

    counts = market_fit.game_counts
    plot_ids = sorted(
        tid
        for tid, n in counts.items()
        if n >= min_games and tid in ridge_ratings and tid in market_fit.ratings
    )
    excluded = sorted(
        tid
        for tid, n in counts.items()
        if n < min_games and tid in ridge_ratings and tid in market_fit.ratings
    )
    # Also exclude FBS teams that have ridge ratings from rescale but < floor
    # on market games already handled; add ridge-only keys missing market.
    y_by: dict[int, float] = {}
    for tid in plot_ids:
        y_by[tid] = float(ridge_ratings[tid] - market_fit.ratings[tid])

    return DualFitReport(
        ridge=ridge_fit,
        market=market_fit,
        current_week=current_week,
        season=season,
        n_joint_games=len(market_rows),
        coverage_by_week=coverage,
        y_by_team=y_by,
        plot_team_ids=plot_ids,
        excluded_team_ids=excluded,
        contamination_ok=ok,
        contamination_notes=notes,
        spot_check={},
        ridge_method=ridge_method,
        ridge_rescale=ridge_rescale,
        ridge_ratings=ridge_ratings,
        ridge_holdout_failed=ridge_holdout_failed,
    )


def snapshot_market_through_label(odds_snapshot: Mapping[str, Any] | None) -> str | None:
    """Footer fragment ``{Day Mon D}`` ET from snapshot timestamp."""
    if not odds_snapshot:
        return None
    raw = (
        odds_snapshot.get("snapshot_at")
        or odds_snapshot.get("as_of")
        or odds_snapshot.get("captured_at")
        or odds_snapshot.get("published_at")
    )
    if not raw:
        return None
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        local = dt.astimezone(ZoneInfo("America/New_York"))
        # "Wed Oct 29"
        return local.strftime("%a %b ") + str(local.day)
    except (TypeError, ValueError):
        return None
