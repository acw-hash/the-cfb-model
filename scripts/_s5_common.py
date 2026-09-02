"""Shared helpers for S5 Phase 0 probes (read-only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.calibrate_social_threshold import (  # noqa: E402
    CACHE_PATH,
    SEASONS,
    _select_public,
)

# Re-export for probe scripts.
__all__ = [
    "BACKTEST_ROOT",
    "CACHE_PATH",
    "GRADED_314_PATH",
    "REPO",
    "S4_ART",
    "S5_ART",
    "SEASONS",
    "THRESHOLD",
]
from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON, assert_lockbox_excluded  # noqa: E402

S4_ART = REPO / "docs" / "notes" / "_artifacts" / "social-s4"
S5_ART = REPO / "docs" / "notes" / "_artifacts" / "social-s5"
GRADED_314_PATH = S4_ART / "p0_graded_314.parquet"
THRESHOLD = 0.05
BACKTEST_ROOT = REPO / "data" / "backtests" / "task23_fundamental_reduced_v3" / "full" / "weeks"
RNG_SEED = 42


def ensure_out() -> Path:
    S5_ART.mkdir(parents=True, exist_ok=True)
    return S5_ART


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def load_graded_314() -> pd.DataFrame:
    assert_lockbox_excluded(list(SEASONS), context="S5 Phase 0")
    if LOCKBOX_SEASON in SEASONS:
        raise AssertionError("lockbox season in SEASONS")
    if not GRADED_314_PATH.is_file():
        raise FileNotFoundError(f"missing S4 artifact: {GRADED_314_PATH}")
    g = pd.read_parquet(GRADED_314_PATH)
    if len(g) != 314:
        raise AssertionError(f"S4 graded rows n={len(g)} != 314")
    return g


def load_public_314_from_cache() -> tuple[pd.DataFrame, list[Any]]:
    """Re-derive the thr=0.05 weeks-2+ set from the S3 cache (must match S4)."""
    cache = pd.read_parquet(CACHE_PATH)
    bets = [b for b in _select_public(cache, THRESHOLD) if int(b.week) >= 2]
    if len(bets) != 314:
        raise AssertionError(f"rederived public set n={len(bets)} != 314")
    return cache, bets


def assert_rows_match_s4(graded: pd.DataFrame, bets: list[Any]) -> dict[str, Any]:
    """Row-for-row identity vs S4 graded artifact (season, week, game_id, bet_on)."""
    keys_s4 = {
        (int(r.season), int(r.week), str(r.game_id), str(r.bet_on))
        for r in graded.itertuples(index=False)
    }
    keys_b = {(int(b.season), int(b.week), str(b.game_id), str(b.bet_on)) for b in bets}
    missing = sorted(keys_s4 - keys_b)
    extra = sorted(keys_b - keys_s4)
    if missing or extra:
        raise AssertionError(
            f"row mismatch vs S4: missing={len(missing)} extra={len(extra)} "
            f"sample_missing={missing[:3]} sample_extra={extra[:3]}"
        )
    # Covered / p_win agreement on merge
    bdf = pd.DataFrame(
        [
            {
                "season": int(b.season),
                "week": int(b.week),
                "game_id": str(b.game_id),
                "bet_on": str(b.bet_on),
                "p_win": float(b.p_win),
                "covered": b.covered,
                "u_pnl": float(b.u_pnl),
                "line_clv": b.line_clv,
            }
            for b in bets
        ]
    )
    m = graded.merge(bdf, on=["season", "week", "game_id", "bet_on"], suffixes=("_s4", "_b"))
    if len(m) != 314:
        raise AssertionError(f"merge n={len(m)} != 314")
    p_err = float(np.nanmax(np.abs(m["p_win_s4"].to_numpy() - m["p_win_b"].to_numpy())))
    cov_mismatch = int((m["covered_s4"] != m["covered_b"]).sum())
    return {
        "n": 314,
        "keys_equal": True,
        "max_abs_p_win_delta": p_err,
        "covered_mismatch": cov_mismatch,
    }


def s3_line_clv(market_line: float, spread_close_home: float, bet_on: str) -> float:
    """S3 expression from calibrate_social_threshold._grade_row (verbatim logic)."""
    close_side = float(spread_close_home) if bet_on == "home" else -float(spread_close_home)
    return float(market_line) - float(close_side)


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return float("nan"), float("nan")
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z / denom) * float(np.sqrt(p * (1 - p) / n + z2 / (4 * n * n)))
    return centre - half, centre + half


def bootstrap_ci(
    values: np.ndarray,
    stat_fn: Any,
    *,
    n_boot: int = 2000,
    seed: int = RNG_SEED,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    values = np.asarray(values, dtype=float)
    point = float(stat_fn(values))
    if values.size == 0:
        return point, float("nan"), float("nan")
    boots = np.empty(n_boot, dtype=float)
    n = values.size
    for i in range(n_boot):
        sample = values[rng.integers(0, n, size=n)]
        boots[i] = float(stat_fn(sample))
    lo = float(np.quantile(boots, alpha / 2))
    hi = float(np.quantile(boots, 1 - alpha / 2))
    return point, lo, hi


def brier_score(p: np.ndarray, y: np.ndarray) -> float:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    return float(np.mean((p - y) ** 2))


def brier_skill_score(p: np.ndarray, y: np.ndarray) -> dict[str, float]:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    base = float(np.mean(y))
    bs_model = brier_score(p, y)
    bs_base = brier_score(np.full_like(y, base), y)
    bss = float("nan") if bs_base <= 0 else 1.0 - bs_model / bs_base
    return {
        "brier_model": bs_model,
        "brier_baseline": bs_base,
        "baseline_rate": base,
        "bss": bss,
    }
