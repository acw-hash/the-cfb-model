"""D6: join CFBD closes for evaluation, powered encompassing test, Part-0 fixes.

μ heads / feature builders / Stage-1 filter fitting / the pre-registered stop
rule are not modified.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy import stats  # type: ignore[import-untyped]

from ncaa_quant.evaluation.d3_eval import part2_bakeoff
from ncaa_quant.evaluation.d4_eval import (
    CANONICAL_V2_SHA,
    encompassing_regression,
    residual_on_residual,
    score_point,
)
from ncaa_quant.evaluation.d5_eval import (
    EncompassingEvalConfig,
    b2_wald_ci,
    encompassing_power,
    underpowered_verdict,
)
from ncaa_quant.evaluation.metrics import mae
from ncaa_quant.evaluation.production_stack import (
    CHANCE_LOG_LOSS,
    MARKET_FEATURE_COLS,
    assert_derived_market_signs,
)
from ncaa_quant.evaluation.significance import paired_block_bootstrap

# Closing-line columns may appear on prediction tables for grading only.
EVAL_ONLY_CLOSE_COLS: frozenset[str] = frozenset(
    {
        "spread_close",
        "total_close",
        "line_source_close",
        "n_books_close",
        "spread_asof",  # as-of is bet-time; still never a fitted μ feature here
        "total_asof",
    }
)

# DESIGN §2 quality rules for line sanity.
SPREAD_ABS_MAX: float = 70.0
TOTAL_MIN: float = 20.0
TOTAL_MAX: float = 100.0

# Pre-registered stop rule (D5.md Part 3) — quoted verbatim; do not amend.
D5_STOP_RULE_VERBATIM: str = (
    "if after the powered sample the joint b2 CI lies entirely below +0.05 "
    "(no material positive weight on stack μ) or fewer than 3 seasons show "
    "reliable positive b2, stop pursuing a fundamental-model betting edge and "
    "treat the market-aware / residual stack as the betting workhorse."
)


class CloseJoinError(ValueError):
    """Invalid CFBD close join for evaluation."""


class CloseAsFeatureError(AssertionError):
    """Closing lines leaked into a fitted feature set."""


def assert_closes_eval_only(feature_columns: Sequence[str]) -> None:
    """Point-in-time discipline: closes are never features / never fitted inputs."""
    cols = {str(c) for c in feature_columns}
    leaked = sorted(cols & EVAL_ONLY_CLOSE_COLS)
    # Market-aware stack may use opening/as-of *feature* cols (mkt_*), never closes.
    close_like = sorted(
        c for c in cols if c.endswith("_close") or c in {"spread_close", "total_close"}
    )
    if leaked or close_like:
        msg = (
            "closing lines must not appear in fitted feature columns "
            f"(leaked={leaked or close_like}); closes are evaluation-only"
        )
        raise CloseAsFeatureError(msg)
    # Also guard the production market-feature contract: no *close* provenance.
    for c in MARKET_FEATURE_COLS:
        if "close" in c.lower():
            msg = f"MARKET_FEATURE_COLS must not include closes: {c}"
            raise CloseAsFeatureError(msg)


def _cfbd_close_lookup(cfbd_lines: pd.DataFrame) -> dict[int, dict[str, Any]]:
    """Median CFBD close per game_id (vectorized)."""
    if cfbd_lines.empty or "game_id" not in cfbd_lines.columns:
        return {}
    work = cfbd_lines
    if "line_type" in work.columns:
        closes = work.loc[work["line_type"].astype(str).str.lower().eq("close")]
        if closes.empty:
            closes = work
    else:
        closes = work
    rows: dict[int, dict[str, Any]] = {}
    grouped = closes.groupby("game_id", sort=False)
    for gid, sub in grouped:
        n_books = int(sub["book"].nunique()) if "book" in sub.columns else 0
        spread = (
            float(sub["spread"].median())
            if "spread" in sub.columns and sub["spread"].notna().any()
            else float("nan")
        )
        total = (
            float(sub["total"].median())
            if "total" in sub.columns and sub["total"].notna().any()
            else float("nan")
        )
        if not np.isfinite(spread) and not np.isfinite(total):
            continue
        rows[int(gid)] = {
            "spread": spread,
            "total": total,
            "line_source": "cfbd_close",
            "n_books": n_books,
        }
    return rows


def join_cfbd_closes_for_evaluation(
    frame: pd.DataFrame,
    cfbd_lines: pd.DataFrame,
    *,
    only_fill_null: bool = True,
    source_tag: str = "cfbd_close_eval",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach CFBD ``line_type=close`` onto a prediction frame (evaluation only).

    Root cause of the 2019–2020-only gap on the archived prediction table:
    ``resolve_lines_for_games`` treated seasons ≥2021 as Odds-snapshot-backed
    and the task23 backtest passed ``snapshots=None``, so closes were written
    null. CFBD closes exist in ``lines_historical`` for 2014–2025; this join
    materializes them for ATS/encompassing without feeding them to μ.

    Parameters
    ----------
    only_fill_null:
        When True, leave already-finite closes untouched (preserve Odds/
        prior CFBD joins). When False, overwrite all with CFBD medians.
    """
    if "game_id" not in frame.columns:
        raise CloseJoinError("frame requires game_id")
    work = frame.copy()
    for col, default in (
        ("spread_close", float("nan")),
        ("total_close", float("nan")),
        ("line_source_close", "null"),
        ("n_books_close", 0),
    ):
        if col not in work.columns:
            work[col] = default

    lookup = _cfbd_close_lookup(cfbd_lines)
    if not lookup:
        return work, {
            "n_rows": int(len(work)),
            "n_filled": 0,
            "n_kept_existing": int(
                np.isfinite(pd.to_numeric(work["spread_close"], errors="coerce")).sum()
            ),
            "n_still_missing": int(len(work)),
            "n_cfbd_close_games": 0,
            "gap_root_cause": (
                "walkforward resolve_lines_for_games gated seasons>=2021 to Odds "
                "snapshots only; task23 backtest passed snapshots=None → null "
                "spread_close/total_close on 2021–2025 despite CFBD closes in "
                "lines_historical 2014–2025. Not a schema change; partition was "
                "materialized but never joined for snapshot-backed seasons."
            ),
            "source_tag": source_tag,
            "eval_only": True,
        }

    lookup_df = pd.DataFrame.from_dict(lookup, orient="index")
    lookup_df.index.name = "game_id"
    lookup_df = lookup_df.reset_index()
    lookup_df = lookup_df.rename(
        columns={
            "spread": "_cfbd_spread",
            "total": "_cfbd_total",
            "n_books": "_cfbd_n_books",
        }
    )
    merged = work.merge(
        lookup_df[["game_id", "_cfbd_spread", "_cfbd_total", "_cfbd_n_books"]],
        on="game_id",
        how="left",
    )
    existing = np.isfinite(
        pd.to_numeric(merged["spread_close"], errors="coerce").to_numpy(dtype=float)
    )
    has_cfbd = np.isfinite(merged["_cfbd_spread"].to_numpy(dtype=float))
    fill = has_cfbd & (~existing if only_fill_null else np.ones(len(merged), dtype=bool))
    n_kept = int(existing.sum()) if only_fill_null else 0
    n_filled = int(fill.sum())
    n_missing = int((~existing & ~has_cfbd).sum()) if only_fill_null else int((~has_cfbd).sum())

    merged.loc[fill, "spread_close"] = merged.loc[fill, "_cfbd_spread"]
    merged.loc[fill, "total_close"] = merged.loc[fill, "_cfbd_total"]
    merged.loc[fill, "line_source_close"] = source_tag
    merged.loc[fill, "n_books_close"] = merged.loc[fill, "_cfbd_n_books"].astype(int)
    work = merged.drop(columns=["_cfbd_spread", "_cfbd_total", "_cfbd_n_books"])

    meta = {
        "n_rows": int(len(work)),
        "n_filled": n_filled,
        "n_kept_existing": n_kept,
        "n_still_missing": n_missing,
        "n_cfbd_close_games": int(len(lookup)),
        "gap_root_cause": (
            "walkforward resolve_lines_for_games gated seasons>=2021 to Odds "
            "snapshots only; task23 backtest passed snapshots=None → null "
            "spread_close/total_close on 2021–2025 despite CFBD closes in "
            "lines_historical 2014–2025. Not a schema change; partition was "
            "materialized but never joined for snapshot-backed seasons."
        ),
        "source_tag": source_tag,
        "eval_only": True,
    }
    return work, meta


