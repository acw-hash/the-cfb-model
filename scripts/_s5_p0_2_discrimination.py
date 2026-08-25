#!/usr/bin/env python3
"""S5 P0-2 — Per-game ATS discrimination (not calibration).

Read-only. Decides whether H4 is a selection problem or collapses into H5.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from sklearn.metrics import roc_auc_score

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts._s5_common import (  # noqa: E402
    BACKTEST_ROOT,
    SEASONS,
    assert_rows_match_s4,
    brier_skill_score,
    dump_json,
    ensure_out,
    load_graded_314,
    load_public_314_from_cache,
    wilson_interval,
)
from ncaa_quant.evaluation.lockbox import assert_lockbox_excluded  # noqa: E402

N_BOOT = 2000
SEED = 42
N_BINS = 5
BREAKEVEN_M110 = 110.0 / (100.0 + 110.0)  # 0.523809...


def _load_walkforward() -> pd.DataFrame:
    assert_lockbox_excluded(list(SEASONS), context="S5 P0-2 walkforward")
    frames = []
    for path in sorted(BACKTEST_ROOT.glob("season=*_week=*.parquet")):
        parts = dict(p.split("=") for p in path.stem.split("_"))
        season, week = int(parts["season"]), int(parts["week"])
        if season not in SEASONS:
            continue
        df = pd.read_parquet(path)
        df = df.copy()
        df["season"] = season
        df["week"] = week
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    return out


def _equal_count_bins(claim: np.ndarray, y: np.ndarray, pnl: np.ndarray) -> list[dict[str, Any]]:
    n = len(claim)
    order = np.argsort(claim, kind="mergesort")
    claim_s, y_s, pnl_s = claim[order], y[order], pnl[order]
    # five equal-count bins (remainder distributed to last bins)
    edges = np.linspace(0, n, N_BINS + 1, dtype=int)
    rows = []
    for b in range(N_BINS):
        lo, hi = int(edges[b]), int(edges[b + 1])
        c = claim_s[lo:hi]
        yy = y_s[lo:hi]
        pp = pnl_s[lo:hi]
        k = int(yy.sum())
        nn = int(len(yy))
        rate = float(yy.mean()) if nn else float("nan")
        wlo, whi = wilson_interval(k, nn)
        rows.append(
            {
                "bin": b + 1,
                "n": nn,
                "mean_claim": float(c.mean()) if nn else None,
                "realized_cover_rate": rate,
                "wilson_95": [wlo, whi],
                "unit_pnl": float(pp.sum()) if nn else None,
                "n_cover": k,
            }
        )
    return rows


def _auc_boot(y: np.ndarray, scores: np.ndarray) -> dict[str, Any]:
    y = np.asarray(y, dtype=int)
    scores = np.asarray(scores, dtype=float)
    if len(np.unique(y)) < 2:
        return {"auc": float("nan"), "ci_95": [None, None], "n": int(len(y))}
    point = float(roc_auc_score(y, scores))
    rng = np.random.default_rng(SEED)
    boots = []
    n = len(y)
    for _ in range(N_BOOT):
        idx = rng.integers(0, n, size=n)
        yy, ss = y[idx], scores[idx]
        if len(np.unique(yy)) < 2:
            continue
        boots.append(float(roc_auc_score(yy, ss)))
    arr = np.asarray(boots, dtype=float)
    return {
        "auc": point,
        "ci_95": [float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))]
        if arr.size
        else [None, None],
        "n_boot_used": int(arr.size),
        "n": int(n),
    }


def _spearman_boot(claim: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    claim = np.asarray(claim, dtype=float)
    y = np.asarray(y, dtype=float)
    rho, pval = sp_stats.spearmanr(claim, y)
    rng = np.random.default_rng(SEED)
    boots = []
    n = len(y)
    for _ in range(N_BOOT):
        idx = rng.integers(0, n, size=n)
        r, _ = sp_stats.spearmanr(claim[idx], y[idx])
        if np.isfinite(r):
            boots.append(float(r))
    arr = np.asarray(boots, dtype=float)
    return {
        "spearman_rho": float(rho),
        "pvalue": float(pval),
        "ci_95": [float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))]
        if arr.size
        else [None, None],
        "n": int(n),
    }


def _bins_flat_readable(bins: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether pattern is distinguishable from flat given Wilson CIs."""
    rates = [b["realized_cover_rate"] for b in bins]
    cis = [b["wilson_95"] for b in bins]
    # crude: do any CI pairs fail to overlap between bottom and top bin?
    bottom, top = cis[0], cis[-1]
    separated = top[0] > bottom[1] or bottom[0] > top[1]
    # monotonic non-decreasing realized rates?
    mono = all(rates[i] <= rates[i + 1] + 1e-15 for i in range(len(rates) - 1))
    return {
        "binomial_se_approx_per_bin": float(np.sqrt(0.5 * 0.5 / 63)),
        "top_vs_bottom_wilson_separated": separated,
        "realized_rates_monotonic_nondecreasing": mono,
        "top_bin_above_breakeven_0_524": rates[-1] > BREAKEVEN_M110,
        "distinguishable_from_flat": separated,
        "note": (
            "With ~63/bin, SE≈0.063. Do not claim a trend the CIs do not support."
        ),
    }


