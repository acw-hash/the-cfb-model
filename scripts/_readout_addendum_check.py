"""TASK 23-READOUT-ADDENDUM — 2019 market-feature equivalence check.

Reconstruct ``mkt_*`` features for every 2019 prediction row in the
Tuesday-decision market-aware run (``FEATURE_TIME=TUESDAY_DECISION``) using
the same production feature path (``market_feature_source=snapshots``).

Pass: every 2019 ``mkt_spread`` / ``mkt_total`` is null and ``mkt_is_missing``
is set. That licenses treating 45.63% [42.9%, 48.6%] vs fundamental 51.3%
as fit-path variance from feature-column presence (NaN-aware splits), n=743
— noise, not a finding.

Fail / STOP: any non-null 2019 ``mkt_*`` is a 2019 feature-source violation
(CFBD or snapshot values on a snapshots-source stack where 2019 has no Odds
history). Do not write the noise claim.

Does not tune. Does not touch the lockbox.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.cli import load_staged_odds_snapshots
from ncaa_quant.evaluation.backtest_runner import (
    load_backtest_config,
    load_staged_games,
    walkforward_config_from_mapping,
)
from ncaa_quant.evaluation.d6_eval import load_cfbd_lines
from ncaa_quant.evaluation.production_stack import (
    MARKET_FEATURE_COLS,
    ProductionFeatureProvider,
)
from ncaa_quant.evaluation.walkforward import resolve_lines_for_games
from ncaa_quant.utils.timeutils import to_utc

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "data" / "staged"
PRED_PATH = (
    ROOT
    / "data"
    / "backtests"
    / "task23_market_aware_reduced_v2_tue"
    / "full"
    / "predictions.parquet"
)
CFG_NAME = "task23_market_aware_full_reduced_v2_tue"
ART = ROOT / "docs" / "notes" / "_artifacts" / "readout_addendum"
MISSING_COL = "mkt_is_missing"
VINTAGE = "RERUN_V2_WEEK_ALIGN"
SCOPE = "REDUCED_PER_ADR_0013"
FEATURE_TIME = "TUESDAY_DECISION"
N_ATS_2019 = 743


def _is_null_value(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str) and val.strip().lower() in {"", "null", "nan", "none"}:
        return True
    try:
        return not np.isfinite(float(val))
    except (TypeError, ValueError):
        return False


def _is_missing_flag(val: Any) -> bool:
    if val is True or val == 1 or val == 1.0:
        return True
    if isinstance(val, str) and val.strip().lower() in {"1", "true", "yes"}:
        return True
    try:
        return bool(int(val)) and float(val) == 1.0
    except (TypeError, ValueError):
        return False


def _load_provider() -> tuple[pd.DataFrame, ProductionFeatureProvider, pd.DataFrame | None, int]:
    payload = load_backtest_config(CFG_NAME)
    cfg = walkforward_config_from_mapping(payload)
    games = load_staged_games(STAGED, [2019])
    if games.empty:
        msg = "no staged 2019 games"
        raise FileNotFoundError(msg)
    snapshots = load_staged_odds_snapshots(STAGED, (2019,))
    n_snap_rows = 0 if snapshots is None or snapshots.empty else int(len(snapshots))
    cfbd_lines = load_cfbd_lines(STAGED, seasons=[2019])
    provider = ProductionFeatureProvider(
        config=cfg,
        snapshots=snapshots,
        cfbd_lines=cfbd_lines if cfbd_lines is not None and not cfbd_lines.empty else None,
    )
    return games, provider, snapshots, n_snap_rows


def check_2019_mkt_equivalence(preds: pd.DataFrame | None = None) -> dict[str, Any]:
    """Return a JSON-serializable report. ``ok`` is True only on full null+missing."""
    ART.mkdir(parents=True, exist_ok=True)
    if preds is None:
        if not PRED_PATH.is_file():
            raise FileNotFoundError(f"Tuesday-decision predictions missing: {PRED_PATH}")
        preds = pd.read_parquet(PRED_PATH)

    pred_mkt_cols = [c for c in preds.columns if str(c).startswith("mkt_") or str(c).startswith("feat__mkt_")]
    y2019 = preds.loc[preds["season"].astype(int) == 2019].copy()
    if y2019.empty:
        raise AssertionError("Tuesday-decision predictions contain no 2019 rows")

    games, provider, snapshots, n_snap_rows = _load_provider()
    games = games.loc[games["game_id"].isin(y2019["game_id"].astype(int))].copy()
    games["game_id"] = games["game_id"].astype(int)
    y2019["game_id"] = y2019["game_id"].astype(int)

    resolved_parts: list[pd.DataFrame] = []
    ladder_parts: list[pd.DataFrame] = []
    for (week, as_of_raw), chunk in y2019.groupby(["week", "as_of"], sort=True):
        week_games = games.loc[games["game_id"].isin(chunk["game_id"])]
        if week_games.empty:
            raise AssertionError(f"no staged games for 2019 week={week} n_pred={len(chunk)}")
        as_of = to_utc(pd.Timestamp(as_of_raw).to_pydatetime())
        mkt = provider._resolve_market_lines(week_games, as_of)
        mkt["week"] = int(week)
        resolved_parts.append(mkt)
        ladder = resolve_lines_for_games(
            week_games,
            as_of,
            snapshots=snapshots,
            cfbd_lines=provider.cfbd_lines,
            config=provider.config,
            closing=False,
            for_features=True,
        )
        ladder_parts.append(ladder[["game_id", "line_source"]])

    resolved = pd.concat(resolved_parts, ignore_index=True)
    ladder_src = pd.concat(ladder_parts, ignore_index=True)
    merged = y2019[["game_id", "week", "as_of", "pred_margin"]].merge(
        resolved,
        on="game_id",
        how="left",
        suffixes=("", "_mkt"),
    ).merge(ladder_src, on="game_id", how="left")
    if len(merged) != len(y2019):
        raise AssertionError(f"resolve join changed row count: pred={len(y2019)} merged={len(merged)}")

    violations: list[dict[str, Any]] = []
    n_spread_null = 0
    n_total_null = 0
    n_missing_set = 0
    n_nbooks_zero = 0
    provenance_counts: dict[str, int] = {}
    line_source_counts: dict[str, int] = {}

    for rec in merged.to_dict(orient="records"):
        gid = int(rec["game_id"])
        week = int(rec["week"])
        spread_null = _is_null_value(rec.get("mkt_spread"))
        total_null = _is_null_value(rec.get("mkt_total"))
        missing_set = _is_missing_flag(rec.get(MISSING_COL))
        nbooks = rec.get("mkt_n_books")
        try:
            nbooks_zero = int(nbooks) == 0 if pd.notna(nbooks) else True
        except (TypeError, ValueError):
            nbooks_zero = _is_null_value(nbooks)
        prov = str(rec.get("market_provenance", "") or "")
        src = str(rec.get("line_source", "") or "")
        provenance_counts[prov] = provenance_counts.get(prov, 0) + 1
        line_source_counts[src] = line_source_counts.get(src, 0) + 1
        if spread_null:
            n_spread_null += 1
        if total_null:
            n_total_null += 1
        if missing_set:
            n_missing_set += 1
        if nbooks_zero:
            n_nbooks_zero += 1
        bad: list[str] = []
        if not spread_null:
            bad.append("mkt_spread")
        if not total_null:
            bad.append("mkt_total")
        if not missing_set:
            bad.append(MISSING_COL)
        if bad:
            violations.append(
                {
                    "game_id": gid,
                    "week": week,
                    "bad_columns": bad,
                    "mkt_spread": rec.get("mkt_spread"),
                    "mkt_total": rec.get("mkt_total"),
                    "mkt_n_books": rec.get("mkt_n_books"),
                    "mkt_is_missing": rec.get(MISSING_COL),
                    "market_provenance": prov,
                    "line_source": src,
                }
            )

    n = int(len(y2019))
    ok = len(violations) == 0 and n_spread_null == n and n_total_null == n and n_missing_set == n
    disposition = (
        "EQUIVALENT_NULL_IS_MISSING"
        if ok
        else "STOP_2019_FEATURE_SOURCE_VIOLATION"
    )
    report: dict[str, Any] = {
        "vintage": VINTAGE,
        "ensemble_scope": SCOPE,
        "feature_time": FEATURE_TIME,
        "run_id": "task23_market_aware_reduced_v2_tue",
        "predictions_path": str(PRED_PATH.relative_to(ROOT)).replace("\\", "/"),
        "n_2019_prediction_rows": n,
        "n_ats_2019_readout": N_ATS_2019,
        "n_2019_odds_snapshot_rows": n_snap_rows,
        "pred_mkt_columns_on_parquet": pred_mkt_cols,
        "market_feature_cols_checked": list(MARKET_FEATURE_COLS),
        "n_mkt_spread_null": n_spread_null,
        "n_mkt_total_null": n_total_null,
        "n_mkt_is_missing": n_missing_set,
        "n_mkt_n_books_zero_or_null": n_nbooks_zero,
        "provenance_counts": provenance_counts,
        "line_source_counts": line_source_counts,
        "mechanism_if_violation": (
            "resolve_lines_for_games(..., for_features=True) with "
            "market_feature_source=snapshots must never admit CFBD open/close; "
            "market_provenance must be stamped from line_source at resolution, "
            "never inferred from non-nullness or config."
        ),
        "n_violations": len(violations),
        "violation_sample": violations[:20],
        "ok": ok,
        "disposition": disposition,
        "noise_claim_licensed": ok,
        "divergence_if_ok": {
            "market_aware_2019_ats": "45.63%",
            "market_aware_2019_ci": "[42.9%, 48.6%]",
            "fundamental_regraded_v2_2019_ats": "51.3%",
            "n": N_ATS_2019,
            "mechanism": "fit-path variance from feature-column presence (NaN-aware splits)",
            "label": "noise — not a finding",
        },
    }
    out = ART / "2019_mkt_equivalence.json"
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def main() -> int:
    report = check_2019_mkt_equivalence()
    print(json.dumps({k: report[k] for k in (
        "disposition",
        "ok",
        "n_2019_prediction_rows",
        "n_mkt_spread_null",
        "n_mkt_total_null",
        "n_mkt_is_missing",
        "n_violations",
        "n_2019_odds_snapshot_rows",
        "provenance_counts",
        "line_source_counts",
        "noise_claim_licensed",
    )}, indent=2))
    if not report["ok"]:
        print(
            "STOP: 2019 feature-source violation — non-null mkt_* on "
            "Tuesday-decision market-aware prediction rows. "
            "Do not record 45.63% vs 51.3% as NaN-aware-split noise.",
            file=sys.stderr,
        )
        return 2
    print(
        "CHECK PASS: 2019 mkt_* null + is_missing on every prediction row. "
        "45.63% [42.9%, 48.6%] vs fundamental 51.3% is fit-path variance "
        "from feature-column presence (NaN-aware splits), n=743 — noise, not a finding."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
