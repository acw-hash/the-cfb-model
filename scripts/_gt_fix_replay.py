"""GT-FIX replay: enrich staged plays from raw CFBD archives (zero API).

Steps 2–4 of TASK GT-FIX. Does not call CFBD/Odds. Run:
``uv run python scripts/_gt_fix_replay.py``.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.evaluation.production_stack import build_observations_from_staged
from ncaa_quant.features.builders.efficiency import build_play_game_observations
from ncaa_quant.features.builders.tempo import (
    build_expected_possessions_training_frame,
    build_tempo_observations,
    expected_possessions_oos_mae,
    save_expected_possessions_artifact,
)
from ncaa_quant.features.epa import apply_garbage_time, classify_play_type
from ncaa_quant.ingestion.cfbd import (
    _clock_seconds,
    _play_score,
    normalize_plays_payload,
)
from ncaa_quant.ingestion.teams import load_team_name_map
from ncaa_quant.ratings.diagnostics import filter_health_stats
from ncaa_quant.ratings.state_space import (
    StateSpaceConfig,
    build_game_observations_from_plays,
    run_filter,
)

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "data" / "staged"
RAW = ROOT / "data" / "raw" / "cfbd"
OUT = ROOT / "data" / "tmp" / "gt_fix"
SS_OLD = ROOT / "data" / "tmp" / "state_space_acceptance_14"
SEASONS = list(range(2014, 2026))

# gt-diag blowout fixtures (2023 Q4 last play of each game).
BLOWOUT_GAME_IDS: tuple[int, ...] = (
    401523992,
    401525822,
    401531438,
    401532398,
    401520168,
    401525466,
    401520322,
    401520199,
    401520339,
    401551773,
)

NEW_COLS = ("offense_score", "defense_score", "clock", "score_margin")


def _latest_raw_plays(season: int, week: int) -> list[Path]:
    """Latest archive per season_type for one week (handles multi-date dumps)."""
    by_stype: dict[str, list[Path]] = defaultdict(list)
    for path in RAW.glob(f"**/plays_s{season}_w{week}_*.json"):
        # plays_s2023_w1_regular_TIMESTAMP.json
        name = path.name
        rest = name[len(f"plays_s{season}_w{week}_") :]
        stype = rest.split("_", 1)[0]
        by_stype[stype].append(path)
    chosen: list[Path] = []
    for stype, paths in sorted(by_stype.items()):
        chosen.append(sorted(paths, key=lambda p: p.name)[-1])
    return chosen


def _score_fields_from_raw(paths: list[Path]) -> dict[int, dict[str, Any]]:
    """play_id → score/clock/wp extracted from raw JSON (no team resolution)."""
    out: dict[int, dict[str, Any]] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            pid = item.get("id") if item.get("id") is not None else item.get("play_id")
            try:
                play_id = int(pid)
            except (TypeError, ValueError):
                continue
            offense_score = _play_score(
                item.get("offenseScore")
                if item.get("offenseScore") is not None
                else item.get("offense_score")
            )
            defense_score = _play_score(
                item.get("defenseScore")
                if item.get("defenseScore") is not None
                else item.get("defense_score")
            )
            score_margin = (
                offense_score - defense_score
                if offense_score is not None and defense_score is not None
                else None
            )
            wp = item.get("homeWinProb") if item.get("homeWinProb") is not None else item.get("wp")
            try:
                wp_f = float(wp) if wp is not None else None
            except (TypeError, ValueError):
                wp_f = None
            out[play_id] = {
                "offense_score": offense_score,
                "defense_score": defense_score,
                "clock": _clock_seconds(item.get("clock")),
                "score_margin": score_margin,
                "wp": wp_f,
            }
    return out


def _load_season_table(store: ParquetStore, table: str, season: int) -> pd.DataFrame:
    paths = list(store._matching_paths(table, {"season": int(season)}))  # noqa: SLF001
    if not paths:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


def _partition_weeks(store: ParquetStore, season: int) -> list[int]:
    root = store.root / "plays" / f"season={season}"
    if not root.exists():
        return []
    weeks: list[int] = []
    for child in root.iterdir():
        if child.is_dir() and child.name.startswith("week="):
            weeks.append(int(child.name.split("=", 1)[1]))
    return sorted(weeks)


def enrich_plays_partition(
    store: ParquetStore,
    *,
    season: int,
    week: int,
) -> dict[str, Any]:
    part = {"season": season, "week": week}
    path = store._partition_path("plays", part)  # noqa: SLF001
    if not path.exists():
        return {"season": season, "week": week, "skipped": True}
    staged = pd.read_parquet(path)
    n_before = len(staged)
    raw_paths = _latest_raw_plays(season, week)
    if not raw_paths:
        for col in NEW_COLS:
            if col not in staged.columns:
                staged[col] = pd.Series([pd.NA] * len(staged), dtype="Int64")
        store.write_partition("plays", staged, part, mode="overwrite", validate=True)
        return {
            "season": season,
            "week": week,
            "n_before": n_before,
            "n_after": len(staged),
            "delta": 0,
            "raw_paths": [],
            "null_offense_score": 1.0,
            "reason": "no_raw_archives",
        }

    lookup = _score_fields_from_raw(raw_paths)
    for col in NEW_COLS:
        staged[col] = staged["play_id"].map(
            lambda pid, c=col: lookup.get(int(pid), {}).get(c) if int(pid) in lookup else None
        )
    # Refresh wp from raw when present; leave null otherwise (never zero-fill).
    if "wp" in staged.columns:
        staged["wp"] = [
            lookup[int(pid)]["wp"] if int(pid) in lookup else None for pid in staged["play_id"]
        ]

    n_after = len(staged)
    if n_after != n_before:
        msg = f"row count drift season={season} week={week}: {n_before} → {n_after}"
        raise RuntimeError(msg)

    store.write_partition("plays", staged, part, mode="overwrite", validate=True)
    return {
        "season": season,
        "week": week,
        "n_before": n_before,
        "n_after": n_after,
        "delta": n_after - n_before,
        "raw_paths": [str(p.relative_to(ROOT)) for p in raw_paths],
        "null_offense_score": float(staged["offense_score"].isna().mean()),
        "null_defense_score": float(staged["defense_score"].isna().mean()),
        "null_clock": float(staged["clock"].isna().mean()),
        "null_score_margin": float(staged["score_margin"].isna().mean()),
        "null_wp": float(staged["wp"].isna().mean()),
        "matched_play_ids": int(staged["play_id"].isin(lookup).sum()),
    }


def _handpick_decisions(plays: pd.DataFrame, games: pd.DataFrame) -> dict[str, Any]:
    blowouts: list[dict[str, Any]] = []
    for gid in BLOWOUT_GAME_IDS:
        gp = plays.loc[(plays["game_id"] == gid) & (plays["period"] == 4)]
        if gp.empty:
            blowouts.append({"game_id": gid, "found": False})
            continue
        play = gp.sort_values("play_id").iloc[-1]
        flagged = apply_garbage_time(play.to_frame().T)
        blowouts.append(
            {
                "game_id": gid,
                "found": True,
                "score_margin": (
                    None
                    if pd.isna(play.get("score_margin"))
                    else int(play["score_margin"])
                ),
                "garbage_time": bool(flagged["garbage_time"].iloc[0]),
                "gt_rule": str(flagged["gt_rule"].iloc[0]),
            }
        )

    # Close-game controls: final |margin| <= 8, last Q4 play.
    g = games.copy()
    g = g.dropna(subset=["home_points", "away_points", "game_id"])
    g["final_margin"] = (g["home_points"].astype(float) - g["away_points"].astype(float)).abs()
    close = g.loc[g["final_margin"] <= 8].sort_values("final_margin").head(10)
    controls: list[dict[str, Any]] = []
    for _, game in close.iterrows():
        gid = int(game["game_id"])
        gp = plays.loc[(plays["game_id"] == gid) & (plays["period"] == 4)]
        if gp.empty:
            continue
        play = gp.sort_values("play_id").iloc[-1]
        flagged = apply_garbage_time(play.to_frame().T)
        controls.append(
            {
                "game_id": gid,
                "final_margin": float(game["final_margin"]),
                "score_margin": (
                    None
                    if pd.isna(play.get("score_margin"))
                    else int(play["score_margin"])
                ),
                "garbage_time": bool(flagged["garbage_time"].iloc[0]),
                "gt_rule": str(flagged["gt_rule"].iloc[0]),
            }
        )
        if len(controls) >= 10:
            break
    return {"blowouts": blowouts, "close_controls": controls}


def _annotate_play_types(plays: pd.DataFrame) -> pd.DataFrame:
    """Add is_rush/is_pass/is_special_teams/is_penalty without dropping staged ids."""
    work = plays.copy()
    if "is_rush" in work.columns and "is_pass" in work.columns:
        return work
    flags = work["play_type"].map(lambda t: classify_play_type(None if pd.isna(t) else str(t)))
    work["is_rush"] = flags.map(lambda x: bool(x[0]))
    work["is_pass"] = flags.map(lambda x: bool(x[1]))
    work["is_special_teams"] = flags.map(lambda x: bool(x[2]))
    work["is_penalty"] = flags.map(lambda x: bool(x[3]))
    return work


def _exp_poss_mae(
    plays: pd.DataFrame,
    games: pd.DataFrame,
    teams: pd.DataFrame,
    drives: pd.DataFrame,
    *,
    drop_garbage: bool,
) -> dict[str, Any]:
    """2023 week holdout MAE (train week<=10, test week>=11) matching notes/11."""
    g23 = games.loc[games["season"] == 2023].copy()
    p23 = plays.loc[plays["season"] == 2023].copy()
    d23 = drives.loc[drives["season"] == 2023].copy() if not drives.empty else pd.DataFrame()
    t23 = teams.loc[teams["season"] == 2023].copy() if "season" in teams.columns else teams
    if p23.empty or g23.empty or d23.empty:
        return {"available": False, "reason": "missing_2023_inputs"}
    # Tempo annotate requires clock/score_margin columns; pre-enrich staged may
    # lack them — leave null (never zero-fill) so exclusions degrade safely.
    for col in ("clock", "score_margin", "offense_score", "defense_score"):
        if col not in p23.columns:
            p23[col] = pd.Series([pd.NA] * len(p23), dtype="Int64")
    p23 = _annotate_play_types(p23)
    tempo_obs = build_tempo_observations(p23, g23, t23, drop_garbage=drop_garbage)
    train_frame = build_expected_possessions_training_frame(tempo_obs, g23, d23)
    if train_frame.empty or "week" not in train_frame.columns:
        return {"available": False, "reason": "empty_training_frame", "n_tempo": len(tempo_obs)}
    train_mask = train_frame["week"] <= 10
    test_mask = train_frame["week"] >= 11
    if not train_mask.any() or not test_mask.any():
        return {"available": False, "reason": "empty_week_split", "n_train_frame": len(train_frame)}
    artifact, mae = expected_possessions_oos_mae(
        train_frame, train_mask=train_mask, test_mask=test_mask
    )
    return {
        "available": True,
        "mae": mae,
        "n_train": int(train_mask.sum()),
        "n_test": int(test_mask.sum()),
        "artifact": artifact,
    }


def _filter_health_from_plays(
    plays: pd.DataFrame,
    games: pd.DataFrame,
    teams: pd.DataFrame,
    *,
    drop_garbage: bool,
) -> dict[str, Any]:
    g = games.loc[games["completed"].astype(bool)].copy()
    g = g.loc[g["home_points"].notna() & g["away_points"].notna()]
    obs = build_game_observations_from_plays(plays, g, drop_garbage=drop_garbage)
    if obs.empty:
        return {"available": False, "n_obs": 0}
    fbs: set[int] = set()
    if not teams.empty and "classification" in teams.columns:
        mask = teams["classification"].astype(str).str.casefold() == "fbs"
        fbs = {int(x) for x in teams.loc[mask, "team_id"]}
    obs = obs.copy()
    obs["home_is_fcs"] = ~obs["home_team_id"].isin(fbs) if fbs else False
    obs["away_is_fcs"] = ~obs["away_team_id"].isin(fbs) if fbs else False
    cfg = StateSpaceConfig()
    t0 = time.perf_counter()
    result = run_filter(obs, config=cfg, fbs_team_ids=fbs or None)
    health = filter_health_stats(result.innovations)
    return {
        "available": True,
        "n_obs": int(len(obs)),
        "filter_wall_clock_sec": time.perf_counter() - t0,
        "filter_log_lik": float(result.log_likelihood),
        "filter_health": {
            "mean_z": float(health.mean_z),
            "var_z": float(health.var_z),
            "n": int(health.n),
            "misspecified": bool(health.misspecified),
        },
        "observations": obs,
        "filter_result": result,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    store = ParquetStore(STAGED)
    cfg = load_config()
    team_map = load_team_name_map(cfg.data.team_names_path)

    # ---- Pre-enrich snapshots (WORLD A / GT-inert baseline) ----
    print("loading pre-enrich staged frames…", flush=True)
    plays_before_frames: list[pd.DataFrame] = []
    games_frames: list[pd.DataFrame] = []
    teams_frames: list[pd.DataFrame] = []
    drives_frames: list[pd.DataFrame] = []
    row_counts_before: dict[str, int] = {}
    for season in SEASONS:
        p = _load_season_table(store, "plays", season)
        row_counts_before[str(season)] = int(len(p))
        if not p.empty:
            plays_before_frames.append(p)
        g = _load_season_table(store, "games", season)
        if not g.empty:
            games_frames.append(g)
        t = _load_season_table(store, "teams", season)
        if not t.empty:
            teams_frames.append(t)
        d = _load_season_table(store, "drives", season)
        if not d.empty:
            drives_frames.append(d)

    plays_before = (
        pd.concat(plays_before_frames, ignore_index=True) if plays_before_frames else pd.DataFrame()
    )
    games = pd.concat(games_frames, ignore_index=True) if games_frames else pd.DataFrame()
    teams = pd.concat(teams_frames, ignore_index=True) if teams_frames else pd.DataFrame()
    drives = pd.concat(drives_frames, ignore_index=True) if drives_frames else pd.DataFrame()

    print("computing BEFORE expected-possessions MAE + filter health…", flush=True)
    mae_before = _exp_poss_mae(
        plays_before, games, teams, drives, drop_garbage=True
    )
    # Drop non-serializable artifact handle for the before report.
    mae_before_report = {k: v for k, v in mae_before.items() if k != "artifact"}
    health_before = _filter_health_from_plays(
        plays_before, games, teams, drop_garbage=True
    )
    health_before_report = {
        k: v for k, v in health_before.items() if k not in {"observations", "filter_result"}
    }

    # ---- Step 2: enrich every plays partition from raw ----
    print("enriching plays partitions from raw…", flush=True)
    partition_reports: list[dict[str, Any]] = []
    for season in SEASONS:
        for week in _partition_weeks(store, season):
            rep = enrich_plays_partition(store, season=season, week=week)
            partition_reports.append(rep)
            if not rep.get("skipped"):
                print(
                    f"  {season} w{week}: n={rep['n_after']} delta={rep['delta']} "
                    f"null_margin={rep.get('null_score_margin')}",
                    flush=True,
                )

    # Reload enriched plays.
    plays_frames = [_load_season_table(store, "plays", s) for s in SEASONS]
    plays = pd.concat([p for p in plays_frames if not p.empty], ignore_index=True)

    season_summary: dict[str, Any] = {}
    gt_incapable: list[str] = []
    for season in SEASONS:
        p = plays.loc[plays["season"] == season] if not plays.empty else pd.DataFrame()
        n = int(len(p))
        n_before = row_counts_before[str(season)]
        null_rates = {
            col: (float(p[col].isna().mean()) if col in p.columns and n else None)
            for col in (*NEW_COLS, "wp")
        }
        if n and null_rates.get("score_margin", 1.0) is not None:
            if float(null_rates["score_margin"]) > 0.50:
                gt_incapable.append(str(season))
        season_summary[str(season)] = {
            "n_before": n_before,
            "n_after": n,
            "delta": n - n_before,
            "null_rates": null_rates,
        }

    # Smoke: normalizer still works on a raw week (sanity, not a write path).
    sample_raw = _latest_raw_plays(2023, 1)
    if sample_raw and not teams.empty:
        school_to_id = {
            str(r.school): int(r.team_id)
            for r in teams.loc[teams["season"] == 2023].itertuples(index=False)
            if getattr(r, "school", None) is not None
        }
        _ = normalize_plays_payload(
            sample_raw[0].read_bytes(),
            season=2023,
            week=1,
            ingested_at=datetime.now(tz=UTC),
            school_to_id=school_to_id,
            team_map=team_map,
        )

    # ---- Step 3: flag verification ----
    print("flag verification…", flush=True)
    flag_table: dict[str, Any] = {}
    for season in SEASONS:
        p = plays.loc[plays["season"] == season]
        g = games.loc[games["season"] == season]
        if p.empty:
            continue
        flagged = apply_garbage_time(p)
        n_off = int(len(flagged))
        n_on = int((~flagged["garbage_time"].astype(bool)).sum())
        n_gt = int(flagged["garbage_time"].astype(bool).sum())
        _, n_on_stack, n_off_stack = build_observations_from_staged(
            plays=p, games=g, garbage_time_filter=True
        )
        flag_table[str(season)] = {
            "n_plays": n_off,
            "n_garbage": n_gt,
            "flag_rate": (n_gt / n_off) if n_off else None,
            "n_on": n_on_stack,
            "n_off": n_off_stack,
            "n_on_lt_n_off": n_on_stack < n_off_stack,
            "fallback_frac": float(flagged["gt_fallback_used"].astype(bool).mean()),
            "wp_nonnull": int(p["wp"].notna().sum()) if "wp" in p.columns else 0,
        }

    p23 = plays.loc[plays["season"] == 2023]
    g23 = games.loc[games["season"] == 2023]
    fixtures = _handpick_decisions(p23, g23)

    # ---- Step 4: rematerialize ----
    print("rematerializing downstream…", flush=True)
    mae_after = _exp_poss_mae(plays, games, teams, drives, drop_garbage=True)
    artifact = mae_after.pop("artifact", None)
    artifact_path = OUT / "expected_possessions.json"
    if artifact is not None:
        save_expected_possessions_artifact(artifact, artifact_path)

    # Efficiency observations (Task 10 ridge inputs) — GT on.
    eff_path = OUT / "efficiency_play_game_obs.parquet"
    if not plays.empty and not games.empty and not teams.empty:
        annotated = _annotate_play_types(plays)
        # Havoc / success helpers used when present; rates still compute on EPA.
        from ncaa_quant.features.epa import is_havoc_play, is_successful_play

        if "is_havoc" not in annotated.columns:
            annotated["is_havoc"] = annotated["play_type"].map(
                lambda t: is_havoc_play(None if pd.isna(t) else str(t))
            )
        if "is_success" not in annotated.columns:
            annotated["is_success"] = [
                is_successful_play(
                    None if pd.isna(d) else int(d),
                    None if pd.isna(dist) else int(dist),
                    None if pd.isna(yg) else int(yg),
                )
                for d, dist, yg in zip(
                    annotated["down"], annotated["distance"], annotated["yards_gained"], strict=True
                )
            ]
        eff_obs = build_play_game_observations(
            annotated, games, teams, drives=drives if not drives.empty else None, drop_garbage=True
        )
        eff_obs.to_parquet(eff_path, index=False)
    else:
        eff_obs = pd.DataFrame()

    # Stage-1 observations from GT-filtered plays.
    health_after = _filter_health_from_plays(plays, games, teams, drop_garbage=True)
    obs_after = health_after.pop("observations", pd.DataFrame())
    filt_result = health_after.pop("filter_result", None)
    obs_path = OUT / "stage1_observations_from_plays.parquet"
    if isinstance(obs_after, pd.DataFrame) and not obs_after.empty:
        obs_after.to_parquet(obs_path, index=False)
    if filt_result is not None:
        filt_result.history.to_parquet(OUT / "state_space_history.parquet", index=False)
        filt_result.innovations.to_parquet(OUT / "state_space_innovations.parquet", index=False)

    # Mark old Task 14 acceptance artifacts SUPERSEDED (do not delete).
    SS_OLD.mkdir(parents=True, exist_ok=True)
    superseded = SS_OLD / "SUPERSEDED.md"
    superseded.write_text(
        "\n".join(
            [
                "# SUPERSEDED — GT-FIX",
                "",
                "These Task 14 acceptance artifacts (advanced-box observation path,",
                "GT-inert relative to staged PBP) are superseded by",
                "`data/tmp/gt_fix/` play-level GT-filtered filter outputs.",
                "",
                f"Superseded at: {datetime.now(tz=UTC).isoformat()}",
                "Do not delete — leave the trail.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    report = {
        "step0_ruling": {
            "raw_fields_present": [
                "offenseScore",
                "defenseScore",
                "period",
                "clock",
                "ppa",
            ],
            "raw_wp_fields_present": False,
            "wp_ruling": (
                "CFBD /plays archives do not include homeWinProb/wp; staged wp "
                "stays null; Connelly-from-scores is the operative GT definition "
                "per DESIGN §4.2 fallback order."
            ),
        },
        "row_counts": season_summary,
        "gt_incapable_seasons": gt_incapable,
        "flag_table": flag_table,
        "fixtures": fixtures,
        "expected_possessions_mae": {
            "before": mae_before_report,
            "after": mae_after,
            "artifact_path": str(artifact_path.relative_to(ROOT))
            if artifact_path.exists()
            else None,
            "notes11_baseline_mae": 2.778104,
        },
        "filter_health": {
            "before_play_path_gt_inert": health_before_report,
            "after_play_path_gt_active": health_after,
            "old_task14_advanced_box_summary": (
                json.loads((SS_OLD / "summary.json").read_text(encoding="utf-8"))
                if (SS_OLD / "summary.json").exists()
                else None
            ),
            "superseded_marker": str(superseded.relative_to(ROOT)),
        },
        "artifacts": {
            "efficiency_obs": str(eff_path.relative_to(ROOT)) if eff_path.exists() else None,
            "stage1_obs": str(obs_path.relative_to(ROOT)) if obs_path.exists() else None,
            "n_efficiency_rows": int(len(eff_obs)) if isinstance(eff_obs, pd.DataFrame) else 0,
        },
        "partition_sample": partition_reports[:3] + partition_reports[-3:],
        "n_partitions_enriched": len([r for r in partition_reports if not r.get("skipped")]),
    }
    report_path = OUT / "gt_fix_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"wrote {report_path}", flush=True)


if __name__ == "__main__":
    main()
