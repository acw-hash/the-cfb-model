"""W9-L Amendment 2 acceptance: live champion-method predict_publish.

No R2, no fit, no real hysteresis/ledger write. Recompute Kalman per call.
"""

from __future__ import annotations

import hashlib
import json
import os
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

os.environ["NCAA_QUANT_WEBAPP__EXPORT_ENABLED"] = "false"

from ncaa_quant.config import load_config
from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.walkforward import WeekDecisionCalendar, week_decision_as_of
from ncaa_quant.pipelines.predict import (
    exclude_games_kicked_off_before,
    live_predict_rows,
    load_champion_walkforward_config,
    load_production_prediction_rows,
    rating_snapshot_digest,
    run_isolated_week_export,
)

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent
V3_PRED = ROOT / "data" / "backtests" / "task23_fundamental_reduced_v3" / "full" / "predictions.parquet"
ISOLATION_PATHS = (
    ROOT / "data" / "webapp" / "tier_state.json",
    ROOT / "data" / "webapp" / "tier_changes.jsonl",
    ROOT / "data" / "pipeline_state" / "idempotency.json",
    ROOT / "data" / "artifacts" / "state_space" / "filter_history.parquet",
    ROOT / "data" / "artifacts" / "expected_possessions" / "live.json",
)


def sha256_file(path: Path) -> str:
    if not path.is_file():
        return "ABSENT"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hash_isolation() -> dict[str, str]:
    return {str(p.as_posix()): sha256_file(p) for p in ISOLATION_PATHS}


def _log(msg: str) -> None:
    now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"W9-L {now} {msg}", flush=True)


def inspect_reingest() -> dict[str, Any]:
    cfg = load_config()
    staged = Path(cfg.paths.staged_dir)
    games = load_staged_games(staged, (2026,))
    w1 = games.loc[games["week"].astype(int) == 1].copy()
    et = pd.to_datetime(w1["event_time"], utc=True)
    kick = pd.to_datetime(w1["start_date"], utc=True)
    ingested = pd.to_datetime(w1["ingested_at"], utc=True) if "ingested_at" in w1.columns else None
    n_clamped = 0
    if ingested is not None:
        n_clamped = int((et == ingested).sum())
    wf = load_champion_walkforward_config()
    calendar = WeekDecisionCalendar.from_games(w1)
    as_of = week_decision_as_of(2026, 1, wf, calendar=calendar)
    kept, n_excluded = exclude_games_kicked_off_before(w1, as_of)
    early = w1.loc[kick < pd.Timestamp(as_of)].copy()
    early_ids = [str(int(x)) for x in early["game_id"]]
    return {
        "n_2026_games": int(len(games)),
        "n_week1": int(len(w1)),
        "n_clamped_event_time_eq_ingested_at": n_clamped,
        "as_of": as_of.isoformat(),
        "n_excluded_kickoff_before_as_of": n_excluded,
        "n_publish": int(len(kept)),
        "early_game_ids": early_ids,
        "event_time_min": et.min().isoformat() if len(et) else None,
        "event_time_max": et.max().isoformat() if len(et) else None,
        "kickoff_min": kick.min().isoformat() if len(kick) else None,
        "historical_week1_note": "2024 week 1 was 146; 2025 week 1 was 142; CFBD 2026-08-18 still 99",
    }


def residual_27() -> dict[str, Any]:
    """Quantify v3 week-1 rows with as_of after kickoff. Do not reopen reval."""
    if not V3_PRED.is_file():
        return {"error": f"missing {V3_PRED.as_posix()}"}
    pred = pd.read_parquet(V3_PRED)
    w1 = pred.loc[pred["week"].astype(int) == 1].copy()
    w1 = w1.loc[w1["season"].astype(int).isin([2021, 2022, 2023, 2024])].copy()
    w1["game_id"] = w1["game_id"].astype("int64")
    cfg = load_config()
    games = load_staged_games(Path(cfg.paths.staged_dir), (2021, 2022, 2023, 2024))
    g1 = games.loc[games["week"].astype(int) == 1, ["game_id", "start_date"]].copy()
    g1["game_id"] = g1["game_id"].astype("int64")
    w1 = w1.merge(g1, on="game_id", how="left")
    w1 = w1.reset_index(drop=True)
    as_of = pd.to_datetime(w1["as_of"], utc=True)
    kick = pd.to_datetime(w1["start_date"], utc=True)
    early_mask = kick < as_of
    early = w1.loc[early_mask].copy()
    rest = w1.loc[~early_mask].copy()
    by_season = {
        int(s): int((early["season"].astype(int) == int(s)).sum())
        for s in (2021, 2022, 2023, 2024)
    }
    out: dict[str, Any] = {
        "n_week1_2021_2024": int(len(w1)),
        "n_as_of_after_kickoff": int(len(early)),
        "by_season": by_season,
        "successor": "W9-L-residual-week1-straddle-metrics",
        "revalidation_reopened": False,
    }

    def _ats_rate(frame: pd.DataFrame) -> dict[str, Any]:
        p = pd.to_numeric(frame["p_ats_home"], errors="coerce")
        margin = pd.to_numeric(frame["realized_margin"], errors="coerce")
        spread = pd.to_numeric(frame["spread_close"], errors="coerce")
        if "p_ats_home_is_missing" in frame.columns:
            missing = frame["p_ats_home_is_missing"].astype(bool)
            p = p.where(~missing)
        cover = margin + spread
        ok = p.notna() & margin.notna() & spread.notna() & (cover != 0)
        n = int(ok.sum())
        if n == 0:
            return {"n": 0, "ats": None, "logloss": None}
        pv = p.loc[ok].clip(1e-6, 1 - 1e-6).to_numpy(dtype=float)
        yv = (cover.loc[ok] > 0).to_numpy(dtype=float)
        pick_home = pv >= 0.5
        hits = (pick_home & (yv == 1.0)) | (~pick_home & (yv == 0.0))
        logloss = float((-(yv * np.log(pv) + (1.0 - yv) * np.log(1.0 - pv))).mean())
        return {"n": n, "ats": float(hits.mean()), "logloss": logloss}

    if "p_ats_home" in w1.columns and "spread_close" in w1.columns:
        out["ats_all_week1"] = _ats_rate(w1)
        out["ats_straddle"] = _ats_rate(early)
        out["ats_week1_without_straddle"] = _ats_rate(rest)
    else:
        out["ats_note"] = "ATS recompute skipped; count recorded only"
    return out


