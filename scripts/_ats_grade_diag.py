"""TASK ATS-GRADE-DIAG — read-only diagnosis of snapshot-regime ATS collapse.

Sanctioned: this script + docs/notes/ats-grade-diag.md + tests fixtures only.
Does not mutate data/.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PRED_FUND = ROOT / "data/backtests/task23_fundamental_reduced_v1/full/predictions.parquet"
PRED_MKT = ROOT / "data/backtests/task23_market_aware_reduced_v1/full/predictions.parquet"
PRED_A6 = ROOT / "data/backtests/task23_a6_reduced_v1/A6_cfbd_open_close/predictions.parquet"
GAMES_DIR = ROOT / "data/staged/games"
LINES_DIR = ROOT / "data/staged/lines_historical"
SNAPS_DIR = ROOT / "data/staged/odds_snapshots"
TEAMS_DIR = ROOT / "data/staged/teams"
OUT_JSON = ROOT / "docs/notes/_artifacts/ats_grade_diag/summary.json"


@dataclass(frozen=True)
class HandRow:
    bucket: str
    season: int
    week: int
    game_id: int
    home: str
    away: str
    home_pts: int
    away_pts: int
    realized_margin: float
    book_close_home_spread: float
    book_close_source: str
    book_close_side_note: str
    pred_margin: float
    model_picked_home: bool
    hand_home_covers: bool | None  # None = push
    hand_model_ats_hit: bool | None
    grader_spread_close: float
    grader_line_source: str
    grader_p_ats_home: float
    grader_home_covers: bool | None
    grader_model_ats_hit: bool | None
    agree: bool


def _read_hive(path: Path, seasons: list[int] | None = None) -> pd.DataFrame:
    """Read partitioned parquet; coerce common dict-encoded ints."""
    files = sorted(path.rglob("*.parquet"))
    if not files:
        msg = f"no parquet under {path}"
        raise FileNotFoundError(msg)
    frames: list[pd.DataFrame] = []
    for f in files:
        if seasons is not None:
            # path like .../season=2019/...
            parts = {p.split("=")[0]: p.split("=")[1] for p in f.parts if "=" in p}
            if "season" in parts and int(parts["season"]) not in seasons:
                continue
        part = pd.read_parquet(f)
        # Avoid Arrow dict/int merge failures across hive partitions.
        for col in ("season", "week", "game_id", "home_team_id", "away_team_id", "team_id"):
            if col in part.columns:
                part[col] = pd.to_numeric(part[col], errors="coerce")
        for col in ("home_team", "away_team", "side", "book", "market", "school"):
            if col in part.columns:
                part[col] = part[col].astype(str)
        frames.append(part)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _team_name_map(teams: pd.DataFrame) -> dict[int, str]:
    """Map team_id → school name (latest season wins)."""
    t = teams.sort_values("season")
    out: dict[int, str] = {}
    for row in t.itertuples(index=False):
        out[int(row.team_id)] = str(row.school)
    return out


def _attach_team_names(games: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    names = _team_name_map(teams)
    out = games.copy()
    out["home_team"] = out["home_team_id"].map(lambda x: names.get(int(x), str(x)))
    out["away_team"] = out["away_team_id"].map(lambda x: names.get(int(x), str(x)))
    return out


def ats_home_cover(margin: float, home_spread: float) -> bool | None:
    edge = float(margin) + float(home_spread)
    if not np.isfinite(edge) or abs(edge) < 1e-12:
        return None
    return bool(edge > 0)


def binary_ats_hit(p_ats_home: float, home_covers: bool | None) -> bool | None:
    if home_covers is None or not np.isfinite(p_ats_home):
        return None
    picked_home = bool(p_ats_home >= 0.5)
    return picked_home == home_covers


def load_predictions() -> pd.DataFrame:
    p = pd.read_parquet(PRED_FUND)
    return p.loc[~p["exclude_from_headline"]].copy()


def summarize_spread_regimes(preds: pd.DataFrame) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for season, g in preds.groupby("season"):
        sp = g["spread_close"].dropna()
        rows.append(
            {
                "season": int(season),
                "n": int(len(g)),
                "finite_spread": int(sp.notna().sum()),
                "spread_mean": float(sp.mean()) if len(sp) else None,
                "spread_median": float(sp.median()) if len(sp) else None,
                "abs_median": float(sp.abs().median()) if len(sp) else None,
                "pct_abs_lt_0_5": float((sp.abs() < 0.5).mean()) if len(sp) else None,
                "line_sources": {
                    str(k): int(v) for k, v in g["line_source_close"].value_counts().items()
                },
            }
        )
    return {"by_season": rows}


def recompute_ats(df: pd.DataFrame) -> tuple[float, int]:
    sp = df["spread_close"].to_numpy(dtype=float)
    m = df["realized_margin"].to_numpy(dtype=float)
    p = df["p_ats_home"].to_numpy(dtype=float)
    cover = m + sp
    decided = np.isfinite(cover) & (np.abs(cover) > 1e-12) & np.isfinite(p)
    if not np.any(decided):
        return float("nan"), 0
    y = (cover[decided] > 0).astype(float)
    pred = (p[decided] >= 0.5).astype(float)
    return float(np.mean(pred == y)), int(decided.sum())


def snapshot_home_close_spread(
    snaps: pd.DataFrame,
    *,
    game_id: int,
    home_team: str,
    kickoff: pd.Timestamp,
) -> tuple[float, str, int, dict[str, Any]]:
    """Median CFBD-home-side spread from last snapshot strictly before kickoff.

    ``home_team`` must be the CFBD designated home school name. Odds listing
    ``home_team`` may be swapped on neutrals (5b-patch2); side is name-based, so
    filter ``side == cfbd_home`` — never Odds ``home_team``.
    """
    meta: dict[str, Any] = {}
    work = snaps.loc[
        (snaps["game_id"] == game_id)
        & (snaps["market"] == "spread")
        & snaps["line"].notna()
        & (snaps["event_time"] < kickoff)
    ].copy()
    if work.empty:
        return float("nan"), "null", 0, meta
    latest = work["event_time"].max()
    window = work.loc[work["event_time"] == latest]
    snap_listed_home = str(window["home_team"].iloc[0]) if "home_team" in window.columns else ""
    meta["odds_listed_home"] = snap_listed_home
    meta["cfbd_home"] = str(home_team)
    meta["listing_swap"] = bool(
        snap_listed_home and snap_listed_home.casefold() != str(home_team).casefold()
    )
    # CFBD-home perspective (matches realized_margin = home_pts - away_pts).
    home_rows = window.loc[window["side"].astype(str) == str(home_team)]
    if home_rows.empty:
        ht = str(home_team).casefold()
        home_rows = window.loc[window["side"].astype(str).str.casefold() == ht]
    all_med = float(window["line"].median())
    meta["all_sides_median"] = all_med
    if home_rows.empty:
        return all_med, "all_sides_median_BUG", int(window["book"].nunique()), meta
    home_med = float(home_rows["line"].median())
    meta["cfbd_home_side_median"] = home_med
    return home_med, "odds_cfbd_home_side_median", int(home_rows["book"].nunique()), meta


def cfbd_home_close_spread(lines: pd.DataFrame, game_id: int) -> tuple[float, str, int]:
    sub = lines.loc[lines["game_id"] == game_id]
    if sub.empty:
        return float("nan"), "null", 0
    if "line_type" in sub.columns:
        typed = sub.loc[sub["line_type"].astype(str).str.lower().eq("close")]
        if typed.empty:
            typed = sub
        source = "cfbd_close"
    else:
        typed = sub
        source = "cfbd"
    if "spread" not in typed.columns or not typed["spread"].notna().any():
        return float("nan"), "null", 0
    n_books = int(typed["book"].nunique()) if "book" in typed.columns else 0
    return float(typed["spread"].median()), source, n_books


def pick_fixture_game_ids(
    preds: pd.DataFrame,
    *,
    fund: pd.DataFrame,
    mkt: pd.DataFrame | None,
) -> dict[str, list[int]]:
    """Select 8+8+8 game_ids for hand grading."""
    y2019 = preds.loc[preds["season"] == 2019].copy()
    # Prefer CFBD closes with decisive ATS (non-push) and diverse weeks
    y2019 = y2019.loc[y2019["spread_close"].notna() & y2019["p_ats_home"].notna()]
    y2019["cover_edge"] = y2019["realized_margin"] + y2019["spread_close"]
    y2019 = y2019.loc[y2019["cover_edge"].abs() > 1e-12]

    snap = preds.loc[preds["season"].between(2021, 2024)].copy()
    snap = snap.loc[
        snap["line_source_close"].astype(str).str.startswith("odds_api_snapshot")
        & snap["spread_close"].notna()
        & snap["p_ats_home"].notna()
    ]
    # Prefer the characteristic near-zero bug rows, plus a few non-zero
    snap_zero = snap.loc[snap["spread_close"].abs() < 0.5]
    snap_nonzero = snap.loc[snap["spread_close"].abs() >= 0.5]

    disagree: list[int] = []
    if mkt is not None and not mkt.empty:
        f = fund.set_index("game_id")
        m = mkt.set_index("game_id")
        common = f.index.intersection(m.index)
        for gid in common:
            if int(f.loc[gid, "season"]) < 2021:
                continue
            pf = float(f.loc[gid, "p_ats_home"])
            pm = float(m.loc[gid, "p_ats_home"])
            if not (np.isfinite(pf) and np.isfinite(pm)):
                continue
            # hard-pick disagreement with a finite fund close
            if (pf >= 0.5) != (pm >= 0.5) and np.isfinite(float(f.loc[gid, "spread_close"])):
                disagree.append(int(gid))
        # diversify seasons
        disagree_df = f.loc[disagree, ["season", "week"]].copy()
        disagree_df["game_id"] = disagree_df.index
        picked_d: list[int] = []
        for _season, g in disagree_df.groupby("season"):
            picked_d.extend(g.sort_values("week")["game_id"].astype(int).head(2).tolist())
        # fill to 8
        for gid in disagree:
            if len(picked_d) >= 8:
                break
            if gid not in picked_d:
                picked_d.append(gid)
        disagree = picked_d[:8]

    def _sample_diverse(df: pd.DataFrame, n: int, seed: int = 23) -> list[int]:
        if df.empty:
            return []
        rng = np.random.default_rng(seed)
        out: list[int] = []
        # round-robin by season then week bands
        for _season, g in df.groupby("season"):
            weeks = sorted(g["week"].unique())
            for w in weeks[:: max(1, len(weeks) // 2)]:
                pool = g.loc[g["week"] == w, "game_id"].astype(int).tolist()
                if pool:
                    out.append(int(rng.choice(pool)))
                if len(out) >= n:
                    return out[:n]
        remain = [int(x) for x in df["game_id"].tolist() if int(x) not in out]
        rng.shuffle(remain)
        out.extend(remain)
        return out[:n]

    # 8 from 2019
    g2019 = _sample_diverse(y2019, 8, seed=2019)
    # 8 snapshot closes: 6 near-zero + 2 non-zero if available
    z = _sample_diverse(snap_zero, 6, seed=2021)
    nz = _sample_diverse(snap_nonzero, 2, seed=2022)
    gsnap = (z + nz)[:8]
    while len(gsnap) < 8:
        extra = _sample_diverse(snap, 8, seed=99)
        for gid in extra:
            if gid not in gsnap:
                gsnap.append(gid)
            if len(gsnap) >= 8:
                break

    return {
        "cfbd_2019": g2019,
        "snapshot_closes": gsnap,
        "fund_mkt_disagree": disagree[:8],
    }


def build_hand_rows(
    preds: pd.DataFrame,
    games: pd.DataFrame,
    lines: pd.DataFrame,
    snaps: pd.DataFrame,
    picks: dict[str, list[int]],
) -> list[HandRow]:
    gmap = games.drop_duplicates("game_id").set_index("game_id")
    pmap = preds.drop_duplicates("game_id").set_index("game_id")
    rows: list[HandRow] = []
    for bucket, gids in picks.items():
        for gid in gids:
            if gid not in pmap.index or gid not in gmap.index:
                continue
            pr = pmap.loc[gid]
            gm = gmap.loc[gid]
            season = int(pr["season"])
            week = int(pr["week"])
            home = str(gm["home_team"] if "home_team" in gm.index else gm.get("home", ""))
            away = str(gm["away_team"] if "away_team" in gm.index else gm.get("away", ""))
            # CFBD games schema uses home_team / away_team
            if "home_team" in gm.index:
                home = str(gm["home_team"])
                away = str(gm["away_team"])
            home_pts = int(pr["home_points"])
            away_pts = int(pr["away_points"])
            margin = float(pr["realized_margin"])
            pred_m = float(pr["pred_margin"])
            p_ats = float(pr["p_ats_home"])
            grader_sp = float(pr["spread_close"])
            grader_src = str(pr["line_source_close"])

            kickoff = pd.Timestamp(gm["event_time"])
            if kickoff.tzinfo is None:
                kickoff = kickoff.tz_localize("UTC")
            else:
                kickoff = kickoff.tz_convert("UTC")

            if season >= 2021 and not snaps.empty:
                book_sp, book_src, _n, meta = snapshot_home_close_spread(
                    snaps, game_id=gid, home_team=home, kickoff=kickoff
                )
                all_med = meta.get("all_sides_median", float("nan"))
                swap = meta.get("listing_swap", False)
                note = (
                    f"cfbd_home_side={book_sp:.3f}; all_sides_median={all_med}; "
                    f"listing_swap={swap}; odds_listed_home={meta.get('odds_listed_home')}"
                )
                if not np.isfinite(book_sp):
                    book_sp, book_src, _n = cfbd_home_close_spread(lines, gid)
                    note = f"snapshot miss → {book_src}"
            else:
                book_sp, book_src, _n = cfbd_home_close_spread(lines, gid)
                note = "CFBD home-perspective close (median across books)"

            hand_cover = ats_home_cover(margin, book_sp)
            model_pick_home = bool(p_ats >= 0.5) if np.isfinite(p_ats) else (pred_m >= 0.0)
            hand_hit = None
            if hand_cover is not None:
                hand_hit = model_pick_home == hand_cover

            grader_cover = ats_home_cover(margin, grader_sp)
            grader_hit = binary_ats_hit(p_ats, grader_cover)

            # Agreement: same cover label AND same hit/miss (or both push)
            if hand_cover is None and grader_cover is None:
                agree = True
            elif hand_cover is None or grader_cover is None:
                agree = False
            else:
                agree = (hand_cover == grader_cover) and (hand_hit == grader_hit)

            rows.append(
                HandRow(
                    bucket=bucket,
                    season=season,
                    week=week,
                    game_id=int(gid),
                    home=home,
                    away=away,
                    home_pts=home_pts,
                    away_pts=away_pts,
                    realized_margin=margin,
                    book_close_home_spread=float(book_sp) if np.isfinite(book_sp) else float("nan"),
                    book_close_source=book_src,
                    book_close_side_note=note,
                    pred_margin=pred_m,
                    model_picked_home=model_pick_home,
                    hand_home_covers=hand_cover,
                    hand_model_ats_hit=hand_hit,
                    grader_spread_close=grader_sp,
                    grader_line_source=grader_src,
                    grader_p_ats_home=p_ats,
                    grader_home_covers=grader_cover,
                    grader_model_ats_hit=grader_hit,
                    agree=agree,
                )
            )
    return rows


def main() -> None:
    preds = load_predictions()
    print("loaded predictions", len(preds))
    regime = summarize_spread_regimes(preds)
    print(json.dumps(regime, indent=2))

    print("\n=== ATS recomputed from stored columns ===")
    for label, mask in [
        ("2019", preds["season"] == 2019),
        ("2021-2024", preds["season"].between(2021, 2024)),
        ("snap_near0", (preds["season"] >= 2021) & (preds["spread_close"].abs() < 0.5)),
        (
            "cfbd_close_eval",
            preds["line_source_close"].astype(str).eq("cfbd_close_eval"),
        ),
    ]:
        a, n = recompute_ats(preds.loc[mask])
        print(f"{label}: {a * 100:.2f}% n={n}")

    # SU
    print("\n=== SU accuracy (p_ml) ===")
    for season, g in preds.groupby("season"):
        m = g["realized_margin"].to_numpy(float)
        p = g["p_ml_home"].to_numpy(float)
        decided = np.isfinite(m) & (np.abs(m) > 1e-12) & np.isfinite(p)
        y = (m[decided] > 0).astype(float)
        pred = (p[decided] >= 0.5).astype(float)
        print(f"{season}: {np.mean(pred == y) * 100:.1f}% n={decided.sum()}")

    seasons = [2019, 2021, 2022, 2023, 2024]
    print("\nloading games/lines/snaps/teams (read-only)...")
    games = _attach_team_names(_read_hive(GAMES_DIR, seasons), _read_hive(TEAMS_DIR, seasons))
    lines = _read_hive(LINES_DIR, seasons)
    snaps = _read_hive(SNAPS_DIR, [2021, 2022, 2023, 2024])
    if "event_time" in snaps.columns:
        snaps["event_time"] = pd.to_datetime(snaps["event_time"], utc=True)
    print("games", len(games), "lines", len(lines), "snaps", len(snaps))

    mkt = None
    if PRED_MKT.exists():
        mkt = pd.read_parquet(PRED_MKT)
        mkt = mkt.loc[~mkt["exclude_from_headline"]]

    picks = pick_fixture_game_ids(preds, fund=preds, mkt=mkt)
    print("picks", {k: len(v) for k, v in picks.items()})
    print(picks)

    # Ensure 24 unique if possible
    all_ids = []
    for k in ("cfbd_2019", "snapshot_closes", "fund_mkt_disagree"):
        for gid in picks[k]:
            if gid not in all_ids:
                all_ids.append(gid)
    print("unique fixtures", len(all_ids))

    rows = build_hand_rows(preds, games, lines, snaps, picks)
    print(f"\n=== HAND TABLE ({len(rows)} rows) ===")
    agree_n = sum(1 for r in rows if r.agree)
    print(f"agree={agree_n}/{len(rows)}")

    # print markdown table
    hdr = (
        "| bucket | season | week | game_id | matchup | score | book home close | "
        "pred_margin | pick | hand cover | hand hit | grader spread | grader cover | "
        "grader hit | agree |"
    )
    print(hdr)
    print("|---|---:|---:|---:|---|---|---:|---:|:---:|:---:|:---:|---:|:---:|:---:|:---:|")
    for r in rows:
        matchup = f"{r.away} @ {r.home}"
        score = f"{r.away_pts}-{r.home_pts}"
        hand_c = (
            "PUSH" if r.hand_home_covers is None else ("HOME" if r.hand_home_covers else "AWAY")
        )
        hand_h = (
            "—" if r.hand_model_ats_hit is None else ("HIT" if r.hand_model_ats_hit else "MISS")
        )
        grd_c = (
            "PUSH" if r.grader_home_covers is None else ("HOME" if r.grader_home_covers else "AWAY")
        )
        grd_h = (
            "—" if r.grader_model_ats_hit is None else ("HIT" if r.grader_model_ats_hit else "MISS")
        )
        pick = "HOME" if r.model_picked_home else "AWAY"
        book = f"{r.book_close_home_spread:.2f} ({r.book_close_source})"
        grd_sp = f"{r.grader_spread_close:.2f} ({r.grader_line_source})"
        print(
            f"| {r.bucket} | {r.season} | {r.week} | {r.game_id} | {matchup} | "
            f"{score} | {book} | {r.pred_margin:.2f} | {pick} | {hand_c} | {hand_h} | "
            f"{grd_sp} | {grd_c} | {grd_h} | {'YES' if r.agree else 'NO'} |"
        )

    # Corrected ATS using home-side snapshot closes for 2021-2024
    print("\n=== CORRECTED ATS (home-side snapshot close) sample estimate ===")
    snap_preds = preds.loc[preds["season"].between(2021, 2024)].copy()
    # merge home team
    ginfo = games[["game_id", "home_team", "event_time"]].drop_duplicates("game_id")
    merged = snap_preds.merge(ginfo, on="game_id", how="left")
    merged["event_time"] = pd.to_datetime(merged["event_time"], utc=True)

    # Vectorized-ish: for each game compute home-side close (may be slow; sample or full)
    corrected_spreads = []
    sources = []
    n_swaps = 0
    for row in merged.itertuples(index=False):
        sp, src, _n, meta = snapshot_home_close_spread(
            snaps,
            game_id=int(row.game_id),
            home_team=str(row.home_team),
            kickoff=pd.Timestamp(row.event_time),
        )
        if meta.get("listing_swap"):
            n_swaps += 1
        if not np.isfinite(sp):
            sp2, src2, _ = cfbd_home_close_spread(lines, int(row.game_id))
            sp, src = sp2, f"fallback_{src2}"
        corrected_spreads.append(sp)
        sources.append(src)
    merged["corrected_spread"] = corrected_spreads
    merged["corrected_src"] = sources

    cover = merged["realized_margin"].to_numpy(float) + merged["corrected_spread"].to_numpy(float)
    p = merged["p_ats_home"].to_numpy(float)
    # IMPORTANT: p_ats was computed against BUGGY spread≈0. Re-grading with corrected
    # spread but stored p_ats is still apples-to-oranges for probability, but for
    # hard-pick accuracy we should re-derive pick from pred_margin vs corrected line:
    # model picks home when pred_margin + spread > 0 (same as market edge).
    edge_pred = merged["pred_margin"].to_numpy(float) + merged["corrected_spread"].to_numpy(float)
    decided = np.isfinite(cover) & (np.abs(cover) > 1e-12) & np.isfinite(edge_pred)
    y = (cover[decided] > 0).astype(float)
    pred_side = (edge_pred[decided] > 0).astype(float)
    print(
        f"hard-pick ATS vs corrected home close (from pred_margin): "
        f"{np.mean(pred_side == y) * 100:.2f}% n={decided.sum()}"
    )
    # Also grade stored p_ats (computed at buggy line) vs corrected outcomes
    decided2 = np.isfinite(cover) & (np.abs(cover) > 1e-12) & np.isfinite(p)
    y2 = (cover[decided2] > 0).astype(float)
    pred2 = (p[decided2] >= 0.5).astype(float)
    print(
        f"stored p_ats_home (>=0.5) vs corrected outcomes: "
        f"{np.mean(pred2 == y2) * 100:.2f}% n={decided2.sum()}"
    )
    print("corrected spread abs median", float(np.nanmedian(np.abs(merged["corrected_spread"]))))
    print("corrected src counts", pd.Series(sources).value_counts().to_dict())
    print("listing_swap count", n_swaps)

    # A6 line sources
    a6_info: dict[str, Any] = {}
    if PRED_A6.exists():
        a6 = pd.read_parquet(PRED_A6)
        a6 = a6.loc[~a6["exclude_from_headline"] & a6["season"].between(2021, 2024)]
        a6_info = {
            "line_sources_close": {
                str(k): int(v) for k, v in a6["line_source_close"].value_counts().items()
            },
            "spread_median": float(a6["spread_close"].median()),
            "pct_abs_lt_0_5": float((a6["spread_close"].abs() < 0.5).mean()),
            "ats": list(recompute_ats(a6)),
            "note": (
                "market_feature_source=cfbd_open_close affects FEATURES only; "
                "spread_close grading still uses resolve_lines_for_games snapshot ladder"
            ),
        }
        print("\n=== A6 grading closes ===")
        print(json.dumps(a6_info, indent=2))

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "regime": regime,
        "picks": picks,
        "hand_rows": [asdict(r) for r in rows],
        "agree_rate": agree_n / max(len(rows), 1),
        "corrected_ats_from_pred_margin": float(np.mean(pred_side == y)),
        "corrected_ats_n": int(decided.sum()),
        "listing_swap_count": n_swaps,
        "a6": a6_info,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print("wrote", OUT_JSON)


if __name__ == "__main__":
    main()
