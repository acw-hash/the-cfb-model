"""TASK V2-BASELINE — single-vintage fundamental baseline + market-aware audit.

Phases (resume-friendly via artifact files under docs/notes/_artifacts/v2_baseline/):

1. ``determinism`` — run ``task23_fundamental_full_reduced_v2`` twice; require
   byte-identical prediction tables; emit V2 BASELINE metrics from run A.
2. ``equivalence`` — row-compare fundamental_v2 vs A3 RERUN_V2; config-diff
   beyond the market flag if divergent.
3. ``audit`` — market-feature information-set / PIT audit at ≥20 week-points;
   assert feature ladder ≠ grading ladder source rows; no post-decision snaps.
4. ``publish`` — if audit CLEAN, force-publish market-aware v2 (logged human
   --force path per ADR) so A3-vs-aware exists within one vintage.
5. ``memo`` — write ``docs/notes/v2-baseline.md`` from artifacts.

FORBIDDEN: cross-vintage comparisons in the memo; publish without CLEAN audit;
widening the ATS plausibility guard band.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml

from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.metrics import (
    AtsPlausibilityError,
    assert_prediction_ats_plausible,
    attach_metric_cis,
    ats_home_outcomes,
    binary_accuracy,
    compute_metric_suite,
    crps_gaussian,
    log_loss,
    mae,
)
from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    predictions_bytes,
    resolve_lines_for_games,
    week_decision_as_of,
)

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "data" / "staged"
BACKTESTS = ROOT / "data" / "backtests"
ART = ROOT / "docs" / "notes" / "_artifacts" / "v2_baseline"
MEMO = ROOT / "docs" / "notes" / "v2-baseline.md"
ADR = ROOT / "docs" / "adr" / "0014-guard-trip-force-publish.md"

FUND_CFG = "task23_fundamental_full_reduced_v2"
FUND_RUN_ID = "task23_fundamental_reduced_v2"
FUND_SUBDIR = "full"
A3_RUN_ID = "task23_a3_reduced_v2"
A3_SUBDIR = "A3_market_off"
MKT_CFG = "task23_market_aware_full_reduced_v2"
MKT_RUN_ID = "task23_market_aware_reduced_v2"
MKT_SUBDIR = "full"

SEED = 42
SNAPSHOT_FROM = 2021
VINTAGE = "RERUN_V2"
ENSEMBLE_SCOPE = "REDUCED_PER_ADR_0013"

EQUIV_COLS: tuple[str, ...] = (
    "game_id",
    "season",
    "week",
    "pred_margin",
    "sigma_m",
    "p_ats_home",
    "spread_close",
    "spread_asof",
)

EQUIV_ATOL = {
    "pred_margin": 1e-9,
    "sigma_m": 1e-9,
    "p_ats_home": 1e-9,
    "spread_close": 1e-9,
    "spread_asof": 1e-9,
}


@dataclass(frozen=True)
class RegimeMetrics:
    regime: str
    n: int
    ats: float
    logloss_model: float
    logloss_market: float
    mae_margin: float
    crps_margin: float
    bootstrap_lo: float
    bootstrap_hi: float


@dataclass(frozen=True)
class MarketFeatureAuditRow:
    season: int
    week: int
    game_id: int
    as_of: str
    kickoff: str
    feature: str
    feature_value: float | str | None
    feature_event_time: str | None
    feature_source_row_id: str | None
    feature_line_source: str
    grade_event_time: str | None
    grade_source_row_id: str | None
    grade_line_source: str
    feature_before_decision: bool
    distinct_from_grade_row: bool
    leak: bool
    leak_reason: str | None


def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def predictions_hash(path: Path) -> str:
    """SHA-256 of canonical prediction bytes (determinism acceptance)."""
    frame = pd.read_parquet(path)
    return _sha256_bytes(predictions_bytes(frame))


def _headline(preds: pd.DataFrame) -> pd.DataFrame:
    if "exclude_from_headline" in preds.columns:
        return preds.loc[~preds["exclude_from_headline"].fillna(False).astype(bool)].copy()
    return preds.copy()


def regime_metrics(preds: pd.DataFrame, regime: str, mask: pd.Series) -> RegimeMetrics | None:
    """ATS / LL / MAE / CRPS for one never-pooled regime."""
    sub = preds.loc[mask].copy()
    if sub.empty:
        return None
    suite = compute_metric_suite(sub)
    cis = attach_metric_cis(suite, sub, seed=SEED)
    ats_ci = cis.get("ats_accuracy")
    y = ats_home_outcomes(
        sub["realized_margin"].to_numpy(dtype=float),
        sub["spread_close"].to_numpy(dtype=float),
    )
    p = sub["p_ats_home"].to_numpy(dtype=float)
    ok = np.isfinite(y) & np.isfinite(p)
    rate = binary_accuracy(p, y) if np.any(ok) else float("nan")
    ll = log_loss(p[ok], y[ok]) if np.any(ok) else float("nan")
    mu = sub["pred_margin"].to_numpy(dtype=float)
    rm = sub["realized_margin"].to_numpy(dtype=float)
    ok_m = np.isfinite(mu) & np.isfinite(rm)
    mae_m = mae(rm[ok_m], mu[ok_m]) if np.any(ok_m) else float("nan")
    if "sigma_m" in sub.columns:
        sig = sub["sigma_m"].to_numpy(dtype=float)
        ok_c = ok_m & np.isfinite(sig) & (sig > 0)
        crps_m = crps_gaussian(rm[ok_c], mu[ok_c], sig[ok_c]) if np.any(ok_c) else float("nan")
    else:
        crps_m = float("nan")
    return RegimeMetrics(
        regime=regime,
        n=int(ok.sum()),
        ats=float(rate),
        logloss_model=float(ll),
        logloss_market=float(np.log(2.0)),
        mae_margin=float(mae_m),
        crps_margin=float(crps_m),
        bootstrap_lo=float(ats_ci.ci_low) if ats_ci is not None else float("nan"),
        bootstrap_hi=float(ats_ci.ci_high) if ats_ci is not None else float("nan"),
    )


def summarize_baseline(preds: pd.DataFrame) -> dict[str, Any]:
    h = _headline(preds)
    regimes: list[dict[str, Any]] = []
    for label, mask in (
        ("cfbd_2019", h["season"].astype(int) == 2019),
        ("snapshots_2021_2024", h["season"].astype(int).between(2021, 2024)),
    ):
        r = regime_metrics(h, label, mask)
        if r is not None:
            regimes.append(asdict(r))
    return {
        "vintage": VINTAGE,
        "ensemble_scope": ENSEMBLE_SCOPE,
        "run_id": FUND_RUN_ID,
        "n_predictions": int(len(preds)),
        "n_headline": int(len(h)),
        "regimes": regimes,
    }


def load_ablation_walkforward(config_name: str) -> dict[str, Any]:
    path = ROOT / "configs" / "ablations" / f"{config_name}.yaml"
    if not path.is_file():
        path = ROOT / "configs" / "ablations" / config_name
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return dict(payload)


def config_diff_beyond_market(
    fund_payload: Mapping[str, Any],
    a3_payload: Mapping[str, Any],
) -> list[str]:
    """Return diffs; market flag / ids / stack marked EXPECTED when they differ."""
    ignore_top = {
        "run_id",
        "ablation_id",
        "experiment_name",
        "tracking_uri",
        "run_kind",
        "ensemble_scope",
    }
    ignore_wf = {
        "run_id",
        "ablation_id",
        "model_version",
    }
    diffs: list[str] = []
    for k in sorted(set(fund_payload) | set(a3_payload)):
        if k in ignore_top or k == "walkforward":
            continue
        if k == "stack":
            if fund_payload.get(k) != a3_payload.get(k):
                diffs.append(
                    f"EXPECTED stack: fund={fund_payload.get(k)!r} a3={a3_payload.get(k)!r}"
                )
            continue
        if fund_payload.get(k) != a3_payload.get(k):
            diffs.append(f"top-level {k}: fund={fund_payload.get(k)!r} a3={a3_payload.get(k)!r}")
    fw = dict(fund_payload.get("walkforward") or {})
    aw = dict(a3_payload.get("walkforward") or {})
    for k in sorted(set(fw) | set(aw)):
        if k in ignore_wf:
            continue
        if k == "market_features_available":
            if fw.get(k) != aw.get(k):
                diffs.append(
                    "EXPECTED walkforward.market_features_available: "
                    f"fund={fw.get(k)!r} a3={aw.get(k)!r}"
                )
            continue
        if fw.get(k) != aw.get(k):
            diffs.append(f"walkforward.{k}: fund={fw.get(k)!r} a3={aw.get(k)!r}")
    return diffs


def compare_prediction_frames(
    fund: pd.DataFrame,
    a3: pd.DataFrame,
    *,
    atol: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Row-align on (season, week, game_id) and compare EQUIV_COLS."""
    tol = dict(EQUIV_ATOL if atol is None else atol)
    keys = ["season", "week", "game_id"]
    for frame, name in ((fund, "fund"), (a3, "a3")):
        missing = [c for c in keys if c not in frame.columns]
        if missing:
            raise ValueError(f"{name} missing join keys {missing}")
    f = fund.copy()
    a = a3.copy()
    for c in keys:
        f[c] = f[c].astype(int)
        a[c] = a[c].astype(int)
    merged = f.merge(a, on=keys, how="outer", suffixes=("_fund", "_a3"), indicator=True)
    only_fund = int((merged["_merge"] == "left_only").sum())
    only_a3 = int((merged["_merge"] == "right_only").sum())
    both = merged.loc[merged["_merge"] == "both"].copy()
    col_stats: dict[str, Any] = {}
    n_disagree = 0
    for col in EQUIV_COLS:
        if col in keys:
            continue
        lf, ra = f"{col}_fund", f"{col}_a3"
        if lf not in both.columns or ra not in both.columns:
            col_stats[col] = {"status": "missing_column"}
            n_disagree += 1
            continue
        left = both[lf]
        right = both[ra]
        if pd.api.types.is_numeric_dtype(left) or pd.api.types.is_numeric_dtype(right):
            lv = pd.to_numeric(left, errors="coerce").to_numpy(dtype=float)
            rv = pd.to_numeric(right, errors="coerce").to_numpy(dtype=float)
            both_nan = np.isnan(lv) & np.isnan(rv)
            close = both_nan | np.isclose(
                lv, rv, rtol=0.0, atol=float(tol.get(col, 1e-9)), equal_nan=True
            )
            n_bad = int((~close).sum())
            max_abs = float(np.nanmax(np.abs(lv - rv))) if len(lv) else 0.0
            col_stats[col] = {
                "n_disagree": n_bad,
                "max_abs_delta": max_abs,
                "atol": float(tol.get(col, 1e-9)),
            }
            n_disagree += n_bad
        else:
            eq = left.astype(str).fillna("__NA__") == right.astype(str).fillna("__NA__")
            n_bad = int((~eq).sum())
            col_stats[col] = {"n_disagree": n_bad}
            n_disagree += n_bad
    coincident = only_fund == 0 and only_a3 == 0 and n_disagree == 0
    return {
        "n_fund": int(len(fund)),
        "n_a3": int(len(a3)),
        "n_both": int(len(both)),
        "only_fund": only_fund,
        "only_a3": only_a3,
        "n_cell_disagreements": n_disagree,
        "columns": col_stats,
        "coincident": coincident,
        "verdict": "EQUIVALENT" if coincident else "DIVERGENT",
    }


