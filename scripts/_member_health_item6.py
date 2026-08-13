"""Item 6 reporting for MEMBER-HEALTH-FIX Tuesday re-run."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.metrics import ats_home_outcomes, log_loss

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "docs" / "notes" / "_artifacts" / "member_health_fix"
RUN = ROOT / "data/backtests/task23_market_aware_reduced_v2_tue/full"
A3 = ROOT / "data/backtests/task23_a3_reduced_v2/A3_market_off/predictions.parquet"


def _intentional_null_mask(frame: pd.DataFrame) -> pd.Series:
    if "null_reason" not in frame.columns:
        return pd.Series(False, index=frame.index)
    r = frame["null_reason"]
    return (
        r.notna()
        & r.astype(str).str.len().gt(0)
        & (r.astype(str) != "nan")
        & (r.astype(str) != "None")
    )


def _ats_stats(frame: pd.DataFrame) -> dict[str, float | int]:
    y = ats_home_outcomes(
        frame["realized_margin"].to_numpy(dtype=float),
        frame["spread_close"].to_numpy(dtype=float),
    )
    p = frame["p_ats_home"].to_numpy(dtype=float)
    m = np.isfinite(y) & np.isfinite(p)
    if not np.any(m):
        return {"ats": float("nan"), "n": 0, "ll": float("nan"), "mae": float("nan"), "lo": float("nan"), "hi": float("nan")}
    hits = (p[m] >= 0.5).astype(float) == y[m]
    rate = float(hits.mean())
    n = int(m.sum())
    ll = float(log_loss(np.clip(p[m], 1e-12, 1.0 - 1e-12), y[m]))
    mae = float(
        np.abs(
            pd.to_numeric(frame["pred_margin"], errors="coerce") - frame["realized_margin"]
        )
        .dropna()
        .mean()
    )
    rng = np.random.default_rng(0)
    idx = np.flatnonzero(m)
    boots = []
    for _ in range(2000):
        b = rng.choice(idx, size=len(idx), replace=True)
        boots.append(float(((p[b] >= 0.5).astype(float) == y[b]).mean()))
    lo = float(np.quantile(boots, 0.025))
    hi = float(np.quantile(boots, 0.975))
    return {"ats": rate, "n": n, "ll": ll, "mae": mae, "lo": lo, "hi": hi}


def main() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    preds = pd.read_parquet(RUN / "predictions.parquet")
    man = json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))
    extra = man.get("extra") or {}
    gate = json.loads(extra.get("quality_gate") or "{}")
    nnls = json.loads(extra.get("nnls_fold_reports") or "[]")

    focus_rows: list[dict[str, object]] = []
    for season, weeks in ((2019, [1, 2, 3, 4]), (2023, list(range(1, 11)))):
        for week in weeks:
            sub = preds.loc[(preds["season"] == season) & (preds["week"] == week)]
            if sub.empty:
                focus_rows.append(
                    {
                        "season": season,
                        "week": week,
                        "status": "ABSENT",
                        "n": 0,
                        "n_finite_mu": 0,
                        "sd_mu": None,
                        "null_reasons": {},
                        "w_lgbm": None,
                        "w_enet": None,
                        "lgbm_credible": None,
                        "enet_credible": None,
                    }
                )
                continue
            mu = pd.to_numeric(sub["pred_margin"], errors="coerce")
            n_finite = int(mu.notna().sum())
            sd = float(mu.dropna().std(ddof=0)) if n_finite >= 2 else float("nan")
            nr = (
                sub.loc[_intentional_null_mask(sub), "null_reason"]
                .astype(str)
                .value_counts()
                .to_dict()
            )
            def _w(col: str) -> float:
                if col not in sub.columns:
                    return float("nan")
                s = pd.to_numeric(sub[col], errors="coerce").dropna()
                return float(s.iloc[0]) if len(s) else float("nan")

            focus_rows.append(
                {
                    "season": season,
                    "week": week,
                    "status": "NULL" if nr else "OK",
                    "n": int(len(sub)),
                    "n_finite_mu": n_finite,
                    "sd_mu": sd,
                    "null_reasons": nr,
                    "w_lgbm": _w("w_lgbm_mu_margin"),
                    "w_enet": _w("w_enet_mu_margin"),
                    "lgbm_credible": (
                        bool(sub["lgbm_credible"].iloc[0])
                        if "lgbm_credible" in sub.columns
                        else None
                    ),
                    "enet_credible": (
                        bool(sub["enet_credible"].iloc[0])
                        if "enet_credible" in sub.columns
                        else None
                    ),
                }
            )

    scored = preds.loc[~_intentional_null_mask(preds)].copy()
    if "exclude_from_headline" in scored.columns:
        scored = scored.loc[~scored["exclude_from_headline"].fillna(False).astype(bool)]
    cfbd = scored.loc[scored["season"].astype(int) == 2019]
    snap = scored.loc[scored["season"].astype(int).isin([2021, 2022, 2023, 2024])]
    stats_2019 = _ats_stats(cfbd)
    stats_snap = _ats_stats(snap)

    a3 = pd.read_parquet(A3)
    if "exclude_from_headline" in a3.columns:
        a3 = a3.loc[~a3["exclude_from_headline"].fillna(False).astype(bool)]
    a3_2019 = _ats_stats(a3.loc[a3["season"].astype(int) == 2019])
    a3_snap = _ats_stats(a3.loc[a3["season"].astype(int).isin([2021, 2022, 2023, 2024])])

    # Guard bands from prior notes
    band_2019 = (0.445, 0.555)
    band_snap = (0.4746, 0.5254)
    def disposition(rate: float, band: tuple[float, float]) -> str:
        if not np.isfinite(rate):
            return "NO_GRADED"
        if band[0] <= rate <= band[1]:
            return "INSIDE_BAND_PUBLISHED"
        return "OUTSIDE_BAND"

    ungrad = preds.loc[_intentional_null_mask(preds)]
    ungrad_blocks = (
        ungrad.groupby(["season", "week", "null_reason"], sort=True)
        .size()
        .reset_index(name="n")
        .to_dict(orient="records")
        if not ungrad.empty
        else []
    )

    # Member status for first few folds (cold-start + 2023 offseason/w5)
    fold_snip = []
    for i, fold in enumerate(nnls[:6]):
        fold_snip.append(
            {
                "fold_index": i,
                "weights": fold.get("weights"),
                "member_status": fold.get("member_status"),
                "n_oof_rows": fold.get("n_oof_rows"),
                "n_train_labels": fold.get("n_train_labels"),
            }
        )

    cold_start_blocks = [
        b
        for b in focus_rows
        if isinstance(b.get("null_reasons"), dict)
        and "cold_start_insufficient" in b["null_reasons"]
    ]
    no_cred_blocks = [
        b
        for b in focus_rows
        if isinstance(b.get("null_reasons"), dict)
        and "no_credible_members" in b["null_reasons"]
    ]

    payload = {
        "n_predictions": int(len(preds)),
        "gate": gate,
        "focus_blocks": focus_rows,
        "ats_table": {
            "cfbd_2019": stats_2019,
            "snapshots_2021_2024": stats_snap,
            "band_2019": list(band_2019),
            "band_snap": list(band_snap),
            "disposition_2019": disposition(float(stats_2019["ats"]), band_2019),
            "disposition_snap": disposition(float(stats_snap["ats"]), band_snap),
        },
        "aware_vs_a3": {
            "cfbd_2019": {
                "aware_ats": stats_2019["ats"],
                "aware_n": stats_2019["n"],
                "a3_ats": a3_2019["ats"],
                "a3_n": a3_2019["n"],
                "delta_pp": (float(stats_2019["ats"]) - float(a3_2019["ats"])) * 100.0,
            },
            "snapshots_2021_2024": {
                "aware_ats": stats_snap["ats"],
                "aware_n": stats_snap["n"],
                "a3_ats": a3_snap["ats"],
                "a3_n": a3_snap["n"],
                "delta_pp": (float(stats_snap["ats"]) - float(a3_snap["ats"])) * 100.0,
            },
        },
        "ungradable_blocks": ungrad_blocks,
        "n_cold_start_insufficient_focus": len(cold_start_blocks),
        "n_no_credible_focus": len(no_cred_blocks),
        "nnls_fold_snip": fold_snip,
        "wall_clock_sec": extra.get("wall_clock_sec"),
        "label": extra.get("label"),
    }
    (ART / "item6_report.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
