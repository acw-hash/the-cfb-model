"""Per-hypothesis analysis for the situational-angle harness.

Given a flagged team-game frame (``qualifies`` / ``baseline`` already
evaluated, both-qualify games already dropped) this module computes the
pre-registered statistics for one definition on one season split:

- qualifying counts per season;
- mean ``ats_residual`` and mean ``ridge_residual`` with season-week
  cluster-robust SE; cover rate with season-week block-bootstrap CI;
- OLS ``ats_residual ~ qualifies + spread + venue + conference_game + week
  bucket`` (vs the baseline group, and vs the field);
- cover rate within spread buckets; per-season sign table;
- linear trend (H3) and continuous regression (H14) when configured;
- Tuesday→close line movement toward the angle (snapshot-backed seasons).

CFBD-close and Odds-API-close instruments are computed separately and never
pooled (DESIGN §7.2 item 8). Sensitivity seasons (2020) are reported as a
separate row and excluded from every headline cell (§7.2 item 5).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.research.angles import (
    AnglesConfig,
    ConditionSet,
    FlaggedFrame,
    Hypothesis,
    qualifying_counts_by_season,
    spread_bucket_label,
    week_bucket_label,
)
from ncaa_quant.research.angles_stats import (
    block_bootstrap_rate,
    cluster_mean,
    ols_cluster,
    sign_table,
)

INSTRUMENTS: tuple[str, ...] = ("cfbd", "odds")
SNAPSHOT_BACKED_FROM_SEASON_DEFAULT = 2021


@dataclass
class DefinitionResult:
    """Everything computed for one definition (primary or a variant)."""

    hypothesis_id: str
    definition_id: str
    kind: str  # primary | variant
    statistic: str
    direction: str | None
    seasons: tuple[int, ...]
    sensitivity_seasons: tuple[int, ...]
    counts_by_season: list[dict[str, Any]]
    n_both_qualify_games: int
    both_qualify_game_ids: tuple[int, ...]
    by_instrument: dict[str, dict[str, Any]]
    ridge: dict[str, Any]
    primary_p: float
    primary_estimate: float
    primary_instrument: str
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "definition_id": self.definition_id,
            "kind": self.kind,
            "statistic": self.statistic,
            "direction": self.direction,
            "seasons": list(self.seasons),
            "sensitivity_seasons": list(self.sensitivity_seasons),
            "counts_by_season": self.counts_by_season,
            "n_both_qualify_games": self.n_both_qualify_games,
            "both_qualify_game_ids": list(self.both_qualify_game_ids),
            "by_instrument": self.by_instrument,
            "ridge": self.ridge,
            "primary_p": self.primary_p,
            "primary_estimate": self.primary_estimate,
            "primary_instrument": self.primary_instrument,
            "notes": self.notes,
        }


def _design(
    rows: pd.DataFrame,
    *,
    spread_col: str,
    week_buckets: Sequence[Sequence[int]],
    extra: Mapping[str, np.ndarray] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Design matrix: intercept, [extra...], spread, venue dummies, conference, week buckets."""
    n = len(rows)
    cols: list[np.ndarray] = [np.ones(n)]
    names: list[str] = ["intercept"]
    for nm, arr in (extra or {}).items():
        cols.append(np.asarray(arr, dtype=float))
        names.append(nm)
    cols.append(pd.to_numeric(rows[spread_col], errors="coerce").to_numpy(dtype=float))
    names.append("spread")
    venue = rows["venue"].astype(str).to_numpy()
    cols.append((venue == "home").astype(float))
    names.append("venue_home")
    cols.append((venue == "neutral").astype(float))
    names.append("venue_neutral")
    cols.append(rows["conference_game"].fillna(False).astype(bool).astype(float).to_numpy())
    names.append("conference_game")
    labels = [week_bucket_label(int(w), week_buckets) for w in rows["week"]]
    uniq = sorted(set(labels))
    for lab in uniq[1:]:  # first bucket is the reference level
        cols.append(np.array([1.0 if x == lab else 0.0 for x in labels]))
        names.append(f"week_{lab}")
    stacked = np.column_stack(cols)
    keep_idx = [0]
    keep_names = [names[0]]
    for i in range(1, stacked.shape[1]):
        col = stacked[:, i]
        finite = col[np.isfinite(col)]
        if finite.size == 0:
            continue
        if float(np.std(finite)) > 1e-12:
            keep_idx.append(i)
            keep_names.append(names[i])
    return stacked[:, keep_idx], keep_names


