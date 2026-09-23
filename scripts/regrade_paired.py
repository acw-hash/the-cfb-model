#!/usr/bin/env python3
"""
Ridge — paired snapshot re-grade (READ-ONLY, offline).

Compares two publish snapshots on the SAME games, so slate composition
cancels and snapshot age is the only thing varying. Answers the §1.3
precedence question: does the Sept 1 tuesday_primary (4 days out) beat the
Aug 27 daily_refresh (9 days out) that REFRESH_KIND_PRECEDENCE selected?

Reads local publish_history (append-only per publish_history.py) and the
published results file for realized outcomes. Touches no network, no R2,
writes nothing unless --json-out is given.

Only games satisfying ALL of the following enter the comparison:
  * graded in results (grade_status == "graded", actual_margin present)
  * present with a non-null mu_margin in BOTH snapshots
  * snapshot published_at strictly before kickoff_utc, for BOTH snapshots
The last rule is what keeps this a fair test rather than hindsight.

Usage:
  # inspect what the history file actually contains first
  python regrade_paired.py data\\webapp\\publish_history\\2026_w1.jsonl --inspect

  python regrade_paired.py data\\webapp\\publish_history\\2026_w1.jsonl \\
      --results data\\results\\latest_results_2026.json \\
      --baseline 2026-08-27T11:33:22Z --candidate 2026-09-01T18:04:28Z \\
      --pooled-teams pooled_prior_teams.txt --json-out regrade_w1.json
"""

import argparse
import json
import math
import random
import statistics
import sys
from datetime import datetime, timezone

BOOTSTRAP_ITERS = 10000
SEED = 20260908


def parse_utc(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def bootstrap_ci(values, stat=statistics.mean, iters=BOOTSTRAP_ITERS, alpha=0.05):
    n = len(values)
    if n < 2:
        return None, None
    rng = random.Random(SEED)
    draws = []
    for _ in range(iters):
        draws.append(stat([values[rng.randrange(n)] for _ in range(n)]))
    draws.sort()
    return draws[int(alpha / 2 * iters)], draws[int((1 - alpha / 2) * iters) - 1]


def wilson_ci(k, n, z=1.96):
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
    return "—" if lo is None or hi is None else f"[{lo:.{nd}f}, {hi:.{nd}f}]"


def load_history(path):
    """Return list of snapshot dicts, in file order."""
    snaps = []
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                snaps.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  line {i}: not valid JSON ({e}) — skipped", file=sys.stderr)
    return snaps


def game_rows(snap):
    """The per-game array inside a snapshot record."""
    g = snap.get("games")
    return g if isinstance(g, list) else []


def index_by_game(snap):
    out = {}
    for r in game_rows(snap):
        gid = r.get("game_id")
        if gid is not None:
            out[str(gid)] = r
    return out


def label(snap):
    return f"{snap.get('refresh_kind', '?')} @ {snap.get('published_at', '?')}"


def inspect(snaps):
    print(f"\n{len(snaps)} snapshot record(s)\n")
    for i, s in enumerate(snaps):
        rows = game_rows(s)
        print(f"[{i}] {label(s)}")
        print(f"     week={s.get('week')} season={s.get('season')} "
              f"schema={s.get('schema_version')} as_of={s.get('as_of')} "
              f"as_of_source={s.get('as_of_source')} stale={s.get('publish_stale')}")
        print(f"     games: {len(rows)}")
        if rows:
            keys = sorted(rows[0].keys())
            print(f"     per-game fields ({len(keys)}): {', '.join(keys)}")
            nn = sum(1 for r in rows if r.get("mu_margin") is not None)
            iv = sum(1 for r in rows if r.get("margin_interval_lo") is not None)
            kick = sum(1 for r in rows if r.get("kickoff_utc"))
            print(f"     non-null mu_margin: {nn}   with interval: {iv}   "
                  f"with kickoff_utc: {kick}")
        print()
    print("Pick --baseline and --candidate from the published_at values above.")


def pick(snaps, stamp):
    hits = [s for s in snaps if s.get("published_at") == stamp]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"No snapshot with published_at == {stamp}. "
                         f"Available: {[s.get('published_at') for s in snaps]}")
    raise SystemExit(f"{len(hits)} snapshots share published_at {stamp}; cannot disambiguate.")


