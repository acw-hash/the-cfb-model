"""Compare 2026 dry week-1 mu/p_win to v3 backtest week 1. No metrics on 2025."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent


def main() -> None:
    pred = pd.read_parquet(
        ROOT / "data" / "backtests" / "task23_fundamental_reduced_v3" / "full" / "predictions.parquet"
    )
    w1 = pred.loc[pred["week"].astype(int) == 1].copy()
    out: dict = {
        "v3_w1_n": int(len(w1)),
        "v3_w1_seasons": sorted(int(s) for s in w1["season"].unique()),
        "by_season": {},
    }
    mu_all = pd.to_numeric(w1["pred_margin"], errors="coerce")
    out["v3_w1_mu"] = {"min": float(mu_all.min()), "max": float(mu_all.max())}
    if "sigma_m" in w1.columns:
        sig = pd.to_numeric(w1["sigma_m"], errors="coerce")
        out["v3_w1_sigma"] = {
            "min": float(sig.min()),
            "max": float(sig.max()),
            "finite": int(sig.notna().sum()),
        }
    if "p_ml_home" in w1.columns:
        p_all = pd.to_numeric(w1["p_ml_home"], errors="coerce")
        out["v3_w1_p_ml"] = {
            "gt_085": int((p_all > 0.85).sum()),
            "lt_015": int((p_all < 0.15).sum()),
            "strong_like": int(((p_all > 0.85) | (p_all < 0.15)).sum()),
            "n": int(p_all.notna().sum()),
        }
    for season, sub in w1.groupby(w1["season"].astype(int)):
        m = pd.to_numeric(sub["pred_margin"], errors="coerce")
        rec = {
            "n": int(len(sub)),
            "mu_min": float(m.min()),
            "mu_max": float(m.max()),
            "abs_mu_gt_19": int((m.abs() > 19).sum()),
            "abs_mu_gt_30": int((m.abs() > 30).sum()),
        }
        if "p_ml_home" in sub.columns:
            p = pd.to_numeric(sub["p_ml_home"], errors="coerce")
            rec["p_gt_085"] = int((p > 0.85).sum())
            rec["p_lt_015"] = int((p < 0.15).sum())
            rec["strong_like"] = int(((p > 0.85) | (p < 0.15)).sum())
        out["by_season"][str(int(season))] = rec

    wp = json.loads((ART / "dry_export" / "week_predictions.json").read_text(encoding="utf-8"))
    games = wp["games"]
    pwin = [float(g["p_win_home"]) for g in games if g.get("p_win_home") is not None]
    mus = [float(g["mu_margin"]) for g in games if g.get("mu_margin") is not None]
    tiers: dict[str, int] = {}
    for g in games:
        t = str(g.get("conviction_tier"))
        tiers[t] = tiers.get(t, 0) + 1
    top = sorted(games, key=lambda g: abs(float(g.get("mu_margin") or 0)), reverse=True)[:8]
    out["dry_2026_w1"] = {
        "n": len(games),
        "p_gt_085": sum(1 for x in pwin if x > 0.85),
        "p_lt_015": sum(1 for x in pwin if x < 0.15),
        "strong_like": sum(1 for x in pwin if x > 0.85 or x < 0.15),
        "abs_mu_gt_19": sum(1 for x in mus if abs(x) > 19),
        "abs_mu_gt_30": sum(1 for x in mus if abs(x) > 30),
        "tiers": tiers,
        "largest_abs_mu": [
            {
                "game_id": g["game_id"],
                "matchup": f"{g.get('away_team')} @ {g.get('home_team')}",
                "mu": g.get("mu_margin"),
                "p_win_home": g.get("p_win_home"),
                "tier": g.get("conviction_tier"),
            }
            for g in top
        ],
    }
    print(json.dumps(out, indent=2))
    (ART / "w1_compare.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
