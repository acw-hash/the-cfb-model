#!/usr/bin/env python3
"""
Ridge — week-1 grading diagnostic (READ-ONLY).

Answers, from results_<season>.json alone:
  * the full kickoff-date histogram (where are the 31 games unaccounted for
    by the 8 / 60 split?)
  * MAE by kickoff date and by snapshot age, each with a bootstrap CI, so an
    n=8 bucket is not read as a trend
  * interval coverage with a Wilson CI, per bucket
  * the null / suppression audit that reconciles graded-row count against
    interval-eligible count
  * top misses, with an optional FCS split when a team list is supplied

Does NOT re-grade. Re-grading needs the Sept 1 tuesday_primary per-game rows,
which are not in this file; see week1-grading-discovery.md.

Reads one file. Writes nothing unless --json-out is given. Stdlib only, so it
runs outside the project env. No values are inferred or filled in: anything
absent is reported as absent.

Usage:
  python analyze_grading.py results_2026.json
  python analyze_grading.py results_2026.json --week 1
  python analyze_grading.py results_2026.json --fcs-teams fcs.txt --json-out w1.json
"""

import argparse
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

BOOTSTRAP_ITERS = 10000
SEED = 20260908


# ---------------------------------------------------------------- utilities

def parse_utc(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def bootstrap_ci(values, stat=statistics.mean, iters=BOOTSTRAP_ITERS, alpha=0.05):
    """Percentile bootstrap CI. Returns (lo, hi) or (None, None) if n < 2."""
    n = len(values)
    if n < 2:
        return None, None
    rng = random.Random(SEED)
    draws = []
    for _ in range(iters):
        draws.append(stat([values[rng.randrange(n)] for _ in range(n)]))
    draws.sort()
    lo = draws[int(alpha / 2 * iters)]
    hi = draws[int((1 - alpha / 2) * iters) - 1]
    return lo, hi


def wilson_ci(k, n, z=1.96):
    """Wilson score interval — correct for small n, unlike the normal approx."""
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return centre - half, centre + half


def fmt(x, nd=2):
    return "—" if x is None else f"{x:.{nd}f}"


def fmt_ci(lo, hi, nd=2):
    if lo is None or hi is None:
        return "—"
    return f"[{lo:.{nd}f}, {hi:.{nd}f}]"


def rule(char="-", width=78):
    print(char * width)


def section(title):
    print()
    rule("=")
    print(title)
    rule("=")


# ---------------------------------------------------------------- extraction

def graded_from_parts(row):
    gf = row.get("graded_from") or {}
    if not isinstance(gf, dict):
        return None, None
    return gf.get("refresh_kind"), gf.get("published_at")


def snapshot_age_days(row):
    """kickoff_utc - graded_from.published_at, in days. None if either absent."""
    kick = parse_utc(row.get("kickoff_utc"))
    _, pub = graded_from_parts(row)
    pub = parse_utc(pub)
    if kick is None or pub is None:
        return None
    return (kick - pub).total_seconds() / 86400.0


def abs_error(row):
    mu = row.get("mu_margin")
    actual = row.get("actual_margin")
    if mu is None or actual is None:
        return None
    return abs(float(mu) - float(actual))


def kickoff_date(row):
    kick = parse_utc(row.get("kickoff_utc"))
    return kick.date().isoformat() if kick else "unknown"


# ---------------------------------------------------------------- reporting

def bucket_table(buckets, label):
    """buckets: ordered list of (name, rows). Prints n / MAE+CI / coverage+CI."""
    print(f"{label:<16} {'n':>4} {'fc':>4} {'MAE':>7} {'MAE 95% CI':>18} "
          f"{'med':>6} {'cov':>10} {'cov 95% CI':>16}")
    rule()
    for name, rows in buckets:
        errs = [e for e in (abs_error(r) for r in rows) if e is not None]
        mae = statistics.mean(errs) if errs else None
        med = statistics.median(errs) if errs else None
        lo, hi = bootstrap_ci(errs) if errs else (None, None)

        hits = [r.get("margin_interval_hit") for r in rows]
        hits = [h for h in hits if h is not None]
        k, n_iv = sum(1 for h in hits if h), len(hits)
        cov = k / n_iv if n_iv else None
        clo, chi = wilson_ci(k, n_iv)

        cov_s = f"{k}/{n_iv} {cov:.3f}" if n_iv else "—"
        print(f"{name:<16} {len(rows):>4} {len(errs):>4} {fmt(mae):>7} "
              f"{fmt_ci(lo, hi):>18} {fmt(med):>6} {cov_s:>10} "
              f"{fmt_ci(clo, chi, 3):>16}")
    print()
    print("  n = rows in bucket; fc = rows with a non-null mu_margin (MAE basis);")
    print("  cov = margin_interval_hit rate over rows where the interval exists.")


def main():
    ap = argparse.ArgumentParser(description="Ridge week-1 grading diagnostic (read-only).")
    ap.add_argument("results", help="path to results_<season>.json")
    ap.add_argument("--week", type=int, default=None,
                    help="restrict to one week (default: all weeks in file)")
    ap.add_argument("--fcs-teams", default=None,
                    help="file with one team name per line; enables the FCS split")
    ap.add_argument("--exclude-games", default=None,
                    help="comma-separated game_ids to exclude from a second pass")
    ap.add_argument("--top", type=int, default=15, help="how many top misses to list")
    ap.add_argument("--json-out", default=None,
                    help="write the computed summary here for later comparison")
    args = ap.parse_args()

    with open(args.results, "r", encoding="utf-8") as fh:
        doc = json.load(fh)

    all_rows = doc.get("games", [])
    if args.week is not None:
        all_rows = [r for r in all_rows if r.get("week") == args.week]

    section("FILE")
    for key in ("schema_version", "season", "published_at", "grading_rule", "fixture"):
        if key in doc:
            print(f"  {key:<16} {doc[key]}")
        elif key == "fixture":
            print(f"  {'fixture':<16} (absent)")
    print(f"  {'rows in scope':<16} {len(all_rows)}"
          + (f"  (week {args.week})" if args.week is not None else "  (all weeks)"))

    status = Counter(r.get("grade_status", "missing") for r in all_rows)
    print(f"  {'grade_status':<16} " + ", ".join(f"{k}={v}" for k, v in sorted(status.items())))

    rows = [r for r in all_rows if r.get("grade_status") == "graded"]
    if not rows:
        print("\nNo rows with grade_status == 'graded'. Nothing to analyse.")
        return 1

    # ------------------------------------------------------------ provenance
    section("GRADED_FROM PROVENANCE")
    prov = Counter(graded_from_parts(r) for r in rows)
    print(f"{'refresh_kind':<20} {'published_at':<26} {'n':>5}")
    rule()
    for (kind, pub), n in sorted(prov.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        print(f"{str(kind):<20} {str(pub):<26} {n:>5}")
    if len(prov) == 1:
        print("\n  Every graded row resolves to a single snapshot. Under DESIGN §1.3 the")
        print("  refresh_kind ladder outranks recency, so a later publish of a")
        print("  lower-precedence kind loses to an earlier daily_refresh.")

    # ------------------------------------------------------------ null audit
    section("NULL / SUPPRESSION AUDIT")
    fields = ["mu_margin", "sigma_margin", "margin_interval_lo", "margin_interval_hi",
              "mu_total", "p_win_home", "conviction_tier"]
    print(f"{'field':<24} {'present':>8} {'null':>6} {'absent':>7}")
    rule()
    for f in fields:
        present = sum(1 for r in rows if r.get(f) is not None)
        null = sum(1 for r in rows if f in r and r.get(f) is None)
        absent = len(rows) - present - null
        print(f"{f:<24} {present:>8} {null:>6} {absent:>7}")

    iv_rows = [r for r in rows if r.get("margin_interval_hit") is not None]
    print(f"\n  rows with a usable margin interval: {len(iv_rows)} of {len(rows)}"
          f"  ({len(rows) - len(iv_rows)} without)")
    reasons = Counter(r.get("null_reason") for r in rows if r.get("null_reason"))
    if reasons:
        print("  null_reason: " + ", ".join(f"{k}={v}" for k, v in reasons.most_common()))
    else:
        print("  null_reason: none set on any row")

    nominals = Counter(r.get("margin_interval_nominal") for r in iv_rows)
    if nominals:
        print("  margin_interval_nominal: "
              + ", ".join(f"{k}={v}" for k, v in nominals.most_common()))

    # ------------------------------------------------------------ headline
    section("HEADLINE")
    errs = [e for e in (abs_error(r) for r in rows) if e is not None]
    mae = statistics.mean(errs) if errs else None
    med_ae = statistics.median(errs) if errs else None
    lo, hi = bootstrap_ci(errs) if errs else (None, None)
    print(f"  graded rows                {len(rows)}")
    print(f"  rows with a forecast       {len(errs)}")
    if errs:
        print(f"  MAE (margin)               {fmt(mae)}  95% CI {fmt_ci(lo, hi)}")
        print(f"  median AE                  {fmt(med_ae)}")
    else:
        print("  MAE (margin)               — (no row carries both mu_margin and")
        print("                             actual_margin; nothing to average)")

    k = sum(1 for r in iv_rows if r.get("margin_interval_hit"))
    clo, chi = wilson_ci(k, len(iv_rows))
    if iv_rows:
        print(f"  margin interval coverage   {k}/{len(iv_rows)} = "
              f"{fmt(k / len(iv_rows), 3)}  95% CI {fmt_ci(clo, chi, 3)}")
    else:
        print("  margin interval coverage   — (no row carries an interval)")

    briers = [(r["p_win_home"] - (1.0 if r.get("home_win") else 0.0)) ** 2
              for r in rows if r.get("p_win_home") is not None and r.get("home_win") is not None]
    if briers:
        blo, bhi = bootstrap_ci(briers)
        print(f"  Brier (p_win_home)         {fmt(statistics.mean(briers), 4)}"
              f"  95% CI {fmt_ci(blo, bhi, 4)}  n={len(briers)}")

    # ------------------------------------------------------- kickoff histogram
    section("BY KICKOFF DATE  — the full histogram")
    by_date = defaultdict(list)
    for r in rows:
        by_date[kickoff_date(r)].append(r)
    ordered = [(d, by_date[d]) for d in sorted(by_date)]
    bucket_table(ordered, "kickoff date")
    print("\n  Ages for each date (kickoff minus graded_from.published_at, days):")
    for d, rs in ordered:
        ages = [a for a in (snapshot_age_days(r) for r in rs) if a is not None]
        if ages:
            span = f"{min(ages):.2f}–{max(ages):.2f}" if len(set(round(a, 2) for a in ages)) > 1 \
                else f"{ages[0]:.2f}"
            print(f"    {d}  n={len(rs):>3}  age {span} d")

    # ------------------------------------------------------------ age buckets
    section("BY SNAPSHOT AGE")
    edges = [(0, 3, "< 3 d"), (3, 5, "3–5 d"), (5, 7, "5–7 d"),
             (7, 10, "7–10 d"), (10, 1e9, "≥ 10 d")]
    age_buckets = []
    for lo_e, hi_e, name in edges:
        sel = [r for r in rows
               if (a := snapshot_age_days(r)) is not None and lo_e <= a < hi_e]
        if sel:
            age_buckets.append((name, sel))
    unknown = [r for r in rows if snapshot_age_days(r) is None]
    if unknown:
        age_buckets.append(("age unknown", unknown))
    bucket_table(age_buckets, "age bucket")

    # ------------------------------------------------------------ top misses
    section(f"TOP {args.top} MISSES")
    scored = sorted(((abs_error(r), r) for r in rows if abs_error(r) is not None),
                    key=lambda t: -t[0])[:args.top]
    print(f"{'AE':>6}  {'game_id':<12} {'matchup':<44} {'mu':>7} {'act':>5} {'hit':>4}")
    rule()
    for e, r in scored:
        match = f"{r.get('away_team', '?')} @ {r.get('home_team', '?')}"[:44]
        hit = r.get("margin_interval_hit")
        hit_s = "—" if hit is None else ("y" if hit else "n")
        print(f"{e:>6.1f}  {str(r.get('game_id', '?')):<12} {match:<44} "
              f"{fmt(r.get('mu_margin'), 1):>7} {str(r.get('actual_margin', '?')):>5} {hit_s:>4}")

    # ------------------------------------------------------------- FCS split
    section("FCS SPLIT")
    fcs = set()
    if args.fcs_teams:
        with open(args.fcs_teams, "r", encoding="utf-8") as fh:
            fcs = {ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")}
        print(f"  loaded {len(fcs)} team names from {args.fcs_teams}")

    if not fcs:
        print("  No team list supplied, and results_<season>.json carries no FBS/FCS")
        print("  flag, so no split is computed. Rerun with --fcs-teams to separate")
        print("  the pooled-prior games from the snapshot-age question. Guessing from")
        print("  team names is not done here.")
    else:
        def has_fcs(r):
            return r.get("home_team") in fcs or r.get("away_team") in fcs
        fcs_rows = [r for r in rows if has_fcs(r)]
        fbs_rows = [r for r in rows if not has_fcs(r)]
        bucket_table([("FBS only", fbs_rows), ("has FCS side", fcs_rows)], "subset")

        if fbs_rows:
            print("\n  Age buckets, FBS-only:")
            sub = []
            for lo_e, hi_e, name in edges:
                sel = [r for r in fbs_rows
                       if (a := snapshot_age_days(r)) is not None and lo_e <= a < hi_e]
                if sel:
                    sub.append((name, sel))
            bucket_table(sub, "age (FBS)")

    # -------------------------------------------------------- manual exclude
    if args.exclude_games:
        drop = {g.strip() for g in args.exclude_games.split(",") if g.strip()}
        kept = [r for r in rows if str(r.get("game_id")) not in drop]
        section(f"EXCLUDING {len(rows) - len(kept)} NAMED GAMES")
        sub = []
        for lo_e, hi_e, name in edges:
            sel = [r for r in kept
                   if (a := snapshot_age_days(r)) is not None and lo_e <= a < hi_e]
            if sel:
                sub.append((name, sel))
        bucket_table(sub, "age bucket")

    # ------------------------------------------------------------- json out
    if args.json_out:
        out = {
            "source": args.results,
            "schema_version": doc.get("schema_version"),
            "season": doc.get("season"),
            "published_at": doc.get("published_at"),
            "week_filter": args.week,
            "n_graded": len(rows),
            "n_forecast": len(errs),
            "mae": mae,
            "mae_ci95": [lo, hi],
            "median_ae": med_ae,
            "interval_hits": k,
            "interval_n": len(iv_rows),
            "brier": statistics.mean(briers) if briers else None,
            "by_kickoff_date": {
                d: {
                    "n": len(rs),
                    "mae": (statistics.mean(v)
                            if (v := [e for e in (abs_error(r) for r in rs) if e is not None])
                            else None),
                }
                for d, rs in ordered
            },
            "per_game": [
                {
                    "game_id": r.get("game_id"),
                    "kickoff_utc": r.get("kickoff_utc"),
                    "kickoff_date": kickoff_date(r),
                    "snapshot_age_days": snapshot_age_days(r),
                    "graded_from": r.get("graded_from"),
                    "mu_margin": r.get("mu_margin"),
                    "actual_margin": r.get("actual_margin"),
                    "abs_error": abs_error(r),
                    "margin_interval_hit": r.get("margin_interval_hit"),
                }
                for r in rows
            ],
        }
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nWrote {args.json_out} ({len(out['per_game'])} per-game rows) "
              f"for comparison against a re-grade.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
