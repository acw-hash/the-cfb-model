"""W9-D Amendment 1 interval/point coherence diagnostic. Report only. No fit."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

Z80 = float(norm.ppf(0.9))  # central 80% Gaussian half-width in σ units
CQR_NOMINAL = 0.8


def _pct(arr: np.ndarray, q: float) -> float:
    return float(np.percentile(arr, q))


def _summarize(arr: np.ndarray) -> dict[str, float]:
    return {
        "min": float(np.min(arr)),
        "p10": _pct(arr, 10),
        "median": float(np.median(arr)),
        "p90": _pct(arr, 90),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
    }


def _load_games(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    games = payload["games"]
    assert isinstance(games, list)
    return games


def _rows(games: list[dict[str, Any]]) -> pd.DataFrame:
    recs = []
    for g in games:
        mu = float(g["mu_margin"])
        lo = float(g["margin_interval_lo"])
        hi = float(g["margin_interval_hi"])
        sigma = float(g["sigma_margin"])
        width = hi - lo
        pos = (mu - lo) / width if width > 0 else float("nan")
        g_lo = mu - Z80 * sigma
        g_hi = mu + Z80 * sigma
        recs.append(
            {
                "game_id": str(g["game_id"]),
                "home_team": g.get("home_team"),
                "away_team": g.get("away_team"),
                "mu": mu,
                "abs_mu": abs(mu),
                "lo": lo,
                "hi": hi,
                "sigma": sigma,
                "width": width,
                "pos": pos,
                "g_lo": g_lo,
                "g_hi": g_hi,
                "delta_lo": lo - g_lo,
                "delta_hi": hi - g_hi,
                "delta_width": width - (g_hi - g_lo),
            }
        )
    return pd.DataFrame(recs)


def _abs_mu_bins(frame: pd.DataFrame) -> list[dict[str, Any]]:
    edges = [0.0, 7.0, 14.0, 21.0, 28.0, math.inf]
    labels = ["[0,7)", "[7,14)", "[14,21)", "[21,28)", "[28,inf)"]
    out = []
    for lo, hi, label in zip(edges[:-1], edges[1:], labels, strict=True):
        mask = (frame["abs_mu"] >= lo) & (frame["abs_mu"] < hi)
        sub = frame.loc[mask]
        if sub.empty:
            out.append({"bin": label, "n": 0})
            continue
        pos = sub["pos"].to_numpy(dtype=float)
        outside = (pos < 0.25) | (pos > 0.75)
        out.append(
            {
                "bin": label,
                "n": int(len(sub)),
                "n_outside_[0.25,0.75]": int(np.sum(outside)),
                "pos": _summarize(pos),
                "abs_mu": _summarize(sub["abs_mu"].to_numpy(dtype=float)),
            }
        )
    return out


def _anomalous(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[(frame["pos"] < 0.25) | (frame["pos"] > 0.75)].sort_values("pos")


def _cqr_state() -> dict[str, Any]:
    from ncaa_quant.registry.bundle import load_production_ensemble

    path = ROOT / "data" / "registry" / "artifacts" / "v2" / "production_ensemble.pkl"
    ens = load_production_ensemble(path)
    cqr = getattr(ens, "_cqr", None)
    aci = getattr(ens, "_aci", None)
    if cqr is None:
        return {
            "path": str(path),
            "present": False,
            "class": type(ens).__name__,
            "aci_present": aci is not None,
        }
    thr = cqr.score_thresholds.get(CQR_NOMINAL)
    return {
        "path": str(path),
        "present": True,
        "calibration_seasons": list(cqr.calibration_seasons),
        "score_thresholds": {str(k): float(v) for k, v in cqr.score_thresholds.items()},
        "threshold_80": None if thr is None else float(thr),
        "aci_present": aci is not None,
        "coverage": {
            str(k): {
                "nominal": float(v.nominal),
                "empirical": float(v.empirical),
                "n": int(v.n),
                "mean_width": float(v.mean_width),
            }
            for k, v in (cqr.coverage or {}).items()
        },
    }


def _training_mu_range() -> dict[str, Any]:
    path = (
        ROOT
        / "data"
        / "backtests"
        / "task23_fundamental_reduced_v3"
        / "full"
        / "predictions.parquet"
    )
    frame = pd.read_parquet(path)
    pred = pd.to_numeric(frame["pred_margin"], errors="coerce")
    realized = pd.to_numeric(frame["realized_margin"], errors="coerce")
    seasons = pd.to_numeric(frame["season"], errors="coerce")
    week = pd.to_numeric(frame["week"], errors="coerce") if "week" in frame.columns else None

    def _range(series: pd.Series) -> dict[str, float | int]:
        finite = series[np.isfinite(series.to_numpy(dtype=float))]
        return {
            "n": int(len(finite)),
            "min": float(finite.min()),
            "p01": float(np.percentile(finite, 1)),
            "p10": float(np.percentile(finite, 10)),
            "median": float(finite.median()),
            "p90": float(np.percentile(finite, 90)),
            "p99": float(np.percentile(finite, 99)),
            "max": float(finite.max()),
        }

    out: dict[str, Any] = {
        "path": str(path),
        "n_rows": int(len(frame)),
        "pred_margin": _range(pred),
        "realized_margin": _range(realized),
        "by_season_pred": {},
    }
    for season in sorted({int(s) for s in seasons.dropna().unique()}):
        mask = seasons == season
        out["by_season_pred"][str(season)] = _range(pred.loc[mask])
    if week is not None:
        w1 = (week == 1) & seasons.isin([2019, 2021, 2022, 2023, 2024])
        out["week1_pred_margin"] = _range(pred.loc[w1])
        out["week1_realized_margin"] = _range(realized.loc[w1])
    calib = seasons.isin([2023, 2024])
    out["calib_2023_2024_pred"] = _range(pred.loc[calib])
    out["calib_2023_2024_realized"] = _range(realized.loc[calib])
    return out


def _slate_report(
    name: str,
    games: list[dict[str, Any]],
    *,
    threshold: float,
    train_pred_min: float,
    train_pred_max: float,
) -> dict[str, Any]:
    frame = _rows(games)
    pos = frame["pos"].to_numpy(dtype=float)
    outside = (pos < 0.25) | (pos > 0.75)
    n_out = int(np.sum(outside))
    n_mu_outside_interval = int(np.sum((pos < 0.0) | (pos > 1.0)))
    frame["q10"] = frame["lo"] + threshold
    frame["q90"] = frame["hi"] - threshold
    q_width = frame["q90"] - frame["q10"]
    frame["pos_q"] = np.where(q_width > 0, (frame["mu"] - frame["q10"]) / q_width, np.nan)
    frame["q90_below_mu"] = frame["q90"] < frame["mu"]
    frame["q10_above_mu"] = frame["q10"] > frame["mu"]
    frame["outside_train_pred"] = (frame["mu"] < train_pred_min) | (frame["mu"] > train_pred_max)
    anom = _anomalous(frame)
    anom_out_train = int(anom["outside_train_pred"].sum()) if not anom.empty else 0
    return {
        "name": name,
        "n": int(len(frame)),
        "pos": _summarize(pos),
        "n_outside_[0.25,0.75]": n_out,
        "n_mu_outside_published_interval": n_mu_outside_interval,
        "frac_outside_[0.25,0.75]": float(n_out / len(frame)),
        "pos_vs_abs_mu": _abs_mu_bins(frame),
        "gaussian_z80": Z80,
        "delta_lo_vs_gaussian": _summarize(frame["delta_lo"].to_numpy(dtype=float)),
        "delta_hi_vs_gaussian": _summarize(frame["delta_hi"].to_numpy(dtype=float)),
        "delta_width_vs_gaussian": _summarize(frame["delta_width"].to_numpy(dtype=float)),
        "pos_raw_q10_q90": _summarize(frame["pos_q"].to_numpy(dtype=float)),
        "n_q90_below_mu": int(frame["q90_below_mu"].sum()),
        "n_q10_above_mu": int(frame["q10_above_mu"].sum()),
        "n_mu_outside_train_pred_range": int(frame["outside_train_pred"].sum()),
        "n_anomalous_and_outside_train_pred": anom_out_train,
        "anomalous_rows": [
            {
                "game_id": row.game_id,
                "matchup": f"{row.away_team} @ {row.home_team}",
                "mu": float(row.mu),
                "lo": float(row.lo),
                "hi": float(row.hi),
                "sigma": float(row.sigma),
                "pos": float(row.pos),
                "pos_q10_q90": float(row.pos_q),
                "q10": float(row.q10),
                "q90": float(row.q90),
                "g_lo": float(row.g_lo),
                "g_hi": float(row.g_hi),
                "delta_lo": float(row.delta_lo),
                "delta_hi": float(row.delta_hi),
                "outside_train_pred": bool(row.outside_train_pred),
            }
            for row in anom.itertuples(index=False)
        ],
    }


def main() -> dict[str, Any]:
    cqr = _cqr_state()
    train = _training_mu_range()
    thr = float(cqr.get("threshold_80") or 0.0)
    pred_min = float(train["pred_margin"]["min"])
    pred_max = float(train["pred_margin"]["max"])
    train_p99 = float(train["pred_margin"]["p99"])
    realized_min = float(train["realized_margin"]["min"])
    realized_max = float(train["realized_margin"]["max"])
    week1_max = float(train["week1_pred_margin"]["max"])
    fixture = ROOT / "webapp" / "fixtures" / "week_predictions.json"
    sandbox = ART / "sandbox_roundtrip" / "week_predictions.json"
    slates = {
        "2026_w1": _slate_report(
            "2026 week 1",
            _load_games(sandbox),
            threshold=thr,
            train_pred_min=pred_min,
            train_pred_max=pred_max,
        ),
        "2024_w5": _slate_report(
            "2024 week 5",
            _load_games(fixture),
            threshold=thr,
            train_pred_min=pred_min,
            train_pred_max=pred_max,
        ),
    }
    for key, games_path in (("2026_w1", sandbox), ("2024_w5", fixture)):
        frame = _rows(_load_games(games_path))
        anom = _anomalous(frame)
        slate = slates[key]
        if anom.empty:
            slate["n_anomalous_abs_mu_gt_train_p99"] = 0
            slate["n_anomalous_mu_outside_realized"] = 0
            slate["n_anomalous_abs_mu_gt_week1_train_max"] = 0
            continue
        slate["n_anomalous_abs_mu_gt_train_p99"] = int((anom["abs_mu"] > train_p99).sum())
        slate["n_anomalous_mu_outside_realized"] = int(
            ((anom["mu"] < realized_min) | (anom["mu"] > realized_max)).sum()
        )
        slate["n_anomalous_abs_mu_gt_week1_train_max"] = int((anom["abs_mu"] > abs(week1_max)).sum())
    return {
        "cqr": cqr,
        "gaussian_z_for_80pct": Z80,
        "construction": (
            "published lo/hi = sorted q10/q90 ± CQR threshold (symmetric). "
            "CQR cannot move (mu-lo)/(hi-lo) off 0.5 unless q10/q90 are already "
            "asymmetric around mu."
        ),
        "training": train,
        "slates": slates,
    }


if __name__ == "__main__":
    report = main()
    out = ART / "amendment1_interval.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("cqr", "gaussian_z_for_80pct")}, indent=2))
    for key, slate in report["slates"].items():
        print(
            f"{key} n={slate['n']} pos={slate['pos']} "
            f"n_out={slate['n_outside_[0.25,0.75]']} "
            f"n_mu_outside_interval={slate['n_mu_outside_published_interval']} "
            f"n_mu_ood={slate['n_mu_outside_train_pred_range']} "
            f"n_anom_ood={slate['n_anomalous_and_outside_train_pred']}"
        )
        print(f"  gaussian d_lo={slate['delta_lo_vs_gaussian']}")
        print(f"  gaussian d_hi={slate['delta_hi_vs_gaussian']}")
        print(f"  bins={json.dumps(slate['pos_vs_abs_mu'])}")
    print(f"wrote {out}")