def summarise(name, pairs, key):
    """pairs: list of dicts. key: 'base' or 'cand'."""
    errs = [p[f"ae_{key}"] for p in pairs]
    lo, hi = bootstrap_ci(errs)
    hits = [p[f"hit_{key}"] for p in pairs if p[f"hit_{key}"] is not None]
    k, n = sum(1 for h in hits if h), len(hits)
    clo, chi = wilson_ci(k, n)
    cov = f"{k}/{n} = {fmt(k / n, 3)}" if n else "—"
    print(f"  {name:<34} MAE {fmt(statistics.mean(errs)):>6}  "
          f"{fmt_ci(lo, hi):>16}   med {fmt(statistics.median(errs)):>6}   "
          f"cov {cov:<14} {fmt_ci(clo, chi, 3)}")


def report(pairs, base, cand, title):
    if not pairs:
        print(f"\n{title}: no eligible games.")
        return None
    print(f"\n{title}  (n = {len(pairs)} games, identical in both arms)")
    summarise(f"baseline  {base.get('refresh_kind')}", pairs, "base")
    summarise(f"candidate {cand.get('refresh_kind')}", pairs, "cand")

    deltas = [p["ae_base"] - p["ae_cand"] for p in pairs]
    mean_d = statistics.mean(deltas)
    dlo, dhi = bootstrap_ci(deltas)
    better = sum(1 for d in deltas if d > 0)
    blo, bhi = wilson_ci(better, len(deltas))

    print()
    print(f"  paired delta (baseline AE − candidate AE)")
    print(f"    mean            {fmt(mean_d)}  95% CI {fmt_ci(dlo, dhi)}")
    print(f"    median          {fmt(statistics.median(deltas))}")
    print(f"    candidate wins  {better}/{len(deltas)} = "
          f"{fmt(better / len(deltas), 3)}  95% CI {fmt_ci(blo, bhi, 3)}")
    print()
    if dlo is not None and dlo > 0:
        print("    CI excludes zero and is positive: the fresher snapshot is")
        print("    measurably better on these games. §1.3 precedence is costing")
        print("    accuracy in the published grades.")
    elif dhi is not None and dhi < 0:
        print("    CI excludes zero and is negative: the older snapshot is better.")
        print("    Unexpected — check that both arms are genuinely pre-kickoff.")
    else:
        print("    CI spans zero: no detectable difference at this n. Precedence")
        print("    is not demonstrably costing accuracy; a spec change would be")
        print("    on principle, not on measured harm.")
    return {
        "n": len(pairs),
        "mae_baseline": statistics.mean([p["ae_base"] for p in pairs]),
        "mae_candidate": statistics.mean([p["ae_cand"] for p in pairs]),
        "mean_delta": mean_d,
        "delta_ci95": [dlo, dhi],
        "candidate_wins": better,
    }