def main() -> int:
    out = ensure_out()
    graded = load_graded_314()
    _, bets = load_public_314_from_cache()
    match = assert_rows_match_s4(graded, bets)
    print(f"[p0-2] row match ok n={match['n']}", flush=True)

    y = graded["covered"].astype(int).to_numpy()
    claim = graded["p_win"].to_numpy(dtype=float)
    pnl = graded["u_pnl"].to_numpy(dtype=float)
    assert int(graded["pushed"].sum()) == 0

    bins = _equal_count_bins(claim, y, pnl)
    flat = _bins_flat_readable(bins)
    auc_314 = _auc_boot(y, claim)
    spear_314 = _spearman_boot(claim, y)

    print("[p0-2] loading walk-forward frames …", flush=True)
    wf = _load_walkforward()
    # p_ats_home vs home cover
    ats = wf.loc[
        wf["p_ats_home"].notna()
        & wf["realized_margin"].notna()
        & wf["spread_close"].notna()
    ].copy()
    adj = ats["realized_margin"].to_numpy(dtype=float) + ats["spread_close"].to_numpy(
        dtype=float
    )
    push = np.abs(adj) < 1e-12
    ats = ats.loc[~push].copy()
    y_home = (
        ats["realized_margin"].to_numpy(dtype=float)
        + ats["spread_close"].to_numpy(dtype=float)
        > 0
    ).astype(int)
    p_ats = ats["p_ats_home"].to_numpy(dtype=float)
    auc_ats = _auc_boot(y_home, p_ats)
    spear_ats = _spearman_boot(p_ats, y_home.astype(float))

    # ML on all WF
    ml = wf.loc[wf["p_ml_home"].notna() & wf["realized_margin"].notna()].copy()
    y_ml = (ml["realized_margin"].to_numpy(dtype=float) > 0).astype(int)
    p_ml = ml["p_ml_home"].to_numpy(dtype=float)

    # Join p_ml_home onto the 314 fixtures
    wf_idx = wf.drop_duplicates(subset=["game_id"], keep="last").copy()
    wf_idx["game_id"] = wf_idx["game_id"].astype(str)
    g314 = graded.copy()
    g314["game_id"] = g314["game_id"].astype(str)
    g314 = g314.merge(
        wf_idx[["game_id", "p_ml_home", "p_ats_home", "realized_margin"]].rename(
            columns={"realized_margin": "rm_wf"}
        ),
        on="game_id",
        how="left",
    )
    ml_314 = g314.loc[g314["p_ml_home"].notna() & g314["realized_margin"].notna()]
    y_ml_314 = (ml_314["realized_margin"].to_numpy(dtype=float) > 0).astype(int)
    p_ml_314 = ml_314["p_ml_home"].to_numpy(dtype=float)
    auc_ml_314 = _auc_boot(y_ml_314, p_ml_314)

    bss = {
        "p_ats_home_all_games": brier_skill_score(p_ats, y_home.astype(float)),
        "p_ml_home_all_games": brier_skill_score(p_ml, y_ml.astype(float)),
        "p_ml_home_on_314": brier_skill_score(p_ml_314, y_ml_314.astype(float)),
        "p_win_on_314": brier_skill_score(claim, y.astype(float)),
    }
    # attach n
    bss["p_ats_home_all_games"]["n"] = int(len(p_ats))
    bss["p_ml_home_all_games"]["n"] = int(len(p_ml))
    bss["p_ml_home_on_314"]["n"] = int(len(p_ml_314))
    bss["p_win_on_314"]["n"] = int(len(claim))

    ats_near_half = abs(auc_314["auc"] - 0.5) < 0.03
    ml_near_half = abs(auc_ml_314["auc"] - 0.5) < 0.03
    instrument_suspect = ats_near_half and ml_near_half

    if instrument_suspect:
        reading = (
            "STOP - both ATS and ML AUC near 0.5 on the 314; instrument suspect."
        )
        status = "STOP_INSTRUMENT_SUSPECT"
    elif auc_314["auc"] < 0.53 and bss["p_win_on_314"]["bss"] <= 0:
        reading = (
            "AUC ~ 0.5 and BSS <= 0 -> no per-game ATS discrimination exists; "
            "H4 and H5 are the same finding and no selection rule fixes it."
        )
        status = "MEASURED_NO_DISCRIMINATION"
    elif auc_314["auc"] >= 0.53:
        reading = (
            "AUC materially above 0.5 -> discrimination exists and H4 stands as a rule problem."
        )
        status = "MEASURED_DISCRIMINATION_EXISTS"
    else:
        reading = (
            f"AUC={auc_314['auc']:.3f} with BSS={bss['p_win_on_314']['bss']:.4f}; "
            "borderline - see CI and bin table."
        )
        status = "MEASURED_BORDERLINE"

    payload = {
        "status": status,
        "should_hold": (
            "If there is anything to select on: realized cover rate rises monotonically "
            f"with claim, and the top bin sits above -110 break-even ({BREAKEVEN_M110:.4f})."
        ),
        "row_match": match,
        "breakeven_m110": BREAKEVEN_M110,
        "bins_314": bins,
        "bins_flatness": flat,
        "auc_p_win_vs_cover_314": auc_314,
        "spearman_p_win_vs_cover_314": spear_314,
        "all_games_p_ats_home": {
            "n": int(len(p_ats)),
            "auc": auc_ats,
            "spearman": spear_ats,
            "mean_claim": float(p_ats.mean()),
            "realized_home_cover": float(y_home.mean()),
        },
        "bss": bss,
        "instrument_control_p_ml_home_on_314": {
            "n": int(len(p_ml_314)),
            "auc": auc_ml_314,
            "ats_auc_314": auc_314["auc"],
            "ml_auc_314": auc_ml_314["auc"],
            "instrument_suspect": instrument_suspect,
        },
        "one_sentence_reading": reading,
    }
    dump_json(out / "p0_2_discrimination.json", payload)
    print(
        f"[p0-2] AUC_ats_314={auc_314['auc']:.4f} AUC_ml_314={auc_ml_314['auc']:.4f}",
        flush=True,
    )
    print(f"[p0-2] status={status}", flush=True)
    if instrument_suspect:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