def post_join_line_coverage(
    frame: pd.DataFrame,
    *,
    canonical_seasons: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Games with a finite close, by season — count and fraction of frame."""
    seasons = (
        sorted(int(s) for s in canonical_seasons)
        if canonical_seasons is not None
        else sorted(int(s) for s in frame["season"].dropna().unique())
    )
    rows: dict[str, Any] = {}
    total_n = 0
    total_close = 0
    for s in seasons:
        sub = frame.loc[frame["season"] == s]
        n = int(len(sub))
        sp = pd.to_numeric(sub.get("spread_close"), errors="coerce")
        tot = pd.to_numeric(sub.get("total_close"), errors="coerce")
        n_sp = int(np.isfinite(sp.to_numpy(dtype=float)).sum()) if n else 0
        n_tot = int(np.isfinite(tot.to_numpy(dtype=float)).sum()) if n else 0
        both = (
            int(
                (
                    np.isfinite(sp.to_numpy(dtype=float)) & np.isfinite(tot.to_numpy(dtype=float))
                ).sum()
            )
            if n
            else 0
        )
        rows[str(s)] = {
            "n_canonical": n,
            "n_spread_close": n_sp,
            "n_total_close": n_tot,
            "n_both": both,
            "frac_spread_close": float(n_sp / n) if n else float("nan"),
            "frac_total_close": float(n_tot / n) if n else float("nan"),
        }
        total_n += n
        total_close += n_sp
    return {
        "by_season": rows,
        "total_n_canonical": total_n,
        "total_n_with_spread_close": total_close,
        "frac_spread_close": float(total_close / total_n) if total_n else float("nan"),
    }


def validate_joined_closes(
    frame: pd.DataFrame,
    cfbd_lines: pd.DataFrame,
    *,
    n_spot: int = 25,
    seed: int = 0,
) -> dict[str, Any]:
    """§2 quality rules + season spread distribution + spot-check vs raw CFBD."""
    sp = pd.to_numeric(frame.get("spread_close"), errors="coerce")
    tot = pd.to_numeric(frame.get("total_close"), errors="coerce")
    finite_sp = sp[np.isfinite(sp.to_numpy(dtype=float))]
    finite_tot = tot[np.isfinite(tot.to_numpy(dtype=float))]
    spread_ok = bool((finite_sp.abs() < SPREAD_ABS_MAX).all()) if len(finite_sp) else True
    total_ok = (
        bool(((finite_tot >= TOTAL_MIN) & (finite_tot <= TOTAL_MAX)).all())
        if len(finite_tot)
        else True
    )
    by_season: dict[str, Any] = {}
    if "season" in frame.columns:
        for s, chunk in frame.groupby("season"):
            s_sp = pd.to_numeric(chunk["spread_close"], errors="coerce")
            vals = s_sp[np.isfinite(s_sp.to_numpy(dtype=float))]
            by_season[str(int(s))] = {
                "n": int(len(vals)),
                "mean": float(vals.mean()) if len(vals) else float("nan"),
                "std": float(vals.std(ddof=0)) if len(vals) else float("nan"),
                "p10": float(vals.quantile(0.10)) if len(vals) else float("nan"),
                "p50": float(vals.quantile(0.50)) if len(vals) else float("nan"),
                "p90": float(vals.quantile(0.90)) if len(vals) else float("nan"),
            }

    lookup = _cfbd_close_lookup(cfbd_lines)
    candidates = frame.loc[
        np.isfinite(sp.to_numpy(dtype=float)), ["game_id", "spread_close", "total_close"]
    ]
    rng = np.random.default_rng(seed)
    n_take = min(n_spot, len(candidates))
    spot_rows: list[dict[str, Any]] = []
    n_match = 0
    if n_take > 0:
        idx = rng.choice(len(candidates), size=n_take, replace=False)
        sample = candidates.iloc[idx]
        for row in sample.itertuples(index=False):
            gid = int(row.game_id)
            raw = lookup.get(gid)
            raw_sp = float(raw["spread"]) if raw else float("nan")
            raw_tot = float(raw["total"]) if raw else float("nan")
            match = bool(
                np.isfinite(raw_sp)
                and np.isclose(float(row.spread_close), raw_sp, atol=1e-6, equal_nan=False)
            )
            if match:
                n_match += 1
            spot_rows.append(
                {
                    "game_id": gid,
                    "joined_spread": float(row.spread_close),
                    "raw_cfbd_spread": raw_sp,
                    "joined_total": float(row.total_close),
                    "raw_cfbd_total": raw_tot,
                    "spread_match": match,
                }
            )
    return {
        "spread_abs_lt_70": spread_ok,
        "totals_in_20_100": total_ok,
        "n_finite_spread": int(len(finite_sp)),
        "n_finite_total": int(len(finite_tot)),
        "n_spread_out_of_range": int((finite_sp.abs() >= SPREAD_ABS_MAX).sum())
        if len(finite_sp)
        else 0,
        "n_total_out_of_range": int(((finite_tot < TOTAL_MIN) | (finite_tot > TOTAL_MAX)).sum())
        if len(finite_tot)
        else 0,
        "spread_by_season": by_season,
        "spot_check": {
            "n": n_take,
            "n_match": n_match,
            "match_rate": float(n_match / n_take) if n_take else float("nan"),
            "rows": spot_rows,
        },
        "passed": bool(spread_ok and total_ok and (n_take == 0 or n_match == n_take)),
    }


def diagnose_expected_possessions(
    *,
    feature_store_root: Path | str,
    registry_has_name: bool = True,
) -> dict[str, Any]:
    """Locate where expected_possessions dies in the feature pipeline."""
    root = Path(feature_store_root)
    # Registry: documented in Task 11 / registry.yaml (caller may confirm).
    # Materialization: look for partitions under data/features.
    candidates = [
        root / "expected_possessions",
        root / "tempo" / "expected_possessions",
        root,
    ]
    found_files: list[str] = []
    for c in candidates:
        if c.is_dir():
            found_files.extend(str(p) for p in c.rglob("*expected_possessions*") if p.is_file())
        elif c.is_file() and "expected_possessions" in c.name:
            found_files.append(str(c))
    materialized = bool(found_files)
    # Builder exists in code; CLI features materialize is NotImplemented.
    death = (
        "registered_and_not_materialized"
        if registry_has_name and not materialized
        else ("materialized_and_not_joined" if materialized else "not_built")
    )
    return {
        "death_point": death,
        "registry": "present" if registry_has_name else "absent",
        "builder": "ncaa_quant.features.builders.tempo.ExpectedPossessionsFeatureBuilder",
        "materialized_files": found_files[:20],
        "note": (
            "Feature is registered (registry.yaml) and coded (tempo.py) but "
            "data/features has no expected_possessions partitions; D4/D5 "
            "sigma revive NaN-fills the column (null rate 1.0). Builder "
            "requires a fitted ExpectedPossessionsArtifact; CLI `features` "
            "is NotImplemented. Defer materialization — not a join bug."
        ),
        "fix": False,
        "deferred": True,
    }


def priced_vs_pooled_season_note(frame: pd.DataFrame) -> dict[str, Any]:
    """Correct the void n=4 unpriced contrast: report 2019 vs pooled MAE."""
    y = pd.to_numeric(frame["realized_margin"], errors="coerce")
    mu = pd.to_numeric(frame["pred_margin"], errors="coerce")
    ok = np.isfinite(y.to_numpy()) & np.isfinite(mu.to_numpy())
    pooled_mae = float(mae(y.to_numpy()[ok], mu.to_numpy()[ok])) if ok.any() else float("nan")
    s2019 = (frame["season"] == 2019).to_numpy() & ok
    mae_2019 = (
        float(mae(y.to_numpy()[s2019], mu.to_numpy()[s2019])) if s2019.any() else float("nan")
    )
    # Within-2019 unpriced count (void for inference).
    if "spread_close" in frame.columns:
        sp = pd.to_numeric(frame["spread_close"], errors="coerce")
        unpriced_2019 = int(
            ((frame["season"] == 2019) & ok & ~np.isfinite(sp.to_numpy(dtype=float))).sum()
        )
    else:
        unpriced_2019 = 0
    return {
        "pooled_mae": pooled_mae,
        "mae_2019": mae_2019,
        "n_2019": int(s2019.sum()),
        "n_pooled": int(ok.sum()),
        "unpriced_2019_n": unpriced_2019,
        "note": (
            f"2019 is the stack's worst season ({mae_2019:.2f} vs {pooled_mae:.2f} pooled), "
            "consistent with it being the earliest season, the shortest Stage-1 "
            "history, and the smallest training window. The D4 encompassing test "
            "therefore ran on the least favorable season available. "
            f"n={unpriced_2019} unpriced games within 2019 supports no comparison."
        ),
    }


def sigma_bakeoff_paired_cis(
    frame: pd.DataFrame,
    *,
    n_boot: int = 1000,
    seed: int = 0,
    schemes: Sequence[str] = ("S0", "S1", "S4"),
) -> dict[str, Any]:
    """Paired week-block bootstrap CIs on CRPS deltas for S0/S1/S4."""
    bake = part2_bakeoff(frame)
    # Rebuild per-game σ arrays the same way as part2_bakeoff for paired scores.
    work = frame.copy()
    y = pd.to_numeric(work["realized_margin"], errors="coerce").to_numpy(dtype=float)
    mu = pd.to_numeric(work["pred_margin"], errors="coerce").to_numpy(dtype=float)
    sig = pd.to_numeric(work["sigma_m"], errors="coerce").to_numpy(dtype=float)
    season = work["season"].to_numpy(dtype=int)
    week = work["week"].to_numpy(dtype=int) if "week" in work.columns else np.zeros(len(work))
    mask = np.isfinite(y) & np.isfinite(mu) & np.isfinite(sig)
    y, mu, sig, season, week = y[mask], mu[mask], sig[mask], season[mask], week[mask]
    abs_r = np.abs(y - mu)
    seasons = sorted(int(s) for s in np.unique(season))
    store: dict[str, list[float]] = {s: [] for s in ("S0", "S1", "S2", "S3", "S4")}
    y_hold: list[float] = []
    mu_hold: list[float] = []
    blocks: list[tuple[int, int]] = []
    for test_s in seasons[1:]:
        tr = season < test_s
        te = season == test_s
        if not tr.any() or not te.any():
            continue
        resid_sd = float(np.std(y[tr] - mu[tr], ddof=0))
        s0 = np.full(int(te.sum()), resid_sd)
        s1 = sig[te]
        c = resid_sd / max(float(np.mean(sig[tr])), 1e-8)
        s2 = c * sig[te]
        from sklearn.linear_model import LinearRegression  # type: ignore[import-untyped]

        lr = LinearRegression().fit(sig[tr].reshape(-1, 1), abs_r[tr])
        intercept, slope = float(lr.intercept_), float(lr.coef_[0])
        s3 = np.maximum(intercept + slope * sig[te], 1e-6)
        s4 = np.empty(int(te.sum()), dtype=float)
        te_idx = np.where(te)[0]
        for j, i in enumerate(te_idx):
            w = int(week[i])
            bucket = week[tr] == w
            if int(bucket.sum()) >= 5:
                s4[j] = float(np.std(y[tr][bucket] - mu[tr][bucket], ddof=0))
            else:
                s4[j] = resid_sd
        for name, arr in (("S0", s0), ("S1", s1), ("S2", s2), ("S3", s3), ("S4", s4)):
            store[name].extend(arr.tolist())
        y_hold.extend(y[te].tolist())
        mu_hold.extend(mu[te].tolist())
        blocks.extend((int(test_s), int(week[i])) for i in te_idx)

    y_h = np.asarray(y_hold, dtype=float)
    mu_h = np.asarray(mu_hold, dtype=float)

    # Per-game CRPS contributions (Gaussian closed form via metrics helper on
    # length-1 slices is slow; use vectorized formula matching crps_gaussian).
    def _crps_per_game(yy: np.ndarray, mm: np.ndarray, ss: np.ndarray) -> np.ndarray:
        s = np.maximum(ss, 1e-8)
        z = (yy - mm) / s
        # CRPS(N(μ,σ²), y) = σ * (z(2Φ(z)-1) + 2φ(z) - 1/√π)
        out = s * (
            z * (2.0 * stats.norm.cdf(z) - 1.0) + 2.0 * stats.norm.pdf(z) - 1.0 / math.sqrt(math.pi)
        )
        return np.asarray(out, dtype=float)

    per: dict[str, np.ndarray] = {
        name: _crps_per_game(y_h, mu_h, np.asarray(store[name], dtype=float)) for name in schemes
    }
    # Sanity: mean matches bakeoff table within tolerance.
    table_by = {r["scheme"]: r for r in bake["table"]}
    pairwise: dict[str, Any] = {}
    for a, b in (("S1", "S0"), ("S4", "S0"), ("S4", "S1")):
        if a not in per or b not in per:
            continue
        ci = paired_block_bootstrap(per[a], per[b], blocks, n_boot=n_boot, alpha=0.05, seed=seed)
        pairwise[f"{a}_minus_{b}"] = {
            "delta_crps": ci.estimate,
            "ci_low": ci.ci_low,
            "ci_high": ci.ci_high,
            "n": ci.n,
            "overlaps_zero": bool(ci.ci_low <= 0.0 <= ci.ci_high),
        }

    # Prefer simplest when CIs overlap zero for S4−S1 and S4−S0 (or S1−S0).
    s4_s1 = pairwise.get("S4_minus_S1", {})
    s4_s0 = pairwise.get("S4_minus_S0", {})
    s1_s0 = pairwise.get("S1_minus_S0", {})
    indistinguishable = bool(
        s4_s1.get("overlaps_zero", True)
        and s4_s0.get("overlaps_zero", True)
        and s1_s0.get("overlaps_zero", True)
    )
    if indistinguishable:
        recommendation = (
            "S0/S1/S4 CRPS deltas have overlapping paired-bootstrap CIs "
            "(indistinguishable). Prefer S4 (week-bucket constant) on "
            "parsimony, not because it won."
        )
        prefer = "S4_parsimony"
    else:
        # Lowest point CRPS among the three wins if separated.
        best = min(schemes, key=lambda s: float(table_by[s]["crps"]))
        recommendation = f"Prefer {best} on CRPS (paired CIs separate schemes)."
        prefer = best

    return {
        "bakeoff_table": bake["table"],
        "pairwise_crps_delta": pairwise,
        "indistinguishable": indistinguishable,
        "recommendation": recommendation,
        "prefer": prefer,
        "mean_check": {
            s: {
                "per_game_mean": float(np.mean(per[s])),
                "bakeoff_crps": float(table_by[s]["crps"]),
                "matches": bool(abs(float(np.mean(per[s])) - float(table_by[s]["crps"])) < 1e-6),
            }
            for s in schemes
            if s in table_by
        },
    }


def detectable_b2_at_power(se_b2: float, *, power: float = 0.80, alpha: float = 0.05) -> float:
    """Minimum |b2| detectable at given power for the achieved SE."""
    z = float(stats.norm.ppf(1.0 - alpha / 2.0) + stats.norm.ppf(power))
    return float(z * abs(se_b2))


def _week_bucket(week: int) -> str:
    if week <= 4:
        return "1-4"
    if week <= 9:
        return "5-9"
    return "10+"


def _run_enc_slice(
    y: np.ndarray,
    mkt: np.ndarray,
    stk: np.ndarray,
    blocks: Sequence[Any],
    *,
    n_boot: int,
    seed: int,
    substantial_b2: float,
) -> dict[str, Any]:
    enc = encompassing_regression(y, mkt, stk, blocks, n_boot=n_boot, seed=seed)
    return {
        **enc.__dict__,
        "ci95": b2_wald_ci(enc.b2, enc.se_b2),
        "verdict_underpowered": underpowered_verdict(enc, substantial_edge=substantial_b2),
    }


def run_powered_encompassing(
    frame: pd.DataFrame,
    config: EncompassingEvalConfig,
    *,
    fbs_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    """Full-sample encompassing + stability slices + stop-rule verdict."""
    work = frame.loc[frame["season"].isin(config.seasons)].copy()
    if fbs_mask is not None:
        if len(fbs_mask) != len(frame):
            raise ValueError("fbs_mask length must match input frame")
        fbs_on_frame = fbs_mask.astype(bool)
        fbs_work = fbs_on_frame[frame["season"].isin(config.seasons).to_numpy()]
    else:
        fbs_work = None

    line = pd.to_numeric(work[config.line_column], errors="coerce")
    y = pd.to_numeric(work["realized_margin"], errors="coerce")
    stk = pd.to_numeric(work["pred_margin"], errors="coerce")
    mkt = config.market_implied_sign * line
    mask = np.isfinite(line) & np.isfinite(y) & np.isfinite(stk) & np.isfinite(mkt)
    work = work.loc[mask].copy()
    y_a = y[mask].to_numpy(dtype=float)
    mkt_a = mkt[mask].to_numpy(dtype=float)
    stk_a = stk[mask].to_numpy(dtype=float)
    seasons = work["season"].to_numpy(dtype=int)
    weeks = (
        work["week"].to_numpy(dtype=int)
        if "week" in work.columns
        else np.zeros(len(work), dtype=int)
    )
    blocks = list(zip(seasons.tolist(), weeks.tolist(), strict=True))
    fbs_a = fbs_work[mask.to_numpy()] if fbs_work is not None else None

    joint = encompassing_regression(
        y_a, mkt_a, stk_a, blocks, n_boot=config.n_boot, seed=config.seed
    )
    ror = residual_on_residual(y_a, mkt_a, stk_a)

    # Full-sample combination weight: fit w on all rows; MAE delta CI via
    # paired block bootstrap (in-sample w — reported honestly).
    grid = np.linspace(0.0, 1.0, 101)
    best_w, best_mae = 0.0, float("inf")
    for w in grid:
        pred = w * stk_a + (1.0 - w) * mkt_a
        err = float(np.mean(np.abs(y_a - pred)))
        if err < best_mae:
            best_mae, best_w = err, float(w)
    comb = best_w * stk_a + (1.0 - best_w) * mkt_a
    abs_comb = np.abs(y_a - comb)
    abs_mkt = np.abs(y_a - mkt_a)
    ci = paired_block_bootstrap(
        abs_comb, abs_mkt, blocks, n_boot=config.n_boot, alpha=0.05, seed=config.seed + 7
    )
    opt = {
        "w": best_w,
        "mae_combined": float(np.mean(abs_comb)),
        "mae_market": float(np.mean(abs_mkt)),
        "delta_mae": float(np.mean(abs_comb) - np.mean(abs_mkt)),
        "delta_ci": {"low": ci.ci_low, "high": ci.ci_high, "estimate": ci.estimate},
        "n": int(len(y_a)),
        "note": "w fit on full sample; delta CI is paired week-block bootstrap",
    }

    per_season: dict[str, Any] = {}
    for s in sorted(set(int(x) for x in seasons)):
        sel = seasons == s
        if int(sel.sum()) < 20:
            continue
        enc_s = _run_enc_slice(
            y_a[sel],
            mkt_a[sel],
            stk_a[sel],
            [blocks[i] for i, ok in enumerate(sel) if ok],
            n_boot=config.n_boot,
            seed=config.seed + int(s),
            substantial_b2=config.substantial_b2,
        )
        enc_s["n_games"] = int(sel.sum())
        enc_s["meets_min_games"] = bool(int(sel.sum()) >= config.min_games_per_season)
        per_season[str(s)] = enc_s

    # Week buckets
    by_week_bucket: dict[str, Any] = {}
    for label in ("1-4", "5-9", "10+"):
        sel = np.array([_week_bucket(int(w)) == label for w in weeks], dtype=bool)
        if int(sel.sum()) < 30:
            continue
        by_week_bucket[label] = _run_enc_slice(
            y_a[sel],
            mkt_a[sel],
            stk_a[sel],
            [blocks[i] for i, ok in enumerate(sel) if ok],
            n_boot=config.n_boot,
            seed=config.seed + hash(label) % 10_000,
            substantial_b2=config.substantial_b2,
        )
        by_week_bucket[label]["n"] = int(sel.sum())

    # Exclude 2019
    ex2019 = seasons != 2019
    exclude_2019 = None
    if int(ex2019.sum()) >= 30:
        exclude_2019 = _run_enc_slice(
            y_a[ex2019],
            mkt_a[ex2019],
            stk_a[ex2019],
            [blocks[i] for i, ok in enumerate(ex2019) if ok],
            n_boot=config.n_boot,
            seed=config.seed + 2019,
            substantial_b2=config.substantial_b2,
        )
        exclude_2019["n"] = int(ex2019.sum())

    fbs_vs_fbs = None
    if fbs_a is not None:
        sel = fbs_a.astype(bool)
        if int(sel.sum()) >= 30:
            fbs_vs_fbs = _run_enc_slice(
                y_a[sel],
                mkt_a[sel],
                stk_a[sel],
                [blocks[i] for i, ok in enumerate(sel) if ok],
                n_boot=config.n_boot,
                seed=config.seed + 99,
                substantial_b2=config.substantial_b2,
            )
            fbs_vs_fbs["n"] = int(sel.sum())

    positive_seasons = [
        s for s, row in per_season.items() if row.get("b2", 0) > 0 and row.get("p_b2", 1) < 0.10
    ]
    ci95 = b2_wald_ci(joint.b2, joint.se_b2)
    detectable = detectable_b2_at_power(joint.se_b2)
    power = {
        "b2_0.10": asdict(encompassing_power(joint.se_b2, joint.n, b2_target=0.10)),
        "b2_0.15": asdict(encompassing_power(joint.se_b2, joint.n, b2_target=0.15)),
        "detectable_b2_80pct": detectable,
        "detectable_above_0.10": bool(detectable > 0.10),
    }

    # Pre-registered stop rule evaluation (D5) — do not amend thresholds.
    ci_entirely_below_005 = bool(np.isfinite(ci95["high"]) and ci95["high"] < 0.05)
    n_stable = len(positive_seasons)
    stop_triggered = bool(ci_entirely_below_005 or n_stable < config.stability_min_seasons_positive)
    edge_declared = bool(
        joint.b2 > 0 and np.isfinite(joint.p_b2) and joint.p_b2 < 0.10 and ci95["low"] > 0.0
    )
    if edge_declared and not stop_triggered and n_stable >= config.stability_min_seasons_positive:
        rule_status = "met"
        verdict_sentence = (
            f"Pre-registered rule MET: joint b2={joint.b2:.4f} "
            f"(95% CI [{ci95['low']:.3f}, {ci95['high']:.3f}], p={joint.p_b2:.4f}, "
            f"n={joint.n}) with {n_stable} seasons of reliable positive b2."
        )
    elif stop_triggered:
        rule_status = "missed"
        verdict_sentence = (
            f"Pre-registered stop rule TRIGGERED (missed): "
            f"b2={joint.b2:.4f} 95% CI [{ci95['low']:.3f}, {ci95['high']:.3f}], "
            f"reliable-positive seasons={n_stable} "
            f"(need ≥{config.stability_min_seasons_positive}); "
            "stop pursuing a fundamental-model betting edge."
        )
    else:
        rule_status = "inconclusive"
        verdict_sentence = (
            f"Pre-registered rule INCONCLUSIVE: b2={joint.b2:.4f} "
            f"95% CI [{ci95['low']:.3f}, {ci95['high']:.3f}], p={joint.p_b2:.4f}, "
            f"n={joint.n}, reliable-positive seasons={n_stable}; "
            f"detectable |b2| at 80% power ≈ {detectable:.3f}."
        )

    return {
        "canonical_v2_sha": CANONICAL_V2_SHA,
        "config": asdict(config),
        "joint": {
            **joint.__dict__,
            "ci95": ci95,
            "verdict_underpowered": underpowered_verdict(
                joint, substantial_edge=config.substantial_b2
            ),
            "stack_mae": score_point(y_a, stk_a)["mae"],
            "market_mae": score_point(y_a, mkt_a)["mae"],
            "n": joint.n,
        },
        "residual_on_residual": ror,
        "optimal_combination": opt,
        "per_season": per_season,
        "by_week_bucket": by_week_bucket,
        "exclude_2019": exclude_2019,
        "fbs_vs_fbs": fbs_vs_fbs,
        "power": power,
        "stability": {
            "seasons_with_reliable_positive_b2": positive_seasons,
            "required": config.stability_min_seasons_positive,
            "stable": n_stable >= config.stability_min_seasons_positive,
        },
        "stop_rule": {
            "verbatim_from_d5": D5_STOP_RULE_VERBATIM,
            "ci_entirely_below_plus_0.05": ci_entirely_below_005,
            "n_reliable_positive_seasons": n_stable,
            "triggered": stop_triggered,
            "edge_declared": edge_declared,
            "status": rule_status,
            "verdict_sentence": verdict_sentence,
        },
    }


def load_cfbd_lines(staged_root: Path | str, seasons: Sequence[int] | None = None) -> pd.DataFrame:
    """Load staged ``lines_historical`` partitions."""
    root = Path(staged_root) / "lines_historical"
    paths = list(root.rglob("*.parquet"))
    if not paths:
        return pd.DataFrame()
    frames = [pd.read_parquet(p) for p in paths]
    out = pd.concat(frames, ignore_index=True)
    if seasons is not None and "season" in out.columns:
        out = out.loc[out["season"].isin(list(seasons))]
    return out


__all__ = [
    "CHANCE_LOG_LOSS",
    "D5_STOP_RULE_VERBATIM",
    "EVAL_ONLY_CLOSE_COLS",
    "assert_closes_eval_only",
    "assert_derived_market_signs",
    "detectable_b2_at_power",
    "diagnose_expected_possessions",
    "join_cfbd_closes_for_evaluation",
    "load_cfbd_lines",
    "post_join_line_coverage",
    "priced_vs_pooled_season_note",
    "run_powered_encompassing",
    "sigma_bakeoff_paired_cis",
    "validate_joined_closes",
]