def _mean_block(rows: pd.DataFrame, col: str, *, ci: float) -> dict[str, Any]:
    cm = cluster_mean(rows[col].to_numpy(dtype=float), rows["season_week"].to_numpy(), ci=ci)
    return cm.as_dict()


def _bootstrap_block(
    rows: pd.DataFrame, col: str, *, reps: int, seed: int, ci: float
) -> dict[str, Any]:
    br = block_bootstrap_rate(
        rows[col].to_numpy(dtype=float), rows["season_week"].to_numpy(), reps=reps, seed=seed, ci=ci
    )
    return br.as_dict()


def _spread_buckets(
    rows: pd.DataFrame,
    *,
    spread_col: str,
    resid_col: str,
    cover_col: str,
    buckets: Mapping[str, Mapping[str, float]],
    ci: float,
) -> list[dict[str, Any]]:
    labels = [spread_bucket_label(float(s), buckets) for s in rows[spread_col]]
    out: list[dict[str, Any]] = []
    for name in buckets:
        m = np.array([lab == name for lab in labels])
        sub = rows.loc[m]
        cov = sub[cover_col].to_numpy(dtype=float)
        cov = cov[np.isfinite(cov)]
        cm = cluster_mean(
            sub[resid_col].to_numpy(dtype=float), sub["season_week"].to_numpy(), ci=ci
        )
        out.append(
            {
                "bucket": name,
                "n": int(cm.n),
                "cover_rate": float(cov.mean()) if cov.size else float("nan"),
                "n_cover": int(cov.size),
                "mean_ats_residual": cm.mean,
                "se": cm.se,
            }
        )
    return out


def _trend(
    rows: pd.DataFrame,
    definition: ConditionSet,
    *,
    resid_col: str,
    cover_col: str,
    ci: float,
) -> dict[str, Any] | None:
    if definition.trend is None:
        return None
    reg = str(definition.trend.get("regressor") or "opp_consecutive_weeks")
    cap = definition.trend.get("cap")
    x = pd.to_numeric(rows[reg], errors="coerce").to_numpy(dtype=float)
    if cap is not None:
        x = np.minimum(x, float(cap))
    y = rows[resid_col].to_numpy(dtype=float)
    design = np.column_stack([np.ones(len(x)), x])
    ols = ols_cluster(y, design, ["intercept", reg], rows["season_week"].to_numpy())
    buckets_cfg = definition.trend.get("buckets") or {}
    bucket_rows: list[dict[str, Any]] = []
    raw_x = pd.to_numeric(rows[reg], errors="coerce").to_numpy(dtype=float)
    for name, bounds in buckets_cfg.items():
        lo = bounds.get("min")
        hi = bounds.get("max")
        m = np.isfinite(raw_x)
        if lo is not None:
            m &= raw_x >= float(lo)
        if hi is not None:
            m &= raw_x <= float(hi)
        sub = rows.loc[m]
        cm = cluster_mean(
            sub[resid_col].to_numpy(dtype=float), sub["season_week"].to_numpy(), ci=ci
        )
        cov = sub[cover_col].to_numpy(dtype=float)
        cov = cov[np.isfinite(cov)]
        bucket_rows.append(
            {
                "bucket": str(name),
                "n": cm.n,
                "mean_ats_residual": cm.mean,
                "se": cm.se,
                "cover_rate": float(cov.mean()) if cov.size else float("nan"),
            }
        )
    slope = (
        ols.term(reg) if ols.n else {"coef": float("nan"), "se": float("nan"), "p": float("nan")}
    )
    return {
        "regressor": reg,
        "cap": cap,
        "n": ols.n,
        "n_clusters": ols.n_clusters,
        "slope": slope,
        "ols": ols.as_dict(),
        "buckets": bucket_rows,
    }


