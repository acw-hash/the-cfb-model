"""ATS-GRADE-FIX — regrade stored predictions with the fixed home-side ladder.

Writes parallel ``grade_v2/predictions.parquet`` under each run dir; never
overwrites v1 ``predictions.parquet``. Recomputes ``spread_close`` /
``spread_asof`` via :func:`resolve_lines_for_games` and refreshes ``p_ats_home``
at the corrected close (Gaussian Φ((μ+S)/σ)).

REGRADE only (features untouched): fundamental, A1, A2, A4, A5.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.metrics import (
    assert_prediction_ats_plausible,
    ats_home_outcomes,
    attach_metric_cis,
    binary_accuracy,
    compute_metric_suite,
    log_loss,
    report_a2_components_by_basis,
)
from ncaa_quant.evaluation.walkforward import WalkForwardConfig, resolve_lines_for_games

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "data" / "staged"
BACKTESTS = ROOT / "data" / "backtests"
OUT_SUMMARY = ROOT / "docs" / "notes" / "_artifacts" / "ats_grade_fix" / "regrade_summary.json"

# Features were clean — regrade only (FORBIDDEN to re-run these stacks).
REGRADE_RUNS: tuple[tuple[str, str], ...] = (
    ("task23_fundamental_reduced_v1", "full"),
    ("task23_a1_reduced_v1", "A1_league_mean"),
    ("task23_a2_reduced_v1", "A2_frozen_after_week_1"),
    ("task23_a4_reduced_v1", "A4_single_lgbm"),
    ("task23_a5_reduced_v1", "A5_gt_off"),
)

GRADE_VERSION = "v2"
SNAPSHOT_FROM = 2021


@dataclass(frozen=True)
class RegimeAts:
    regime: str
    n: int
    ats: float
    logloss_model: float
    logloss_market: float
    bootstrap_lo: float
    bootstrap_hi: float
    naive_lo: float
    naive_hi: float
    spread_abs_median: float
    pct_near0: float


def _read_hive(path: Path, seasons: list[int]) -> pd.DataFrame:
    files = sorted(path.rglob("*.parquet"))
    frames: list[pd.DataFrame] = []
    for f in files:
        parts = {p.split("=")[0]: p.split("=")[1] for p in f.parts if "=" in p}
        if "season" in parts and int(parts["season"]) not in seasons:
            continue
        part = pd.read_parquet(f)
        for col in ("season", "week", "game_id"):
            if col in part.columns:
                part[col] = pd.to_numeric(part[col], errors="coerce")
        for col in ("home_team", "away_team", "side", "book", "market"):
            if col in part.columns:
                part[col] = part[col].astype(str)
        frames.append(part)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _p_ats_gaussian(mu: np.ndarray, sigma: np.ndarray, spread: np.ndarray) -> np.ndarray:
    """P(home covers) under N(μ,σ²): Φ((μ + S) / σ).

    Missing σ stays NaN. Inventing p ∈ {0.999, 0.001, 0.5} from sign(μ+S) is
    forbidden (ADR 0014 / DESIGN §1.6 honest absence).
    """
    out = np.full(mu.shape, np.nan, dtype=float)
    ok = np.isfinite(mu) & np.isfinite(spread) & np.isfinite(sigma) & (sigma > 0)
    out[ok] = stats.norm.cdf((mu[ok] + spread[ok]) / sigma[ok])
    return out


def regrade_predictions(
    preds: pd.DataFrame,
    *,
    games: pd.DataFrame,
    snapshots: pd.DataFrame,
    cfbd_lines: pd.DataFrame,
    config: WalkForwardConfig,
) -> pd.DataFrame:
    """Return a copy with corrected closes and refreshed ATS probabilities."""
    out = preds.copy()
    ginfo = games.drop_duplicates("game_id").set_index("game_id")
    close_cache: dict[int, pd.Series] = {}
    asof_cache: dict[tuple[int, str], pd.Series] = {}

    snap_close = np.full(len(out), np.nan)
    snap_asof = np.full(len(out), np.nan)
    src_close: list[str] = []
    src_asof: list[str] = []
    n_books_close: list[int] = []
    n_books_asof: list[int] = []

    for i, row in enumerate(out.itertuples(index=False)):
        gid = int(row.game_id)
        if gid not in ginfo.index:
            src_close.append("null")
            src_asof.append("null")
            n_books_close.append(0)
            n_books_asof.append(0)
            continue
        gm = ginfo.loc[gid]
        game_row = pd.DataFrame(
            [
                {
                    "game_id": gid,
                    "game_key": str(getattr(row, "game_key", "") or ""),
                    "season": int(row.season),
                    "week": int(row.week),
                    "event_time": pd.Timestamp(gm["event_time"]),
                    "home_team": str(gm["home_team"]) if pd.notna(gm.get("home_team")) else None,
                    "away_team": str(gm["away_team"]) if pd.notna(gm.get("away_team")) else None,
                }
            ]
        )
        as_of = pd.Timestamp(row.as_of)
        if as_of.tzinfo is None:
            as_of = as_of.tz_localize("UTC")
        as_of_key = (gid, as_of.isoformat())

        if gid not in close_cache:
            close_cache[gid] = resolve_lines_for_games(
                game_row,
                as_of.to_pydatetime(),
                snapshots=snapshots,
                cfbd_lines=cfbd_lines,
                config=config,
                closing=True,
            ).iloc[0]
        if as_of_key not in asof_cache:
            asof_cache[as_of_key] = resolve_lines_for_games(
                game_row,
                as_of.to_pydatetime(),
                snapshots=snapshots,
                cfbd_lines=cfbd_lines,
                config=config,
                closing=False,
            ).iloc[0]

        close = close_cache[gid]
        asof = asof_cache[as_of_key]
        snap_close[i] = float(close["spread"])
        snap_asof[i] = float(asof["spread"])
        src_close.append(str(close["line_source"]))
        src_asof.append(str(asof["line_source"]))
        n_books_close.append(int(close["n_books"]))
        n_books_asof.append(int(asof["n_books"]))

    out["spread_close"] = snap_close
    out["spread_asof"] = snap_asof
    out["line_source_close"] = src_close
    out["line_source_asof"] = src_asof
    out["n_books_close"] = n_books_close
    out["n_books_asof"] = n_books_asof
    out["grade_version"] = GRADE_VERSION

    mu = out["pred_margin"].to_numpy(dtype=float)
    sigma = (
        out["sigma_m"].to_numpy(dtype=float)
        if "sigma_m" in out.columns
        else np.full(len(out), np.nan)
    )
    p_ats = _p_ats_gaussian(mu, sigma, snap_close)
    out["p_ats_home_raw"] = p_ats
    out["p_ats_home"] = p_ats
    out["p_ats_home_is_missing"] = ~np.isfinite(p_ats)
    out["p_mkt_ats_home"] = 0.5
    return out


def _regime_ats(frame: pd.DataFrame, regime: str, mask: pd.Series) -> RegimeAts | None:
    sub = frame.loc[mask].copy()
    if sub.empty:
        return None
    suite = compute_metric_suite(sub)
    cis = attach_metric_cis(suite, sub, seed=23)
    ats_ci = cis.get("ats_accuracy")
    ats_naive = cis.get("ats_accuracy_naive")
    y = ats_home_outcomes(
        sub["realized_margin"].to_numpy(dtype=float),
        sub["spread_close"].to_numpy(dtype=float),
    )
    p = sub["p_ats_home"].to_numpy(dtype=float)
    mask_y = np.isfinite(y) & np.isfinite(p)
    n_rate = int(mask_y.sum())
    if ats_ci is not None and int(ats_ci.n) != n_rate:
        raise RuntimeError(
            f"ATS rate n={n_rate} != bootstrap CI n={int(ats_ci.n)} for regime={regime!r}"
        )
    if ats_naive is not None and int(ats_naive.n) != n_rate:
        raise RuntimeError(
            f"ATS rate n={n_rate} != naive CI n={int(ats_naive.n)} for regime={regime!r}"
        )
    rate = binary_accuracy(p, y) if np.any(mask_y) else float("nan")
    ll = log_loss(p[mask_y], y[mask_y]) if np.any(mask_y) else float("nan")
    sp = pd.to_numeric(sub["spread_close"], errors="coerce")
    return RegimeAts(
        regime=regime,
        n=n_rate,
        ats=float(rate),
        logloss_model=float(ll),
        logloss_market=float(np.log(2.0)),
        bootstrap_lo=float(ats_ci.ci_low) if ats_ci is not None else float("nan"),
        bootstrap_hi=float(ats_ci.ci_high) if ats_ci is not None else float("nan"),
        naive_lo=float(ats_naive.ci_low) if ats_naive is not None else float("nan"),
        naive_hi=float(ats_naive.ci_high) if ats_naive is not None else float("nan"),
        spread_abs_median=float(sp.abs().median()) if sp.notna().any() else float("nan"),
        pct_near0=float((sp.abs() < 0.5).mean()) if sp.notna().any() else float("nan"),
    )


def summarize_run(preds: pd.DataFrame) -> dict[str, Any]:
    headline = preds
    if "exclude_from_headline" in preds.columns:
        headline = preds.loc[~preds["exclude_from_headline"].fillna(False).astype(bool)]
    regimes = []
    for label, mask in [
        ("cfbd_2019", headline["season"] == 2019),
        ("snapshots_2021_2024", headline["season"].between(2021, 2024)),
    ]:
        r = _regime_ats(headline, label, mask)
        if r is not None:
            regimes.append(asdict(r))
    basis = [
        {
            "metric": rec.metric,
            "value": rec.value,
            "n": rec.n,
            "basis": rec.basis,
            "seasons": list(rec.seasons),
        }
        for rec in report_a2_components_by_basis(headline)
    ]
    return {"regimes": regimes, "basis": basis}


def main() -> None:
    seasons = [2019, 2021, 2022, 2023, 2024]
    print("loading staged games/lines/snaps…")
    games = load_staged_games(STAGED, seasons)
    # continuity 2020 not needed for regrade metrics
    lines = _read_hive(STAGED / "lines_historical", seasons)
    snaps = _read_hive(STAGED / "odds_snapshots", [2021, 2022, 2023, 2024])
    if "event_time" in snaps.columns:
        snaps["event_time"] = pd.to_datetime(snaps["event_time"], utc=True)
    if "event_time" in games.columns:
        games["event_time"] = pd.to_datetime(games["event_time"], utc=True)
    cfg = WalkForwardConfig()
    print(f"games={len(games)} lines={len(lines)} snaps={len(snaps)}")

    summary: dict[str, Any] = {"grade_version": GRADE_VERSION, "runs": {}}
    for run_id, subdir in REGRADE_RUNS:
        src = BACKTESTS / run_id / subdir / "predictions.parquet"
        if not src.is_file():
            print(f"SKIP missing {src}")
            continue
        print(f"regrading {run_id}/{subdir}…")
        preds = pd.read_parquet(src)
        graded = regrade_predictions(
            preds, games=games, snapshots=snaps, cfbd_lines=lines, config=cfg
        )
        assert_prediction_ats_plausible(graded)
        out_dir = BACKTESTS / run_id / subdir / "grade_v2"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "predictions.parquet"
        graded.to_parquet(out_path, index=False)
        meta = {
            "grade_version": GRADE_VERSION,
            "source_predictions": str(src.relative_to(ROOT)).replace("\\", "/"),
            "n_rows": int(len(graded)),
            "vintage": "REGRADED_V2",
        }
        (out_dir / "grade_manifest.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
        run_summary = summarize_run(graded)
        summary["runs"][run_id] = run_summary
        print(json.dumps({run_id: run_summary}, indent=2))

    OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("wrote", OUT_SUMMARY)


if __name__ == "__main__":
    main()