def crossing_fraction(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    n_warn = 0
    lo_changed = 0
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # Crossing already happened inside predict(); inspect published bounds vs raw if present.
        _ = caught
    for rec in rows:
        q_cols = sorted(
            k for k in rec if str(k).startswith("pred_margin_q") and rec.get(k) is not None
        )
        if len(q_cols) < 2:
            continue
        vals = [float(rec[k]) for k in q_cols]
        if any(vals[i] > vals[i + 1] + 1e-12 for i in range(len(vals) - 1)):
            n_warn += 1
        # After sort, published interval uses ordered q05/q95 (or cqr).
        if rec.get("cqr_lo") is not None and rec.get("pred_margin_q05") is not None:
            if abs(float(rec["cqr_lo"]) - float(rec["pred_margin_q05"])) > 1e-9:
                lo_changed += 1
    return {"n": n, "n_raw_q_crossing_still_in_row": n_warn, "n_cqr_lo_differs_q05": lo_changed}


def compare_2024_w5(live_rows: list[dict[str, Any]]) -> dict[str, Any]:
    oracle = load_production_prediction_rows(2024, 5)
    a = {str(r["game_id"]): r for r in live_rows}
    b = {str(r["game_id"]): r for r in oracle}
    ids = sorted(set(a) & set(b), key=lambda x: int(x))
    fields = ("mu_margin", "sigma_margin", "p_ml_home")

    def _stats(field: str) -> dict[str, float | None]:
        deltas: list[float] = []
        for gid in ids:
            va = a[gid].get(field)
            vb = b[gid].get(field)
            if va is None or vb is None:
                continue
            try:
                deltas.append(abs(float(va) - float(vb)))
            except (TypeError, ValueError):
                continue
        if not deltas:
            return {"n": 0, "min": None, "median": None, "p90": None, "max": None}
        s = pd.Series(deltas)
        return {
            "n": int(len(deltas)),
            "min": float(s.min()),
            "median": float(s.median()),
            "p90": float(s.quantile(0.9)),
            "max": float(s.max()),
        }

    return {
        "n_live": len(live_rows),
        "n_oracle": len(oracle),
        "n_common": len(ids),
        "fields": {f: _stats(f) for f in fields},
        "note": "Amendment 1 withdrew expect-0.0; champion-method vs stored parquet",
    }


def main() -> None:
    started = datetime.now(tz=UTC)
    _log(f"start={started.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    inspect = inspect_reingest()
    _log("reingest=" + json.dumps(inspect, default=str))
    residual = residual_27()
    _log("residual27=" + json.dumps(residual, default=str))

    hashes_before = hash_isolation()
    _log("isolation_before=" + json.dumps(hashes_before, sort_keys=True))

    cfg = load_config()
    _log(f"export_enabled={cfg.webapp.export_enabled}")

    _log("2024 week 5 live predict (champion method)")
    with warnings.catch_warnings(record=True) as w5_warn:
        warnings.simplefilter("always")
        live_w5 = live_predict_rows(2024, 5)
    w5_cross = [str(w.message) for w in w5_warn if "quantile crossing" in str(w.message).lower()]
    w5_compare = compare_2024_w5(live_w5)
    w5_compare["quantile_crossing_warnings"] = w5_cross
    w5_compare["rating_digest"] = live_w5[0].get("rating_digest") if live_w5 else None
    _log("w5_oracle_delta=" + json.dumps(w5_compare, default=str))

    _log("2026 week 1 live predict pass 1")
    with warnings.catch_warnings(record=True) as w1a_warn:
        warnings.simplefilter("always")
        rows_a = live_predict_rows(2026, 1)
    digest_a = str(rows_a[0].get("rating_digest") or "")
    _log(f"pass1 n={len(rows_a)} rating_digest={digest_a}")

    _log("2026 week 1 live predict pass 2 (determinism)")
    with warnings.catch_warnings(record=True) as w1b_warn:
        warnings.simplefilter("always")
        rows_b = live_predict_rows(2026, 1)
    digest_b = str(rows_b[0].get("rating_digest") or "")
    _log(f"pass2 n={len(rows_b)} rating_digest={digest_b}")

    def _mu_map(rows: list[dict[str, Any]]) -> dict[str, float]:
        out: dict[str, float] = {}
        for r in rows:
            gid = str(r["game_id"])
            out[gid] = float(r["mu_margin"])
        return out

    mu_a = _mu_map(rows_a)
    mu_b = _mu_map(rows_b)
    identical_ids = set(mu_a) == set(mu_b)
    max_mu_delta = 0.0
    if identical_ids:
        max_mu_delta = max(abs(mu_a[g] - mu_b[g]) for g in mu_a)
    pred_hash_a = hashlib.sha256(
        json.dumps({k: mu_a[k] for k in sorted(mu_a, key=lambda x: int(x))}, sort_keys=True).encode()
    ).hexdigest()
    pred_hash_b = hashlib.sha256(
        json.dumps({k: mu_b[k] for k in sorted(mu_b, key=lambda x: int(x))}, sort_keys=True).encode()
    ).hexdigest()
    _log(f"determinism rating_digest_a={digest_a}")
    _log(f"determinism rating_digest_b={digest_b}")
    _log(f"determinism pred_hash_a={pred_hash_a}")
    _log(f"determinism pred_hash_b={pred_hash_b}")
    _log(f"determinism identical_ids={identical_ids} max_mu_delta={max_mu_delta}")
    if digest_a != digest_b or pred_hash_a != pred_hash_b:
        raise RuntimeError("non-deterministic live ratings/predictions")

    out_dir = ART / "live_export"
    state_dir = ART / "live_state"
    as_of = inspect["as_of"]
    def _public_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        drop = {"rating_digest"}
        return [{k: v for k, v in r.items() if k not in drop} for r in rows]

    iso = run_isolated_week_export(
        season=2026,
        week=1,
        output_dir=out_dir,
        state_dir=state_dir,
        published_at=datetime.fromisoformat(str(as_of).replace("Z", "+00:00")),
        predict_fn=lambda _ctx: _public_rows(rows_b),
    )
    week_path = out_dir / "week_predictions.json"
    week = json.loads(week_path.read_text(encoding="utf-8"))
    games_out = week.get("games") or []
    tiers: dict[str, int] = {}
    n_null_tier = 0
    n_sigma_refused = 0
    mus: list[float] = []
    sigs: list[float] = []
    for g in games_out:
        tier = g.get("conviction_tier")
        if tier is None:
            n_null_tier += 1
            tiers["null_suppressed"] = tiers.get("null_suppressed", 0) + 1
        else:
            tiers[str(tier)] = tiers.get(str(tier), 0) + 1
        if g.get("sigma_margin_credible") is False:
            n_sigma_refused += 1
        if g.get("mu_margin") is not None:
            mus.append(float(g["mu_margin"]))
        if g.get("sigma_margin") is not None:
            sigs.append(float(g["sigma_margin"]))

    summary = {
        "n_games_predicted": len(games_out),
        "mu_range": [min(mus), max(mus)] if mus else None,
        "sigma_range": [min(sigs), max(sigs)] if sigs else None,
        "conviction_tier_distribution": tiers,
        "n_null_tier_suppressed": n_null_tier,
        "n_sigma_refused": n_sigma_refused,
        "as_of": inspect["as_of"],
        "n_week": inspect["n_week1"],
        "n_excluded": inspect["n_excluded_kickoff_before_as_of"],
        "model_identity": week.get("model_identity"),
        "feature_time_label": week.get("feature_time_label"),
        "export_enabled": iso.get("export_enabled"),
        "pred_hash_a": pred_hash_a,
        "pred_hash_b": pred_hash_b,
        "rating_digest_a": digest_a,
        "rating_digest_b": digest_b,
        "quantile_crossing_2026": [str(w.message) for w in w1a_warn if "quantile crossing" in str(w.message).lower()],
        "w5_compare": w5_compare,
        "reingest": inspect,
        "residual_27": residual,
    }
    _log("summary=" + json.dumps(summary, default=str))
    (ART / "live_publish_summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
    )

    hashes_after = hash_isolation()
    _log("isolation_after=" + json.dumps(hashes_after, sort_keys=True))
    changed = [k for k, v in hashes_before.items() if hashes_after.get(k) != v]
    _log(f"isolation_changed={changed}")
    if changed:
        raise RuntimeError(f"isolation violated: {changed}")
    ended = datetime.now(tz=UTC)
    _log(
        f"end={ended.strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"elapsed_sec={(ended - started).total_seconds():.3f}"
    )


if __name__ == "__main__":
    main()