def main():
    ap = argparse.ArgumentParser(description="Paired snapshot re-grade (read-only).")
    ap.add_argument("history", help="path to {season}_w{week}.jsonl")
    ap.add_argument("--results", help="published results_<season>.json (for outcomes)")
    ap.add_argument("--baseline", help="published_at of the snapshot currently used")
    ap.add_argument("--candidate", help="published_at of the snapshot to test")
    ap.add_argument("--inspect", action="store_true", help="show snapshot shapes and exit")
    ap.add_argument("--pooled-teams", help="one team name per line; adds a split")
    ap.add_argument("--json-out", help="write the comparison here")
    args = ap.parse_args()

    snaps = load_history(args.history)
    if args.inspect or not (args.results and args.baseline and args.candidate):
        inspect(snaps)
        if not args.inspect:
            print("\nSupply --results, --baseline and --candidate to run the comparison.")
        return 0

    base, cand = pick(snaps, args.baseline), pick(snaps, args.candidate)
    base_at, cand_at = parse_utc(base["published_at"]), parse_utc(cand["published_at"])

    with open(args.results, "r", encoding="utf-8") as fh:
        res = json.load(fh)
    truth = {str(r["game_id"]): r for r in res.get("games", [])
             if r.get("grade_status") == "graded" and r.get("actual_margin") is not None}

    bi, ci = index_by_game(base), index_by_game(cand)

    print("\n" + "=" * 78)
    print("PAIRED RE-GRADE")
    print("=" * 78)
    print(f"  baseline   {label(base)}   rows {len(bi)}")
    print(f"  candidate  {label(cand)}   rows {len(ci)}")
    print(f"  results    {args.results}   graded rows {len(truth)}")

    pairs, drops = [], {"not_graded": 0, "missing_a_snapshot": 0,
                        "null_mu": 0, "not_pre_kickoff": 0, "no_kickoff": 0}

    for gid, t in truth.items():
        b, c = bi.get(gid), ci.get(gid)
        if b is None or c is None:
            drops["missing_a_snapshot"] += 1
            continue
        if b.get("mu_margin") is None or c.get("mu_margin") is None:
            drops["null_mu"] += 1
            continue
        kick = parse_utc(t.get("kickoff_utc") or b.get("kickoff_utc") or c.get("kickoff_utc"))
        if kick is None:
            drops["no_kickoff"] += 1
            continue
        if not (base_at < kick and cand_at < kick):
            drops["not_pre_kickoff"] += 1
            continue

        actual = float(t["actual_margin"])

        def hit(row):
            lo, hi = row.get("margin_interval_lo"), row.get("margin_interval_hi")
            return None if lo is None or hi is None else bool(lo <= actual <= hi)

        pairs.append({
            "game_id": gid,
            "home_team": t.get("home_team"), "away_team": t.get("away_team"),
            "kickoff_utc": t.get("kickoff_utc"), "actual_margin": actual,
            "mu_base": float(b["mu_margin"]), "mu_cand": float(c["mu_margin"]),
            "ae_base": abs(float(b["mu_margin"]) - actual),
            "ae_cand": abs(float(c["mu_margin"]) - actual),
            "hit_base": hit(b), "hit_cand": hit(c),
            "age_base_days": (kick - base_at).total_seconds() / 86400.0,
            "age_cand_days": (kick - cand_at).total_seconds() / 86400.0,
        })

    print(f"\n  eligible pairs {len(pairs)}   dropped: "
          + ", ".join(f"{k}={v}" for k, v in drops.items() if v))
    if pairs:
        print(f"  age baseline  {min(p['age_base_days'] for p in pairs):.2f}"
              f"–{max(p['age_base_days'] for p in pairs):.2f} d")
        print(f"  age candidate {min(p['age_cand_days'] for p in pairs):.2f}"
              f"–{max(p['age_cand_days'] for p in pairs):.2f} d")

    out = {"baseline": base.get("published_at"), "candidate": cand.get("published_at"),
           "all": report(pairs, base, cand, "ALL ELIGIBLE GAMES")}

    if args.pooled_teams and pairs:
        with open(args.pooled_teams, "r", encoding="utf-8") as fh:
            pooled = {ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")}
        full = [p for p in pairs
                if p["home_team"] not in pooled and p["away_team"] not in pooled]
        out["full_history_only"] = report(
            full, base, cand, f"BOTH SIDES IN filter_history ({len(pooled)} pooled names loaded)")

    if pairs:
        print("\n  BIGGEST MOVES (baseline AE − candidate AE)")
        for p in sorted(pairs, key=lambda p: -(p["ae_base"] - p["ae_cand"]))[:5]:
            print(f"    {p['ae_base'] - p['ae_cand']:+7.1f}  "
                  f"{p['away_team']} @ {p['home_team']}  "
                  f"mu {p['mu_base']:.1f} → {p['mu_cand']:.1f}  act {p['actual_margin']:.0f}")
        for p in sorted(pairs, key=lambda p: (p["ae_base"] - p["ae_cand"]))[:5]:
            print(f"    {p['ae_base'] - p['ae_cand']:+7.1f}  "
                  f"{p['away_team']} @ {p['home_team']}  "
                  f"mu {p['mu_base']:.1f} → {p['mu_cand']:.1f}  act {p['actual_margin']:.0f}")

    if args.json_out:
        out["pairs"] = pairs
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
