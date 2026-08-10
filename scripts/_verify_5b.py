"""TASK 5B-VERIFY — read-only validation of historical odds backfill (no API).

Lockbox 2025: hygiene only (progress markers, quarantine counts, partition
existence). Evaluative metrics (coverage %, n_books, reconcile, crosswalk
rates, timestamp sampling) printed for 2021–2024.
"""

from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.odds_api import (
    _DEDUPE_COLS,
    _find_historical_slot_archive,
    _is_empty_historical_slot,
    coverage_report,
    dedupe_snapshots,
    is_unit_complete,
    parse_historical_envelope,
    plan_historical_units,
    reconcile_cfbd_close_vs_slot_close,
)
from ncaa_quant.quality.validators import CFBD_SLOT_CLOSE_TOLERANCE

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "odds_api_historical"
STAGED = ROOT / "data" / "staged"
SEASONS_EVAL = (2021, 2022, 2023, 2024)
SEASONS_ALL = (2021, 2022, 2023, 2024, 2025)
LOCKBOX = 2025
DPS = ("tuesday_0600_et", "saturday_0600_et", "slot_close")
ARCHIVE_NAME_RE = re.compile(
    r"^(?P<req>\d{8}T\d{12}Z)_(?P<ret>\d{8}T\d{12}Z)\.json$"
)


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _pct(n: int, d: int) -> float:
    return 100.0 * n / d if d else 0.0


def _parse_stamp(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)


