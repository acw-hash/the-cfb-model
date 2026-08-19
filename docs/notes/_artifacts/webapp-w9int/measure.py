"""W9-INT: empirical coverage of the published margin-interval construction.

Report only. No model, CQR, export, schema, or artifact write outside this folder.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
PARQUET = (
    ROOT
    / "data"
    / "backtests"
    / "task23_fundamental_reduced_v3"
    / "full"
    / "predictions.parquet"
)
OUT_DIR = Path(__file__).resolve().parent
CQR_CONSTANT = 6.837  # W9-D Amendment 1: champion _cqr.score_thresholds[0.8]
GAUSS_Z = 1.28  # task counterfactual: μ ± 1.28σ
ABS_MU_EDGES = (0.0, 7.0, 14.0, 21.0, 28.0, 35.0, np.inf)
ABS_MU_LABELS = ("[0,7)", "[7,14)", "[14,21)", "[21,28)", "[28,35)", "[35,∞)")
THIN_N = 50


def _finite(s: pd.Series) -> pd.Series:
    return np.isfinite(pd.to_numeric(s, errors="coerce"))


def _rate(hits: np.ndarray) -> dict[str, Any]:
    n = int(hits.size)
    k = int(np.sum(hits)) if n else 0
    return {"n": n, "hits": k, "coverage": (k / n) if n else None, "thin": n < THIN_N}


def _pos_summary(pos: np.ndarray) -> dict[str, Any]:
    finite = pos[np.isfinite(pos)]
    if finite.size == 0:
        return {"n": 0}
    outside = (finite < 0.25) | (finite > 0.75)
    return {
        "n": int(finite.size),
        "min": float(np.min(finite)),
        "p10": float(np.percentile(finite, 10)),
        "median": float(np.median(finite)),
        "p90": float(np.percentile(finite, 90)),
        "max": float(np.max(finite)),
        "n_outside_0.25_0.75": int(np.sum(outside)),
        "frac_outside_0.25_0.75": float(np.mean(outside)),
    }


def _abs_mu_bucket(abs_mu: np.ndarray) -> np.ndarray:
    return pd.cut(
        abs_mu,
        bins=list(ABS_MU_EDGES),
        labels=list(ABS_MU_LABELS),
        right=False,
        include_lowest=True,
    ).astype(str)


def _coverage_by(y: np.ndarray, lo: np.ndarray, hi: np.ndarray, keys: np.ndarray) -> list[dict[str, Any]]:
    rows = []
    for key in pd.unique(keys):
        m = keys == key
        inside = (y[m] >= lo[m]) & (y[m] <= hi[m])
        rec = _rate(inside)
        rec["key"] = key if not isinstance(key, (np.floating, float)) else float(key)
        rec["n_below_lo"] = int(np.sum(y[m] < lo[m]))
        rec["n_above_hi"] = int(np.sum(y[m] > hi[m]))
        rows.append(rec)
    return rows


def main() -> None:
    st = PARQUET.stat()
    df = pd.read_parquet(PARQUET)
    n_total = int(len(df))
    n_2025 = int((df["season"] == 2025).sum())
    if n_2025 != 0:
        raise SystemExit(f"STOP: N_2025={n_2025}")

    provenance = {
        "path": str(PARQUET.as_posix()),
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
        "size_bytes": int(st.st_size),
        "n_rows": n_total,
        "run_id": sorted({str(x) for x in df["run_id"].dropna().unique()}),
        "model_version": sorted({str(x) for x in df["model_version"].dropna().unique()}),
        "N_2025": n_2025,
        "seasons": {int(k): int(v) for k, v in df["season"].value_counts().sort_index().items()},
        "cqr_constant_applied": CQR_CONSTANT,
        "gauss_z": GAUSS_Z,
        "measured_at_utc": datetime.now(tz=UTC).isoformat(),
    }

    finite_mu = _finite(df["pred_margin"])
    finite_q10 = _finite(df["pred_margin_q10"])
    finite_q90 = _finite(df["pred_margin_q90"])
    finite_y = _finite(df["realized_margin"])
    finite_cqr = _finite(df["cqr_lo"]) & _finite(df["cqr_hi"])
    finite_sig = _finite(df["sigma_m"]) & (pd.to_numeric(df["sigma_m"], errors="coerce") > 0)

    denoms = {
        "n_parquet": n_total,
        "n_finite_mu": int(finite_mu.sum()),
        "n_finite_q10": int(finite_q10.sum()),
        "n_finite_q90": int(finite_q90.sum()),
        "n_finite_q10_and_q90": int((finite_q10 & finite_q90).sum()),
        "n_finite_realized_margin": int(finite_y.sum()),
        "n_finite_cqr_lo_hi": int(finite_cqr.sum()),
        "n_finite_sigma_m_gt0": int(finite_sig.sum()),
        "n_cqr_is_missing_true": int((df["cqr_is_missing"] == True).sum()),  # noqa: E712
        "n_null_reason_no_credible_members": int(
            (df["null_reason"] == "no_credible_members").sum()
        ),
        "n_missing_realized_margin": int((~finite_y).sum()),
        "n_eligible_published_construction": int(
            (finite_mu & finite_q10 & finite_q90 & finite_y).sum()
        ),
        "share_ineligible": 1.0
        - (
            int((finite_mu & finite_q10 & finite_q90 & finite_y).sum()) / n_total
            if n_total
            else 0.0
        ),
    }

    mask = finite_mu & finite_q10 & finite_q90 & finite_y
    s = df.loc[mask].copy()
    mu = s["pred_margin"].to_numpy(dtype=float)
    q10 = s["pred_margin_q10"].to_numpy(dtype=float)
    q90 = s["pred_margin_q90"].to_numpy(dtype=float)
    y = s["realized_margin"].to_numpy(dtype=float)
    sig = s["sigma_m"].to_numpy(dtype=float)
    q_lo = np.minimum(q10, q90)
    q_hi = np.maximum(q10, q90)
    lo = q_lo - CQR_CONSTANT
    hi = q_hi + CQR_CONSTANT
    stored_lo = s["cqr_lo"].to_numpy(dtype=float)
    stored_hi = s["cqr_hi"].to_numpy(dtype=float)
    implied_thr = q_lo - stored_lo
    gauss_lo = mu - GAUSS_Z * sig
    gauss_hi = mu + GAUSS_Z * sig
    width = hi - lo
    pos = np.divide(mu - lo, width, out=np.full_like(mu, np.nan), where=width > 0)
    abs_mu = np.abs(mu)
    buckets = _abs_mu_bucket(abs_mu)
    week1 = s["week"].to_numpy(dtype=int) == 1
    seasons = s["season"].to_numpy(dtype=int)
    inside = (y >= lo) & (y <= hi)

    sig_ok = np.isfinite(sig) & (sig > 0)
    sig_q = np.full(len(s), "sigma_missing", dtype=object)
    if sig_ok.any():
        quartiles = pd.qcut(sig[sig_ok], 4, labels=["Q1", "Q2", "Q3", "Q4"])
        sig_q[sig_ok] = np.asarray(quartiles.astype(str))

    reconstruct_vs_stored = {
        "n_with_stored_cqr": int(np.isfinite(stored_lo).sum()),
        "max_abs_lo_diff": float(np.nanmax(np.abs(lo - stored_lo))),
        "max_abs_hi_diff": float(np.nanmax(np.abs(hi - stored_hi))),
        "median_abs_lo_diff": float(np.nanmedian(np.abs(lo - stored_lo))),
        "implied_threshold_min": float(np.nanmin(implied_thr)),
        "implied_threshold_median": float(np.nanmedian(implied_thr)),
        "implied_threshold_max": float(np.nanmax(implied_thr)),
        "n_implied_thr_unique_rounded_1e6": int(np.unique(np.round(implied_thr, 6)).size),
        "n_q10_gt_q90": int((q10 > q90).sum()),
        "note": (
            "export.py copies cqr_lo/cqr_hi when present; those columns on this "
            "parquet use walk-forward CQR thresholds (not a single 6.837). "
            "Tuesday publish uses champion 6.837 on sorted q10/q90. Primary "
            "tables reconstruct that champion construction."
        ),
    }

    def pack_cov(lo_a: np.ndarray, hi_a: np.ndarray, extra_mask: np.ndarray | None = None) -> dict[str, Any]:
        m = np.ones(len(y), dtype=bool) if extra_mask is None else extra_mask
        yy, llo, hhi = y[m], lo_a[m], hi_a[m]
        ins = (yy >= llo) & (yy <= hhi)
        rec = _rate(ins)
        rec["n_below_lo"] = int(np.sum(yy < llo))
        rec["n_above_hi"] = int(np.sum(yy > hhi))
        rec["n_miss"] = rec["n_below_lo"] + rec["n_above_hi"]
        return rec

    overall = pack_cov(lo, hi)
    by_abs_mu = _coverage_by(y, lo, hi, buckets)
    by_abs_mu.sort(key=lambda r: ABS_MU_LABELS.index(str(r["key"])) if str(r["key"]) in ABS_MU_LABELS else 99)
    by_season = sorted(_coverage_by(y, lo, hi, seasons), key=lambda r: int(r["key"]))
    by_week = [
        {**pack_cov(lo, hi, week1), "key": "week=1"},
        {**pack_cov(lo, hi, ~week1), "key": "week>=2"},
    ]
    by_sigma_q = sorted(_coverage_by(y, lo, hi, sig_q), key=lambda r: str(r["key"]))

    coherence = {
        "n": int(len(s)),
        "n_q10_lt_mu_lt_q90": int(np.sum((q10 < mu) & (mu < q90))),
        "frac_q10_lt_mu_lt_q90": float(np.mean((q10 < mu) & (mu < q90))),
        "n_q90_lt_mu": int(np.sum(q90 < mu)),
        "n_mu_lt_q10": int(np.sum(mu < q10)),
        "n_published_lo_lt_mu_lt_hi": int(np.sum((lo < mu) & (mu < hi))),
        "frac_published_lo_lt_mu_lt_hi": float(np.mean((lo < mu) & (mu < hi))),
        "n_mu_above_published_hi": int(np.sum(mu >= hi)),
        "n_mu_below_published_lo": int(np.sum(mu <= lo)),
        "by_abs_mu": [],
    }
    for lab in ABS_MU_LABELS:
        m = buckets == lab
        coherence["by_abs_mu"].append(
            {
                "key": lab,
                "n": int(m.sum()),
                "frac_q10_lt_mu_lt_q90": float(np.mean((q10[m] < mu[m]) & (mu[m] < q90[m])))
                if m.any()
                else None,
                "n_q90_lt_mu": int(np.sum(q90[m] < mu[m])),
                "frac_published_lo_lt_mu_lt_hi": float(np.mean((lo[m] < mu[m]) & (mu[m] < hi[m])))
                if m.any()
                else None,
            }
        )

    pos_overall = _pos_summary(pos)
    pos_by_abs_mu = []
    for lab in ABS_MU_LABELS:
        m = buckets == lab
        rec = _pos_summary(pos[m])
        rec["key"] = lab
        pos_by_abs_mu.append(rec)

    miss = {
        "overall": {
            "n_miss": int(np.sum(~inside)),
            "n_below_lo": int(np.sum(y < lo)),
            "n_above_hi": int(np.sum(y > hi)),
        },
        "by_abs_mu": [],
    }
    for lab in ABS_MU_LABELS:
        m = buckets == lab
        miss["by_abs_mu"].append(
            {
                "key": lab,
                "n": int(m.sum()),
                "n_miss": int(np.sum(~inside[m])),
                "n_below_lo": int(np.sum(y[m] < lo[m])),
                "n_above_hi": int(np.sum(y[m] > hi[m])),
            }
        )

    gauss_ok = sig_ok
    counterfactual = {
        "raw_sorted_q10_q90_no_cqr": pack_cov(q_lo, q_hi),
        "mu_pm_1.28_sigma": pack_cov(gauss_lo, gauss_hi, gauss_ok),
        "stored_walkforward_cqr_lo_hi": pack_cov(stored_lo, stored_hi),
        "published_sorted_q_pm_6.837": overall,
        "by_abs_mu": {
            "published": by_abs_mu,
            "raw_q": _coverage_by(y, q_lo, q_hi, buckets),
            "gauss": _coverage_by(y[gauss_ok], gauss_lo[gauss_ok], gauss_hi[gauss_ok], buckets[gauss_ok]),
        },
    }
    for name in ("raw_q", "gauss"):
        counterfactual["by_abs_mu"][name].sort(
            key=lambda r: ABS_MU_LABELS.index(str(r["key"])) if str(r["key"]) in ABS_MU_LABELS else 99
        )

    n_mu_ge_28 = int((abs_mu >= 28).sum())
    n_mu_ge_35 = int((abs_mu >= 35).sum())
    week1_mu_ge_28 = int(((abs_mu >= 28) & week1).sum())
    week1_specificity = {
        "n_analysis": int(len(s)),
        "n_week1": int(week1.sum()),
        "n_abs_mu_ge_28": n_mu_ge_28,
        "frac_abs_mu_ge_28": n_mu_ge_28 / len(s) if len(s) else None,
        "n_abs_mu_ge_35": n_mu_ge_35,
        "frac_abs_mu_ge_35": n_mu_ge_35 / len(s) if len(s) else None,
        "n_week1_abs_mu_ge_28": week1_mu_ge_28,
        "frac_week1_that_are_abs_mu_ge_28": (week1_mu_ge_28 / int(week1.sum())) if week1.any() else None,
        "frac_analysis_that_are_week1_and_abs_mu_ge_28": week1_mu_ge_28 / len(s) if len(s) else None,
        "coverage_week1": pack_cov(lo, hi, week1),
        "coverage_week1_abs_mu_ge_28": pack_cov(lo, hi, week1 & (abs_mu >= 28)),
        "coverage_abs_mu_ge_28": pack_cov(lo, hi, abs_mu >= 28),
        "coverage_week1_by_abs_mu": sorted(
            _coverage_by(y[week1], lo[week1], hi[week1], buckets[week1]),
            key=lambda r: ABS_MU_LABELS.index(str(r["key"])) if str(r["key"]) in ABS_MU_LABELS else 99,
        ),
        "2026_week1_reference": {
            "n_published_games": 91,
            "n_abs_mu_ge_28": 53,
            "frac_abs_mu_ge_28": 53 / 91,
        },
    }

    payload = {
        "provenance": provenance,
        "denominators": denoms,
        "reconstruct_vs_stored_cqr": reconstruct_vs_stored,
        "1_coverage_published": {
            "overall": overall,
            "by_abs_mu": by_abs_mu,
            "by_season": by_season,
            "by_week": by_week,
            "by_sigma_quartile": by_sigma_q,
        },
        "2_coherence": coherence,
        "3_position": {"overall": pos_overall, "by_abs_mu": pos_by_abs_mu},
        "4_miss_asymmetry": miss,
        "5_counterfactual": counterfactual,
        "6_week1_specificity": week1_specificity,
    }
    out = OUT_DIR / "coverage.json"
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"wrote": str(out), "n_eligible": denoms["n_eligible_published_construction"], "coverage": overall}, indent=2))


if __name__ == "__main__":
    main()
