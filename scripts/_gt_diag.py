"""GT-DIAG: read-only diagnosis of why A5 sees n_on == n_off on staged plays.

Sanctioned for TASK GT-DIAG. Writes nothing to the lake; prints JSON evidence
for docs/notes/gt-diag.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ncaa_quant.config import DataConfig
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.evaluation.production_stack import build_observations_from_staged
from ncaa_quant.features.epa import (
    CONNELLY_MARGIN_BY_PERIOD,
    WP_GT_HIGH,
    WP_GT_LOW,
    apply_garbage_time,
    filter_garbage_time,
    load_season_plays_from_cfbd_raw,
)

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "data" / "staged"
RAW = ROOT / "data" / "raw" / "cfbd"
SEASONS = list(range(2014, 2026))


def _load_season_plays(store: ParquetStore, season: int) -> pd.DataFrame:
    paths = list(store._matching_paths("plays", {"season": int(season)}))  # noqa: SLF001
    if not paths:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


def _load_season_games(store: ParquetStore, season: int) -> pd.DataFrame:
    paths = list(store._matching_paths("games", {"season": int(season)}))  # noqa: SLF001
    if not paths:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


def column_inventory(plays: pd.DataFrame) -> dict[str, Any]:
    cols = sorted(plays.columns.astype(str))
    gt_inputs = {
        "garbage_time": "garbage_time" in plays.columns,
        "wp": "wp" in plays.columns,
        "wp_before": "wp_before" in plays.columns,
        "wp_after": "wp_after" in plays.columns,
        "score_margin": "score_margin" in plays.columns,
        "offense_score": "offense_score" in plays.columns,
        "defense_score": "defense_score" in plays.columns,
        "clock": "clock" in plays.columns,
        "period": "period" in plays.columns,
    }
    null_rates: dict[str, float | None] = {}
    for c in ("wp", "wp_before", "epa", "period"):
        if c in plays.columns:
            null_rates[c] = float(plays[c].isna().mean())
        else:
            null_rates[c] = None
    gt_stats: dict[str, Any] = {"present": False}
    if "garbage_time" in plays.columns:
        s = plays["garbage_time"]
        gt_stats = {
            "present": True,
            "null_frac": float(s.isna().mean()),
            "true_frac": float(s.fillna(False).astype(bool).mean()),
            "n_true": int(s.fillna(False).astype(bool).sum()),
        }
    return {
        "n_plays": int(len(plays)),
        "columns": cols,
        "gt_input_presence": gt_inputs,
        "null_rates": null_rates,
        "staged_garbage_time": gt_stats,
    }


def a5_counts(plays: pd.DataFrame, games: pd.DataFrame) -> dict[str, Any]:
    """Mirror build_observations_from_staged counting + filter effect."""
    _, n_on, n_off = build_observations_from_staged(
        plays=plays,
        games=games,
        garbage_time_filter=True,
    )
    # Explicit apply path (same short-circuit as production_stack)
    flagged = apply_garbage_time(plays) if "garbage_time" not in plays.columns else plays
    n_true = (
        int(flagged["garbage_time"].astype(bool).sum())
        if "garbage_time" in flagged.columns
        else 0
    )
    # What if we aliased staged wp -> wp_before?
    aliased = plays.copy()
    if "wp" in aliased.columns and "wp_before" not in aliased.columns:
        aliased["wp_before"] = aliased["wp"]
    flagged_alias = apply_garbage_time(aliased)
    n_true_alias = int(flagged_alias["garbage_time"].astype(bool).sum())

    filtered = filter_garbage_time(plays)
    return {
        "n_on": n_on,
        "n_off": n_off,
        "n_on_lt_n_off": n_on < n_off,
        "n_garbage_true_after_apply": n_true,
        "n_garbage_true_if_wp_aliased_to_wp_before": n_true_alias,
        "n_after_filter_garbage_time": int(len(filtered)),
        "apply_looks_for_wp_col_default": "wp_before",
        "staged_has_wp_not_wp_before": ("wp" in plays.columns)
        and ("wp_before" not in plays.columns),
    }


def handpick_blowout_q4(
    plays: pd.DataFrame,
    games: pd.DataFrame,
    *,
    season: int,
    n: int = 10,
) -> list[dict[str, Any]]:
    """10 Q4 plays from blowout games (final |margin| > 28) — obvious GT candidates."""
    g = games.copy()
    if "home_points" not in g.columns or "away_points" not in g.columns:
        return []
    g = g.dropna(subset=["home_points", "away_points", "game_id"])
    g["final_margin"] = (g["home_points"].astype(float) - g["away_points"].astype(float)).abs()
    blowouts = g.loc[g["final_margin"] > 28].sort_values("final_margin", ascending=False)
    rows: list[dict[str, Any]] = []
    for _, game in blowouts.iterrows():
        gid = int(game["game_id"])
        gp = plays.loc[(plays["game_id"] == gid) & (plays["period"] == 4)]
        if gp.empty:
            continue
        # last play of Q4 as the "game-end" proxy
        play = gp.sort_values("play_id").iloc[-1]
        work = play.to_frame().T
        flagged = apply_garbage_time(work)
        alias = work.copy()
        if "wp" in alias.columns:
            alias["wp_before"] = alias["wp"]
        flagged_alias = apply_garbage_time(alias)
        rows.append(
            {
                "season": season,
                "game_id": gid,
                "final_margin": float(game["final_margin"]),
                "period": int(play["period"]),
                "play_id": int(play["play_id"]),
                "wp": None if pd.isna(play.get("wp")) else float(play["wp"]),
                "has_score_margin_col": "score_margin" in play.index
                and pd.notna(play.get("score_margin")),
                "garbage_time_decision": bool(flagged["garbage_time"].iloc[0]),
                "garbage_time_if_wp_aliased": bool(flagged_alias["garbage_time"].iloc[0]),
                "gt_rule": str(flagged["gt_rule"].iloc[0]),
                "gt_fallback_used": bool(flagged["gt_fallback_used"].iloc[0]),
            }
        )
        if len(rows) >= n:
            break
    return rows


def raw_connelly_sanity(season: int = 2023) -> dict[str, Any]:
    """Show Connelly DOES fire when scores come from raw archives (Task 8 path)."""
    try:
        raw_plays = load_season_plays_from_cfbd_raw(RAW, season, validate=False)
    except FileNotFoundError as exc:
        return {"available": False, "error": str(exc)}
    n = len(raw_plays)
    n_gt = int(raw_plays["garbage_time"].astype(bool).sum()) if n else 0
    n_fb = int(raw_plays["gt_fallback_used"].astype(bool).sum()) if n else 0
    wp_nn = int(raw_plays["wp_before"].notna().sum()) if n else 0
    return {
        "available": True,
        "season": season,
        "n_plays": n,
        "n_garbage": n_gt,
        "garbage_frac": (n_gt / n) if n else None,
        "n_fallback": n_fb,
        "wp_before_nonnull": wp_nn,
        "note": "normalize_epa_plays from raw JSON (scores+clock present; WP still null)",
    }


def thresholds() -> dict[str, Any]:
    cfg = DataConfig()
    return {
        "design_section": "DESIGN §4.2: exclude wp > 0.98 or < 0.02; Connelly fallback",
        "code_WP_GT_LOW": WP_GT_LOW,
        "code_WP_GT_HIGH": WP_GT_HIGH,
        "config_garbage_wp_low": cfg.garbage_wp_low,
        "config_garbage_wp_high": cfg.garbage_wp_high,
        "config_wired_into_apply_garbage_time": False,
        "note": (
            "apply_garbage_time hardcodes WP_GT_LOW/HIGH module constants; "
            "DataConfig.garbage_wp_* is unused by the filter today"
        ),
        "connelly_margin_by_period": dict(CONNELLY_MARGIN_BY_PERIOD),
    }


def call_path() -> dict[str, str]:
    return {
        "a5_measure": (
            "cli backtest run → build_observations_from_staged(plays=staged) → "
            "apply_garbage_time(plays) if 'garbage_time' not in columns → "
            "n_on = (~garbage_time).sum(), n_off = len(plays)"
        ),
        "filter_act": (
            "build_observations_from_staged → build_game_observations_from_plays"
            "(..., drop_garbage=...) → filter_garbage_time(plays) → "
            "apply_garbage_time if column absent → drop garbage_time==True"
        ),
        "same_stage": (
            "Both measure and filter operate on the same staged plays DataFrame "
            "loaded from data/staged/plays/. Not a measurement-stage mismatch."
        ),
    }


def main() -> None:
    store = ParquetStore(STAGED)
    per_season: dict[str, Any] = {}
    blowouts_2023: list[dict[str, Any]] = []
    for season in SEASONS:
        plays = _load_season_plays(store, season)
        games = _load_season_games(store, season)
        inv = column_inventory(plays)
        counts = a5_counts(plays, games) if not plays.empty else {}
        per_season[str(season)] = {"inventory": inv, "a5": counts}
        if season == 2023 and not plays.empty and not games.empty:
            blowouts_2023 = handpick_blowout_q4(plays, games, season=season, n=10)

    # Raw archive coverage for seasons (zero-API replay feasibility)
    raw_coverage: dict[str, int] = {}
    for season in SEASONS:
        raw_coverage[str(season)] = len(list(RAW.glob(f"**/plays_s{season}_*.json")))

    report = {
        "verdict_hint": "WORLD_A",
        "thresholds": thresholds(),
        "call_path": call_path(),
        "per_season": per_season,
        "blowout_q4_handpicks_2023": blowouts_2023,
        "raw_connelly_sanity_2023": raw_connelly_sanity(2023),
        "raw_plays_archive_counts": raw_coverage,
        "intended_gt_output_landing": {
            "task": "Task 8 (features/epa.py), not Task 9/13",
            "persisted_to_staged": False,
            "runtime_path": (
                "normalize_epa_plays / apply_garbage_time produce in-memory "
                "garbage_time; staged PlaysSchema never includes the flag or "
                "offenseScore/defenseScore/clock"
            ),
            "task8_acceptance": "ran load_season_plays_from_cfbd_raw on 2023 raw only",
            "task10_note": "efficiency builders used CFBD raw plays for 2023 materialization",
        },
    }
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