def _continuous_regression(
    all_rows: pd.DataFrame,
    definition: ConditionSet,
    *,
    spread_col: str,
    resid_col: str,
    week_buckets: Sequence[Sequence[int]],
) -> dict[str, Any] | None:
    if definition.regression is None:
        return None
    reg = str(definition.regression.get("regressor") or "opp_luck_last")
    scale = float(definition.regression.get("scale_per_points") or 1.0)
    work = all_rows
    on_qual = str(definition.regression.get("on") or "") == "qualifies"
    if on_qual and "qualifies" in all_rows.columns:
        work = all_rows.loc[all_rows["qualifies"]]
    sub = work.loc[
        pd.to_numeric(work[reg], errors="coerce").notna()
        & pd.to_numeric(work[resid_col], errors="coerce").notna()
    ]
    if sub.empty:
        return {"regressor": reg, "scale_per_points": scale, "n": 0}
    x_reg = pd.to_numeric(sub[reg], errors="coerce").to_numpy(dtype=float) / scale
    design, names = _design(
        sub,
        spread_col=spread_col,
        week_buckets=week_buckets,
        extra={f"{reg}_per_{scale:g}pts": x_reg},
    )
    ols = ols_cluster(
        sub[resid_col].to_numpy(dtype=float), design, names, sub["season_week"].to_numpy()
    )
    simple = ols_cluster(
        sub[resid_col].to_numpy(dtype=float),
        np.column_stack([np.ones(len(x_reg)), x_reg]),
        ["intercept", f"{reg}_per_{scale:g}pts"],
        sub["season_week"].to_numpy(),
    )
    return {
        "regressor": reg,
        "scale_per_points": scale,
        "n": ols.n,
        "n_clusters": ols.n_clusters,
        "slope_with_controls": ols.term(f"{reg}_per_{scale:g}pts"),
        "slope_simple": simple.term(f"{reg}_per_{scale:g}pts"),
        "ols": ols.as_dict(),
    }


def _line_movement(rows: pd.DataFrame, *, ci: float) -> dict[str, Any]:
    both = rows.loc[
        pd.to_numeric(rows["spread_tue_odds"], errors="coerce").notna()
        & pd.to_numeric(rows["spread_close_odds"], errors="coerce").notna()
    ]
    mv = both["line_move_toward_angle"].to_numpy(dtype=float)
    cm = cluster_mean(mv, both["season_week"].to_numpy(), ci=ci)
    return {
        "n": int(mv.size),
        "mean_move_toward_angle_pts": cm.mean,
        "se": cm.se,
        "p_two_sided": cm.p_two_sided,
        "share_toward": float(np.mean(mv > 0)) if mv.size else float("nan"),
        "share_away": float(np.mean(mv < 0)) if mv.size else float("nan"),
        "share_unchanged": float(np.mean(mv == 0)) if mv.size else float("nan"),
        "seasons": sorted({int(s) for s in both["season"].unique()}),
    }


