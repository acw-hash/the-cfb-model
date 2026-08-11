"""MKT-ASOF-FIX — root-cause count, grading mirror-check, re-audit helpers.

Phases:
  step1   — count (season, game) with kickoff < week_decision_as_of (2021–2024)
  step3   — per-season count of graded closes at/after kickoff; regrade if needed
  audit   — market-feature ladder audit (≥20 week-points) + prophecy sample
  all     — step1 → step3 → audit

Does not widen the ATS guard band. Does not touch the lockbox.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.leakage import assert_no_prophecy_features, audit_prophecy_features
from ncaa_quant.evaluation.metrics import (
    ats_home_outcomes,
    attach_metric_cis,
    binary_accuracy,
    compute_metric_suite,
    crps_gaussian,
    log_loss,
    mae,
)
from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    resolve_lines_for_games,
    week_decision_as_of,
)
from ncaa_quant.features.market_lines import feature_as_of_for_game

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "data" / "staged"
BACKTESTS = ROOT / "data" / "backtests"
ART = ROOT / "docs" / "notes" / "_artifacts" / "mkt_asof_fix"
SEED = 42
SNAPSHOT_FROM = 2021
SEASONS = (2021, 2022, 2023, 2024)

# Import audit helpers from the V2-BASELINE script (same ladder audit contract).
_V2 = ROOT / "scripts" / "_v2_baseline.py"
_spec = importlib.util.spec_from_file_location("v2_baseline_asof", _V2)
assert _spec is not None and _spec.loader is not None
_v2 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _v2
_spec.loader.exec_module(_v2)
audit_market_feature_ladders = _v2.audit_market_feature_ladders
sample_week_points = _v2.sample_week_points


@dataclass(frozen=True)
class RegimeMetrics:
    regime: str
    n: int
    ats: float
    logloss_model: float
    mae_margin: float
    crps_margin: float
    bootstrap_lo: float
    bootstrap_hi: float


def _ensure_art() -> None:
    ART.mkdir(parents=True, exist_ok=True)


def _read_hive(path: Path, seasons: list[int]) -> pd.DataFrame:
    files = sorted(path.rglob("*.parquet"))
    frames: list[pd.DataFrame] = []
    for f in files:
        parts = {p.split("=")[0]: p.split("=")[1] for p in f.parts if "=" in p}
        if "season" in parts and int(parts["season"]) not in seasons:
            continue
        part = pd.read_parquet(f)
        frames.append(part)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_odds(seasons: tuple[int, ...] = SEASONS) -> pd.DataFrame:
    return _read_hive(STAGED / "odds_snapshots", list(seasons))


def load_lines(seasons: tuple[int, ...] = SEASONS) -> pd.DataFrame:
    return _read_hive(STAGED / "lines_historical", list(seasons))


def step1_root_cause() -> dict[str, Any]:
    """Count games where fixed week decision as_of falls after kickoff."""
    _ensure_art()
    cfg = WalkForwardConfig()
    games = load_staged_games(STAGED, list(SEASONS))
    games = games.loc[games["season"].astype(int).between(2021, 2024)].copy()
    games["event_time"] = pd.to_datetime(games["event_time"], utc=True)
    rows: list[dict[str, Any]] = []
    for r in games.itertuples(index=False):
        season = int(r.season)
        week = int(r.week)
        kick = pd.Timestamp(r.event_time).to_pydatetime()
        as_of = week_decision_as_of(season, week, cfg)
        leak = kick < as_of
        feat_ao = feature_as_of_for_game(kick, as_of, season=season, week=week)
        rows.append(
            {
                "season": season,
                "week": week,
                "game_id": int(r.game_id),
                "kickoff": kick.isoformat(),
                "week_as_of": as_of.isoformat(),
                "kickoff_before_week_as_of": leak,
                "feature_as_of": feat_ao.isoformat() if feat_ao is not None else None,
            }
        )
    frame = pd.DataFrame(rows)
    n_total = int(len(frame))
    n_leak = int(frame["kickoff_before_week_as_of"].sum())
    by_season = (
        frame.groupby("season")["kickoff_before_week_as_of"]
        .agg(n_games="count", n_kick_before_as_of="sum")
        .reset_index()
        .to_dict(orient="records")
    )
    out = {
        "seasons": list(SEASONS),
        "n_games": n_total,
        "n_kickoff_before_week_as_of": n_leak,
        "by_season": by_season,
        "hypothesis": (
            "fixed per-week decision timestamps; games with kickoff < week_as_of "
            "previously resolved post-kickoff feature snapshots"
        ),
        "sample": frame.loc[frame["kickoff_before_week_as_of"]]
        .head(15)
        .to_dict(orient="records"),
    }
    (ART / "step1_root_cause.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(
        f"STEP1: n_games={n_total} n_kickoff_before_week_as_of={n_leak} "
        f"by_season={by_season}"
    )
    return out


def _snapshot_et_for_source(
    snaps: pd.DataFrame, *, source_row_id: str | None, game_id: int
) -> pd.Timestamp | None:
    if not source_row_id or snaps.empty or "snapshot_id" not in snaps.columns:
        return None
    sub = snaps.loc[
        (snaps["snapshot_id"].astype(str) == str(source_row_id))
        & (snaps["game_id"].astype("Int64") == game_id)
    ]
    if sub.empty or "event_time" not in sub.columns:
        return None
    return pd.Timestamp(pd.to_datetime(sub.iloc[0]["event_time"], utc=True))


def step3_grading_mirror(
    *,
    regrade_if_needed: bool = True,
) -> dict[str, Any]:
    """Count graded closes at/after kickoff; regrade contaminated tables if any."""
    _ensure_art()
    cfg = WalkForwardConfig()
    games = load_staged_games(STAGED, list(SEASONS) + [2019])
    games["event_time"] = pd.to_datetime(games["event_time"], utc=True)
    snaps = load_odds()
    if not snaps.empty and "event_time" in snaps.columns:
        snaps["event_time"] = pd.to_datetime(snaps["event_time"], utc=True)
    lines = load_lines(tuple(list(SEASONS) + [2019]))

    # Probe current closing ladder on staged 2021–2024 games.
    g = games.loc[games["season"].astype(int).between(2021, 2024)].copy()
    per_season: dict[str, Any] = {}
    contaminated_gids: list[int] = []
    for season in SEASONS:
        sub = g.loc[g["season"].astype(int) == season]
        if sub.empty:
            per_season[str(season)] = {"n_games": 0, "n_close_at_or_after_kickoff": 0}
            continue
        # Batch by week for as_of (closing ignores as_of for bound, but API needs one).
        n_bad = 0
        for week, wg in sub.groupby(sub["week"].astype(int)):
            as_of = week_decision_as_of(int(season), int(week), cfg)
            resolved = resolve_lines_for_games(
                wg,
                as_of,
                snapshots=snaps if not snaps.empty else None,
                cfbd_lines=lines if not lines.empty else None,
                config=cfg,
                closing=True,
            )
            for r in resolved.itertuples(index=False):
                gid = int(r.game_id)
                kick = pd.Timestamp(wg.loc[wg["game_id"] == gid, "event_time"].iloc[0])
                sid = getattr(r, "source_row_id", None)
                if sid is None or (isinstance(sid, float) and np.isnan(sid)):
                    continue
                if str(r.line_source).startswith("cfbd"):
                    # CFBD close has no snapshot event_time; not an Odds at/after kickoff.
                    continue
                et = _snapshot_et_for_source(snaps, source_row_id=str(sid), game_id=gid)
                if et is not None and et >= kick:
                    n_bad += 1
                    contaminated_gids.append(gid)
        per_season[str(season)] = {
            "n_games": int(len(sub)),
            "n_close_at_or_after_kickoff": int(n_bad),
        }

    n_bad_total = sum(int(v["n_close_at_or_after_kickoff"]) for v in per_season.values())
    out: dict[str, Any] = {
        "per_season": per_season,
        "n_close_at_or_after_kickoff_total": n_bad_total,
        "closing_enforces_strictly_before_kickoff": n_bad_total == 0,
        "contaminated_game_ids_sample": contaminated_gids[:20],
    }
    if n_bad_total == 0:
        out["disposition"] = (
            "zero graded closes at/after kickoff under current closing=True ladder; "
            "v2 tables stand (no regrade)"
        )
        print("STEP3: zero at/after-kickoff closes — v2 tables stand")
    else:
        out["disposition"] = "NONZERO contamination — regrade required"
        print(f"STEP3: CONTAMINATED n={n_bad_total} per_season={per_season}")
        if regrade_if_needed:
            out["regrade"] = _regrade_contaminated_runs(games, snaps, lines, cfg)

    (ART / "step3_grading_mirror.json").write_text(
        json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return out


def _headline(preds: pd.DataFrame) -> pd.DataFrame:
    if "exclude_from_headline" in preds.columns:
        return preds.loc[~preds["exclude_from_headline"].fillna(False).astype(bool)].copy()
    return preds.copy()


def _regime_metrics(preds: pd.DataFrame, regime: str, mask: pd.Series) -> RegimeMetrics | None:
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
        mae_margin=float(mae_m),
        crps_margin=float(crps_m),
        bootstrap_lo=float(ats_ci.ci_low) if ats_ci is not None else float("nan"),
        bootstrap_hi=float(ats_ci.ci_high) if ats_ci is not None else float("nan"),
    )


def _summarize_preds(preds: pd.DataFrame) -> list[dict[str, Any]]:
    h = _headline(preds)
    out: list[dict[str, Any]] = []
    for label, mask in (
        ("cfbd_2019", h["season"].astype(int) == 2019),
        ("snapshots_2021_2024", h["season"].astype(int).between(2021, 2024)),
    ):
        r = _regime_metrics(h, label, mask)
        if r is not None:
            out.append(asdict(r))
    return out


def _regrade_contaminated_runs(
    games: pd.DataFrame,
    snaps: pd.DataFrame,
    lines: pd.DataFrame,
    cfg: WalkForwardConfig,
) -> dict[str, Any]:
    """Re-resolve closes with kickoff constraint; drop games that lose close."""
    from scipy import stats

    # Mirror ATS-GRADE regrade targets that consume snapshot closes.
    targets = [
        ("task23_fundamental_reduced_v1", "full", "grade_v2"),
        ("task23_a3_reduced_v2", "A3_market_off", None),
        ("task23_market_aware_reduced_v2", "full", None),
    ]
    results: dict[str, Any] = {}
    for run_id, subdir, grade_sub in targets:
        run_dir = BACKTESTS / run_id / subdir
        pred_path = (
            run_dir / grade_sub / "predictions.parquet"
            if grade_sub
            else run_dir / "predictions.parquet"
        )
        if not pred_path.is_file():
            results[run_id] = {"skipped": True, "reason": f"missing {pred_path}"}
            continue
        preds = pd.read_parquet(pred_path)
        before = _summarize_preds(preds)
        # Re-resolve close per row.
        new_close = []
        ungraded = 0
        for row in preds.itertuples(index=False):
            gid = int(row.game_id)
            season = int(row.season)
            week = int(row.week)
            grow = games.loc[games["game_id"] == gid]
            if grow.empty:
                new_close.append(float("nan"))
                ungraded += 1
                continue
            as_of = week_decision_as_of(season, week, cfg)
            resolved = resolve_lines_for_games(
                grow,
                as_of,
                snapshots=snaps if not snaps.empty else None,
                cfbd_lines=lines if not lines.empty else None,
                config=cfg,
                closing=True,
            )
            sp = float(resolved.iloc[0]["spread"]) if not resolved.empty else float("nan")
            if not np.isfinite(sp):
                ungraded += 1
            new_close.append(sp)
        preds = preds.copy()
        preds["spread_close"] = new_close
        # Refresh p_ats_home at corrected close when Gaussian columns present.
        if {"pred_margin", "sigma_m"}.issubset(preds.columns):
            mu = preds["pred_margin"].to_numpy(dtype=float)
            sig = preds["sigma_m"].to_numpy(dtype=float)
            sp = preds["spread_close"].to_numpy(dtype=float)
            ok = np.isfinite(mu) & np.isfinite(sig) & (sig > 0) & np.isfinite(sp)
            p = np.full(len(preds), np.nan)
            p[ok] = stats.norm.cdf((mu[ok] + sp[ok]) / sig[ok])
            preds["p_ats_home"] = p
        after = _summarize_preds(preds)
        out_dir = ART / "regrade" / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        preds.to_parquet(out_dir / "predictions.parquet", index=False)
        results[run_id] = {
            "before": before,
            "after": after,
            "n_ungraded_after_close_loss": ungraded,
            "artifact": str(out_dir / "predictions.parquet"),
        }
    return results


def phase_audit() -> dict[str, Any]:
    """Market-feature information-set audit + planted-prophecy over mkt_*."""
    _ensure_art()
    cfg = WalkForwardConfig(seed=SEED)
    seasons = [2019, *SEASONS]
    games = load_staged_games(STAGED, seasons)
    games["event_time"] = pd.to_datetime(games["event_time"], utc=True)
    snaps = load_odds()
    lines = load_lines(tuple(seasons))
    pts = sample_week_points(games, n_min=20, seed=SEED)
    result = audit_market_feature_ladders(
        games,
        snaps,
        lines,
        config=cfg,
        week_points=pts,
    )
    (ART / "market_feature_audit.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )

    # Prophecy audit on a feature frame built from resolved market columns.
    week_games = games.loc[
        games.apply(lambda r: (int(r.season), int(r.week)) in set(pts), axis=1)
    ].copy()
    # Build a compact feature matrix at week as_of for prophecy check.
    feat_rows: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    for season, week in pts:
        wg = week_games.loc[
            (week_games["season"].astype(int) == season) & (week_games["week"].astype(int) == week)
        ]
        if wg.empty:
            continue
        as_of = week_decision_as_of(season, week, cfg)
        resolved = resolve_lines_for_games(
            wg,
            as_of,
            snapshots=snaps if not snaps.empty else None,
            cfbd_lines=lines if not lines.empty else None,
            config=cfg,
            closing=False,
        )
        for r in resolved.itertuples(index=False):
            gid = int(r.game_id)
            spread = float(r.spread) if pd.notna(r.spread) else float("nan")
            total = float(r.total) if pd.notna(r.total) else float("nan")
            missing = 1.0 if (not np.isfinite(spread) and not np.isfinite(total)) else 0.0
            feat_rows.append(
                {
                    "game_id": gid,
                    "feat__mkt_spread": spread,
                    "feat__mkt_total": total,
                    "feat__mkt_n_books": float(int(r.n_books) if pd.notna(r.n_books) else 0),
                    "feat__mkt_is_missing": missing,
                }
            )
            gr = wg.loc[wg["game_id"] == gid].iloc[0]
            # Labels from games if present.
            hm = gr.get("home_points", np.nan)
            am = gr.get("away_points", np.nan)
            if pd.notna(hm) and pd.notna(am):
                label_rows.append(
                    {
                        "game_id": gid,
                        "realized_margin": float(hm) - float(am),
                        "realized_total": float(hm) + float(am),
                    }
                )
    prophecy_summary: dict[str, Any]
    if feat_rows and label_rows:
        feats = pd.DataFrame(feat_rows)
        labels = pd.DataFrame(label_rows)
        merged = feats.merge(labels, on="game_id", how="inner")
        # Drop rows missing labels.
        if not merged.empty:
            prop = audit_prophecy_features(
                merged[
                    [
                        "game_id",
                        "feat__mkt_spread",
                        "feat__mkt_total",
                        "feat__mkt_n_books",
                        "feat__mkt_is_missing",
                    ]
                ],
                merged[["game_id", "realized_margin", "realized_total"]],
            )
            try:
                assert_no_prophecy_features(prop)
                prophecy_ok = True
                prophecy_msg = prop.describe()
            except Exception as exc:  # noqa: BLE001
                prophecy_ok = False
                prophecy_msg = str(exc)
            prophecy_summary = {
                "passed": prophecy_ok,
                "detail": prophecy_msg,
                "n_features_checked": prop.n_features_checked,
                "n_findings": len(prop.findings),
            }
        else:
            prophecy_summary = {"passed": True, "detail": "no labeled rows", "n_findings": 0}
    else:
        prophecy_summary = {"passed": True, "detail": "insufficient rows", "n_findings": 0}

    out = {
        "audit_verdict": result.get("verdict"),
        "n_leaks": result.get("n_leaks"),
        "n_week_points": result.get("n_week_points"),
        "n_feature_rows": result.get("n_feature_rows"),
        "prophecy": prophecy_summary,
        "artifact": "docs/notes/_artifacts/mkt_asof_fix/market_feature_audit.json",
    }
    (ART / "audit_summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(
        f"AUDIT: verdict={out['audit_verdict']} leaks={out['n_leaks']} "
        f"prophecy_passed={prophecy_summary.get('passed')}"
    )
    return out


def summarize_published_tables() -> dict[str, Any]:
    """Load REGRADED_V2 / RERUN_V2 reference numbers for the memo."""
    regrade = ROOT / "docs" / "notes" / "_artifacts" / "ats_grade_fix" / "regrade_summary.json"
    rerun = ROOT / "docs" / "notes" / "_artifacts" / "ats_grade_fix" / "rerun_v2_summary.json"
    out: dict[str, Any] = {}
    if regrade.is_file():
        out["REGRADED_V2"] = json.loads(regrade.read_text(encoding="utf-8"))
    if rerun.is_file():
        out["RERUN_V2"] = json.loads(rerun.read_text(encoding="utf-8"))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=("step1", "step3", "audit", "all"),
        nargs="?",
        default="all",
    )
    args = parser.parse_args()
    t0 = time.time()
    if args.phase in {"step1", "all"}:
        step1_root_cause()
    if args.phase in {"step3", "all"}:
        step3_grading_mirror()
    if args.phase in {"audit", "all"}:
        phase_audit()
    print(f"done in {time.time() - t0:.1f}s -> {ART}")


if __name__ == "__main__":
    main()
