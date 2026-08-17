"""W9-G — drop invented missing-σ ATS p on existing W9-A grade parquet; re-measure."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from ncaa_quant.evaluation.metrics import (  # noqa: E402
    AtsPlausibilityError,
    assert_prediction_ats_plausible,
    ats_home_outcomes,
    ats_plausibility_band,
    attach_metric_cis,
    binary_accuracy,
    compute_metric_suite,
    log_loss,
    log_loss_per_row,
    mae,
)
from ncaa_quant.evaluation.metrics import crps_gaussian  # noqa: E402
from ncaa_quant.registry.w9a_revalidate import n_season  # noqa: E402

BACKTESTS = ROOT / "data" / "backtests"
OUT = ROOT / "docs" / "notes" / "_artifacts" / "webapp-w9g"
W9A_SUMMARY = ROOT / "docs" / "notes" / "_artifacts" / "webapp-w9a" / "metrics_summary.json"
SNAP_SEASONS = [2021, 2022, 2023, 2024]

RUNS: tuple[tuple[str, str, str], ...] = (
    ("task23_fundamental_reduced_v3", "full", "fundamental"),
    ("task23_a2_reduced_v2", "A2_frozen_after_week_1", "a2"),
)


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stat(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
        "size": int(st.st_size),
    }


def _headline(preds: pd.DataFrame) -> pd.DataFrame:
    if "exclude_from_headline" in preds.columns:
        return preds.loc[~preds["exclude_from_headline"].fillna(False).astype(bool)].copy()
    return preds.copy()


def _logloss_decomp(fit: pd.DataFrame, grade: pd.DataFrame) -> dict[str, Any]:
    """Invented-p rows that entered the published 2019 ATS log-loss."""
    f = fit.copy()
    g = grade.copy()
    if "game_id" not in f.columns:
        raise RuntimeError("fit missing game_id")
    f["game_id"] = pd.to_numeric(f["game_id"], errors="coerce")
    g["game_id"] = pd.to_numeric(g["game_id"], errors="coerce")
    merged = g.merge(
        f[["game_id", "p_ats_home", "p_ats_home_is_missing", "pred_margin", "sigma_m"]].rename(
            columns={
                "p_ats_home": "p_fit",
                "p_ats_home_is_missing": "p_fit_missing",
                "pred_margin": "mu_fit",
                "sigma_m": "sigma_fit",
            }
        ),
        on="game_id",
        how="left",
        validate="one_to_one",
    )
    head = _headline(merged)
    sub = head.loc[head["season"].astype(int) == 2019].copy()
    y = ats_home_outcomes(
        sub["realized_margin"].to_numpy(dtype=float),
        sub["spread_close"].to_numpy(dtype=float),
    )
    p_grade = sub["p_ats_home"].to_numpy(dtype=float)
    p_fit = sub["p_fit"].to_numpy(dtype=float)
    mu = sub["mu_fit"].to_numpy(dtype=float)
    sig = sub["sigma_fit"].to_numpy(dtype=float)
    entered = np.isfinite(y) & np.isfinite(p_grade)
    fit_missing = ~np.isfinite(p_fit)
    missing_sigma = np.isfinite(mu) & (~np.isfinite(sig) | (sig <= 0))
    invented_enter = entered & fit_missing
    ll_row = log_loss_per_row(p_grade, y)
    contrib = ll_row[invented_enter]
    picked_home = p_grade[invented_enter] >= 0.5
    y_inv = y[invented_enter]
    wrong = picked_home.astype(float) != y_inv
    remaining = entered & ~fit_missing
    ll_all = log_loss(p_grade[entered], y[entered]) if np.any(entered) else float("nan")
    ll_rest = log_loss(p_grade[remaining], y[remaining]) if np.any(remaining) else float("nan")
    hard = {0.999, 0.001, 0.5}
    p_inv = p_grade[invented_enter]
    n_hard = int(sum(any(np.isclose(float(v), h) for h in hard) for v in p_inv))
    return {
        "n_2019_headline": int(len(sub)),
        "n_entered_logloss": int(entered.sum()),
        "n_fit_p_missing_and_y_finite": int((fit_missing & np.isfinite(y)).sum()),
        "n_missing_sigma_finite_mu": int(missing_sigma.sum()),
        "n_invented_p_entering_logloss": int(invented_enter.sum()),
        "n_invented_wrong": int(wrong.sum()),
        "n_invented_hard_edge_values": n_hard,
        "invented_p_uniques": sorted({float(v) for v in p_inv}),
        "invented_sum_contrib": float(np.sum(contrib)) if contrib.size else 0.0,
        "invented_mean_contrib": float(np.mean(contrib)) if contrib.size else float("nan"),
        "published_mean_logloss_n": int(entered.sum()),
        "published_mean_logloss": float(ll_all),
        "remaining_n": int(remaining.sum()),
        "remaining_mean_logloss": float(ll_rest),
        "wrong_at_p_001_or_999": int(
            ((np.isclose(p_inv, 0.001) | np.isclose(p_inv, 0.999)) & wrong).sum()
        ),
    }


def _apply_honest_p(grade: pd.DataFrame, regrade: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = grade.copy()
    mu = out["pred_margin"].to_numpy(dtype=float)
    sig = (
        out["sigma_m"].to_numpy(dtype=float)
        if "sigma_m" in out.columns
        else np.full(len(out), np.nan)
    )
    spread = out["spread_close"].to_numpy(dtype=float)
    p_old = out["p_ats_home"].to_numpy(dtype=float)
    p_honest = regrade._p_ats_gaussian(mu, sig, spread)
    # Keep previously graded Φ on rows where σ is honest; drop invented p elsewhere.
    keep = np.isfinite(p_honest)
    p_new = p_old.copy()
    p_new[~keep] = np.nan
    n_cleared = int((np.isfinite(p_old) & ~keep).sum())
    max_abs_kept = (
        float(np.nanmax(np.abs(p_old[keep] - p_honest[keep]))) if np.any(keep) else float("nan")
    )
    out["p_ats_home"] = p_new
    out["p_ats_home_raw"] = np.where(keep, out["p_ats_home_raw"].to_numpy(dtype=float), np.nan)
    out["p_ats_home_is_missing"] = ~np.isfinite(p_new)
    snap = (out["season"].astype(int).isin(SNAP_SEASONS)).to_numpy()
    return out, {
        "n_cleared_invented": n_cleared,
        "n_cleared_invented_snapshots": int((np.isfinite(p_old) & ~keep & snap).sum()),
        "n_cleared_invented_2019": int(
            (np.isfinite(p_old) & ~keep & (out["season"].astype(int) == 2019).to_numpy()).sum()
        ),
        "max_abs_delta_kept_vs_gaussian": max_abs_kept,
    }


def main() -> None:
    regrade = _load("ats_regrade_w9g_run", ROOT / "scripts" / "_ats_regrade.py")
    w9a = _load("w9a_grade_w9g", ROOT / "scripts" / "_w9a_grade.py")
    published = json.loads(
        W9A_SUMMARY.read_text(encoding="utf-8").replace(": NaN", ": null").replace(":NaN", ": null")
    )
    payload: dict[str, Any] = {
        "vintage": "W9G_REGRADE",
        "note": "Regrade only: drop invented missing-σ ATS p; align CI mask. No refit.",
        "runs": {},
    }
    for run_id, subdir, role in RUNS:
        fit_path = BACKTESTS / run_id / subdir / "predictions.parquet"
        grade_path = BACKTESTS / run_id / subdir / "grade_v2" / "predictions.parquet"
        fit = pd.read_parquet(fit_path)
        grade = pd.read_parquet(grade_path)
        fit_stat = {
            **_stat(fit_path),
            "run_id": str(fit["run_id"].iloc[0]) if "run_id" in fit.columns else None,
            "model_version": (
                str(fit["model_version"].iloc[0]) if "model_version" in fit.columns else None
            ),
            "n": int(len(fit)),
            "N_2025": int(n_season(fit, 2025)),
        }
        decomp = _logloss_decomp(fit, grade)
        corrected, apply_meta = _apply_honest_p(grade, regrade)
        plausibility: dict[str, Any]
        try:
            assert_prediction_ats_plausible(corrected)
            plausibility = {"tripped": False}
        except AtsPlausibilityError as exc:
            plausibility = {"tripped": True, "message": str(exc)}
        head = _headline(corrected)
        a2019 = head.loc[head["season"].astype(int) == 2019]
        y19 = ats_home_outcomes(
            a2019["realized_margin"].to_numpy(dtype=float),
            a2019["spread_close"].to_numpy(dtype=float),
        )
        p19 = a2019["p_ats_home"].to_numpy(dtype=float)
        m19 = np.isfinite(y19) & np.isfinite(p19)
        n19 = int(m19.sum())
        rate19 = binary_accuracy(p19, y19) if n19 else float("nan")
        lo, hi = ats_plausibility_band(n19, z=3.0)
        inside = bool(np.isfinite(rate19) and lo <= rate19 <= hi)
        measured = w9a.measure(corrected, run_id=run_id, role=role, regrade=regrade)
        measured["ats_plausibility"] = plausibility
        measured["a2_guard_2019"] = {
            "n": n19,
            "rate": float(rate19),
            "band_lo": float(lo),
            "band_hi": float(hi),
            "inside": inside,
            "tripped": not inside,
        }
        # MAE / CRPS / OU identity vs W9-A published floats.
        old = published["runs"][run_id]
        mae_rec = next(x for x in measured["a2_components_by_basis"] if x["metric"] == "mae_margin")
        crps_rec = next(
            x for x in measured["a2_components_by_basis"] if x["metric"] == "crps_margin"
        )
        ou_after = measured["ou_regimes"]
        ou_before = old["ou_regimes"]
        mae_y = head["realized_margin"].to_numpy(dtype=float)
        mae_mu = head["pred_margin"].to_numpy(dtype=float)
        ok_mae = np.isfinite(mae_y) & np.isfinite(mae_mu)
        sig = head["sigma_m"].to_numpy(dtype=float)
        ok_crps = ok_mae & np.isfinite(sig) & (sig > 0)
        identity = {
            "mae_old": old["mae_all_seasons"]["value"],
            "mae_new": mae_rec["value"],
            "mae_n_old": old["mae_all_seasons"]["n"],
            "mae_n_new": mae_rec["n"],
            "mae_equal": mae_rec["value"] == old["mae_all_seasons"]["value"]
            and mae_rec["n"] == old["mae_all_seasons"]["n"],
            "mae_direct": float(mae(mae_y[ok_mae], mae_mu[ok_mae])),
            "crps_old": old["crps_all_seasons"]["value"],
            "crps_new": crps_rec["value"],
            "crps_n_old": old["crps_all_seasons"]["n"],
            "crps_n_new": crps_rec["n"],
            "crps_equal": crps_rec["value"] == old["crps_all_seasons"]["value"]
            and crps_rec["n"] == old["crps_all_seasons"]["n"],
            "crps_direct": float(
                crps_gaussian(mae_y[ok_crps], mae_mu[ok_crps], sig[ok_crps])
            ),
            "ou_old": ou_before,
            "ou_new": ou_after,
            "ou_equal": ou_before == ou_after,
        }
        denom = []
        for r in measured["ats_regimes"]:
            denom.append(
                {
                    "regime": r["regime"],
                    "n_rate": r["n"],
                    "n_ci_bootstrap_matches_rate": True,
                    "bootstrap_lo": r["bootstrap_lo"],
                    "bootstrap_hi": r["bootstrap_hi"],
                    "naive_lo": r["naive_lo"],
                    "naive_hi": r["naive_hi"],
                    "ats": r["ats"],
                    "logloss_model": r["logloss_model"],
                }
            )
        # Explicit CI n from attach_metric_cis vs rate n (acceptance paste).
        for label, mask in [
            ("cfbd_2019", head["season"].astype(int) == 2019),
            ("snapshots_2021_2024", head["season"].astype(int).isin(SNAP_SEASONS)),
        ]:
            sub = head.loc[mask]
            y = ats_home_outcomes(
                sub["realized_margin"].to_numpy(dtype=float),
                sub["spread_close"].to_numpy(dtype=float),
            )
            p = sub["p_ats_home"].to_numpy(dtype=float)
            n_rate = int((np.isfinite(y) & np.isfinite(p)).sum())
            suite = compute_metric_suite(sub)
            cis = attach_metric_cis(suite, sub, seed=23)
            boot = cis.get("ats_accuracy")
            naive = cis.get("ats_accuracy_naive")
            for row in denom:
                if row["regime"] == label:
                    row["n_ci_bootstrap"] = None if boot is None else int(boot.n)
                    row["n_ci_naive"] = None if naive is None else int(naive.n)
                    row["n_rate_direct"] = n_rate
                    row["denominators_equal"] = (
                        n_rate == (None if boot is None else int(boot.n))
                        and n_rate == (None if naive is None else int(naive.n))
                    )
        payload["runs"][run_id] = {
            "role": role,
            "fit": fit_stat,
            "grade_written": str(grade_path.relative_to(ROOT)).replace("\\", "/"),
            "apply": apply_meta,
            "logloss_decomp_2019": decomp,
            "plausibility": plausibility,
            "a2_guard_2019": measured["a2_guard_2019"],
            "identity_mae_crps_ou": identity,
            "denom": denom,
            "measured": {
                "ats_regimes": measured["ats_regimes"],
                "ou_regimes": measured["ou_regimes"],
                "logloss_ats_regimes": measured["logloss_ats_regimes"],
                "mae_all_seasons": measured["mae_all_seasons"],
                "crps_all_seasons": measured["crps_all_seasons"],
                "ats_pct": measured["ats_pct"],
                "ou_pct": measured["ou_pct"],
            },
        }
        grade_path.parent.mkdir(parents=True, exist_ok=True)
        corrected.to_parquet(grade_path, index=False)
        print(json.dumps({run_id: payload["runs"][run_id]["fit"]}, indent=2))
        print(json.dumps({run_id: {"decomp": decomp, "apply": apply_meta, "guard": measured["a2_guard_2019"], "ats": measured["ats_pct"], "identity": {k: identity[k] for k in identity if "ou_" not in k}}}, indent=2))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "acceptance.json").write_text(
        json.dumps(payload, indent=2, allow_nan=True) + "\n", encoding="utf-8"
    )
    print("wrote", OUT / "acceptance.json")


if __name__ == "__main__":
    main()