def _instrument_block(
    flagged: FlaggedFrame,
    definition: ConditionSet,
    cfg: AnglesConfig,
    *,
    instrument: str,
    seasons: Sequence[int],
    sensitivity_seasons: Sequence[int],
    market: str = "spread",
) -> dict[str, Any]:
    inf = cfg.inference
    ci = float((inf.get("bootstrap") or {}).get("ci", 0.95))
    reps = int((inf.get("bootstrap") or {}).get("reps", 2000))
    seed = int((inf.get("bootstrap") or {}).get("seed", 0))
    week_buckets = [
        list(b) for b in (inf.get("week_buckets") or [[1, 4], [5, 8], [9, 12], [13, 20]])
    ]
    spread_buckets = dict(inf.get("spread_buckets") or {})
    min_n = int(inf.get("min_n_for_test", 30))

    # Totals use a single CFBD close total; Odds total instrument is not wired.
    if market == "total" or definition.statistic in {
        "mean_total_residual",
        "mean_under_residual",
    }:
        resid_col = "total_residual_cfbd"
        cover_col = "cover_under_cfbd"
        spread_col = "total_close_cfbd"
    else:
        resid_col = f"ats_residual_{instrument}"
        cover_col = f"cover_{instrument}"
        spread_col = f"spread_close_{instrument}"
    rows_all = flagged.rows
    has_line = (
        pd.to_numeric(rows_all[spread_col], errors="coerce").notna()
        & pd.to_numeric(rows_all[resid_col], errors="coerce").notna()
    )
    in_seasons = rows_all["season"].isin([int(s) for s in seasons])
    in_sens = rows_all["season"].isin([int(s) for s in sensitivity_seasons])
    field_rows = rows_all.loc[has_line & in_seasons]
    qual = field_rows.loc[field_rows["qualifies"]]
    base = field_rows.loc[field_rows["baseline"]]
    sens_qual = rows_all.loc[has_line & in_sens & rows_all["qualifies"]]

    block: dict[str, Any] = {
        "instrument": instrument,
        "line_source_label": "cfbd_close" if instrument == "cfbd" else "odds_api_snapshot_close",
        "seasons_with_lines": sorted({int(s) for s in field_rows["season"].unique()}),
        "n_qualifying": int(len(qual)),
        "n_baseline": int(len(base)),
        "n_field": int(len(field_rows)),
        "mean_ats_residual": _mean_block(qual, resid_col, ci=ci),
        "mean_total_residual": _mean_block(qual, resid_col, ci=ci),
        "cover": _bootstrap_block(qual, cover_col, reps=reps, seed=seed, ci=ci),
        "baseline_mean_ats_residual": _mean_block(base, resid_col, ci=ci),
        "baseline_cover": _bootstrap_block(base, cover_col, reps=reps, seed=seed + 1, ci=ci),
        "sensitivity": {
            "seasons": [int(s) for s in sensitivity_seasons],
            "n_qualifying": int(len(sens_qual)),
            "mean_ats_residual": _mean_block(sens_qual, resid_col, ci=ci),
            "mean_total_residual": _mean_block(sens_qual, resid_col, ci=ci),
            "cover": _bootstrap_block(sens_qual, cover_col, reps=reps, seed=seed + 2, ci=ci),
        },
        "spread_buckets": _spread_buckets(
            qual,
            spread_col=spread_col,
            resid_col=resid_col,
            cover_col=cover_col,
            buckets=spread_buckets,
            ci=ci,
        ),
        "sign_table": sign_table(
            qual["season"].to_numpy(),
            qual[resid_col].to_numpy(dtype=float),
            qual[cover_col].to_numpy(dtype=float),
        ),
        "tested": bool(len(qual) >= min_n),
        "min_n_for_test": min_n,
    }

    # Regression vs baseline: qualifies ∪ baseline rows.
    qb = pd.concat([qual, base], ignore_index=True)
    if len(qb) > 0:
        design, names = _design(
            qb,
            spread_col=spread_col,
            week_buckets=week_buckets,
            extra={"qualifies": qb["qualifies"].astype(float).to_numpy()},
        )
        block["regression_vs_baseline"] = ols_cluster(
            qb[resid_col].to_numpy(dtype=float), design, names, qb["season_week"].to_numpy()
        ).as_dict()
    else:
        block["regression_vs_baseline"] = {"n": 0, "n_clusters": 0, "terms": []}

    # Regression vs field: qualifies ∪ every other row not mirroring a qualifying game.
    qual_games = set(int(g) for g in qual["game_id"].unique())
    others = field_rows.loc[~field_rows["qualifies"] & ~field_rows["game_id"].isin(qual_games)]
    qf = pd.concat([qual, others], ignore_index=True)
    if len(qf) > 0 and len(qual) > 0:
        design, names = _design(
            qf,
            spread_col=spread_col,
            week_buckets=week_buckets,
            extra={"qualifies": qf["qualifies"].astype(float).to_numpy()},
        )
        block["regression_vs_field"] = ols_cluster(
            qf[resid_col].to_numpy(dtype=float), design, names, qf["season_week"].to_numpy()
        ).as_dict()
    else:
        block["regression_vs_field"] = {"n": 0, "n_clusters": 0, "terms": []}

    block["trend"] = _trend(qual, definition, resid_col=resid_col, cover_col=cover_col, ci=ci)
    block["continuous_regression"] = _continuous_regression(
        field_rows,
        definition,
        spread_col=spread_col,
        resid_col=resid_col,
        week_buckets=week_buckets,
    )
    if instrument == "odds":
        block["line_movement"] = _line_movement(qual, ci=ci)
    return block


def _ridge_block(
    flagged: FlaggedFrame,
    cfg: AnglesConfig,
    *,
    seasons: Sequence[int],
    sensitivity_seasons: Sequence[int],
) -> dict[str, Any]:
    ci = float((cfg.inference.get("bootstrap") or {}).get("ci", 0.95))
    rows = flagged.rows
    has = pd.to_numeric(rows["ridge_residual"], errors="coerce").notna()
    qual = rows.loc[has & rows["qualifies"] & rows["season"].isin([int(s) for s in seasons])]
    base = rows.loc[has & rows["baseline"] & rows["season"].isin([int(s) for s in seasons])]
    sens = rows.loc[
        has & rows["qualifies"] & rows["season"].isin([int(s) for s in sensitivity_seasons])
    ]
    return {
        "seasons_with_predictions": sorted({int(s) for s in rows.loc[has, "season"].unique()}),
        "n_qualifying": int(len(qual)),
        "mean_ridge_residual": _mean_block(qual, "ridge_residual", ci=ci),
        "baseline_mean_ridge_residual": _mean_block(base, "ridge_residual", ci=ci),
        "sensitivity": {
            "seasons": [int(s) for s in sensitivity_seasons],
            "n_qualifying": int(len(sens)),
            "mean_ridge_residual": _mean_block(sens, "ridge_residual", ci=ci),
        },
        "sign_table": sign_table(
            qual["season"].to_numpy(),
            qual["ridge_residual"].to_numpy(dtype=float),
            np.full(len(qual), np.nan),
        ),
    }