def deliverable_1(store: ParquetStore, cfg: Any) -> dict[str, Any]:
    section("D1 - COVERAGE + SILENT GAPS + QUARANTINE")
    plan = plan_historical_units(store, SEASONS_ALL, decision_points=DPS, config=cfg)
    rows: list[dict[str, Any]] = []
    silent_gaps: list[str] = []

    # Preload historical event_time sets per season for gap checks
    hist_keys: dict[int, set[tuple[str, pd.Timestamp]]] = {}
    for season in SEASONS_ALL:
        odds = store.read("odds_snapshots", filters={"season": int(season)})
        if odds.empty:
            hist_keys[season] = set()
            continue
        hist = odds[odds["snapshot_source"] == "historical"]
        et = pd.to_datetime(hist["event_time"], utc=True)
        keys: set[tuple[str, pd.Timestamp]] = set()
        for dp, t in zip(hist["decision_point"].astype(str), et, strict=False):
            keys.add((dp, pd.Timestamp(t)))
        hist_keys[season] = keys

    for season in SEASONS_ALL:
        for dp in DPS:
            units = [u for u in plan.units if u.season == season and u.decision_point == dp]
            expected = len(units)
            completed = sum(
                1
                for u in units
                if is_unit_complete(RAW, u.season, u.week, u.decision_point)
            )
            # Silent-gap scan on completed units only
            unit_silent = 0
            for u in units:
                if not is_unit_complete(RAW, u.season, u.week, u.decision_point):
                    continue
                # Unit OK if ANY request has staged rows OR every request is empty/staged
                bad_reqs: list[str] = []
                for req in u.request_times:
                    if _is_empty_historical_slot(RAW, req):
                        continue
                    archive = _find_historical_slot_archive(RAW, req)
                    if archive is None:
                        bad_reqs.append(f"no_archive@{req.isoformat()}")
                        continue
                    m = ARCHIVE_NAME_RE.match(archive.name)
                    if m is None:
                        bad_reqs.append(f"bad_archive_name@{archive.name}")
                        continue
                    ret = pd.Timestamp(_parse_stamp(m.group("ret")))
                    if (dp, ret) in hist_keys.get(season, set()):
                        continue
                    # Also accept rows under any week in season (week_of shift)
                    # Already keyed by (dp, event_time) across season — miss = gap
                    bad_reqs.append(
                        f"no_rows_or_empty@{req.isoformat()} ret={ret.isoformat()} "
                        f"archive={archive.name}"
                    )
                if bad_reqs:
                    unit_silent += 1
                    silent_gaps.append(
                        f"{season} w{u.week} {dp}: " + "; ".join(bad_reqs[:3])
                        + (f" (+{len(bad_reqs) - 3} more)" if len(bad_reqs) > 3 else "")
                    )
            rows.append(
                {
                    "season": season,
                    "decision_point": dp,
                    "expected_units": expected,
                    "completed_markers": completed,
                    "marker_pct": round(_pct(completed, expected), 2),
                    "silent_gap_units": unit_silent,
                    "lockbox": season == LOCKBOX,
                }
            )

    cov_df = pd.DataFrame(rows)
    print("--- expected vs completed (progress markers) ---")
    print(cov_df.to_string(index=False))
    print()
    print("--- stock coverage_report (2021–2024 only; lockbox excluded) ---")
    for line in coverage_report(store, SEASONS_EVAL, decision_points=DPS, config=cfg):
        print(line)
    print()
    print(f"silent gap units: {len(silent_gaps)}")
    if silent_gaps:
        for g in silent_gaps[:50]:
            print(f"  GAP: {g}")
        if len(silent_gaps) > 50:
            print(f"  ... +{len(silent_gaps) - 50} more")
    else:
        print("  none - every completed unit has staged rows or _empty_slots")

    # Quarantine
    q_frames: list[pd.DataFrame] = []
    q_root = STAGED / "odds_snapshots_quarantine"
    for season_dir in sorted(q_root.glob("season=*")):
        for part in season_dir.rglob("*.parquet"):
            q_frames.append(pd.read_parquet(part))
    if q_frames:
        q = pd.concat(q_frames, ignore_index=True)
    else:
        q = pd.DataFrame()
    print()
    print(f"quarantine total rows: {len(q)}")
    if not q.empty:
        print("by season:")
        print(q.groupby("season").size().to_string())
        print("top quarantine_reason:")
        print(q["quarantine_reason"].value_counts().to_string())
        if "book" in q.columns:
            print("top books:")
            print(q["book"].value_counts().head(8).to_string())
        if "market" in q.columns:
            print("by market:")
            print(q["market"].value_counts().to_string())

    return {
        "coverage": cov_df,
        "silent_gaps": silent_gaps,
        "quarantine_n": int(len(q)),
        "quarantine_reasons": (
            q["quarantine_reason"].value_counts().to_dict() if not q.empty else {}
        ),
    }


