"""W9-I Phase 0 inventory. Hygiene counts and operational state only.

Does not compute 2025 metrics, predictions, ATS, or any evaluative output.
Does not ingest, mutate config, or write R2.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
STAGED = ROOT / "data" / "staged"
HIST = ROOT / "data" / "artifacts" / "state_space" / "filter_history.parquet"
OUT = Path(__file__).resolve().parent / "phase0_inventory.json"


def utc_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_season_games(season: int) -> pd.DataFrame:
    season_dir = STAGED / "games" / f"season={season}"
    if not season_dir.is_dir():
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for wdir in sorted(season_dir.glob("week=*")):
        part = wdir / "part.parquet"
        if part.is_file():
            frames.append(pd.read_parquet(part))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def games_hygiene(season: int) -> dict:
    season_dir = STAGED / "games" / f"season={season}"
    rec: dict = {
        "season": season,
        "dir_exists": season_dir.is_dir(),
        "week_dirs": [],
    }
    if not season_dir.is_dir():
        return rec
    rec["week_dirs"] = sorted(p.name for p in season_dir.glob("week=*"))
    g = load_season_games(season)
    rec["n_rows"] = int(len(g))
    rec["columns"] = list(g.columns)
    if g.empty:
        return rec
    if "week" in g.columns:
        rec["n_by_week"] = {int(k): int(v) for k, v in g.groupby("week").size().items()}
    if "season_type" in g.columns:
        rec["season_type"] = {str(k): int(v) for k, v in g["season_type"].astype(str).value_counts().items()}
    if "completed" in g.columns:
        completed = g["completed"].astype(bool)
        rec["completed_true"] = int(completed.sum())
        rec["completed_false"] = int((~completed).sum())
        rec["completed_na"] = int(g["completed"].isna().sum())
        rec["completed_by_week"] = {
            int(k): {
                "n": int(len(sub)),
                "completed_true": int(sub["completed"].astype(bool).sum()),
                "completed_false": int((~sub["completed"].astype(bool)).sum()),
            }
            for k, sub in g.groupby("week")
        }
    for col in ("event_time", "start_date"):
        if col in g.columns:
            et = pd.to_datetime(g[col], utc=True, errors="coerce")
            rec[f"{col}_min"] = str(et.min()) if et.notna().any() else None
            rec[f"{col}_max"] = str(et.max()) if et.notna().any() else None
            if "completed" in g.columns:
                done = et[g["completed"].astype(bool)]
                rec[f"{col}_max_completed"] = str(done.max()) if done.notna().any() else None
            break
    if "home_points" in g.columns and "away_points" in g.columns:
        has_score = g["home_points"].notna() & g["away_points"].notna()
        rec["has_both_scores"] = int(has_score.sum())
        rec["missing_score"] = int((~has_score).sum())
        rec["missing_score_by_week"] = {
            int(k): int((~sub["home_points"].notna() | ~sub["away_points"].notna()).sum())
            for k, sub in g.groupby("week")
        }
    newest_part = None
    newest_mtime = None
    for wdir in season_dir.glob("week=*"):
        part = wdir / "part.parquet"
        if part.is_file():
            mt = utc_mtime(part)
            if newest_mtime is None or (mt or "") > newest_mtime:
                newest_mtime = mt
                newest_part = str(part.as_posix())
    rec["newest_part"] = newest_part
    rec["newest_mtime"] = newest_mtime
    return rec


def table_week_hygiene(table: str, season: int) -> dict:
    d = STAGED / table / f"season={season}"
    rec: dict = {"table": table, "season": season, "dir_exists": d.is_dir()}
    if not d.is_dir():
        return rec
    by: dict[int, int] = {}
    n_parts = 0
    n_rows = 0
    for wdir in sorted(d.glob("week=*")):
        part = wdir / "part.parquet"
        if part.is_file():
            n_parts += 1
            n = int(len(pd.read_parquet(part)))
            n_rows += n
            by[int(str(wdir.name).split("=")[1])] = n
    rec["n_week_parts"] = n_parts
    rec["n_rows"] = n_rows
    rec["weeks"] = sorted(by)
    rec["n_by_week"] = {str(k): v for k, v in by.items()}
    return rec


def season_table(table: str, season: int) -> dict:
    p = STAGED / table / f"season={season}" / "part.parquet"
    rec = {"table": table, "season": season, "exists": p.is_file(), "mtime": utc_mtime(p)}
    if p.is_file():
        rec["n"] = int(len(pd.read_parquet(p)))
        rec["bytes"] = p.stat().st_size
    else:
        rec["n"] = 0
    return rec


def filter_history() -> dict:
    rec: dict = {
        "path": str(HIST.as_posix()),
        "exists": HIST.is_file(),
        "mtime": utc_mtime(HIST),
        "bytes": HIST.stat().st_size if HIST.is_file() else 0,
    }
    if not HIST.is_file():
        return rec
    h = pd.read_parquet(HIST)
    rec["n_rows"] = int(len(h))
    rec["columns"] = list(h.columns)
    if "season" in h.columns:
        rec["seasons"] = sorted(int(s) for s in h["season"].dropna().unique())
        rec["n_by_season"] = {str(int(k)): int(v) for k, v in h.groupby("season").size().items()}
        h25 = h.loc[h["season"].astype(int) == 2025]
        rec["n_2025"] = int(len(h25))
        if not h25.empty and "week" in h25.columns:
            rec["n_2025_by_week"] = {str(int(k)): int(v) for k, v in h25.groupby("week").size().items()}
        for col in ("event_time", "posterior_asof", "as_of"):
            if col in h.columns and not h25.empty:
                et = pd.to_datetime(h25[col], utc=True, errors="coerce")
                rec[f"2025_{col}_min"] = str(et.min()) if et.notna().any() else None
                rec[f"2025_{col}_max"] = str(et.max()) if et.notna().any() else None
        h24 = h.loc[h["season"].astype(int) == 2024]
        rec["n_2024"] = int(len(h24))
        # Compare last posterior per team 2024 vs 2025 for a handful of ids
        team_col = None
        for c in ("team_id", "fbs_team_id", "id"):
            if c in h.columns:
                team_col = c
                break
        rec["team_col"] = team_col
        rec["rating_cols"] = [
            c
            for c in h.columns
            if any(x in c.lower() for x in ("off", "def", "mean", "pace", "st_"))
        ][:30]
        if team_col and "off_epa" in h.columns:
            def last_by_team(frame: pd.DataFrame) -> pd.DataFrame:
                work = frame.copy()
                order_col = "event_time" if "event_time" in work.columns else "week"
                work["_ord"] = pd.to_datetime(work[order_col], utc=True, errors="coerce") if order_col == "event_time" else work[order_col]
                work = work.sort_values("_ord")
                return work.groupby(team_col, as_index=False).tail(1)

            last24 = last_by_team(h24)
            last25 = last_by_team(h25)
            merged = last24.merge(last25, on=team_col, suffixes=("_eoy2024", "_eoy2025"), how="inner")
            rec["n_teams_both_years"] = int(len(merged))
            if not merged.empty:
                delta = merged["off_epa_eoy2025"] - merged["off_epa_eoy2024"]
                rec["off_epa_delta_eoy2025_minus_eoy2024"] = {
                    "n": int(delta.notna().sum()),
                    "n_identical": int((delta.abs() < 1e-12).sum()),
                    "n_moved": int((delta.abs() >= 1e-12).sum()),
                    "min": float(delta.min()) if delta.notna().any() else None,
                    "max": float(delta.max()) if delta.notna().any() else None,
                    "median_abs": float(delta.abs().median()) if delta.notna().any() else None,
                }
                sample_ids = [int(x) for x in merged[team_col].head(8).tolist()]
                rec["sample_team_off_epa"] = []
                for tid in sample_ids:
                    row = merged.loc[merged[team_col] == tid].iloc[0]
                    rec["sample_team_off_epa"].append(
                        {
                            "team_id": tid,
                            "off_epa_eoy2024": float(row["off_epa_eoy2024"]),
                            "off_epa_eoy2025": float(row["off_epa_eoy2025"]),
                        }
                    )
    return rec


def v3_week1() -> dict:
    """Week-1 information-set from the current-code champion (not 2025)."""
    pred_path = ROOT / "data" / "backtests" / "task23_fundamental_reduced_v3" / "full" / "predictions.parquet"
    rec: dict = {"path": str(pred_path.as_posix()), "exists": pred_path.is_file()}
    if not pred_path.is_file():
        return rec
    p = pd.read_parquet(pred_path)
    rec["n"] = int(len(p))
    rec["seasons"] = sorted(int(s) for s in p["season"].dropna().unique()) if "season" in p.columns else []
    rec["n_2025"] = int((p["season"].astype(int) == 2025).sum()) if "season" in p.columns else None
    if {"season", "week"}.issubset(p.columns):
        w1 = p.loc[(p["season"].astype(int) >= 2021) & (p["week"].astype(int) == 1)]
        rec["w1_2021_2024_n"] = int(len(w1))
        rec["w1_by_season"] = {
            str(int(k)): int(v) for k, v in w1.groupby("season").size().items()
        }
        if "null_reason" in w1.columns:
            reasons = w1["null_reason"].astype(str)
            rec["w1_null_reason_counts"] = {str(k): int(v) for k, v in reasons.value_counts(dropna=False).items()}
        if "pred_margin" in w1.columns:
            mu = pd.to_numeric(w1["pred_margin"], errors="coerce")
            rec["w1_mu"] = {
                "n": int(len(mu)),
                "n_finite": int(mu.notna().sum()),
                "min": float(mu.min()) if mu.notna().any() else None,
                "max": float(mu.max()) if mu.notna().any() else None,
            }
        if "sigma_m" in w1.columns:
            sig = pd.to_numeric(w1["sigma_m"], errors="coerce")
            rec["w1_sigma"] = {
                "n_finite": int(sig.notna().sum()),
                "n_missing": int(sig.isna().sum()),
                "min": float(sig.min()) if sig.notna().any() else None,
                "max": float(sig.max()) if sig.notna().any() else None,
            }
        feat_cols = [c for c in w1.columns if c.startswith("feat__")]
        rec["w1_feat_cols_n"] = len(feat_cols)
        if "feat__expected_possessions" in w1.columns:
            ep = pd.to_numeric(w1["feat__expected_possessions"], errors="coerce")
            rec["w1_expected_possessions"] = {
                "n_finite": int(ep.notna().sum()),
                "n_nan": int(ep.isna().sum()),
            }
    # 2019 week 1 absence
    if {"season", "week"}.issubset(p.columns):
        rec["n_2019_w1"] = int(((p["season"].astype(int) == 2019) & (p["week"].astype(int) == 1)).sum())
        rec["n_2019"] = int((p["season"].astype(int) == 2019).sum())
    return rec


def inventory_other() -> dict:
    files = {
        "registry_index": ROOT / "data" / "registry" / "registry_index.json",
        "v2_ensemble": ROOT / "data" / "registry" / "artifacts" / "v2" / "production_ensemble.pkl",
        "v2_manifest": ROOT / "data" / "registry" / "artifacts" / "v2" / "manifest.json",
        "v2_rating_snapshot": ROOT / "data" / "registry" / "artifacts" / "v2" / "rating_snapshot.json",
        "possessions_live": ROOT / "data" / "artifacts" / "expected_possessions" / "live.json",
        "tier_state": ROOT / "data" / "webapp" / "tier_state.json",
        "tier_changes": ROOT / "data" / "webapp" / "tier_changes.jsonl",
        "idempotency": ROOT / "data" / "pipeline_state" / "idempotency.json",
        "priors": ROOT / "data" / "tmp" / "priors_acceptance_15" / "week1_priors.parquet",
        "champion3_w5": ROOT
        / "data"
        / "backtests"
        / "task23_fundamental_reduced_v2"
        / "full"
        / "weeks"
        / "season=2024_week=5.parquet",
    }
    out = {}
    for name, path in files.items():
        rec = {"path": str(path.as_posix()), "exists": path.is_file(), "mtime": utc_mtime(path)}
        if path.is_file():
            rec["bytes"] = path.stat().st_size
        out[name] = rec
    # calibrator glob
    cands = list(ROOT.glob("data/**/*calibr*")) + list(ROOT.glob("data/**/*pit_recal*"))
    out["calibrator_paths"] = [str(p.as_posix()) for p in cands if p.is_file()]
    # 2026 raw
    raw = ROOT / "data" / "raw"
    games_2026 = list(raw.rglob("*games_s2026*")) if raw.is_dir() else []
    out["raw_games_s2026"] = [str(p.as_posix()) for p in games_2026]
    return out


def main() -> None:
    payload = {
        "inspected_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy": "hygiene counts and operational inventory; no 2025 metrics",
        "games": {str(s): games_hygiene(s) for s in (2024, 2025, 2026)},
        "teams": {str(s): season_table("teams", s) for s in (2024, 2025, 2026)},
        "venues": {str(s): season_table("venues", s) for s in (2024, 2025, 2026)},
        "obs_hygiene_2025": {
            t: table_week_hygiene(t, 2025) for t in ("advanced_box", "plays", "drives")
        },
        "obs_hygiene_2024": {
            t: table_week_hygiene(t, 2024) for t in ("advanced_box", "plays", "drives")
        },
        "filter_history": filter_history(),
        "v3_week1": v3_week1(),
        "other": inventory_other(),
    }
    OUT.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
