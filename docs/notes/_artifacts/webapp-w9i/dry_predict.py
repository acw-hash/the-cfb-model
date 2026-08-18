"""W9-I dry 2026 week-1 predict. No R2, no fit, no real hysteresis/ledger writes.

Policy: 2025 games/advanced_box are loaded only as Kalman observations
(state propagation). No 2025 metrics, grading, or odds-snapshot load.
WalkForwardConfig replay seasons stay 2019–2024 (lockbox not listed).
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

os.environ["NCAA_QUANT_WEBAPP__EXPORT_ENABLED"] = "false"

from ncaa_quant.config import load_config
from ncaa_quant.evaluation.backtest_runner import (
    load_backtest_config,
    load_staged_games,
    walkforward_config_from_mapping,
)
from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON, assert_lockbox_excluded
from ncaa_quant.evaluation.production_stack import ProductionFeatureProvider
from ncaa_quant.evaluation.walkforward import WeekDecisionCalendar, week_decision_as_of
from ncaa_quant.pipelines.predict import (
    _alias_stamp_columns,
    run_isolated_week_export,
)
from ncaa_quant.registry.bundle import ENSEMBLE_FILENAME, load_production_ensemble
from ncaa_quant.registry.store import ModelRegistry

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent
LIVE_SEASON = 2026
LIVE_WEEK = 1
ISOLATION_PATHS = (
    ROOT / "data" / "webapp" / "tier_state.json",
    ROOT / "data" / "webapp" / "tier_changes.jsonl",
    ROOT / "data" / "pipeline_state" / "idempotency.json",
)


def sha256_file(path: Path) -> str:
    if not path.is_file():
        return "ABSENT"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hash_isolation() -> dict[str, str]:
    return {str(p.as_posix()): sha256_file(p) for p in ISOLATION_PATHS}


def snapshot_from_filter_history(hist: pd.DataFrame, as_of: datetime) -> dict[str, float]:
    """Last postgame (else last) row per team with event_time < as_of."""
    work = hist.copy()
    work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    work = work.loc[work["event_time"] < cutoff]
    if work.empty:
        return {}
    if "kind" in work.columns:
        post = work.loc[work["kind"].astype(str) == "postgame"]
        if not post.empty:
            work = post
    latest = work.sort_values("event_time").groupby("team_id", sort=False).tail(1)
    dims = ("off_epa", "def_epa", "st_value", "pace")
    out: dict[str, float] = {}
    for r in latest.itertuples(index=False):
        tid = str(int(r.team_id))
        for dim in dims:
            out[f"{tid}:{dim}"] = float(getattr(r, dim))
            sd = getattr(r, f"sd_{dim}", None)
            if sd is not None and pd.notna(sd):
                out[f"{tid}:sd_{dim}"] = float(sd)
    return out


def off_epa_from_state(state: dict[str, float]) -> dict[str, float]:
    suffix = ":off_epa"
    return {
        k[: -len(suffix)]: float(v)
        for k, v in state.items()
        if k.endswith(suffix) and not k.endswith(":sd_off_epa")
    }


def main() -> None:
    started = datetime.now(tz=UTC)
    print(f"W9-I dry_predict start={started.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(
        "W9-I policy=2025 loaded as Kalman observations only; "
        "not in WalkForwardConfig; no metrics/grading/odds snapshots"
    )
    hashes_before = hash_isolation()
    print(f"W9-I isolation_before={json.dumps(hashes_before, sort_keys=True)}")

    cfg_app = load_config()
    print(f"W9-I load_config.export_enabled={cfg_app.webapp.export_enabled}")
    print(f"W9-I load_config.data.end_season={cfg_app.data.end_season}")
    print(f"W9-I env NCAA_QUANT_WEBAPP__EXPORT_ENABLED={os.environ.get('NCAA_QUANT_WEBAPP__EXPORT_ENABLED')}")

    payload = load_backtest_config("task23_fundamental_full_reduced_v3")
    wf = walkforward_config_from_mapping(payload)
    replay = wf.all_replay_seasons()
    print(f"W9-I walkforward_replay_seasons={list(replay)}")
    assert_lockbox_excluded(replay, context="W9-I dry predict WalkForwardConfig")
    if LOCKBOX_SEASON in replay:
        raise RuntimeError("lockbox listed in replay; refusing")

    staged = Path(cfg_app.paths.staged_dir)
    live_games = load_staged_games(staged, (LIVE_SEASON,))
    live_games = live_games.loc[live_games["week"].astype(int) == LIVE_WEEK].copy()
    if live_games.empty:
        raise FileNotFoundError("2026 week 1 games not staged")
    # Unplayed rows store schedule-knowable event_time (= ingested_at). Kickoff
    # for the Tuesday calendar is start_date.
    cal_games = live_games.copy()
    if "start_date" in cal_games.columns:
        cal_games["event_time"] = pd.to_datetime(cal_games["start_date"], utc=True)
    calendar = WeekDecisionCalendar.from_games(cal_games)
    as_of = week_decision_as_of(LIVE_SEASON, LIVE_WEEK, wf, calendar=calendar)
    print(f"W9-I week1_as_of={as_of.isoformat()} n_live_games={len(live_games)}", flush=True)

    hist_path = ROOT / "data" / "artifacts" / "state_space" / "filter_history.parquet"
    print(
        f"W9-I rating_source={hist_path.as_posix()} "
        "(Task 14 GT-active history; 2025 rows = state propagation, not a fit)",
        flush=True,
    )
    hist = pd.read_parquet(hist_path)
    n_2025_hist = int((hist["season"].astype(int) == LOCKBOX_SEASON).sum()) if "season" in hist.columns else 0
    print(f"W9-I filter_history_n={len(hist)} n_2025_rows={n_2025_hist}", flush=True)
    if n_2025_hist == 0:
        raise RuntimeError("failure mode 2: filter_history has no 2025 rows")

    first_2025 = pd.to_datetime(
        hist.loc[hist["season"].astype(int) == LOCKBOX_SEASON, "event_time"], utc=True
    )
    eoy2024_as_of = first_2025.min().to_pydatetime() - timedelta(seconds=1)
    if eoy2024_as_of.tzinfo is None:
        eoy2024_as_of = eoy2024_as_of.replace(tzinfo=UTC)

    rating_state = snapshot_from_filter_history(hist, as_of)
    eoy_2024_state = snapshot_from_filter_history(hist, eoy2024_as_of)
    enter_2026 = off_epa_from_state(rating_state)
    eoy_2024 = off_epa_from_state(eoy_2024_state)
    print(
        f"W9-I eoy2024_as_of={eoy2024_as_of.isoformat()} "
        f"n_teams_enter2026={len(enter_2026)} n_teams_eoy2024={len(eoy_2024)}",
        flush=True,
    )
    common = sorted(set(enter_2026) & set(eoy_2024), key=lambda x: int(x) if str(x).lstrip("-").isdigit() else x)
    deltas = []
    for tid in common:
        d = float(enter_2026[tid]) - float(eoy_2024[tid])
        deltas.append((tid, float(eoy_2024[tid]), float(enter_2026[tid]), d))
    n_identical = sum(1 for _t, _a, _b, d in deltas if abs(d) < 1e-12)
    n_moved = sum(1 for _t, _a, _b, d in deltas if abs(d) >= 1e-12)
    print(f"W9-I rating_compare n_common={len(common)} n_identical={n_identical} n_moved={n_moved}")
    if n_moved == 0:
        raise RuntimeError("STOP #5 failure mode 2: 2026-entering ratings identical to end-of-2024")

    # Prefer recognizable FBS ids when present.
    prefer = ["61", "333", "194", "130", "99", "251", "8", "265"]
    sample = [row for row in deltas if row[0] in prefer]
    if len(sample) < 5:
        sample = deltas[:8]
    print("W9-I rating_sample=" + json.dumps(
        [
            {
                "team_id": tid,
                "off_epa_eoy2024": a,
                "off_epa_enter2026": b,
                "delta": d,
            }
            for tid, a, b, d in sample
        ]
    ))

    registry = ModelRegistry(ROOT / "data" / "registry", tracking_uri=None)
    champ = registry.resolve_champion()
    art_dir = Path(champ.artifact_dir)
    predictor = load_production_ensemble(art_dir / ENSEMBLE_FILENAME)
    print(
        f"W9-I champion version={champ.version} stage={champ.stage} "
        f"run_id={champ.run_id} artifact={art_dir.as_posix()}"
    )

    provider = ProductionFeatureProvider(config=wf, snapshots=None, cfbd_lines=None)
    poss_path = art_dir / "possessions_artifacts.pkl"
    if poss_path.is_file():
        with poss_path.open("rb") as fh:
            poss = pickle.load(fh)  # noqa: S301 — registry artifact we wrote
        if isinstance(poss, dict):
            provider._possessions_artifacts = poss  # noqa: SLF001 — live attach of last 2024 retrain
            print(f"W9-I possessions_artifact_keys={sorted(poss)[:12]}")

    features = provider.compute_game_features(
        live_games,
        as_of,
        rating_state=rating_state,
        market_features=False,
    )
    ep = pd.to_numeric(features["expected_possessions"], errors="coerce") if "expected_possessions" in features.columns else pd.Series(dtype=float)
    print(
        f"W9-I features n={len(features)} expected_possessions_finite={int(ep.notna().sum())} "
        f"expected_possessions_nan={int(ep.isna().sum())}"
    )
    print(f"W9-I feature_columns={list(features.columns)}")

    pred = predictor.predict(features)
    print(f"W9-I predict n={len(pred)} cols={list(pred.columns)}")
    mu = pd.to_numeric(pred["pred_margin"], errors="coerce")
    sig = pd.to_numeric(pred["sigma_m"], errors="coerce") if "sigma_m" in pred.columns else pd.Series(dtype=float)
    reasons = pred["null_reason"].astype(str) if "null_reason" in pred.columns else pd.Series(["None"] * len(pred))
    sigma_missing = pred["sigma_m_is_missing"].astype(bool) if "sigma_m_is_missing" in pred.columns else ~sig.notna()
    print(
        json.dumps(
            {
                "n": int(len(pred)),
                "mu_finite": int(mu.notna().sum()),
                "mu_null": int(mu.isna().sum()),
                "mu_min": float(mu.min()) if mu.notna().any() else None,
                "mu_max": float(mu.max()) if mu.notna().any() else None,
                "sigma_finite": int(sig.notna().sum()),
                "sigma_min": float(sig.min()) if sig.notna().any() else None,
                "sigma_max": float(sig.max()) if sig.notna().any() else None,
                "sigma_missing_true": int(sigma_missing.sum()),
                "null_reason_counts": {str(k): int(v) for k, v in reasons.value_counts(dropna=False).items()},
            }
        )
    )

    run_id = str(champ.run_id)
    model_version = "production-v0_reduced_v3"

    def _predict(_stale_ctx: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for rec in pred.to_dict(orient="records"):
            rec["season"] = LIVE_SEASON
            rec["week"] = LIVE_WEEK
            rec["run_id"] = run_id
            rec["model_version"] = model_version
            rows.append(_alias_stamp_columns(rec))
        return rows

    out_dir = ART / "dry_export"
    state_dir = ART / "dry_state"
    iso = run_isolated_week_export(
        season=LIVE_SEASON,
        week=LIVE_WEEK,
        output_dir=out_dir,
        state_dir=state_dir,
        published_at=as_of,
        predict_fn=_predict,
    )
    print(f"W9-I isolated_export_enabled={iso.get('export_enabled')}")
    print(f"W9-I isolated_push=False")

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
        basis = g.get("conviction_basis") or {}
        if g.get("sigma_margin_credible") is False:
            n_sigma_refused += 1
        if g.get("mu_margin") is not None:
            mus.append(float(g["mu_margin"]))
        if g.get("sigma_margin") is not None:
            sigs.append(float(g["sigma_margin"]))
        _ = basis
    summary = {
        "n_games_predicted": len(games_out),
        "mu_range": [min(mus), max(mus)] if mus else None,
        "sigma_range": [min(sigs), max(sigs)] if sigs else None,
        "conviction_tier_distribution": tiers,
        "n_null_tier_suppressed": n_null_tier,
        "n_sigma_refused": n_sigma_refused,
        "n_null_reason_rows": int(
            (~reasons.astype(str).isin(["None", "nan", "<NA>", ""])).sum()
        )
        if len(reasons)
        else 0,
        "as_of": as_of.isoformat(),
        "model_identity": week.get("model_identity"),
        "expected_possessions_nan": int(ep.isna().sum()),
        "rating_n_moved": n_moved,
        "rating_n_identical": n_identical,
    }
    print("W9-I dry_predict_summary=" + json.dumps(summary))
    (ART / "dry_predict_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (ART / "rating_compare.json").write_text(
        json.dumps(
            {
                "n_common": len(common),
                "n_identical": n_identical,
                "n_moved": n_moved,
                "sample": [
                    {"team_id": tid, "off_epa_eoy2024": a, "off_epa_enter2026": b, "delta": d}
                    for tid, a, b, d in sample
                ],
                "policy": "2025 observations used for Kalman state only",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    hashes_after = hash_isolation()
    print(f"W9-I isolation_after={json.dumps(hashes_after, sort_keys=True)}")
    changed = [k for k, v in hashes_before.items() if hashes_after.get(k) != v]
    print(f"W9-I isolation_changed={changed}")
    if changed:
        raise RuntimeError(f"isolation violated: {changed}")
    ended = datetime.now(tz=UTC)
    print(f"W9-I dry_predict end={ended.strftime('%Y-%m-%dT%H:%M:%SZ')} elapsed_sec={(ended-started).total_seconds():.3f}")


if __name__ == "__main__":
    main()