def deliverable_2(store: ParquetStore) -> dict[str, Any]:
    section("D2 - TIMESTAMP DISCIPLINE (real archives, >=20 rows)")
    # Sample archives across seasons 2021–2024
    archives: list[Path] = []
    for day_dir in sorted(RAW.iterdir()):
        if not day_dir.is_dir() or day_dir.name.startswith("_"):
            continue
        year = int(day_dir.name[:4]) if day_dir.name[:4].isdigit() else 0
        if year < 2021 or year > 2024:
            continue
        for p in day_dir.glob("*.json"):
            if ARCHIVE_NAME_RE.match(p.name):
                archives.append(p)
    rng = random.Random(42)
    # Stratify roughly by year
    by_year: dict[int, list[Path]] = defaultdict(list)
    for p in archives:
        by_year[int(p.name[:4])].append(p)
    sample: list[Path] = []
    per = max(5, 20 // max(len(by_year), 1))
    for year in sorted(by_year):
        pool = by_year[year]
        k = min(per, len(pool))
        sample.extend(rng.sample(pool, k))
    if len(sample) < 20:
        remaining = [p for p in archives if p not in sample]
        sample.extend(rng.sample(remaining, min(20 - len(sample), len(remaining))))
    sample = sample[: max(20, len(sample))]

    # Index historical event_times → row counts (2021–2024)
    et_counts: Counter[pd.Timestamp] = Counter()
    for season in SEASONS_EVAL:
        odds = store.read("odds_snapshots", filters={"season": int(season)})
        if odds.empty:
            continue
        hist = odds[odds["snapshot_source"] == "historical"]
        for t in pd.to_datetime(hist["event_time"], utc=True):
            et_counts[pd.Timestamp(t)] += 1

    checks: list[dict[str, Any]] = []
    gaps: list[float] = []
    for path in sample:
        m = ARCHIVE_NAME_RE.match(path.name)
        assert m is not None
        req = _parse_stamp(m.group("req"))
        ret_from_name = _parse_stamp(m.group("ret"))
        env = parse_historical_envelope(path.read_bytes(), requested_at=req)
        ret = env.timestamp
        gap_s = (req - ret).total_seconds()
        gaps.append(gap_s)
        n_at_returned = int(et_counts.get(pd.Timestamp(ret), 0))
        # Filename returned stamp must match envelope timestamp.
        ok_name = abs((ret - ret_from_name).total_seconds()) < 1.0
        # Staged rows (if any) live at returned, not requested, when they differ.
        if env.data:
            discipline_ok = ok_name and n_at_returned > 0
            if gap_s > 0.5:
                # Prove we did not stamp event_time = requested for this slot:
                # row count at returned must be positive; gap itself proves
                # returned ≠ requested.
                discipline_ok = discipline_ok and abs(gap_s) > 0.5
        else:
            discipline_ok = ok_name  # empty envelope — no staged rows expected
        checks.append(
            {
                "archive": path.name,
                "requested": req.isoformat(),
                "envelope_returned": ret.isoformat(),
                "filename_returned": ret_from_name.isoformat(),
                "gap_s": gap_s,
                "n_staged_at_returned": n_at_returned,
                "envelope_n_events": len(env.data),
                "ok": discipline_ok,
            }
        )

    cdf = pd.DataFrame(checks)
    print(cdf.to_string(index=False))
    max_gap = max(gaps) if gaps else float("nan")
    print()
    print(f"sampled={len(checks)} all_ok={bool(cdf['ok'].all())} max_(requested-returned)_s={max_gap}")
    # Also scan ALL archives for max gap
    all_gaps: list[float] = []
    for p in archives:
        m = ARCHIVE_NAME_RE.match(p.name)
        if m is None:
            continue
        req = _parse_stamp(m.group("req"))
        ret = _parse_stamp(m.group("ret"))
        all_gaps.append((req - ret).total_seconds())
    print(
        f"all_archives_2021_2024={len(all_gaps)} "
        f"max_gap_s={max(all_gaps) if all_gaps else float('nan')} "
        f"median_gap_s={float(np.median(all_gaps)) if all_gaps else float('nan')}"
    )
    return {
        "n_sampled": len(checks),
        "all_ok": bool(cdf["ok"].all()),
        "max_gap_sample": max_gap,
        "max_gap_all": max(all_gaps) if all_gaps else float("nan"),
        "checks": checks,
    }


def deliverable_3(store: ParquetStore) -> pd.DataFrame:
    section("D3 - n_books_available BY SEASON (2021-2024)")
    rows = []
    for season in SEASONS_EVAL:
        odds = store.read("odds_snapshots", filters={"season": int(season)})
        hist = odds[odds["snapshot_source"] == "historical"] if not odds.empty else odds
        if hist.empty:
            rows.append({"season": season, "n_rows": 0, "null_n_books": 0})
            continue
        null_n = int(hist["n_books_available"].isna().sum())
        snap = hist.drop_duplicates(subset=["event_time", "game_key"])
        vals = snap["n_books_available"].dropna().astype(float)
        rows.append(
            {
                "season": season,
                "n_rows": int(len(hist)),
                "n_snapshot_events": int(len(snap)),
                "null_n_books": null_n,
                "min": int(vals.min()),
                "median": float(vals.median()),
                "max": int(vals.max()),
                "mean": round(float(vals.mean()), 3),
            }
        )
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print()
    print(
        "null n_books_available on historical rows: "
        f"{int(df['null_n_books'].sum())} (must be 0)"
    )
    if len(df) >= 2:
        print(
            f"pattern check: 2021 median={df.iloc[0]['median']} → "
            f"2024 median={df.iloc[-1]['median']}; "
            f"2021 mean={df.iloc[0]['mean']} → 2024 mean={df.iloc[-1]['mean']}"
        )
    return df


def deliverable_4(store: ParquetStore) -> dict[str, Any]:
    section(
        f"D4 - RECONCILE CFBD close vs slot_close "
        f"(tolerance={CFBD_SLOT_CLOSE_TOLERANCE}; uncorrected)"
    )
    out: dict[str, Any] = {"tolerance": CFBD_SLOT_CLOSE_TOLERANCE, "seasons": {}}
    for season in SEASONS_EVAL:
        report = reconcile_cfbd_close_vs_slot_close(store, [season])
        s = pd.Series(report.spread_diffs, dtype="float64")
        t = pd.Series(report.total_diffs, dtype="float64")

        def _stats(series: pd.Series) -> dict[str, float]:
            if series.empty:
                return {
                    "n": 0,
                    "mean": float("nan"),
                    "median": float("nan"),
                    "p95": float("nan"),
                    "share_beyond": float("nan"),
                }
            abs_s = series.abs()
            return {
                "n": int(len(series)),
                "mean": float(series.mean()),
                "median": float(series.median()),
                "p95": float(abs_s.quantile(0.95)),
                "share_beyond": float((abs_s > CFBD_SLOT_CLOSE_TOLERANCE).mean()),
            }

        sd = _stats(s)
        td = _stats(t)
        out["seasons"][season] = {"spread": sd, "total": td, "n_games": report.n_games}
        print(f"season {season} n_games={report.n_games}")
        print(
            f"  spread: n={sd['n']} mean={sd['mean']:.4f} median={sd['median']:.4f} "
            f"p95_|diff|={sd['p95']:.4f} share>|tol|={sd['share_beyond']:.4f}"
        )
        print(
            f"  total:  n={td['n']} mean={td['mean']:.4f} median={td['median']:.4f} "
            f"p95_|diff|={td['p95']:.4f} share>|tol|={td['share_beyond']:.4f}"
        )
    # Overall
    overall = reconcile_cfbd_close_vs_slot_close(store, list(SEASONS_EVAL))
    for line in overall.summary_lines():
        print(line)
    return out


def deliverable_5(store: ParquetStore) -> dict[str, Any]:
    section("D5 - CROSSWALK MATCH RATE (2021-2024)")
    # Classify unmatched against CFBD games + teams classification
    teams = store.read("teams")
    fbs_ids: set[int] = set()
    if not teams.empty and "classification" in teams.columns:
        fbs = teams[teams["classification"].astype(str).str.lower() == "fbs"]
        fbs_ids = set(int(x) for x in fbs["team_id"].dropna())

    out: dict[str, Any] = {}
    for season in SEASONS_EVAL:
        cw = store.read("odds_cfbd_game_crosswalk", filters={"season": int(season)})
        games = store.read("games", filters={"season": int(season)})
        if cw.empty:
            print(f"season {season}: NO crosswalk rows")
            out[season] = {"n": 0}
            continue
        # Unique odds events
        ev = cw.drop_duplicates(subset=["odds_event_id"])
        status = ev["match_status"].value_counts().to_dict()
        matched = ev[ev["match_status"] == "matched"]
        unmatched = ev[ev["match_status"] != "matched"]
        n = len(ev)
        n_matched = len(matched)
        n_unmatched = len(unmatched)

        # Build CFBD school-name set for name-miss detection
        teams_s = store.read("teams", filters={"season": int(season)})
        id_to_school: dict[int, str] = {}
        if not teams_s.empty:
            id_to_school = {
                int(r.team_id): str(r.school) for r in teams_s.itertuples(index=False)
            }
        cfbd_names: set[str] = set()
        fbs_game_ids: set[int] = set()
        game_index: dict[int, Any] = {}
        if not games.empty:
            for g in games.itertuples(index=False):
                game_index[int(g.game_id)] = g
                home = id_to_school.get(int(g.home_team_id))
                away = id_to_school.get(int(g.away_team_id))
                if home:
                    cfbd_names.add(home)
                if away:
                    cfbd_names.add(away)
                if fbs_ids and int(g.home_team_id) in fbs_ids and int(g.away_team_id) in fbs_ids:
                    fbs_game_ids.add(int(g.game_id))

        unmatched_list: list[dict[str, Any]] = []
        reasons: list[dict[str, Any]] = []
        for _, row in unmatched.iterrows():
            home = str(row["home_team"])
            away = str(row["away_team"])
            status_s = str(row["match_status"])
            delta = row.get("kickoff_delta_hours")
            detail = ""
            if status_s == "quarantined":
                reason = "ambiguous_window"
                detail = f"delta_h={delta}"
            elif home not in cfbd_names or away not in cfbd_names:
                missing = []
                if home not in cfbd_names:
                    missing.append(f"home={home}")
                if away not in cfbd_names:
                    missing.append(f"away={away}")
                reason = "name_normalization_miss"
                detail = ",".join(missing)
            elif pd.notna(delta) and float(delta) > 36.0:
                reason = "kickoff_outside_36h"
                detail = f"delta_h={delta}"
            else:
                reason = "no_cfbd_pair_within_tol"
                detail = f"delta_h={delta}"
            unmatched_list.append(
                {
                    "odds_event_id": str(row["odds_event_id"]),
                    "home": home,
                    "away": away,
                    "kickoff": str(row.get("kickoff")),
                    "reason": reason,
                    "detail": detail,
                    "match_status": status_s,
                }
            )
            reasons.append({"reason": reason})

        reason_counts = Counter(r["reason"] for r in reasons)

        matched_gids = set(int(x) for x in matched["game_id"].dropna())
        fbs_unmatched_games = sorted(fbs_game_ids - matched_gids)
        fbs_labels = []
        for gid in fbs_unmatched_games[:30]:
            g = game_index.get(gid)
            if g is None:
                fbs_labels.append(str(gid))
            else:
                home = id_to_school.get(int(g.home_team_id), "?")
                away = id_to_school.get(int(g.away_team_id), "?")
                fbs_labels.append(f"{gid} {away} @ {home} week={g.week}")

        print(
            f"season {season}: events={n} matched={n_matched} "
            f"({_pct(n_matched, n):.1f}%) unmatched={n_unmatched} "
            f"status={status}"
        )
        print(f"  unmatched reasons: {dict(reason_counts)}")
        print(
            f"  CFBD FBS–FBS games with no matched odds event: "
            f"{len(fbs_unmatched_games)}"
        )
        if fbs_unmatched_games:
            for lab in fbs_labels[:15]:
                print(f"    FINDING FBS unmatched: {lab}")
            if len(fbs_unmatched_games) > 15:
                print(f"    ... +{len(fbs_unmatched_games) - 15} more")
        # Print unmatched event sample
        for u in unmatched_list[:12]:
            print(
                f"    unmatched event: {u['away']} @ {u['home']} "
                f"reason={u['reason']} {u['detail']}"
            )
        if len(unmatched_list) > 12:
            print(f"    ... +{len(unmatched_list) - 12} unmatched events")

        out[season] = {
            "n_events": n,
            "n_matched": n_matched,
            "n_unmatched": n_unmatched,
            "match_pct": _pct(n_matched, n),
            "status": status,
            "reason_counts": dict(reason_counts),
            "fbs_unmatched_n": len(fbs_unmatched_games),
            "fbs_unmatched_sample": fbs_labels,
            "unmatched_list": unmatched_list,
        }
    return out


def deliverable_6(cfg: Any) -> dict[str, Any]:
    section("D6 - CREDIT SPEND RECONCILIATION (from pull log; no live API)")
    # Numbers locked in docs/notes/05b.md from the authorized pull.
    # Recompute estimate locally to confirm estimator still agrees.
    from ncaa_quant.data.storage import ParquetStore as PS

    with PS(STAGED) as store:
        plan = plan_historical_units(store, SEASONS_ALL, config=cfg)

    pre_estimate_credits = 56400  # locked pre-pull --estimate
    pre_estimate_requests = 1880
    # From backfill log / 05b notes:
    remaining_before_probe = 99988
    remaining_after_full = 43579
    # Lifetime: probe+prior archives 660 credits (22*30) + resume 55740 = 56400
    actual_credits = remaining_before_probe - remaining_after_full  # via remaining delta
    # Note: remaining_before was after free /sports check before probe;
    # full lifetime spend from notes is exactly 56400.
    lifetime_credits_notes = 56400
    lifetime_requests_notes = 1880
    credits_per = int(cfg.data.odds_historical_credits_per_call)
    ceiling = int(cfg.data.odds_historical_credit_ceiling)
    live_reserve = int(cfg.data.odds_rate_limit_reserve)

    # Current local estimate (should still be 56400 if schedule unchanged)
    current_est_credits = plan.total_credits
    current_est_requests = plan.total_requests

    # x-requests-used delta implied by remaining: used grew by actual_credits
    # if remaining is the only meter (1 credit unit = 1 remaining decrement).
    used_delta = remaining_before_probe - remaining_after_full

    table = {
        "pre_pull_estimate_credits": pre_estimate_credits,
        "pre_pull_estimate_requests": pre_estimate_requests,
        "current_local_estimate_credits": current_est_credits,
        "current_local_estimate_requests": current_est_requests,
        "actual_credits_lifetime_notes": lifetime_credits_notes,
        "actual_requests_lifetime_notes": lifetime_requests_notes,
        "remaining_before_probe": remaining_before_probe,
        "remaining_after_full_pull": remaining_after_full,
        "used_delta_via_remaining": used_delta,
        "credits_per_call": credits_per,
        "historical_ceiling": ceiling,
        "live_reserve": live_reserve,
    }
    for k, v in table.items():
        print(f"  {k}: {v}")

    agree = lifetime_credits_notes == pre_estimate_credits
    est_vs_actual_pct = (
        100.0
        * abs(lifetime_credits_notes - pre_estimate_credits)
        / pre_estimate_credits
        if pre_estimate_credits
        else float("nan")
    )
    print()
    print(
        f"estimate vs actual: agree_exact={agree} "
        f"abs_pct_diff={est_vs_actual_pct:.3f}%"
    )
    print(
        f"remaining-delta {used_delta} vs notes lifetime {lifetime_credits_notes}: "
        f"{'MATCH' if used_delta == lifetime_credits_notes else 'DIAGNOSE'}"
    )
    print(
        f"current local re-estimate {current_est_credits} vs locked "
        f"{pre_estimate_credits}: "
        f"{'MATCH' if current_est_credits == pre_estimate_credits else 'DRIFT'}"
    )
    print(
        f"post-pull remaining {remaining_after_full} vs live_reserve {live_reserve}: "
        f"reserve_intact={remaining_after_full >= live_reserve}"
    )
    return table


def deliverable_7(store: ParquetStore) -> dict[str, Any]:
    section("D7 - DEDUPE AGAINST LIVE ROWS")
    # Load all odds; find (game_key, captured_at_minute) covered by both sources
    seasons_present = sorted(
        int(p.name.split("=")[1])
        for p in (STAGED / "odds_snapshots").glob("season=*")
    )
    overlap_moments = 0
    dup_rows_before = 0
    dup_rows_after = 0
    live_n = 0
    hist_n = 0
    exact_dups = 0

    for season in seasons_present:
        odds = store.read("odds_snapshots", filters={"season": int(season)})
        if odds.empty:
            continue
        live = odds[odds["snapshot_source"] == "live"]
        hist = odds[odds["snapshot_source"] == "historical"]
        live_n += len(live)
        hist_n += len(hist)
        if live.empty or hist.empty:
            continue
        live_m = live.copy()
        hist_m = hist.copy()
        live_m["_minute"] = pd.to_datetime(live_m["captured_at"], utc=True).dt.floor("min")
        hist_m["_minute"] = pd.to_datetime(hist_m["captured_at"], utc=True).dt.floor("min")
        live_keys = set(
            zip(live_m["game_key"].astype(str), live_m["_minute"], strict=False)
        )
        hist_keys = set(
            zip(hist_m["game_key"].astype(str), hist_m["_minute"], strict=False)
        )
        both = live_keys & hist_keys
        overlap_moments += len(both)
        if both:
            live_m["_k"] = list(
                zip(live_m["game_key"].astype(str), live_m["_minute"], strict=False)
            )
            hist_m["_k"] = list(
                zip(hist_m["game_key"].astype(str), hist_m["_minute"], strict=False)
            )
            live_hit = live_m[live_m["_k"].isin(both)].drop(columns=["_k", "_minute"])
            hist_hit = hist_m[hist_m["_k"].isin(both)].drop(columns=["_k", "_minute"])
            combined = pd.concat([live_hit, hist_hit], ignore_index=True)
            before = len(combined)
            after = len(dedupe_snapshots(combined))
            dup_rows_before += before
            dup_rows_after += after

        # Exact duplicate rows within season (dedupe key)
        work = odds.copy()
        work["captured_at_minute"] = pd.to_datetime(
            work["captured_at"], utc=True
        ).dt.floor("min")
        dups = work.duplicated(subset=list(_DEDUPE_COLS), keep=False)
        exact_dups += int(dups.sum())

    print(f"live rows total: {live_n}")
    print(f"historical rows total: {hist_n}")
    print(f"overlapping (game_key, minute) moments: {overlap_moments}")
    print(f"rows at overlapping moments before dedupe: {dup_rows_before}")
    print(f"rows at overlapping moments after dedupe: {dup_rows_after}")
    print(
        f"deduped away: {dup_rows_before - dup_rows_after} "
        f"(moments with both sources: {overlap_moments})"
    )
    print(f"exact dedupe-key duplicate row count (keep=False mask sum): {exact_dups}")
    return {
        "live_n": live_n,
        "hist_n": hist_n,
        "overlap_moments": overlap_moments,
        "deduped_away": dup_rows_before - dup_rows_after,
        "exact_dups": exact_dups,
    }


def main() -> None:
    cfg = load_config()
    print("TASK 5B-VERIFY - read-only; no API spend")
    print(f"raw={RAW} staged={STAGED}")
    print(f"decision_points={cfg.data.odds_historical_decision_points}")
    print(f"CFBD_SLOT_CLOSE_TOLERANCE={CFBD_SLOT_CLOSE_TOLERANCE}")

    with ParquetStore(STAGED) as store:
        d1 = deliverable_1(store, cfg)
        d2 = deliverable_2(store)
        d3 = deliverable_3(store)
        d4 = deliverable_4(store)
        d5 = deliverable_5(store)
        d7 = deliverable_7(store)
    d6 = deliverable_6(cfg)
    _ = (d1, d2, d3, d4, d5, d6, d7)
    print()
    print("5B-VERIFY probe complete (no API spend)")


if __name__ == "__main__":
    main()