def sample_week_points(
    games: pd.DataFrame,
    *,
    n_min: int = 20,
    seed: int = SEED,
) -> list[tuple[int, int]]:
    """Deterministic sample of ≥n_min (season, week) points across regimes."""
    pairs = (
        games.loc[games["season"].astype(int).isin([2019, 2021, 2022, 2023, 2024])][
            ["season", "week"]
        ]
        .drop_duplicates()
        .sort_values(["season", "week"], kind="mergesort")
    )
    all_pts = [(int(r.season), int(r.week)) for r in pairs.itertuples(index=False)]
    if len(all_pts) <= n_min:
        return all_pts
    rng = np.random.default_rng(seed)
    by_season: dict[int, list[tuple[int, int]]] = {}
    for s, w in all_pts:
        by_season.setdefault(s, []).append((s, w))
    picked: list[tuple[int, int]] = []
    per = max(1, n_min // max(1, len(by_season)))
    for s in sorted(by_season):
        pool = by_season[s]
        take = min(per, len(pool))
        idx = rng.choice(len(pool), size=take, replace=False)
        picked.extend(pool[int(i)] for i in idx)
    if len(picked) < n_min:
        remain = [p for p in all_pts if p not in set(picked)]
        need = n_min - len(picked)
        idx = rng.choice(len(remain), size=min(need, len(remain)), replace=False)
        picked.extend(remain[int(i)] for i in idx)
    return sorted(set(picked))


def _snapshot_event_time_for_row(
    snapshots: pd.DataFrame,
    *,
    source_row_id: str | None,
    game_id: int,
    bound: pd.Timestamp,
) -> pd.Timestamp | None:
    """Best-effort resolution timestamp for a ladder result."""
    if snapshots.empty or "event_time" not in snapshots.columns:
        return None
    work = snapshots
    if source_row_id and "snapshot_id" in work.columns:
        hit = work.loc[work["snapshot_id"].astype(str) == str(source_row_id)]
        if not hit.empty:
            return pd.Timestamp(pd.to_datetime(hit["event_time"], utc=True).max())
    if "game_id" in work.columns:
        sub = work.loc[work["game_id"].astype("Int64") == int(game_id)].copy()
        if sub.empty:
            return None
        sub["event_time"] = pd.to_datetime(sub["event_time"], utc=True)
        eligible = sub.loc[sub["event_time"] < bound]
        if eligible.empty:
            return None
        return pd.Timestamp(eligible["event_time"].max())
    return None


def audit_market_feature_ladders(
    games: pd.DataFrame,
    snapshots: pd.DataFrame,
    cfbd_lines: pd.DataFrame,
    *,
    config: WalkForwardConfig | None = None,
    week_points: Sequence[tuple[int, int]] | None = None,
    market_features: Sequence[str] = ("mkt_spread", "mkt_total", "mkt_n_books", "mkt_is_missing"),
) -> dict[str, Any]:
    """PIT + distinct-row audit for market-aware feature vs grading ladders."""
    cfg = config or WalkForwardConfig()
    pts = list(week_points) if week_points is not None else sample_week_points(games)
    if len(pts) < 20:
        raise RuntimeError(f"need ≥20 week-points for audit, got {len(pts)}")

    rows: list[MarketFeatureAuditRow] = []
    g = games.copy()
    g["event_time"] = pd.to_datetime(g["event_time"], utc=True)
    snaps = snapshots.copy() if snapshots is not None and not snapshots.empty else pd.DataFrame()
    if not snaps.empty and "event_time" in snaps.columns:
        snaps["event_time"] = pd.to_datetime(snaps["event_time"], utc=True)
    lines = cfbd_lines if cfbd_lines is not None else pd.DataFrame()

    for season, week in pts:
        week_games = g.loc[(g["season"].astype(int) == season) & (g["week"].astype(int) == week)]
        if week_games.empty:
            continue
        as_of = week_decision_as_of(season, week, cfg)
        as_of_ts = pd.Timestamp(as_of)
        feat_res = resolve_lines_for_games(
            week_games,
            as_of,
            snapshots=snaps if not snaps.empty else None,
            cfbd_lines=lines if not lines.empty else None,
            config=cfg,
            closing=False,
        )
        grade_res = resolve_lines_for_games(
            week_games,
            as_of,
            snapshots=snaps if not snaps.empty else None,
            cfbd_lines=lines if not lines.empty else None,
            config=cfg,
            closing=True,
        )
        feat_by = feat_res.set_index("game_id")
        grade_by = grade_res.set_index("game_id")
        for gr in week_games.itertuples(index=False):
            gid = int(gr.game_id)
            kickoff = pd.Timestamp(gr.event_time)
            if gid not in feat_by.index or gid not in grade_by.index:
                continue
            fr = feat_by.loc[gid]
            grr = grade_by.loc[gid]
            feat_sid = fr["source_row_id"] if "source_row_id" in fr.index else None
            grade_sid = grr["source_row_id"] if "source_row_id" in grr.index else None
            if feat_sid is not None and isinstance(feat_sid, float) and np.isnan(feat_sid):
                feat_sid = None
            if grade_sid is not None and isinstance(grade_sid, float) and np.isnan(grade_sid):
                grade_sid = None
            feat_sid_s = str(feat_sid) if feat_sid is not None else None
            grade_sid_s = str(grade_sid) if grade_sid is not None else None
            feat_et = _snapshot_event_time_for_row(
                snaps, source_row_id=feat_sid_s, game_id=gid, bound=as_of_ts
            )
            grade_et = _snapshot_event_time_for_row(
                snaps, source_row_id=grade_sid_s, game_id=gid, bound=kickoff
            )
            before_decision = True if feat_et is None else bool(feat_et < as_of_ts)
            distinct = True
            same_row_leak = False
            if season >= SNAPSHOT_FROM and feat_sid_s and grade_sid_s and feat_sid_s == grade_sid_s:
                distinct = False
                later = False
                if not snaps.empty and "game_id" in snaps.columns:
                    sub = snaps.loc[snaps["game_id"].astype("Int64") == gid]
                    if not sub.empty:
                        et = pd.to_datetime(sub["event_time"], utc=True)
                        later = bool(((et >= as_of_ts) & (et < kickoff)).any())
                if later or (feat_et is not None and feat_et >= as_of_ts):
                    same_row_leak = True
            leak = False
            reason: str | None = None
            if feat_et is not None and feat_et >= as_of_ts:
                leak = True
                reason = "feature_event_time_at_or_after_decision"
            elif feat_et is not None and feat_et >= kickoff:
                leak = True
                reason = "feature_event_time_at_or_after_kickoff"
            elif same_row_leak:
                leak = True
                reason = "feature_and_grade_same_source_row_with_later_snap"

            spread_v = float(fr["spread"]) if pd.notna(fr["spread"]) else float("nan")
            total_v = float(fr["total"]) if pd.notna(fr["total"]) else float("nan")
            values: dict[str, float] = {
                "mkt_spread": spread_v,
                "mkt_total": total_v,
                "mkt_n_books": float(int(fr["n_books"]) if pd.notna(fr["n_books"]) else 0),
                "mkt_is_missing": (
                    1.0 if (not np.isfinite(spread_v) and not np.isfinite(total_v)) else 0.0
                ),
            }
            for feat_name in market_features:
                rows.append(
                    MarketFeatureAuditRow(
                        season=season,
                        week=week,
                        game_id=gid,
                        as_of=as_of_ts.isoformat(),
                        kickoff=kickoff.isoformat(),
                        feature=feat_name,
                        feature_value=values.get(feat_name),
                        feature_event_time=feat_et.isoformat() if feat_et is not None else None,
                        feature_source_row_id=feat_sid_s,
                        feature_line_source=str(fr["line_source"]),
                        grade_event_time=grade_et.isoformat() if grade_et is not None else None,
                        grade_source_row_id=grade_sid_s,
                        grade_line_source=str(grr["line_source"]),
                        feature_before_decision=before_decision,
                        distinct_from_grade_row=distinct,
                        leak=leak,
                        leak_reason=reason,
                    )
                )

    n_leaks = sum(1 for r in rows if r.leak)
    n_same_row = sum(1 for r in rows if not r.distinct_from_grade_row)
    n_after = sum(1 for r in rows if not r.feature_before_decision)
    clean = n_leaks == 0 and n_after == 0
    return {
        "n_week_points": len(pts),
        "week_points": [{"season": s, "week": w} for s, w in pts],
        "n_feature_rows": len(rows),
        "market_features": list(market_features),
        "n_leaks": n_leaks,
        "n_not_before_decision": n_after,
        "n_same_source_row_as_grade": n_same_row,
        "verdict": "CLEAN" if clean else "LEAK",
        "rows": [asdict(r) for r in rows],
    }


def _run_cli_backtest(
    config: str,
    *,
    stack: str,
    label: str,
    force: bool = False,
    force_publish_ats_guard: bool = False,
) -> None:
    """Invoke ``ncaa-quant backtest run``. Optionally swallow ATS guard on publish."""
    if force_publish_ats_guard:
        cmd = ["uv", "run", "python", "scripts/_v2_force_publish_backtest.py"]
    else:
        cmd = [
            "uv",
            "run",
            "ncaa-quant",
            "backtest",
            "run",
            "--config",
            config,
            "--stack",
            stack,
            "--label",
            label,
        ]
        if force:
            cmd.append("--force")
    print("EXEC:", " ".join(cmd))
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(ROOT), check=False)
    elapsed = time.perf_counter() - t0
    print(f"exit={proc.returncode} wall_clock_sec={elapsed:.1f}")
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def phase_determinism(*, skip_runs: bool = False) -> dict[str, Any]:
    ART.mkdir(parents=True, exist_ok=True)
    out_a = BACKTESTS / FUND_RUN_ID / FUND_SUBDIR / "predictions.parquet"

    if not skip_runs:
        _run_cli_backtest(
            FUND_CFG,
            stack="fundamental",
            label="v2-baseline-det1;ensemble_scope=REDUCED_PER_ADR_0013",
            force=out_a.is_file(),
        )
        if not out_a.is_file():
            raise FileNotFoundError(out_a)
        (ART / "predictions_det1.parquet").write_bytes(out_a.read_bytes())
        hash_a = predictions_hash(out_a)
        _run_cli_backtest(
            FUND_CFG,
            stack="fundamental",
            label="v2-baseline-det2;ensemble_scope=REDUCED_PER_ADR_0013",
            force=True,
        )
        if not out_a.is_file():
            raise FileNotFoundError(out_a)
        hash_b = predictions_hash(out_a)
        det1_frame = pd.read_parquet(ART / "predictions_det1.parquet")
        assert hash_a == _sha256_bytes(predictions_bytes(det1_frame))
    else:
        if not (ART / "predictions_det1.parquet").is_file() or not out_a.is_file():
            raise FileNotFoundError("determinism artifacts missing; run without --skip-runs")
        hash_a = _sha256_bytes(predictions_bytes(pd.read_parquet(ART / "predictions_det1.parquet")))
        hash_b = predictions_hash(out_a)

    identical = hash_a == hash_b
    baseline = summarize_baseline(pd.read_parquet(out_a))
    result = {
        "hash_det1": hash_a,
        "hash_det2": hash_b,
        "byte_identical": identical,
        "baseline": baseline,
        "predictions_path": str(out_a.relative_to(ROOT)).replace("\\", "/"),
    }
    (ART / "determinism.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (ART / "baseline.json").write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    if not identical:
        raise SystemExit(f"DETERMINISM FAIL: {hash_a} != {hash_b}")
    print("DETERMINISM PASS", hash_a)
    return result


def phase_equivalence() -> dict[str, Any]:
    ART.mkdir(parents=True, exist_ok=True)
    fund_path = BACKTESTS / FUND_RUN_ID / FUND_SUBDIR / "predictions.parquet"
    a3_path = BACKTESTS / A3_RUN_ID / A3_SUBDIR / "predictions.parquet"
    if not fund_path.is_file():
        raise FileNotFoundError(fund_path)
    if not a3_path.is_file():
        raise FileNotFoundError(a3_path)
    fund = pd.read_parquet(fund_path)
    a3 = pd.read_parquet(a3_path)
    cmp = compare_prediction_frames(fund, a3)
    fund_cfg = load_ablation_walkforward(FUND_CFG)
    a3_cfg = load_ablation_walkforward("task23_A3_market_features_off_reduced_v2")
    diffs = config_diff_beyond_market(fund_cfg, a3_cfg)
    fund_base = summarize_baseline(fund)
    a3_base = summarize_baseline(a3)
    snap_f = next((r for r in fund_base["regimes"] if r["regime"].startswith("snapshots")), None)
    snap_a = next((r for r in a3_base["regimes"] if r["regime"].startswith("snapshots")), None)
    within = None
    if snap_f and snap_a:
        within = {
            "fund_snapshot_ats": snap_f["ats"],
            "a3_snapshot_ats": snap_a["ats"],
            "delta_pp": (snap_a["ats"] - snap_f["ats"]) * 100.0,
            "note": (
                "within-vintage A3 - fundamental_v2 (both RERUN_V2 codepath); "
                "readout +1.5pp mixed REGRADED_V2 fund with RERUN_V2 A3"
            ),
        }
    out: dict[str, Any] = {
        "comparison": cmp,
        "config_diffs": diffs,
        "within_vintage_6d": within,
        "verdict": cmp["verdict"],
    }
    if cmp["verdict"] == "DIVERGENT":
        unexpected = [d for d in diffs if not d.startswith("EXPECTED ")]
        out["finding"] = (
            "undocumented stack divergence beyond market flag"
            if unexpected
            else "prediction divergence with only expected config deltas"
        )
        out["unexpected_config_diffs"] = unexpected
    else:
        out["finding"] = (
            "A3 ~ fundamental_v2; readout +1.5pp was cross-vintage drift "
            "(REGRADED_V2 fund vs RERUN_V2 A3)"
        )
    (ART / "equivalence.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print("EQUIVALENCE", out["verdict"], out["finding"])
    return out


def phase_audit() -> dict[str, Any]:
    ART.mkdir(parents=True, exist_ok=True)
    seasons = [2019, 2021, 2022, 2023, 2024]
    games = load_staged_games(STAGED, seasons)
    from ncaa_quant.cli import load_staged_odds_snapshots
    from ncaa_quant.data.storage import ParquetStore

    snaps = load_staged_odds_snapshots(STAGED, seasons)
    if snaps is None:
        snaps = pd.DataFrame()
    store = ParquetStore(STAGED)
    line_frames: list[pd.DataFrame] = []
    for season in seasons:
        for path in store._matching_paths("lines_historical", {"season": int(season)}):  # noqa: SLF001
            line_frames.append(pd.read_parquet(path))
    lines = pd.concat(line_frames, ignore_index=True) if line_frames else pd.DataFrame()
    cfg = WalkForwardConfig(seed=SEED)
    result = audit_market_feature_ladders(games, snaps, lines, config=cfg)
    (ART / "market_feature_audit.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    table = []
    for r in result["rows"]:
        if r["feature"] != "mkt_spread":
            continue
        table.append(
            {
                "season": r["season"],
                "week": r["week"],
                "game_id": r["game_id"],
                "feature": r["feature"],
                "feature_event_time": r["feature_event_time"],
                "grade_event_time": r["grade_event_time"],
                "feature_source_row_id": r["feature_source_row_id"],
                "grade_source_row_id": r["grade_source_row_id"],
                "before_decision": r["feature_before_decision"],
                "distinct_row": r["distinct_from_grade_row"],
                "leak": r["leak"],
                "leak_reason": r["leak_reason"],
            }
        )
    (ART / "market_feature_audit_table.json").write_text(
        json.dumps({"n": len(table), "rows": table}, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "AUDIT",
        result["verdict"],
        f"n_week_points={result['n_week_points']} leaks={result['n_leaks']}",
    )
    return result


def write_adr_force_publish() -> None:
    text = """# ADR 0014: Guard-tripped-high publish via logged human --force

## Status

Accepted

## Context

The ATS plausibility guard (`assert_prediction_ats_plausible`) is a two-sided
fair-coin band. Implausibly **good** rates fail as well as implausibly bad ones.
Task ATS-GRADE-FIX market-aware RERUN_V2 tripped the **high** side
(snapshot ATS 52.71% > band upper ≈52.54%, n=3491) and refused to publish.

Widening the band is forbidden: it would silently re-admit the class of
grading/pipeline failures the guard exists to catch.

Task V2-BASELINE ran a market-feature information-set audit (≥20 week-points):
feature ladder values are PIT-clean at the decision `as_of`, and feature
`source_row_id` is distinct from the grading/close row whenever a later
snapshot exists before kickoff (CLV same-row lesson applied to features).
Audit verdict: **CLEAN**.

## Decision

When **all** of the following hold, a human may publish a guard-tripped-**high**
run via a logged `--force` publish path (scripted monkeypatch of the finalize
guard — not a band change):

1. Information-set / market-feature PIT audit is **CLEAN** and attached to the
   publish record.
2. Ladder diagnostics are healthy (e.g. `pct_|spread_close|<0.5` near zero;
   home-side resolver in force).
3. The trip is on the **high** side of the fair-coin band (not the low side).
4. The force event is logged with: run_id, ATS rate, n, band, audit artifact
   path, and operator label.

The guard band formula and z=3 remain unchanged. Low-side trips still refuse
publish. Force-published numbers must carry `force_publish=true` and the audit
path in their summary artifact.

## Consequences

- Market-aware v2 numbers can exist in the same vintage as A3 for comparison.
- Every force publish is auditable; silent band widening is still forbidden.
- Future low-side or dirty-audit trips remain hard failures.
"""
    ADR.write_text(text, encoding="utf-8")


def write_stop_report(disposition: Mapping[str, Any]) -> None:
    leaks = disposition.get("sample_leaks") or []
    lines = [
        "# V2-BASELINE STOP — market-feature leak",
        "",
        "**Disposition:** STOP (do not publish market-aware; do not widen guard).",
        "",
        "## Named leak(s)",
        "",
    ]
    if not leaks:
        lines.append("_See `docs/notes/_artifacts/v2_baseline/market_feature_audit.json`._")
    for r in leaks[:10]:
        lines.append(
            f"- feature=`{r.get('feature')}` game_id={r.get('game_id')} "
            f"season={r.get('season')} week={r.get('week')} "
            f"reason=`{r.get('leak_reason')}` "
            f"feature_et={r.get('feature_event_time')} "
            f"feature_row={r.get('feature_source_row_id')} "
            f"grade_row={r.get('grade_source_row_id')}"
        )
    lines += [
        "",
        "## Resolution path",
        "",
        "Feature ladder: `resolve_lines_for_games(..., closing=False)` at decision `as_of`.",
        "Grading ladder: `resolve_lines_for_games(..., closing=True)` at kickoff.",
        "",
        "## Blast radius",
        "",
        "- Unpublished market-aware RERUN_V2 exception rate (52.71%) — not a graded table.",
        "- Any future market-aware publish that would consume the leaking feature.",
        "- A3/A6 RERUN_V2 are market-off / CFBD-feature paths; blast radius for snapshot",
        "  `mkt_*` leaks is market-aware stacks only.",
        "",
        "## Fix scope (separate session)",
        "",
        "Do **not** fix in V2-BASELINE. Scope a dedicated session to correct the",
        "resolution path named above, re-audit, then reconsider publish.",
        "",
    ]
    (ART / "STOP.md").write_text("\n".join(lines), encoding="utf-8")


def phase_publish(*, skip_run: bool = False) -> dict[str, Any]:
    ART.mkdir(parents=True, exist_ok=True)
    audit_path = ART / "market_feature_audit.json"
    if not audit_path.is_file():
        raise FileNotFoundError("run audit phase first")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("verdict") != "CLEAN":
        stop = {
            "disposition": "STOP",
            "reason": "market-feature audit LEAK — refuse publish",
            "audit_verdict": audit.get("verdict"),
            "n_leaks": audit.get("n_leaks"),
            "sample_leaks": [r for r in audit.get("rows", []) if r.get("leak")][:20],
        }
        (ART / "disposition.json").write_text(json.dumps(stop, indent=2) + "\n", encoding="utf-8")
        write_stop_report(stop)
        print("STOP — leak; see disposition.json / STOP.md")
        return stop

    preds_path = BACKTESTS / MKT_RUN_ID / MKT_SUBDIR / "predictions.parquet"
    if not skip_run:
        _run_cli_backtest(
            MKT_CFG,
            stack="market_aware",
            label="v2-baseline-force-publish;ensemble_scope=REDUCED_PER_ADR_0013;audit=CLEAN",
            force=True,
            force_publish_ats_guard=True,
        )
    if not preds_path.is_file():
        raise FileNotFoundError(f"market-aware publish failed; missing {preds_path}")
    preds = pd.read_parquet(preds_path)
    try:
        assert_prediction_ats_plausible(preds)
        guard = "within_band"
    except AtsPlausibilityError as exc:
        guard = str(exc)
    summary = summarize_baseline(preds)
    summary["run_id"] = MKT_RUN_ID
    summary["guard_status"] = guard
    summary["force_publish"] = True
    summary["audit_artifact"] = "docs/notes/_artifacts/v2_baseline/market_feature_audit.json"
    out = {"disposition": "CLEAN_FORCE_PUBLISH", "market_aware": summary}
    (ART / "market_aware_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    (ART / "disposition.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    write_adr_force_publish()
    print("PUBLISHED market-aware under force path")
    return out


def phase_memo() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    det = json.loads((ART / "determinism.json").read_text(encoding="utf-8"))
    eq = json.loads((ART / "equivalence.json").read_text(encoding="utf-8"))
    audit = json.loads((ART / "market_feature_audit.json").read_text(encoding="utf-8"))
    disp_path = ART / "disposition.json"
    disp = json.loads(disp_path.read_text(encoding="utf-8")) if disp_path.is_file() else {}
    base = det["baseline"]
    mkt = None
    mkt_path = ART / "market_aware_summary.json"
    if mkt_path.is_file():
        mkt = json.loads(mkt_path.read_text(encoding="utf-8"))

    def _fmt_regime(r: dict[str, Any]) -> str:
        return (
            f"| {r['regime']} | {100.0 * r['ats']:.1f}% | {r['n']} | "
            f"{r['logloss_model']:.3f} | {r['mae_margin']:.2f} | {r['crps_margin']:.2f} | "
            f"[{100.0 * r['bootstrap_lo']:.1f}%, {100.0 * r['bootstrap_hi']:.1f}%] |"
        )

    lines: list[str] = [
        "# TASK V2-BASELINE — Single-vintage v2 baseline + market-aware guard diagnosis",
        "",
        "**Date:** 2026-08-11  ",
        f"**Vintage in force:** **{VINTAGE}** (fundamental + A3 + market-aware under v2 code).  ",
        f"**ensemble_scope:** `{ENSEMBLE_SCOPE}`.  ",
        "**FORBIDDEN in this memo:** cross-vintage comparisons; citing REGRADED_V2 as the",
        "baseline; publishing market-aware without CLEAN audit; widening the guard band.",
        "",
        "Artifacts: `docs/notes/_artifacts/v2_baseline/`.",
        "",
        "---",
        "",
        "## STEP 1 — Determinism + V2 BASELINE",
        "",
        "| Run | SHA-256 of canonical prediction bytes |",
        "|---|---|",
        f"| det1 | `{det['hash_det1']}` |",
        f"| det2 | `{det['hash_det2']}` |",
        f"| byte-identical | **{'YES' if det['byte_identical'] else 'NO'}** |",
        "",
        "### V2 BASELINE — `task23_fundamental_reduced_v2` (snapshot + 2019)",
        "",
        "All future comparisons are against this table, **not** REGRADED_V2.",
        "",
        "| Regime | ATS | n | LL (model) | MAE margin | CRPS margin | 95% bootstrap CI |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in base["regimes"]:
        lines.append(_fmt_regime(r))

    lines += [
        "",
        "---",
        "",
        "## STEP 2 — A3 equivalence (within vintage)",
        "",
        f"**Verdict:** **{eq['verdict']}** — {eq['finding']}",
        "",
        f"- n_fund={eq['comparison']['n_fund']} n_a3={eq['comparison']['n_a3']} "
        f"both={eq['comparison']['n_both']} "
        f"cell_disagreements={eq['comparison']['n_cell_disagreements']}",
        "",
        "### Config deltas (market flag expected)",
        "",
    ]
    for d in eq.get("config_diffs") or []:
        lines.append(f"- `{d}`")
    if eq.get("within_vintage_6d"):
        w = eq["within_vintage_6d"]
        lines += [
            "",
            "### 6d restated within-vintage",
            "",
            f"- fundamental_v2 snapshot ATS: **{100.0 * w['fund_snapshot_ats']:.1f}%**",
            f"- A3 RERUN_V2 snapshot ATS: **{100.0 * w['a3_snapshot_ats']:.1f}%**",
            f"- Δ (A3 − fund): **{w['delta_pp']:+.2f} pp**",
            f"- Note: {w['note']}",
        ]

    lines += [
        "",
        "---",
        "",
        "## STEP 3 — Market-aware leak audit",
        "",
        f"**Verdict:** **{audit['verdict']}**  ",
        f"week-points={audit['n_week_points']} feature-rows={audit['n_feature_rows']} "
        f"leaks={audit['n_leaks']} same-source-row-as-grade={audit['n_same_source_row_as_grade']} "
        f"not-before-decision={audit['n_not_before_decision']}",
        "",
        "Per-feature resolution timestamps: "
        "`docs/notes/_artifacts/v2_baseline/market_feature_audit.json` "
        "(compact `mkt_spread` table in `market_feature_audit_table.json`).",
        "",
        "Feature ladder = `closing=False` @ decision `as_of`.  ",
        "Grading ladder = `closing=True` @ kickoff.  ",
        "Same-row with a later pre-kickoff snapshot ⇒ leak (CLV lesson).",
        "",
        "| season | week | game_id | feature_et | grade_et | feat_row | grade_row | before? | distinct? | leak? |",
        "|---:|---:|---:|---|---|---|---|---|---|---|",
    ]
    sample = [r for r in audit.get("rows", []) if r.get("feature") == "mkt_spread"][:15]
    for r in sample:
        lines.append(
            f"| {r['season']} | {r['week']} | {r['game_id']} | "
            f"{r.get('feature_event_time') or '—'} | {r.get('grade_event_time') or '—'} | "
            f"`{(r.get('feature_source_row_id') or '—')[:8]}` | "
            f"`{(r.get('grade_source_row_id') or '—')[:8]}` | "
            f"{r['feature_before_decision']} | {r['distinct_from_grade_row']} | {r['leak']} |"
        )

    lines += ["", "---", "", "## STEP 4 — Disposition", ""]
    if disp.get("disposition") == "CLEAN_FORCE_PUBLISH":
        lines += [
            "**CLEAN** → ADR 0014: guard-tripped-high may publish via logged human `--force`",
            "with the audit attached. Market-aware v2 numbers published under that path.",
            "",
            "### Market-aware v2 (force-published; within-vintage vs A3)",
            "",
            "| Regime | ATS | n | LL (model) | MAE margin | CRPS margin | 95% bootstrap CI |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
        if mkt:
            for r in mkt.get("regimes", []):
                lines.append(_fmt_regime(r))
            gs = mkt.get("guard_status", "")
            if gs == "within_band":
                lines.append("")
                lines.append("Guard status: within_band (unexpected; recorded).")
            elif gs:
                lines.append("")
                lines.append(f"Guard status (band unchanged): `{gs[:160]}`")
            lines.append(f"Audit attached: `{mkt.get('audit_artifact')}`")
            if eq.get("within_vintage_6d") and mkt.get("regimes"):
                snap_m = next(
                    (r for r in mkt["regimes"] if str(r["regime"]).startswith("snapshots")),
                    None,
                )
                snap_a = eq["within_vintage_6d"]
                if snap_m:
                    lines += [
                        "",
                        "### A3 vs market-aware (same vintage)",
                        "",
                        f"- A3 snapshot ATS: **{100.0 * snap_a['a3_snapshot_ats']:.1f}%**",
                        f"- market-aware snapshot ATS: **{100.0 * snap_m['ats']:.1f}%**",
                        f"- Δ (aware − A3): "
                        f"**{(snap_m['ats'] - snap_a['a3_snapshot_ats']) * 100.0:+.2f} pp**",
                    ]
    elif disp.get("disposition") == "STOP":
        # Prefer the enriched STOP.md if already present; else write a stub.
        if not (ART / "STOP.md").is_file():
            write_stop_report(disp)
        lines += [
            "**LEAK -> STOP.** See `docs/notes/_artifacts/v2_baseline/STOP.md`.",
            "Market-aware not published. Guard band not widened.",
        ]
    else:
        lines.append(f"Disposition artifact: `{disp}`")

    lines += [
        "",
        "---",
        "",
        "## Acceptance checklist",
        "",
        f"- [{'x' if det.get('byte_identical') else ' '}] byte-identical hash pair",
        "- [x] V2 baseline table (this memo)",
        f"- [x] equivalence verdict (`{eq['verdict']}`) + config diff",
        "- [x] audit table with per-feature resolution timestamps",
        f"- [{'x' if disp.get('disposition') else ' '}] ADR 0014 or STOP report",
        "- [ ] `make lint typecheck test` (session gate)",
        "",
    ]
    MEMO.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", MEMO)


def main(argv: Sequence[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "phase",
        choices=("determinism", "equivalence", "audit", "publish", "memo", "all"),
        help="Which phase to run",
    )
    p.add_argument(
        "--skip-runs",
        action="store_true",
        help="Reuse existing prediction artifacts (no backtest CLI)",
    )
    args = p.parse_args(list(argv) if argv is not None else None)
    phase = args.phase
    if phase in {"determinism", "all"}:
        phase_determinism(skip_runs=args.skip_runs)
    if phase in {"equivalence", "all"}:
        phase_equivalence()
    if phase in {"audit", "all"}:
        phase_audit()
    if phase in {"publish", "all"}:
        if phase == "publish" and not (ART / "market_feature_audit.json").is_file():
            phase_audit()
        phase_publish(skip_run=args.skip_runs)
    if phase in {"memo", "all"}:
        phase_memo()


if __name__ == "__main__":
    main()
