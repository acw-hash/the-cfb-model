"""Task 23-FIX-DIAG — diagnosis only on staged 2023 partitions. Not a production entrypoint."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.evaluation.metrics import (
    compute_metric_suite,
    interval_coverage_and_width,
    log_loss,
    log_loss_per_row,
    mae,
    pit_values,
    resolve_market_baselines,
    su_outcomes,
)
from ncaa_quant.ingestion.odds_api import plan_historical_units
from ncaa_quant.ratings.elo_baseline import one_step_log_loss, run_elo

ROOT = Path(__file__).resolve().parents[1]
PRED_PATH = ROOT / "data/backtests/task23_fix_smoke/wiring_proof_2023/full/predictions.parquet"
OUT = ROOT / "docs/notes/_artifacts/task23_fix"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    preds = pd.read_parquet(PRED_PATH)
    print("=== D-1 TRACE ===")
    print("p_mkt_ml_home finite", int(preds["p_mkt_ml_home"].notna().sum()), "/", len(preds))
    print("p_mkt_ats_home unique", preds["p_mkt_ats_home"].unique().tolist())
    print("spread_close finite", int(preds["spread_close"].notna().sum()))
    print("line_source_close", preds["line_source_close"].value_counts(dropna=False).to_dict())

    res0 = resolve_market_baselines(preds)
    print("resolve without ML prices:", res0.ml_status, res0.ml_reason[:140])
    suite0 = compute_metric_suite(preds)
    assert suite0.logloss_ml is not None
    print(
        "suite0 logloss_ml model/market",
        suite0.logloss_ml.model,
        suite0.logloss_ml.market,
    )
    print("suite0 extras", suite0.extras.get("market_baseline"))

    store = ParquetStore(str(ROOT / "data/staged"))
    lines = store.read("lines_historical", filters={"season": 2023})
    close = lines.loc[lines["line_type"].astype(str).str.casefold() == "close"].copy()
    close_both = close.dropna(subset=["home_ml", "away_ml"])
    agg = close_both.groupby("game_id", as_index=False).agg(
        home_ml=("home_ml", "median"),
        away_ml=("away_ml", "median"),
    )
    enriched = preds.merge(agg[["game_id", "home_ml", "away_ml"]], on="game_id", how="left")
    print("enriched ML finite pairs", int(enriched[["home_ml", "away_ml"]].dropna().shape[0]))

    res1 = resolve_market_baselines(enriched)
    print("resolve with CFBD ML:", res1.ml_status, "n_finite", res1.n_ml_finite)
    suite1 = compute_metric_suite(enriched)
    assert suite1.logloss_ml is not None and suite1.brier_ml is not None
    print("suite1 logloss_ml model/market", suite1.logloss_ml.model, suite1.logloss_ml.market)
    print("suite1 brier_ml model/market", suite1.brier_ml.model, suite1.brier_ml.market)

    mask = enriched["home_ml"].notna() & enriched["away_ml"].notna()
    y = su_outcomes(
        enriched.loc[mask, "home_points"].to_numpy(),
        enriched.loc[mask, "away_points"].to_numpy(),
    )
    p_model = enriched.loc[mask, "p_ml_home"].to_numpy(dtype=float)
    assert res1.p_mkt_ml_home is not None
    p_mkt = res1.p_mkt_ml_home[mask.to_numpy()]
    overlap_model_ll = log_loss(p_model, y)
    overlap_market_ll = log_loss(p_mkt, y)
    print(
        "overlap n",
        int(mask.sum()),
        "model LL",
        overlap_model_ll,
        "market LL",
        overlap_market_ll,
    )
    print("home win rate", float(np.nanmean(y)))

    print("\n=== D-2 SIGMA ===")
    rm = preds["realized_margin"].to_numpy(dtype=float)
    pm = preds["pred_margin"].to_numpy(dtype=float)
    sm = preds["sigma_m"].to_numpy(dtype=float)
    resid = rm - pm
    mean_sigma = float(np.nanmean(sm))
    resid_sd = float(np.nanstd(resid, ddof=1))
    ratio = mean_sigma / resid_sd if resid_sd > 0 else float("nan")
    print(f"mean_pred_sigma={mean_sigma:.4f} resid_sd={resid_sd:.4f} ratio={ratio:.4f}")
    print(f"MAE={mae(rm, pm):.4f}")

    pit = pit_values(rm, pm, sm)
    hist, _edges = np.histogram(pit[np.isfinite(pit)], bins=10, range=(0, 1), density=True)
    outer = float(np.mean([hist[0], hist[-1]]))
    middle = float(np.mean(hist[3:7]))
    u_ratio = outer / middle if middle else float("nan")
    print(f"PIT dens outer={outer:.3f} middle={middle:.3f} U_ratio={u_ratio:.3f}")
    print("PIT bin densities", [round(float(x), 3) for x in hist])

    iv = interval_coverage_and_width(rm, pm, sm)
    print(
        "Gaussian interval coverage:",
        {k: {kk: round(vv, 4) for kk, vv in v.items()} for k, v in iv.items()},
    )

    cqr_report: dict[str, object] = {}
    if {"cqr_lo", "cqr_hi", "cqr_nominal"}.issubset(preds.columns):
        lo = preds["cqr_lo"].to_numpy(dtype=float)
        hi = preds["cqr_hi"].to_numpy(dtype=float)
        nom = (
            float(preds["cqr_nominal"].dropna().iloc[0])
            if preds["cqr_nominal"].notna().any()
            else float("nan")
        )
        ok = np.isfinite(lo) & np.isfinite(hi) & np.isfinite(rm)
        cover = float(np.mean((rm[ok] >= lo[ok]) & (rm[ok] <= hi[ok]))) if ok.any() else float("nan")
        width = float(np.mean(hi[ok] - lo[ok])) if ok.any() else float("nan")
        n_miss = (
            int(preds["cqr_is_missing"].fillna(False).astype(bool).sum())
            if "cqr_is_missing" in preds.columns
            else None
        )
        cqr_report = {
            "nominal": nom,
            "empirical_coverage": cover,
            "mean_width": width,
            "n": int(ok.sum()),
            "n_missing": n_miss,
        }
        print("CQR on emit:", cqr_report)

    y_all = su_outcomes(preds["home_points"].to_numpy(), preds["away_points"].to_numpy())
    p_all = preds["p_ml_home"].to_numpy(dtype=float)
    ll_row = log_loss_per_row(p_all, y_all)
    top_idx = np.argsort(-np.nan_to_num(ll_row, nan=-1.0))[:10]
    top = preds.iloc[top_idx][
        ["game_id", "week", "home_points", "away_points", "pred_margin", "sigma_m", "p_ml_home"]
    ].copy()
    top["y_home_win"] = y_all[top_idx]
    top["logloss"] = ll_row[top_idx]
    if "p_ml_home_raw" in preds.columns:
        top["p_ml_home_raw"] = preds["p_ml_home_raw"].iloc[top_idx].to_numpy()
    print("clipping: calibrate 1e-6..1-1e-6; metrics log_loss 1e-15..1-1e-15")
    print("p_ml_home min/max", float(np.nanmin(p_all)), float(np.nanmax(p_all)))
    print(top.to_string(index=False))
    pooled = float(np.nanmean(ll_row))
    without_top10 = float(np.nanmean(np.delete(ll_row, top_idx)))
    print(f"pooled LL={pooled:.4f}; without top10={without_top10:.4f}")

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(pit[np.isfinite(pit)], bins=10, range=(0, 1), color="#2c5f2d", edgecolor="white", density=True)
    ax.axhline(1.0, color="#97bc62", linestyle="--", label="uniform")
    ax.set_xlabel("PIT")
    ax.set_ylabel("density")
    ax.set_title("2023 smoke — margin PIT (Normal(μ,σ))")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "pit_2023_diag.png", dpi=140)
    plt.close(fig)

    finite = np.isfinite(p_all) & np.isfinite(y_all)
    pf, yf = p_all[finite], y_all[finite]
    bins = np.linspace(0.0, 1.0, 11)
    centers, freq, counts = [], [], []
    for i in range(10):
        m = (pf >= bins[i]) & (pf < bins[i + 1] if i < 9 else pf <= bins[i + 1])
        centers.append(0.5 * (bins[i] + bins[i + 1]))
        counts.append(int(m.sum()))
        freq.append(float(yf[m].mean()) if m.any() else np.nan)
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], "--", color="#888")
    ax.scatter(centers, freq, s=[max(20, c) for c in counts], c="#2c5f2d")
    ax.set_xlabel("predicted P(home win)")
    ax.set_ylabel("observed frequency")
    ax.set_title("2023 smoke — ML reliability")
    fig.tight_layout()
    fig.savefig(OUT / "reliability_2023_diag.png", dpi=140)
    plt.close(fig)

    print("\n=== D-3 ELO ===")
    games = store.read("games")
    teams = store.read("teams")
    print("games seasons", sorted(int(s) for s in games["season"].dropna().unique()), "n", len(games))
    game_log, _, _ = run_elo(games, teams=teams, fbs_only=True)
    elo_2023 = game_log.loc[game_log["game_id"].isin(preds["game_id"])].copy()
    print("elo rows matching preds", len(elo_2023), "of", len(preds))
    merged = preds.merge(
        elo_2023[["game_id", "p_home", "pred_home_margin"]],
        on="game_id",
        how="inner",
    )
    print("inner join n", len(merged))
    y_m = su_outcomes(merged["home_points"].to_numpy(), merged["away_points"].to_numpy())
    elo_ll = log_loss(merged["p_home"].to_numpy(dtype=float), y_m)
    stack_ll = log_loss(merged["p_ml_home"].to_numpy(dtype=float), y_m)
    elo_mae = mae(
        merged["realized_margin"].to_numpy(dtype=float),
        merged["pred_home_margin"].to_numpy(dtype=float),
    )
    stack_mae = mae(
        merged["realized_margin"].to_numpy(dtype=float),
        merged["pred_margin"].to_numpy(dtype=float),
    )
    print(f"IDENTICAL GAMES n={len(merged)}")
    print(f"Elo   logloss={elo_ll:.4f}  MAE={elo_mae:.4f}")
    print(f"Stack logloss={stack_ll:.4f}  MAE={stack_mae:.4f}")
    print("full elo one-step LL", one_step_log_loss(game_log))

    print("\n=== D-5 LADDER ===")
    cfg = load_config()
    print(
        "ceiling now",
        cfg.data.odds_historical_credit_ceiling,
        "credits/call",
        cfg.data.odds_historical_credits_per_call,
    )
    print(
        "API path: GET /historical/sports/americanfootball_ncaaf/odds — "
        "sport-level snapshot (ALL events at timestamp)"
    )

    rows: list[dict[str, object]] = []
    for label, seasons, dps in [
        (
            "2021-2025 all DPs (baseline tuesday+slot_close)",
            list(range(2021, 2026)),
            ["tuesday_0600_et", "slot_close"],
        ),
        ("2021-2025 tuesday_0600_et only", list(range(2021, 2026)), ["tuesday_0600_et"]),
        ("2024 tuesday only", [2024], ["tuesday_0600_et"]),
        ("2024-2025 tuesday only", [2024, 2025], ["tuesday_0600_et"]),
        ("2024 slot_close only", [2024], ["slot_close"]),
        ("2024-2025 slot_close only", [2024, 2025], ["slot_close"]),
        ("2024 tuesday+slot_close", [2024], ["tuesday_0600_et", "slot_close"]),
    ]:
        plan = plan_historical_units(store, seasons, decision_points=dps, config=cfg)
        rows.append(
            {
                "scope": label,
                "requests": plan.total_requests,
                "credits": plan.total_credits,
                "fits_20k_ceiling": plan.total_credits <= cfg.data.odds_historical_credit_ceiling,
            }
        )
        print(
            f"{label}: req={plan.total_requests} credits={plan.total_credits} "
            f"fits20k={plan.total_credits <= 20000}"
        )
    print("rule-of-thumb tuesday: 5 seasons x ~15 weeks x 1 snap x 30 =", 5 * 15 * 30)

    out = {
        "d1": {
            "frame_p_mkt_ml_finite": int(preds["p_mkt_ml_home"].notna().sum()),
            "frame_p_mkt_ats": "constant_0.5",
            "spread_close_finite": int(preds["spread_close"].notna().sum()),
            "resolution_without_prices": {
                "status": res0.ml_status,
                "reason": res0.ml_reason,
                "model_ll": suite0.logloss_ml.model,
                "market_ll": suite0.logloss_ml.market,
            },
            "resolution_with_cfbd_ml": {
                "status": res1.ml_status,
                "n_finite": res1.n_ml_finite,
                "reason": res1.ml_reason,
                "model_ll": suite1.logloss_ml.model,
                "market_ll": suite1.logloss_ml.market,
                "overlap_n": int(mask.sum()),
                "overlap_model_ll": overlap_model_ll,
                "overlap_market_ll": overlap_market_ll,
                "home_win_rate": float(np.nanmean(y)),
            },
        },
        "d2": {
            "mae": mae(rm, pm),
            "mean_pred_sigma": mean_sigma,
            "resid_sd": resid_sd,
            "sigma_ratio": ratio,
            "pit_bin_density": [float(x) for x in hist],
            "pit_outer_over_middle": u_ratio,
            "gaussian_interval": {str(k): v for k, v in iv.items()},
            "cqr": cqr_report,
            "clipping": {
                "calibrate": "1e-6 .. 1-1e-6",
                "log_loss_metric": "1e-15 .. 1-1e-15",
                "p_ml_home_min": float(np.nanmin(p_all)),
                "p_ml_home_max": float(np.nanmax(p_all)),
            },
            "pooled_ll": pooled,
            "ll_without_top10": without_top10,
            "top10": top.to_dict(orient="records"),
        },
        "d3": {
            "n": len(merged),
            "elo_logloss": elo_ll,
            "stack_logloss": stack_ll,
            "elo_mae": elo_mae,
            "stack_mae": stack_mae,
        },
        "d5": {
            "credits_per_call": cfg.data.odds_historical_credits_per_call,
            "ceiling": cfg.data.odds_historical_credit_ceiling,
            "one_call_returns": "ALL events at timestamp (sport-level /historical/.../odds)",
            "ladder": rows,
        },
    }
    (OUT / "diag_23_fix.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    top.to_csv(OUT / "diag_top10_logloss.csv", index=False)
    print("\nWrote", OUT / "diag_23_fix.json")


if __name__ == "__main__":
    main()
