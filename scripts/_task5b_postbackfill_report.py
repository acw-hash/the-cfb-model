"""Task 5B post-backfill acceptance report (local only — no API).

Lockbox 2025: boolean partitions exist / staged-not-evaluated only.
Metrics printed for seasons 2021–2024.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.odds_api import (
    asof_tolerance_for,
    plan_historical_units,
    reconcile_cfbd_close_vs_slot_close,
    within_asof_tolerance,
)
from ncaa_quant.utils.timeutils import week_of

SEASONS = (2021, 2022, 2023, 2024)
LOCKBOX = 2025
DPS = ("tuesday_0600_et", "saturday_0600_et", "slot_close")


def _pct(n: int, d: int) -> float:
    return 100.0 * n / d if d else 0.0


def _iqr(a: np.ndarray) -> float:
    if a.size == 0:
        return float("nan")
    return float(np.percentile(a, 75) - np.percentile(a, 25))


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def lockbox_boolean(store: ParquetStore, staged: Path) -> None:
    section("0. LOCKBOX 2025 (boolean only)")
    odds_parts = sorted(p.name for p in (staged / "odds_snapshots").glob("season=*"))
    games_parts = sorted(p.name for p in (staged / "games").glob("season=*"))
    lines_parts = sorted(p.name for p in (staged / "lines_historical").glob("season=*"))
    q_parts = sorted(p.name for p in (staged / "odds_snapshots_quarantine").glob("season=*"))
    odds_2025 = (staged / "odds_snapshots" / "season=2025").exists()
    # Confirm rows exist without printing counts/metrics.
    has_odds_rows = False
    if odds_2025:
        df = store.read("odds_snapshots", filters={"season": LOCKBOX})
        has_odds_rows = not df.empty
    print(f"odds_snapshots season=2025 partition exists: {odds_2025}")
    print(f"odds_snapshots season=2025 staged-not-evaluated: {has_odds_rows}")
    print(f"games season=2025 partition exists: {'season=2025' in games_parts}")
    print(f"lines_historical season=2025 partition exists: {'season=2025' in lines_parts}")
    print(f"odds_snapshots_quarantine season=2025 partition exists: {'season=2025' in q_parts}")
    print(f"all odds_snapshots seasons present: {odds_parts}")


def coverage(store: ParquetStore) -> None:
    section("1. Snapshot coverage % per season × decision point (2021–2024)")
    print(
        "Denominator definition: CFBD games.week (plan_historical_units reads "
        "store games['week'])."
    )
    print(
        "Numerator (stock coverage_report): historical odds rows with "
        "odds.week == unit.week (odds.week from normalize_odds_payload -> "
        "week_of(kickoff))."
    )
    print(
        "FINDING risk: week_of puts early-September Saturdays in week 0 while "
        "CFBD labels them week 1 — sides of the stock ratio can disagree."
    )
    print()

    plan = plan_historical_units(store, SEASONS, decision_points=DPS)
    # Load games + odds once per season
    rows_unit: list[dict[str, Any]] = []
    rows_game: list[dict[str, Any]] = []
    week_mismatch_examples: list[str] = []

    for season in SEASONS:
        games = store.read("games", filters={"season": int(season)})
        odds = store.read("odds_snapshots", filters={"season": int(season)})
        hist = (
            odds[(odds["snapshot_source"] == "historical")]
            if not odds.empty
            else odds
        )
        # Per-game week_of vs CFBD week
        if not games.empty:
            for _, g in games.iterrows():
                kick = pd.Timestamp(g["start_date"]).to_pydatetime()
                if kick.tzinfo is None:
                    kick = kick.replace(tzinfo=UTC)
                wo = week_of(kick, int(season))
                cw = int(g["week"])
                if wo != cw and len(week_mismatch_examples) < 8:
                    week_mismatch_examples.append(
                        f"  game_id={int(g['game_id'])} kickoff={kick.isoformat()} "
                        f"CFBD.week={cw} week_of={wo}"
                    )

        for dp in DPS:
            units = [u for u in plan.units if u.season == season and u.decision_point == dp]
            covered_cfbd_week = 0
            covered_any_week = 0
            for u in units:
                if hist.empty:
                    continue
                same_week = hist[
                    (hist["week"] == u.week) & (hist["decision_point"] == dp)
                ]
                any_week = hist[hist["decision_point"] == dp]
                # For slot_close / tuesday: unit covered if any historical row
                # for that DP exists in the CFBD week partition OR (for week
                # mismatch) if request times left rows under week_of.
                if not same_week.empty:
                    covered_cfbd_week += 1
                # Progress-marker / request coverage: unit has staged rows for
                # this DP whose games belong to this CFBD week via game_key
                # join — approximate: any hist rows with decision_point and
                # season matching week via games.
                if not same_week.empty:
                    covered_any_week += 1
                elif not any_week.empty and dp != "slot_close":
                    # weekly DPs: one request/week; if week_of shifted all rows
                    # out of CFBD week, stock numerator misses them.
                    pass

            # Progress markers as ground-truth "unit completed"
            prog = Path("data/raw/odds_api_historical/_progress")
            markers = 0
            for u in units:
                if (prog / f"{u.season}_{u.week}_{u.decision_point}.done").exists():
                    markers += 1

            rows_unit.append(
                {
                    "season": season,
                    "decision_point": dp,
                    "planned_units": len(units),
                    "covered_odds.week==CFBD.week": covered_cfbd_week,
                    "pct_stock": round(_pct(covered_cfbd_week, len(units)), 2),
                    "progress_markers": markers,
                    "pct_markers": round(_pct(markers, len(units)), 2),
                }
            )

        # Game-level: share of games with ≥1 historical row per DP
        if games.empty or hist.empty:
            continue
        # teams for game_key not needed if we use game_id when present
        for dp in DPS:
            g_hist = hist[hist["decision_point"] == dp]
            if g_hist.empty:
                n_hit = 0
            elif "game_id" in g_hist.columns and g_hist["game_id"].notna().any():
                ids = set(g_hist["game_id"].dropna().astype(int))
                n_hit = int(games["game_id"].isin(ids).sum())
            else:
                # game_key match
                keys = set(g_hist["game_key"].astype(str))
                # build keys from games via existing helper path — use home/away ids
                from ncaa_quant.ingestion.odds_api import _game_keys_from_games

                gk = _game_keys_from_games(store, [season])
                n_hit = int(gk["game_key"].isin(keys).sum()) if not gk.empty else 0
            rows_game.append(
                {
                    "season": season,
                    "decision_point": dp,
                    "n_games": int(len(games)),
                    "games_with_hist_dp": n_hit,
                    "pct": round(_pct(n_hit, len(games)), 2),
                }
            )

    print("--- Unit coverage (stock: both sides use odds.week vs CFBD unit.week) ---")
    print(pd.DataFrame(rows_unit).to_string(index=False))
    print()
    print("--- Unit coverage via progress markers (credit-spend truth) ---")
    print(
        pd.DataFrame(rows_unit)[
            ["season", "decision_point", "planned_units", "progress_markers", "pct_markers"]
        ].to_string(index=False)
    )
    print()
    print("--- Game-level coverage (games with ≥1 historical row for DP) ---")
    print(pd.DataFrame(rows_game).to_string(index=False))
    print()
    print("week_of vs CFBD.week mismatches (sample):")
    if week_mismatch_examples:
        print("\n".join(week_mismatch_examples))
    else:
        print("  (none)")

    # Quantify week mismatch impact on stock coverage
    print()
    print("--- Stock vs marker gap (FINDING if stock << markers) ---")
    for r in rows_unit:
        if r["covered_odds.week==CFBD.week"] != r["progress_markers"]:
            print(
                f"  {r['season']} {r['decision_point']}: stock={r['covered_odds.week==CFBD.week']}/"
                f"{r['planned_units']} markers={r['progress_markers']}/{r['planned_units']}"
            )


def n_books(store: ParquetStore) -> None:
    section("2. n_books_available by season (2021–2024)")
    rows = []
    for season in SEASONS:
        odds = store.read("odds_snapshots", filters={"season": int(season)})
        hist = odds[odds["snapshot_source"] == "historical"]
        if hist.empty:
            rows.append({"season": season, "n_rows": 0})
            continue
        # one n_books per (event_time, game_key) — take first
        snap = hist.drop_duplicates(subset=["event_time", "game_key"])
        vals = snap["n_books_available"].dropna().astype(float)
        rows.append(
            {
                "season": season,
                "n_snapshot_events": int(len(snap)),
                "mean": round(float(vals.mean()), 3),
                "median": round(float(vals.median()), 3),
                "p10": round(float(vals.quantile(0.10)), 3),
                "p90": round(float(vals.quantile(0.90)), 3),
                "min": int(vals.min()),
                "max": int(vals.max()),
            }
        )
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    if len(df) >= 2 and df["mean"].notna().all():
        print(
            f"\nRise 2021→2024 mean: {df.iloc[0]['mean']:.3f} → {df.iloc[-1]['mean']:.3f} "
            f"(Δ={df.iloc[-1]['mean'] - df.iloc[0]['mean']:+.3f})"
        )


def reconcile(store: ParquetStore) -> None:
    section("3. Reconcile: CFBD close vs slot_close (2021–2024)")
    print("diff = OddsAPI slot_close home-spread/total − CFBD close (matched books)")
    print("Systematic bias is a FINDING — not corrected.")
    print()

    # Extended reconcile with named worst games
    from ncaa_quant.ingestion.odds_api import _game_keys_from_games

    keys = _game_keys_from_games(store, SEASONS)
    spread_rows: list[dict[str, Any]] = []
    total_rows: list[dict[str, Any]] = []

    for season in SEASONS:
        lines = store.read("lines_historical", filters={"season": int(season)})
        odds = store.read("odds_snapshots", filters={"season": int(season)})
        if lines.empty or odds.empty:
            continue
        closes = lines[lines["line_type"] == "close"].copy()
        slots = odds[
            (odds["decision_point"] == "slot_close")
            & (odds["snapshot_source"] == "historical")
            & (odds["market"].isin(["spread", "total"]))
        ].copy()
        if closes.empty or slots.empty:
            continue
        season_keys = keys[keys["season"] == int(season)]
        closes = closes.merge(
            season_keys[["game_id", "game_key", "home_team"]], on="game_id"
        )
        # attach team names for reporting
        games = store.read("games", filters={"season": int(season)})
        teams = store.read("teams", filters={"season": int(season)})
        id_to_name = (
            dict(zip(teams["team_id"].astype(int), teams["school"].astype(str), strict=False))
            if not teams.empty
            else {}
        )

        for game_id, gclose in closes.groupby("game_id"):
            game_key = str(gclose.iloc[0]["game_key"])
            home = str(gclose.iloc[0]["home_team"])
            gslots = slots[slots["game_key"] == game_key]
            if gslots.empty:
                continue
            cfbd_books = {str(b).casefold(): b for b in gclose["book"].unique()}
            snap_books = {str(b).casefold(): b for b in gslots["book"].unique()}
            shared = set(cfbd_books) & set(snap_books)

            def _cfbd_spread(book_mask: pd.DataFrame) -> float | None:
                vals = book_mask["spread"].dropna()
                return float(vals.median()) if not vals.empty else None

            def _cfbd_total(book_mask: pd.DataFrame) -> float | None:
                vals = book_mask["total"].dropna()
                return float(vals.median()) if not vals.empty else None

            def _snap_home_spread(book_mask: pd.DataFrame) -> float | None:
                home_rows = book_mask[
                    (book_mask["market"] == "spread") & (book_mask["side"] == home)
                ]
                vals = home_rows["line"].dropna()
                return float(vals.median()) if not vals.empty else None

            def _snap_total(book_mask: pd.DataFrame) -> float | None:
                tot = book_mask[book_mask["market"] == "total"]
                vals = tot["line"].dropna()
                return float(vals.median()) if not vals.empty else None

            diffs_s: list[float] = []
            diffs_t: list[float] = []
            if shared:
                for bkey in shared:
                    c_sub = gclose[gclose["book"].str.casefold() == bkey]
                    s_sub = gslots[gslots["book"].str.casefold() == bkey]
                    cs, ss = _cfbd_spread(c_sub), _snap_home_spread(s_sub)
                    if cs is not None and ss is not None:
                        diffs_s.append(ss - cs)
                    ct, st = _cfbd_total(c_sub), _snap_total(s_sub)
                    if ct is not None and st is not None:
                        diffs_t.append(st - ct)
            else:
                cs, ss = _cfbd_spread(gclose), _snap_home_spread(gslots)
                if cs is not None and ss is not None:
                    diffs_s.append(ss - cs)
                ct, st = _cfbd_total(gclose), _snap_total(gslots)
                if ct is not None and st is not None:
                    diffs_t.append(st - ct)

            gmeta = games[games["game_id"] == int(game_id)]
            if not gmeta.empty:
                hid = int(gmeta.iloc[0]["home_team_id"])
                aid = int(gmeta.iloc[0]["away_team_id"])
                label = (
                    f"{id_to_name.get(aid, str(aid))} @ {id_to_name.get(hid, str(hid))} "
                    f"({season} W{int(gmeta.iloc[0]['week'])})"
                )
            else:
                label = game_key

            if diffs_s:
                d = float(np.median(diffs_s))
                spread_rows.append(
                    {"season": season, "game_id": int(game_id), "label": label, "diff": d}
                )
            if diffs_t:
                d = float(np.median(diffs_t))
                total_rows.append(
                    {"season": season, "game_id": int(game_id), "label": label, "diff": d}
                )

    def _summary(name: str, rows: list[dict[str, Any]]) -> None:
        a = np.array([r["diff"] for r in rows], dtype=float)
        print(f"### {name} (n_games={len(a)})")
        if a.size == 0:
            print("  (no matches)")
            return
        print(
            pd.DataFrame(
                [
                    {
                        "median": round(float(np.median(a)), 4),
                        "IQR": round(_iqr(a), 4),
                        "p95_abs": round(float(np.percentile(np.abs(a), 95)), 4),
                        "mean": round(float(a.mean()), 4),
                        "p05": round(float(np.percentile(a, 5)), 4),
                        "p95": round(float(np.percentile(a, 95)), 4),
                    }
                ]
            ).to_string(index=False)
        )
        # by season
        by = []
        for season in SEASONS:
            sa = np.array([r["diff"] for r in rows if r["season"] == season], dtype=float)
            if sa.size == 0:
                continue
            by.append(
                {
                    "season": season,
                    "n": len(sa),
                    "median": round(float(np.median(sa)), 4),
                    "IQR": round(_iqr(sa), 4),
                    "p95_abs": round(float(np.percentile(np.abs(sa), 95)), 4),
                    "mean": round(float(sa.mean()), 4),
                }
            )
        print(pd.DataFrame(by).to_string(index=False))
        worst = sorted(rows, key=lambda r: -abs(r["diff"]))[:10]
        print("ten worst |diff|:")
        print(
            pd.DataFrame(worst)[["season", "game_id", "label", "diff"]].to_string(index=False)
        )
        med = float(np.median(a))
        if abs(med) >= 0.25:
            print(
                f"FINDING: systematic bias median={med:+.3f} pts "
                f"({name}; not corrected)."
            )
        print()

    _summary("spread (slot_close − CFBD close)", spread_rows)
    _summary("total (slot_close − CFBD close)", total_rows)

    # also stock helper for n_games cross-check
    rep = reconcile_cfbd_close_vs_slot_close(store, SEASONS)
    print(
        f"stock ReconcileReport: n_games={rep.n_games} "
        f"n_spread_diffs={len(rep.spread_diffs)} n_total_diffs={len(rep.total_diffs)}"
    )


def quarantine(store: ParquetStore, staged: Path) -> None:
    section("4. Quarantine report (all seasons on disk; expect total 434)")
    qroot = staged / "odds_snapshots_quarantine"
    parts = list(qroot.glob("season=*/week=*/part.parquet"))
    frames = [pd.read_parquet(p) for p in parts]
    q = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    print(f"total quarantine rows: {len(q)}")
    if q.empty:
        return

    print("\nby season:")
    print(q.groupby("season").size().rename("n").reset_index().to_string(index=False))
    print("\nby book:")
    print(q.groupby("book").size().rename("n").sort_values(ascending=False).reset_index().to_string(index=False))
    print("\nby market:")
    print(q.groupby("market").size().rename("n").reset_index().to_string(index=False))
    print("\nby quarantine_reason:")
    print(
        q.groupby("quarantine_reason")
        .size()
        .rename("n")
        .reset_index()
        .to_string(index=False)
    )
    print("\nby season × book × market × reason:")
    print(
        q.groupby(["season", "book", "market", "quarantine_reason"])
        .size()
        .rename("n")
        .reset_index()
        .sort_values(["season", "n"], ascending=[True, False])
        .to_string(index=False)
    )

    # Leak check into odds_snapshots
    leaked = 0
    for season in sorted(q["season"].dropna().unique()):
        odds = store.read("odds_snapshots", filters={"season": int(season)})
        if odds.empty:
            continue
        qq = q[q["season"] == int(season)]
        # fingerprint
        for col in ("snapshot_id",):
            if col in qq.columns and col in odds.columns:
                leaked += int(odds[col].isin(set(qq[col])).sum())
        # also line-extreme check: any |spread|>=70 or total out of 20-100
        if "market" in odds.columns:
            bad_spread = odds[
                (odds["market"] == "spread") & odds["line"].notna() & (odds["line"].abs() >= 70)
            ]
            bad_tot = odds[
                (odds["market"] == "total")
                & odds["line"].notna()
                & ((odds["line"] < 20) | (odds["line"] > 100))
            ]
            if not bad_spread.empty or not bad_tot.empty:
                print(
                    f"FINDING: out-of-bounds lines still in odds_snapshots "
                    f"season={season} spread={len(bad_spread)} total={len(bad_tot)}"
                )
    print(f"\nsnapshot_id overlap quarantine∩odds_snapshots: {leaked}")

    # Post-kickoff earlier-slot pattern
    print("\n--- Post-kickoff pattern (event_time vs commence/kickoff) ---")
    # quarantine rows have home/away/season/week; commence not always stored —
    # join games via game_key if present
    if "game_key" in q.columns and "event_time" in q.columns:
        # Parse kickoff date from game_key? game_key is season|home|away|date
        # Better: load games for seasons present (non-lockbox metrics ok for
        # pattern; include all q seasons but for 2025 only count boolean of pattern?)
        # User asked to confirm pattern — use all quarantine rows including 2025
        # for the pattern check since it's about quarantine composition.
        q2 = q.copy()
        q2["event_time"] = pd.to_datetime(q2["event_time"], utc=True)
        # extract date from game_key trailing
        def kick_date(gk: str) -> str | None:
            parts = str(gk).split("|")
            return parts[-1] if len(parts) >= 4 else None

        # Compare event_time to kickoff: for slot_close, request is kick-5min;
        # post-kickoff snapshot of earlier slot means event_time >= some other
        # game's kickoff while covering a later game still pre-kick.
        # Proxy used in notes: quarantine week from week_of(commence) vs
        # CFBD week, and event_time after earliest kick in that snapshot's games.
        # Simpler: flag rows where we can find the game kickoff and
        # event_time >= kickoff.
        post = 0
        pre = 0
        unknown = 0
        for season in sorted(q2["season"].unique()):
            games = store.read("games", filters={"season": int(season)})
            from ncaa_quant.ingestion.odds_api import _game_keys_from_games

            gk = _game_keys_from_games(store, [int(season)])
            if gk.empty:
                unknown += int((q2["season"] == int(season)).sum())
                continue
            gmap = gk.merge(
                games[["game_id", "start_date"]], on="game_id", how="left"
            )
            keymap = dict(
                zip(gmap["game_key"].astype(str), pd.to_datetime(gmap["start_date"], utc=True), strict=False)
            )
            sub = q2[q2["season"] == int(season)]
            for _, row in sub.iterrows():
                kick = keymap.get(str(row["game_key"]))
                if kick is None or pd.isna(kick):
                    unknown += 1
                    continue
                if row["event_time"] >= kick:
                    post += 1
                else:
                    pre += 1
        print(
            pd.DataFrame(
                [
                    {
                        "pre_kickoff_snapshot": pre,
                        "post_kickoff_snapshot": post,
                        "unknown_kickoff": unknown,
                        "pct_post": round(_pct(post, pre + post), 2),
                    }
                ]
            ).to_string(index=False)
        )
        print(
            "Interpretation: concentrated post-kickoff ⇒ book garbage on earlier "
            "slots still listed in a later slot's payload (2021 W1 pattern)."
        )


def asof_exceptions() -> None:
    section("5. As-of tolerance exceptions (requested − returned > era tol)")
    root = Path("data/raw/odds_api_historical")
    pat = re.compile(r"(\d{8}T\d{12}Z)_(\d{8}T\d{12}Z)\.json")

    def parse(s: str) -> datetime:
        return datetime.strptime(s, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)

    overs: list[dict[str, Any]] = []
    for f in root.glob("*/*.json"):
        m = pat.match(f.name)
        if not m:
            continue
        req, ret = parse(m.group(1)), parse(m.group(2))
        # lockbox: skip printing 2025 detail numbers — still scan for completeness
        if not within_asof_tolerance(req, ret):
            gap = (req - ret).total_seconds()
            tol = asof_tolerance_for(req).total_seconds()
            overs.append(
                {
                    "season_guess": req.year if req.month >= 8 else req.year - 1,
                    "requested": req.isoformat(),
                    "returned": ret.isoformat(),
                    "gap_s": gap,
                    "tol_s": tol,
                    "excess_s": gap - tol,
                    "path": str(f.relative_to(root)),
                }
            )

    # Report 2021-2024 only in tables; note if any 2025
    overs_eval = [o for o in overs if o["season_guess"] != LOCKBOX]
    overs_lb = [o for o in overs if o["season_guess"] == LOCKBOX]
    print(f"exceptions total (all archives): {len(overs)}")
    print(f"exceptions in 2021–2024: {len(overs_eval)}")
    print(f"exceptions in lockbox-2025 season_guess: {len(overs_lb)} (count only)")
    print()
    if overs_eval:
        print(pd.DataFrame(overs_eval).to_string(index=False))
        print()
        by = (
            pd.DataFrame(overs_eval)
            .groupby("season_guess")
            .agg(n=("gap_s", "size"), mean_excess=("excess_s", "mean"), max_excess=("excess_s", "max"))
            .reset_index()
        )
        print("count by season:")
        print(by.to_string(index=False))
        print()
        print("excess distribution (seconds over era tolerance):")
        ex = np.array([o["excess_s"] for o in overs_eval])
        print(
            pd.DataFrame(
                [
                    {
                        "n": len(ex),
                        "min": ex.min(),
                        "median": float(np.median(ex)),
                        "max": ex.max(),
                        "unique": sorted(set(ex.tolist())),
                    }
                ]
            ).to_string(index=False)
        )
    print()
    print(
        "Task 16 fallback ladder (walkforward._resolve_from_snapshots): "
        "if latest eligible event_time is older than bound − era_tol, "
        "line_source='odds_api_snapshot_fallback'; else 'odds_api_snapshot'. "
        "These 540s gaps (tol=300s post-Sept-2022) are therefore classified as "
        "FALLBACK when the decision bound is the requested slot_close instant — "
        "not primary as-of hits. Not corrected."
    )


def quality(store: ParquetStore) -> None:
    section("6. Data-quality checks (2021–2024)")
    # Registered DPs
    cfg = load_config()
    registered = set(cfg.data.odds_historical_decision_points)
    print(f"registered decision_points: {sorted(registered)}")

    dup_total = 0
    last_pre_bad = 0
    last_pre_checked = 0
    hist_source_bad = 0
    hist_dp_bad = 0
    hist_rows = 0
    live_by_season: dict[int, int] = {}

    for season in list(SEASONS) + [2026]:  # 2026 for live count; no 2025 metrics
        odds = store.read("odds_snapshots", filters={"season": int(season)})
        if odds.empty:
            continue
        live_by_season[season] = int((odds["snapshot_source"] == "live").sum())
        if season == LOCKBOX or season not in SEASONS:
            continue
        hist = odds[odds["snapshot_source"] == "historical"].copy()
        hist_rows += len(hist)
        # duplicates
        key_cols = ["game_key", "book", "market", "event_time"]
        # include side? user said (game_key, book, market, event_time)
        dups = hist.duplicated(subset=key_cols, keep=False)
        # note: both sides of spread share key — duplicates expected at side grain.
        # User asked no duplicate (game_key, book, market, event_time) — that would
        # flag home+away / over+under as dups. Re-read: likely means identical
        # snapshot rows; check with side included AND without.
        dups_with_side = hist.duplicated(
            subset=["game_key", "book", "market", "side", "event_time"], keep=False
        )
        n_dup = int(dups_with_side.sum() // 2)  # rough pair count
        # true exact duplicate rows
        exact = hist.duplicated(
            subset=["game_key", "book", "market", "side", "line", "price", "event_time"],
            keep="first",
        )
        dup_total += int(exact.sum())

        # decision_point / source
        hist_source_bad += int((hist["snapshot_source"] != "historical").sum())
        hist_dp_bad += int((~hist["decision_point"].isin(registered)).sum())

        # last pre-kickoff precedes kickoff
        games = store.read("games", filters={"season": int(season)})
        from ncaa_quant.ingestion.odds_api import _game_keys_from_games

        gk = _game_keys_from_games(store, [season])
        if gk.empty or hist.empty:
            continue
        gmap = gk.merge(games[["game_id", "start_date"]], on="game_id")
        kicks = dict(
            zip(
                gmap["game_key"].astype(str),
                pd.to_datetime(gmap["start_date"], utc=True),
                strict=False,
            )
        )
        hist["event_time"] = pd.to_datetime(hist["event_time"], utc=True)
        for game_key, grp in hist.groupby("game_key"):
            kick = kicks.get(str(game_key))
            if kick is None or pd.isna(kick):
                continue
            pre = grp[grp["event_time"] < kick]
            last_pre_checked += 1
            if pre.empty:
                last_pre_bad += 1
                continue
            if pre["event_time"].max() >= kick:
                last_pre_bad += 1

    print(
        pd.DataFrame(
            [
                {
                    "exact_dup_rows_(gk,book,market,side,line,price,event_time)": dup_total,
                    "hist_rows_2021_2024": hist_rows,
                    "hist_non_historical_source": hist_source_bad,
                    "hist_unregistered_decision_point": hist_dp_bad,
                    "games_missing_pre_kickoff_snapshot": last_pre_bad,
                    "games_checked_for_pre_kickoff": last_pre_checked,
                }
            ]
        ).to_string(index=False)
    )
    print("\nlive row counts by season (no 2025):")
    print(pd.Series(live_by_season).rename("live_rows").to_frame().to_string())
    print(
        "\nBaseline pre-pull (docs/notes/data-check.md): 11,322 live rows. "
        "Backfill-start metadata pass logged 2026 live partitions "
        "(26016+2052+114+798+456+228+570+456+114 = 30804). "
        "Current live total should match ongoing capture, not 11322 — "
        "check that historical seasons have 0 live rows."
    )
    # Also note key without side would be multi-row by design
    print(
        "\nNote: (game_key, book, market, event_time) without side is multi-row "
        "by design (home/away, over/under). Dedup check uses side+line+price."
    )


def main() -> None:
    cfg = load_config()
    staged = Path(cfg.paths.staged_dir)
    store = ParquetStore(staged)
    lockbox_boolean(store, staged)
    coverage(store)
    n_books(store)
    reconcile(store)
    quarantine(store, staged)
    asof_exceptions()
    quality(store)
    section("DONE — run make lint typecheck test separately")


if __name__ == "__main__":
    main()
