#!/usr/bin/env python3
"""S3 — Public threshold calibration backtest (amended post-W0).

Replay 2021–2024 walk-forward candidates via the S5 provider at the Tuesday
decision point. Sweep ``public_min_edge_sides`` and report week-1 separately
from weeks 2+. Read-only: never writes ``configs/`` or calls the Odds API.

Outputs
-------
``data/tmp/s3_calibration/candidates_cache.parquet``
``data/tmp/s3_calibration/report.json``
Stdout summary for the note author.

Authority: docs/social/TASKS-social.md S3; W0-DIAG week-1 findings.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.betting.devig import american_to_decimal
from ncaa_quant.betting.kelly import full_kelly, recommended_stake
from ncaa_quant.betting.provider import build_candidates_from_odds
from ncaa_quant.config import BettingConfig, load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON, assert_lockbox_excluded
from ncaa_quant.evaluation.walkforward import WeekDecisionCalendar, week_decision_as_of
from ncaa_quant.pipelines.predict import apply_bet_filters, load_champion_walkforward_config

REPO = Path(__file__).resolve().parents[1]
BACKTEST_ROOT = REPO / "data" / "backtests" / "task23_fundamental_reduced_v3" / "full" / "weeks"
OUT_DIR = REPO / "data" / "tmp" / "s3_calibration"
CACHE_PATH = OUT_DIR / "candidates_cache.parquet"
REPORT_PATH = OUT_DIR / "report.json"

SEASONS = (2021, 2022, 2023, 2024)
EDGE_GRID = tuple(round(x, 2) for x in np.arange(0.02, 0.121, 0.01))
UNIT_FRACTION = 0.005
N_DRAWS = 2_000
SEED = 42
MAX_PUBLIC = 10  # betting.max_bets_per_week default


@dataclass(frozen=True)
class GradedBet:
    season: int
    week: int
    game_id: str
    edge: float
    p_win: float
    american_odds: float
    stake_fraction: float
    residual: float
    market_line: float
    market_line_home: float
    bet_on: str
    spread_close: float | None
    realized_margin: float | None
    covered: bool | None  # None = push or unscored
    pushed: bool
    stake_u: float
    u_pnl: float
    line_clv: float | None  # bet_side_line - close_side_line; higher = better number


def _week_files() -> list[tuple[int, int, Path]]:
    assert_lockbox_excluded(list(SEASONS), context="S3 social threshold calibration")
    out: list[tuple[int, int, Path]] = []
    for path in sorted(BACKTEST_ROOT.glob("season=*_week=*.parquet")):
        parts = dict(p.split("=") for p in path.stem.split("_"))
        season, week = int(parts["season"]), int(parts["week"])
        if season in SEASONS:
            if season == LOCKBOX_SEASON:
                raise AssertionError("lockbox season leaked into S3 file list")
            out.append((season, week, path))
    return out


def _prediction_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in frame.to_dict(orient="records"):
        row = dict(rec)
        if row.get("mu_margin") is None:
            row["mu_margin"] = row.get("pred_margin")
        if row.get("sigma_margin") is None:
            row["sigma_margin"] = row.get("sigma_m")
        if "sigma_margin_credible" not in row:
            row["sigma_margin_credible"] = not bool(row.get("sigma_m_is_missing", False))
        rows.append(row)
    return rows


def _build_cache(*, force: bool = False) -> pd.DataFrame:
    if CACHE_PATH.is_file() and not force:
        print(f"[cache] loading {CACHE_PATH}", flush=True)
        return pd.read_parquet(CACHE_PATH)

    cfg = load_config()
    wf = load_champion_walkforward_config()
    betting = BettingConfig(
        candidates_enabled=True,
        candidate_markets=["side"],
        no_bet_on_qb_unknown=False,
        no_bet_on_stale=False,
        min_edge_sides=0.025,
    )
    app = cfg.model_copy(update={"betting": betting})
    store = ParquetStore(cfg.paths.staged_dir)

    records: list[dict[str, Any]] = []
    for season, week, path in _week_files():
        frame = pd.read_parquet(path)
        rows = _prediction_rows(frame)
        by_gid = {str(int(r["game_id"])): r for r in rows if r.get("game_id") is not None}

        games = store.read("games", filters={"season": season, "week": week})
        cal = WeekDecisionCalendar.from_games(games)
        as_of = week_decision_as_of(season, week, wf, calendar=cal)

        cands, details = build_candidates_from_odds(
            rows,
            season=season,
            week=week,
            as_of=as_of,
            store=store,
            config=app,
            n_draws=N_DRAWS,
            seed=SEED,
        )
        accepted, _rejected = apply_bet_filters(cands, betting_config=betting)

        for cand in accepted:
            if cand.block_reasons:
                continue
            key = f"{cand.game_id}:side"
            det = details.get(key) or details.get(str(cand.game_id)) or {}
            pred = by_gid.get(str(cand.game_id), {})
            stake = 0.0
            if cand.p_win is not None and cand.american_odds is not None:
                stake = float(
                    recommended_stake(
                        float(cand.p_win),
                        float(cand.american_odds),
                        bankroll=1.0,
                        config=betting,
                    ).stake_fraction
                )
            records.append(
                {
                    "season": season,
                    "week": week,
                    "game_id": str(cand.game_id),
                    "edge": float(cand.edge),
                    "p_win": float(cand.p_win) if cand.p_win is not None else float("nan"),
                    "american_odds": (
                        float(cand.american_odds) if cand.american_odds is not None else float("nan")
                    ),
                    "stake_fraction": stake,
                    "residual": float(cand.model_market_residual_points),
                    "bet_on": str(det.get("bet_on") or ""),
                    "market_line": float(det.get("market_line") or float("nan")),
                    "market_line_home": float(det.get("market_line_home") or float("nan")),
                    "side_team": str(det.get("side_team") or ""),
                    "book": str(det.get("book") or ""),
                    "spread_close": (
                        float(pred["spread_close"])
                        if pred.get("spread_close") is not None
                        and np.isfinite(float(pred["spread_close"]))
                        else float("nan")
                    ),
                    "realized_margin": (
                        float(pred["realized_margin"])
                        if pred.get("realized_margin") is not None
                        and np.isfinite(float(pred["realized_margin"]))
                        else float("nan")
                    ),
                    "home_points": pred.get("home_points"),
                    "away_points": pred.get("away_points"),
                }
            )
        print(
            f"[cache] {season}w{week}: accepted={len(accepted)} recorded={len(records)}",
            flush=True,
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame.from_records(records)
    df.to_parquet(CACHE_PATH, index=False)
    print(f"[cache] wrote {CACHE_PATH} n={len(df)}", flush=True)
    return df


def _grade_row(row: Mapping[str, Any]) -> GradedBet:
    realized = row.get("realized_margin")
    bet_on = str(row.get("bet_on") or "")
    home_line = float(row["market_line_home"])
    market_line = float(row["market_line"])
    covered: bool | None = None
    pushed = False
    if realized is not None and np.isfinite(float(realized)):
        rm = float(realized)
        # Home covers when margin + home_line > 0; away when < 0.
        adj = rm + home_line
        if abs(adj) < 1e-12:
            pushed = True
            covered = None
        elif bet_on == "home":
            covered = adj > 0.0
        elif bet_on == "away":
            covered = adj < 0.0
        else:
            covered = None

    stake = float(row["stake_fraction"])
    stake_u = round(stake / UNIT_FRACTION, 1) if stake > 0 else 1.0
    american = float(row["american_odds"])
    b = american_to_decimal(american) - 1.0
    if pushed or covered is None:
        u_pnl = 0.0
    elif covered:
        u_pnl = stake_u * b
    else:
        u_pnl = -stake_u

    line_clv: float | None = None
    close = row.get("spread_close")
    if close is not None and np.isfinite(float(close)) and bet_on in ("home", "away"):
        close_h = float(close)
        # Bettor-side line: higher is always better (-2.5 > -3.5, +3.5 > +2.5).
        close_side = close_h if bet_on == "home" else -close_h
        line_clv = float(market_line - close_side)

    return GradedBet(
        season=int(row["season"]),
        week=int(row["week"]),
        game_id=str(row["game_id"]),
        edge=float(row["edge"]),
        p_win=float(row["p_win"]),
        american_odds=american,
        stake_fraction=stake,
        residual=float(row["residual"]),
        market_line=market_line,
        market_line_home=home_line,
        bet_on=bet_on,
        spread_close=float(close) if close is not None and np.isfinite(float(close)) else None,
        realized_margin=float(realized) if realized is not None and np.isfinite(float(realized)) else None,
        covered=covered,
        pushed=pushed,
        stake_u=stake_u,
        u_pnl=float(u_pnl),
        line_clv=line_clv,
    )


def _select_public(df: pd.DataFrame, threshold: float) -> list[GradedBet]:
    """Per season-week: edge ≥ threshold, sort desc, cap at MAX_PUBLIC."""
    graded: list[GradedBet] = []
    for (_season, _week), group in df.groupby(["season", "week"], sort=True):
        sub = group.loc[group["edge"] >= threshold].sort_values("edge", ascending=False)
        sub = sub.head(MAX_PUBLIC)
        for rec in sub.to_dict(orient="records"):
            graded.append(_grade_row(rec))
    return graded


def _week_keys(seasons_weeks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    return sorted(set(seasons_weeks))


def _summarize_slice(
    bets: list[GradedBet],
    all_weeks: list[tuple[int, int]],
    *,
    label: str,
) -> dict[str, Any]:
    by_week: dict[tuple[int, int], list[GradedBet]] = defaultdict(list)
    for b in bets:
        by_week[(b.season, b.week)].append(b)

    counts = [len(by_week.get(w, [])) for w in all_weeks]
    decided = [b for b in bets if b.covered is not None]
    wins = sum(1 for b in decided if b.covered)
    losses = sum(1 for b in decided if b.covered is False)
    pushes = sum(1 for b in bets if b.pushed)
    hit = (wins / (wins + losses)) if (wins + losses) else None
    u_pnl = float(sum(b.u_pnl for b in bets))
    clvs = [b.line_clv for b in bets if b.line_clv is not None]
    edges = [b.edge for b in bets]
    residuals = [b.residual for b in bets]

    count_arr = np.asarray(counts, dtype=float)
    return {
        "label": label,
        "n_weeks": len(all_weeks),
        "n_bets": len(bets),
        "n_decided": len(decided),
        "n_wins": wins,
        "n_losses": losses,
        "n_pushes": pushes,
        "ats_hit_rate": hit,
        "u_pnl": u_pnl,
        "mean_line_clv": float(np.mean(clvs)) if clvs else None,
        "n_with_line_clv": len(clvs),
        "weekly_count": {
            "min": int(count_arr.min()) if len(count_arr) else 0,
            "p10": float(np.percentile(count_arr, 10)) if len(count_arr) else 0.0,
            "p25": float(np.percentile(count_arr, 25)) if len(count_arr) else 0.0,
            "median": float(np.median(count_arr)) if len(count_arr) else 0.0,
            "p75": float(np.percentile(count_arr, 75)) if len(count_arr) else 0.0,
            "p90": float(np.percentile(count_arr, 90)) if len(count_arr) else 0.0,
            "max": int(count_arr.max()) if len(count_arr) else 0,
            "mean": float(count_arr.mean()) if len(count_arr) else 0.0,
            "frac_zero": float(np.mean(count_arr == 0)) if len(count_arr) else None,
            "frac_in_3_10": (
                float(np.mean((count_arr >= 3) & (count_arr <= 10))) if len(count_arr) else None
            ),
            "histogram": {str(i): int(np.sum(count_arr == i)) for i in range(0, MAX_PUBLIC + 1)},
        },
        "edge_dist": _dist(edges),
        "residual_dist": _dist(residuals),
        "mean_claimed_p": float(np.mean([b.p_win for b in decided])) if decided else None,
    }


def _dist(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    arr = np.asarray(xs, dtype=float)
    return {
        "n": int(len(arr)),
        "min": float(arr.min()),
        "p25": float(np.percentile(arr, 25)),
        "median": float(np.median(arr)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
    }


def _calibration(bets: list[GradedBet], *, n_bins: int = 8) -> dict[str, Any]:
    decided = [b for b in bets if b.covered is not None and np.isfinite(b.p_win)]
    if len(decided) < 10:
        return {"n": len(decided), "bins": []}
    ps = np.asarray([b.p_win for b in decided], dtype=float)
    ys = np.asarray([1.0 if b.covered else 0.0 for b in decided], dtype=float)
    # Quantile bins on claimed p
    edges = np.unique(np.quantile(ps, np.linspace(0.0, 1.0, n_bins + 1)))
    if len(edges) < 3:
        edges = np.linspace(float(ps.min()), float(ps.max()), num=min(n_bins, len(decided)) + 1)
    bins: list[dict[str, Any]] = []
    for i in range(len(edges) - 1):
        lo, hi = float(edges[i]), float(edges[i + 1])
        mask = (ps >= lo) & (ps < hi if i < len(edges) - 2 else ps <= hi)
        if not np.any(mask):
            continue
        bins.append(
            {
                "p_lo": lo,
                "p_hi": hi,
                "n": int(mask.sum()),
                "mean_claimed_p": float(ps[mask].mean()),
                "realized_hit": float(ys[mask].mean()),
                "gap_realized_minus_claimed": float(ys[mask].mean() - ps[mask].mean()),
            }
        )
    return {
        "n": len(decided),
        "overall_claimed_p": float(ps.mean()),
        "overall_realized_hit": float(ys.mean()),
        "overall_gap": float(ys.mean() - ps.mean()),
        "bins": bins,
    }


def _kelly_saturation_report(df: pd.DataFrame) -> dict[str, Any]:
    """Among §12-accepted: how often stake hits the cap; options for usable spread."""
    stakes = df["stake_fraction"].astype(float).to_numpy()
    edges = df["edge"].astype(float).to_numpy()
    ps = df["p_win"].astype(float).to_numpy()
    odds = df["american_odds"].astype(float).to_numpy()
    capped = stakes >= (0.015 - 1e-12)

    # Uncapped quarter-Kelly and half-Kelly for the same bets
    qk = []
    hk = []
    for p, o in zip(ps, odds, strict=True):
        if not (np.isfinite(p) and np.isfinite(o)):
            continue
        fk = full_kelly(float(p), float(o))
        qk.append(0.25 * fk)
        hk.append(0.50 * fk)

    options = []
    for kelly_frac, max_stake in (
        (0.25, 0.015),
        (0.25, 0.03),
        (0.25, 0.05),
        (0.10, 0.015),
        (0.10, 0.03),
        (0.05, 0.015),
    ):
        fracs = []
        for p, o in zip(ps, odds, strict=True):
            if not (np.isfinite(p) and np.isfinite(o)):
                continue
            raw = kelly_frac * full_kelly(float(p), float(o))
            fracs.append(min(raw, max_stake))
        arr = np.asarray(fracs, dtype=float)
        stake_u = arr / UNIT_FRACTION
        options.append(
            {
                "kelly_fraction": kelly_frac,
                "max_stake_pct": max_stake,
                "n": int(len(arr)),
                "frac_at_cap": float(np.mean(np.isclose(arr, max_stake))),
                "stake_median": float(np.median(arr)),
                "stake_p10": float(np.percentile(arr, 10)),
                "stake_p90": float(np.percentile(arr, 90)),
                "u_median": float(np.median(stake_u)),
                "u_p10": float(np.percentile(stake_u, 10)),
                "u_p90": float(np.percentile(stake_u, 90)),
                "unique_unit_bins": int(len(np.unique(np.round(stake_u, 1)))),
                "bankroll_note": (
                    f"single-bet ceiling {max_stake * 100:.1f}% bankroll; "
                    f"at unit_fraction={UNIT_FRACTION}, max display "
                    f"{max_stake / UNIT_FRACTION:.1f}u"
                ),
            }
        )

    # Edge that saturates current 1.5% quarter-Kelly at -110
    return {
        "n_accepted": int(len(df)),
        "frac_capped_at_0015": float(np.mean(capped)),
        "edge_dist_accepted": _dist(edges.tolist()),
        "edge_when_capped": _dist(edges[capped].tolist()),
        "edge_when_uncapped": _dist(edges[~capped].tolist()),
        "quarter_kelly_uncapped_dist": _dist(qk),
        "half_kelly_uncapped_dist": _dist(hk),
        "options": options,
        "saturates_3u_at_neg110_edge": 0.0524,
    }


def _week1_guard_analysis(
    df: pd.DataFrame,
    *,
    threshold: float,
    weeks_w1: list[tuple[int, int]],
    weeks_w2: list[tuple[int, int]],
) -> dict[str, Any]:
    """Compare regimes at a fixed public threshold (for the recommendation)."""
    all_bets = _select_public(df, threshold)
    w1 = [b for b in all_bets if b.week == 1]
    w2 = [b for b in all_bets if b.week != 1]

    def _hit(bs: list[GradedBet]) -> float | None:
        d = [b for b in bs if b.covered is not None]
        if not d:
            return None
        return float(np.mean([1.0 if b.covered else 0.0 for b in d]))

    def _clv(bs: list[GradedBet]) -> float | None:
        xs = [b.line_clv for b in bs if b.line_clv is not None]
        return float(np.mean(xs)) if xs else None

    # Residual guard: drop residual > 7 among week-1 selections
    w1_kept = [b for b in w1 if b.residual <= 7.0]
    w1_blowout = [b for b in w1 if b.residual > 7.0]
    # Higher bar: week-1 needs edge >= threshold + 0.03
    high = threshold + 0.03
    w1_high = [b for b in _select_public(df, high) if b.week == 1]

    return {
        "threshold": threshold,
        "week1": {
            "n": len(w1),
            "hit": _hit(w1),
            "mean_claimed_p": float(np.mean([b.p_win for b in w1 if b.covered is not None]))
            if any(b.covered is not None for b in w1)
            else None,
            "u_pnl": float(sum(b.u_pnl for b in w1)),
            "mean_line_clv": _clv(w1),
            "cal": _calibration(w1),
            "weekly": _summarize_slice(w1, weeks_w1, label="week1")["weekly_count"],
        },
        "weeks_2plus": {
            "n": len(w2),
            "hit": _hit(w2),
            "mean_claimed_p": float(np.mean([b.p_win for b in w2 if b.covered is not None]))
            if any(b.covered is not None for b in w2)
            else None,
            "u_pnl": float(sum(b.u_pnl for b in w2)),
            "mean_line_clv": _clv(w2),
            "cal": _calibration(w2),
            "weekly": _summarize_slice(w2, weeks_w2, label="w2+")["weekly_count"],
        },
        "option_exclude_week1": {
            "description": "Public cards skip week 1 entirely",
            "weeks_2plus_median_count": _summarize_slice(w2, weeks_w2, label="w2+")["weekly_count"][
                "median"
            ],
        },
        "option_higher_bar_week1": {
            "week1_threshold": high,
            "n": len(w1_high),
            "hit": _hit(w1_high),
            "u_pnl": float(sum(b.u_pnl for b in w1_high)),
            "mean_line_clv": _clv(w1_high),
            "mean_claimed_p": float(np.mean([b.p_win for b in w1_high if b.covered is not None]))
            if any(b.covered is not None for b in w1_high)
            else None,
            "weekly_median": _summarize_slice(w1_high, weeks_w1, label="w1_high")["weekly_count"][
                "median"
            ],
        },
        "option_residual_guard_week1": {
            "max_residual": 7.0,
            "n_kept": len(w1_kept),
            "n_dropped": len(w1_blowout),
            "hit_kept": _hit(w1_kept),
            "hit_dropped": _hit(w1_blowout),
            "u_kept": float(sum(b.u_pnl for b in w1_kept)),
            "u_dropped": float(sum(b.u_pnl for b in w1_blowout)),
            "clv_kept": _clv(w1_kept),
            "clv_dropped": _clv(w1_blowout),
            "claimed_p_kept": float(np.mean([b.p_win for b in w1_kept if b.covered is not None]))
            if any(b.covered is not None for b in w1_kept)
            else None,
            "claimed_p_dropped": float(
                np.mean([b.p_win for b in w1_blowout if b.covered is not None])
            )
            if any(b.covered is not None for b in w1_blowout)
            else None,
        },
    }


def run(*, force_cache: bool = False) -> dict[str, Any]:
    df = _build_cache(force=force_cache)
    assert_lockbox_excluded(
        sorted({int(s) for s in df["season"].unique()}),
        context="S3 cache seasons",
    )

    all_weeks = _week_keys([(int(s), int(w)) for s, w in zip(df["season"], df["week"], strict=True)])
    # Include weeks with zero accepts: enumerate from file list
    file_weeks = [(s, w) for s, w, _ in _week_files()]
    all_weeks = _week_keys(file_weeks)
    weeks_w1 = [w for w in all_weeks if w[1] == 1]
    weeks_w2 = [w for w in all_weeks if w[1] != 1]

    grid: list[dict[str, Any]] = []
    for thr in EDGE_GRID:
        bets = _select_public(df, thr)
        w1_bets = [b for b in bets if b.week == 1]
        w2_bets = [b for b in bets if b.week != 1]
        row = {
            "public_min_edge_sides": thr,
            "week1": _summarize_slice(w1_bets, weeks_w1, label="week1"),
            "weeks_2plus": _summarize_slice(w2_bets, weeks_w2, label="weeks_2plus"),
            "calibration_week1": _calibration(w1_bets),
            "calibration_weeks_2plus": _calibration(w2_bets),
        }
        grid.append(row)
        w1 = row["week1"]
        w2 = row["weeks_2plus"]
        print(
            f"thr={thr:.2f}  W1 n={w1['n_bets']} med={w1['weekly_count']['median']:.1f} "
            f"hit={w1['ats_hit_rate']} u={w1['u_pnl']:+.1f}  |  "
            f"W2+ n={w2['n_bets']} med={w2['weekly_count']['median']:.1f} "
            f"hit={w2['ats_hit_rate']} u={w2['u_pnl']:+.1f} "
            f"frac_3_10={w2['weekly_count']['frac_in_3_10']}",
            flush=True,
        )

    # Guard analysis at current default 0.045 and at a mid-grid point
    guards = {
        "at_0_045": _week1_guard_analysis(
            df, threshold=0.045, weeks_w1=weeks_w1, weeks_w2=weeks_w2
        ),
        "at_0_06": _week1_guard_analysis(
            df, threshold=0.06, weeks_w1=weeks_w1, weeks_w2=weeks_w2
        ),
        "at_0_08": _week1_guard_analysis(
            df, threshold=0.08, weeks_w1=weeks_w1, weeks_w2=weeks_w2
        ),
    }

    report = {
        "meta": {
            "seasons": list(SEASONS),
            "lockbox_season": LOCKBOX_SEASON,
            "backtest_root": str(BACKTEST_ROOT),
            "n_draws": N_DRAWS,
            "seed": SEED,
            "edge_grid": list(EDGE_GRID),
            "unit_fraction": UNIT_FRACTION,
            "max_bets_per_week": MAX_PUBLIC,
            "n_weeks_total": len(all_weeks),
            "n_weeks_w1": len(weeks_w1),
            "n_weeks_w2plus": len(weeks_w2),
            "n_section12_accepted": int(len(df)),
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "clv_definition": (
                "line_units: bet_side_line - close_side_line using CFBD/backtest "
                "spread_close (home-centric). Same-book probability CLV NOT MEASURED."
            ),
        },
        "kelly_saturation": _kelly_saturation_report(df),
        "grid": grid,
        "week1_regime": guards,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"wrote {REPORT_PATH}", flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--force-cache",
        action="store_true",
        help="Rebuild provider cache even if parquet exists",
    )
    args = ap.parse_args(argv)
    run(force_cache=bool(args.force_cache))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