def _primary_statistic(definition: ConditionSet, block: dict[str, Any]) -> tuple[float, float]:
    """(estimate, two-sided p) for the definition's pre-registered statistic."""
    if definition.statistic.startswith("trend_slope"):
        trend = block.get("trend") or {}
        slope = trend.get("slope") or {}
        return float(slope.get("coef", float("nan"))), float(slope.get("p", float("nan")))
    if definition.statistic.startswith("slope_"):
        cregr = block.get("continuous_regression") or {}
        slope = cregr.get("slope_simple") or {}
        return float(slope.get("coef", float("nan"))), float(slope.get("p", float("nan")))
    if definition.statistic in {"mean_total_residual", "mean_under_residual"}:
        mean = block.get("mean_total_residual") or block.get("mean_ats_residual") or {}
        return float(mean.get("mean", float("nan"))), float(mean.get("p_two_sided", float("nan")))
    mean = block.get("mean_ats_residual") or {}
    return float(mean.get("mean", float("nan"))), float(mean.get("p_two_sided", float("nan")))


def analyze_definition(
    flagged: FlaggedFrame,
    hypothesis: Hypothesis,
    definition: ConditionSet,
    cfg: AnglesConfig,
    *,
    kind: str,
    seasons: Sequence[int],
    sensitivity_seasons: Sequence[int],
) -> DefinitionResult:
    """Run every pre-registered statistic for one definition on one split."""
    counts = qualifying_counts_by_season(flagged, list(seasons) + list(sensitivity_seasons))
    by_instrument: dict[str, dict[str, Any]] = {}
    instruments = ("cfbd",) if hypothesis.market == "total" else INSTRUMENTS
    for instrument in instruments:
        by_instrument[instrument] = _instrument_block(
            flagged,
            definition,
            cfg,
            instrument=instrument,
            seasons=seasons,
            sensitivity_seasons=sensitivity_seasons,
            market=hypothesis.market,
        )
    if hypothesis.market == "total":
        by_instrument.setdefault("odds", dict(by_instrument["cfbd"]))
    ridge = _ridge_block(flagged, cfg, seasons=seasons, sensitivity_seasons=sensitivity_seasons)
    primary_instrument = str(cfg.multiple_testing.get("instrument", "cfbd_close"))
    inst_key = "cfbd" if primary_instrument.startswith("cfbd") else "odds"
    est, p = _primary_statistic(definition, by_instrument[inst_key])
    notes: list[str] = []
    if not by_instrument[inst_key]["tested"]:
        notes.append(
            f"n_qualifying={by_instrument[inst_key]['n_qualifying']} < min_n_for_test="
            f"{by_instrument[inst_key]['min_n_for_test']}: p excluded from BH (not interpretable)"
        )
        p = float("nan")
    if definition.direction and math.isfinite(est):
        want_pos = definition.direction == "positive"
        if (est > 0) != want_pos:
            notes.append("estimate sign disagrees with the pre-registered direction")
    return DefinitionResult(
        hypothesis_id=hypothesis.id,
        definition_id=definition.id,
        kind=kind,
        statistic=definition.statistic,
        direction=definition.direction,
        seasons=tuple(int(s) for s in seasons),
        sensitivity_seasons=tuple(int(s) for s in sensitivity_seasons),
        counts_by_season=counts.to_dict(orient="records"),
        n_both_qualify_games=flagged.n_both_qualify_games,
        both_qualify_game_ids=flagged.both_qualify_game_ids,
        by_instrument=by_instrument,
        ridge=ridge,
        primary_p=p,
        primary_estimate=est,
        primary_instrument=inst_key,
        notes=notes,
    )


__all__ = ["INSTRUMENTS", "DefinitionResult", "analyze_definition"]
