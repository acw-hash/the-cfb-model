"""MEMBER-HEALTH-FIX Item 5 — verify published runs' member health from stored state.

Reads manifests (nnls_fold_reports) + predictions.parquet for:
  fundamental_v2, A3_v2, A6_v2, SLOT_CLOSE

Infers per-retrain health from stored artifacts (no re-fit):
  - NNLS weights per fold report
  - Whether any (season, week) under a retrain epoch has near-zero SD(mu)
  - Constant-2.5 signature (historical dead-ENet fallback)

STOP if any published run carried a dead/degenerate member with positive weight.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "docs" / "notes" / "_artifacts" / "member_health_fix"

PUBLISHED: dict[str, Path] = {
    "fundamental_v2": ROOT / "data/backtests/task23_fundamental_reduced_v2/full",
    "A3_v2": ROOT / "data/backtests/task23_a3_reduced_v2/A3_market_off",
    "A6_v2": ROOT / "data/backtests/task23_a6_reduced_v2/A6_cfbd_open_close",
    "SLOT_CLOSE": ROOT / "data/backtests/task23_market_aware_reduced_v2_slot_close/full",
}

SD_EPS = 0.01
CONST_2P5_EPS = 1e-6


@dataclass
class RetrainHealth:
    run: str
    fold_index: int
    n_oof_rows: int | None
    w_lgbm: float
    w_enet: float
    fallback: str | None
    n_pred_rows: int
    n_zero_sd_blocks: int
    n_const_2p5_blocks: int
    enet_looks_dead_weighted: bool
    lgbm_looks_degenerate_weighted: bool
    clean: bool
    notes: str


def _load_nnls(manifest_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    extra = payload.get("extra") or {}
    raw = extra.get("nnls_fold_reports") or "[]"
    if isinstance(raw, str):
        return list(json.loads(raw))
    return list(raw)


def _block_sd_table(preds: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if preds.empty or not {"season", "week", "pred_margin"} <= set(preds.columns):
        return pd.DataFrame(rows)
    frame = preds
    if "exclude_from_headline" in frame.columns:
        frame = frame.loc[~frame["exclude_from_headline"].fillna(False).astype(bool)]
    for (season, week), chunk in frame.groupby(["season", "week"], sort=True):
        mu = pd.to_numeric(chunk["pred_margin"], errors="coerce").dropna()
        if len(mu) < 2:
            continue
        sd = float(mu.std(ddof=0))
        near_2p5 = bool(np.all(np.isclose(mu.to_numpy(dtype=float), 2.5, atol=CONST_2P5_EPS)))
        rows.append(
            {
                "season": int(season),
                "week": int(week),
                "n": int(len(mu)),
                "sd": sd,
                "mean": float(mu.mean()),
                "const_2p5": near_2p5,
                "retrain_epoch": (
                    int(chunk["retrain_epoch"].iloc[0])
                    if "retrain_epoch" in chunk.columns
                    else None
                ),
            }
        )
    return pd.DataFrame(rows)


def audit_run(name: str, run_dir: Path) -> list[RetrainHealth]:
    manifest = run_dir / "manifest.json"
    preds_path = run_dir / "predictions.parquet"
    if not manifest.is_file() or not preds_path.is_file():
        return [
            RetrainHealth(
                run=name,
                fold_index=-1,
                n_oof_rows=None,
                w_lgbm=float("nan"),
                w_enet=float("nan"),
                fallback="missing_artifacts",
                n_pred_rows=0,
                n_zero_sd_blocks=0,
                n_const_2p5_blocks=0,
                enet_looks_dead_weighted=False,
                lgbm_looks_degenerate_weighted=False,
                clean=False,
                notes="STOP: missing manifest or predictions",
            )
        ]
    folds = _load_nnls(manifest)
    preds = pd.read_parquet(preds_path)
    blocks = _block_sd_table(preds)
    out: list[RetrainHealth] = []
    # Map retrain_epoch → blocks. Fold reports are ordered chronologically;
    # pair by index with unique retrain epochs present in predictions when possible.
    epochs = (
        sorted({int(x) for x in preds["retrain_epoch"].dropna().unique()})
        if "retrain_epoch" in preds.columns
        else []
    )
    for i, fold in enumerate(folds):
        weights = dict(fold.get("weights") or {})
        w_l = float(weights.get("lgbm_mu_margin", 0.0))
        w_e = float(weights.get("enet_mu_margin", 0.0))
        # Blocks belonging to this fold's epoch (best-effort alignment).
        if epochs and i < len(epochs) and not blocks.empty and "retrain_epoch" in blocks.columns:
            sub = blocks.loc[blocks["retrain_epoch"] == epochs[i]]
        else:
            sub = blocks
        n_zero = int((sub["sd"] < SD_EPS).sum()) if not sub.empty else 0
        n_2p5 = int(sub["const_2p5"].sum()) if not sub.empty else 0
        enet_dead = bool(w_e > 1e-12 and n_2p5 > 0)
        lgbm_deg = bool(w_l > 1e-12 and n_zero > 0 and n_2p5 == 0)
        # Global safety: any zero-SD block anywhere with positive weight on either
        # member is a STOP when that member dominated historically.
        if not sub.empty and n_zero == 0 and not blocks.empty:
            # Also flag global zero-SD if this fold's weights would cover those weeks
            # (conservative: any zero-SD on the run with this fold having enet=1).
            global_zero = int((blocks["sd"] < SD_EPS).sum())
            global_2p5 = int(blocks["const_2p5"].sum())
            if w_e > 0.99 and global_2p5 > 0:
                enet_dead = True
            if w_l > 0.99 and global_zero > 0 and global_2p5 == 0:
                lgbm_deg = True
        clean = not enet_dead and not lgbm_deg and n_zero == 0
        notes = "ok"
        if enet_dead:
            notes = "STOP: positive ENet weight with constant-2.5 blocks"
        elif lgbm_deg:
            notes = "STOP: positive LGBM weight with near-zero SD blocks"
        elif n_zero > 0:
            notes = "STOP: near-zero SD blocks under this epoch"
            clean = False
        out.append(
            RetrainHealth(
                run=name,
                fold_index=i,
                n_oof_rows=fold.get("n_oof_rows"),
                w_lgbm=w_l,
                w_enet=w_e,
                fallback=fold.get("fallback"),
                n_pred_rows=int(len(preds)),
                n_zero_sd_blocks=n_zero,
                n_const_2p5_blocks=n_2p5,
                enet_looks_dead_weighted=enet_dead,
                lgbm_looks_degenerate_weighted=lgbm_deg,
                clean=clean,
                notes=notes,
            )
        )
    if not out:
        # No fold reports — still scan prediction SD.
        n_zero = int((blocks["sd"] < SD_EPS).sum()) if not blocks.empty else 0
        n_2p5 = int(blocks["const_2p5"].sum()) if not blocks.empty else 0
        clean = n_zero == 0 and n_2p5 == 0
        out.append(
            RetrainHealth(
                run=name,
                fold_index=-1,
                n_oof_rows=None,
                w_lgbm=float("nan"),
                w_enet=float("nan"),
                fallback="no_nnls_fold_reports",
                n_pred_rows=int(len(preds)),
                n_zero_sd_blocks=n_zero,
                n_const_2p5_blocks=n_2p5,
                enet_looks_dead_weighted=False,
                lgbm_looks_degenerate_weighted=False,
                clean=clean,
                notes="ok" if clean else "STOP: zero-SD or const-2.5 without fold reports",
            )
        )
    return out


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"runs": {}, "any_stop": False}
    for name, path in PUBLISHED.items():
        health = audit_run(name, path)
        all_rows.extend(asdict(h) for h in health)
        run_clean = all(h.clean for h in health)
        summary["runs"][name] = {
            "path": str(path),
            "n_folds": len(health),
            "clean": run_clean,
            "stops": [h.notes for h in health if not h.clean],
        }
        if not run_clean:
            summary["any_stop"] = True
    frame = pd.DataFrame(all_rows)
    frame.to_csv(ART / "item5_member_health.csv", index=False)
    (ART / "item5_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if summary["any_stop"]:
        print("STOP: published run carried dead/degenerate weighted member")
        return 2
    print("Item 5: all published runs clean from stored state")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
