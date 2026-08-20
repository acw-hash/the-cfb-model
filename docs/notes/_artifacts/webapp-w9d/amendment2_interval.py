"""W9-D Amendment 2: restamp sandbox intervals via the export coherence gate.

Reconstructs pre-CQR q10/q90 from published lo/hi + champion CQR 80% add.
Does not call live predict. Production latest/ is not touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ncaa_quant.webapp.export import (  # noqa: E402
    apply_margin_interval_coherence_gate,
    margin_quantile_heads_coherent,
)

CQR_THR_80 = 6.8371215750064245
V3_PARQUET = (
    ROOT / "data" / "backtests" / "task23_fundamental_reduced_v3" / "full" / "predictions.parquet"
)
SANDBOX_PATHS = (
    ART / "sandbox_roundtrip" / "week_predictions.json",
    ART / "sandbox_export" / "week_predictions.json",
)


def _restamp(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    games = payload["games"]
    n_null_before = 0
    n_suppressed = 0
    n_kept = 0
    n_skewed_kept = 0
    positions: list[float] = []
    for game in games:
        lo = game.get("margin_interval_lo")
        hi = game.get("margin_interval_hi")
        nom = game.get("margin_interval_nominal")
        mu = game.get("mu_margin")
        if lo is None or hi is None:
            n_null_before += 1
            continue
        q10 = float(lo) + CQR_THR_80
        q90 = float(hi) - CQR_THR_80
        if q10 > q90:
            q10, q90 = q90, q10
        new_lo, new_hi, new_nom = apply_margin_interval_coherence_gate(
            mu=float(mu),
            q10=q10,
            q90=q90,
            lo=float(lo),
            hi=float(hi),
            nominal=None if nom is None else float(nom),
        )
        if new_lo is None or new_hi is None:
            n_suppressed += 1
            game["margin_interval_lo"] = None
            game["margin_interval_hi"] = None
            game["margin_interval_nominal"] = None
            continue
        n_kept += 1
        width = float(new_hi) - float(new_lo)
        pos = (float(mu) - float(new_lo)) / width if width > 0 else float("nan")
        positions.append(pos)
        if pos < 0.25 or pos > 0.75:
            n_skewed_kept += 1
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    pos_arr = np.asarray(positions, dtype=float) if positions else np.asarray([], dtype=float)
    return {
        "path": str(path),
        "n": len(games),
        "n_null_before": n_null_before,
        "n_suppressed": n_suppressed,
        "n_kept": n_kept,
        "n_skewed_kept_outside_[0.25,0.75]": n_skewed_kept,
        "kept_pos_median": None if pos_arr.size == 0 else float(np.median(pos_arr)),
    }


def _backtest() -> dict[str, Any]:
    if not V3_PARQUET.is_file():
        return {"present": False, "path": str(V3_PARQUET)}
    frame = pd.read_parquet(V3_PARQUET)
    mu = pd.to_numeric(frame["pred_margin"], errors="coerce")
    q10 = pd.to_numeric(frame["pred_margin_q10"], errors="coerce")
    q90 = pd.to_numeric(frame["pred_margin_q90"], errors="coerce")
    realized = pd.to_numeric(frame["realized_margin"], errors="coerce")
    eligible = mu.notna() & q10.notna() & q90.notna() & realized.notna()
    sub = frame.loc[eligible]
    lo = sub[["pred_margin_q10", "pred_margin_q90"]].min(axis=1)
    hi = sub[["pred_margin_q10", "pred_margin_q90"]].max(axis=1)
    incoherent = 0
    for m, a, b in zip(sub["pred_margin"], lo, hi, strict=True):
        if not margin_quantile_heads_coherent(float(m), float(a), float(b)):
            incoherent += 1
    return {
        "present": True,
        "path": str(V3_PARQUET),
        "n_eligible": int(len(sub)),
        "n_incoherent": incoherent,
    }


def main() -> dict[str, Any]:
    restamps = [_restamp(path) for path in SANDBOX_PATHS if path.is_file()]
    return {
        "cqr_thr_80": CQR_THR_80,
        "sandbox": restamps,
        "v3_backtest": _backtest(),
    }


if __name__ == "__main__":
    report = main()
    out = ART / "amendment2_interval.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"wrote {out}")
