"""S17 read-only margin orientation check. Not product code."""

from __future__ import annotations

import json
from importlib.machinery import SourceFileLoader
from pathlib import Path
from statistics import median

import duckdb

from ncaa_quant.config import load_config

_v = SourceFileLoader("verify_pub", "scripts/verify_published_artifacts.py").load_module()
_get_json = _v._get_json
_s3_client = _v._s3_client

IDS = ("401856660", "401860880", "401858423")
TARGET_PUB = "2026-08-27T11:33:22Z"
TARGET_KIND = "daily_refresh"


def main() -> None:
    con = duckdb.connect()
    sub = con.execute(
        """
        SELECT *
        FROM read_parquet('data/staged/games/season=2026/week=1/part.parquet')
        WHERE CAST(game_id AS VARCHAR) IN ('401856660','401860880','401858423')
        """
    ).fetchdf()
    sub["game_id"] = sub["game_id"].astype(str)
    print("===1 STAGED raw cols===", sorted(sub.columns.tolist()))
    for gid in IDS:
        print(sub[sub["game_id"] == gid].iloc[0].to_dict())

    teams = con.execute(
        "SELECT * FROM read_parquet('data/staged/teams/season=2026/part.parquet')"
    ).fetchdf()
    print("teams_cols", sorted(teams.columns.tolist()))
    id_col = "team_id" if "team_id" in teams.columns else "id"
    school_col = "school" if "school" in teams.columns else "team"
    id_to_school = {int(r[id_col]): r[school_col] for _, r in teams.iterrows()}

    print("===1 STAGED RESOLVED===")
    for gid in IDS:
        r = sub[sub["game_id"] == gid].iloc[0].to_dict()
        home_id = int(r.get("home_team_id", r.get("home_id")))
        away_id = int(r.get("away_team_id", r.get("away_id")))
        home_team = r.get("home_team") or id_to_school.get(home_id)
        away_team = r.get("away_team") or id_to_school.get(away_id)
        print(
            {
                "game_id": gid,
                "home_team": home_team,
                "away_team": away_team,
                "home_points": int(r["home_points"]),
                "away_points": int(r["away_points"]),
                "home_minus_away_pts": int(r["home_points"]) - int(r["away_points"]),
            }
        )

    cfg = load_config()
    client = _s3_client()
    results = _get_json(client, bucket=cfg.webapp.r2_bucket, key="latest/results_2026.json")
    graded = {
        str(g["game_id"]): g
        for g in results["games"]
        if g.get("grade_status") == "graded" and str(g["game_id"]) in IDS
    }
    print("===2 RESULTS===")
    for gid in IDS:
        g = graded[gid]
        print(
            {
                "game_id": gid,
                "home_team": g["home_team"],
                "away_team": g["away_team"],
                "home_points": g["home_points"],
                "away_points": g["away_points"],
                "actual_margin": g["actual_margin"],
                "mu_margin": g["mu_margin"],
                "graded_from": g["graded_from"],
                "margin_interval_lo": g["margin_interval_lo"],
                "margin_interval_hi": g["margin_interval_hi"],
                "margin_interval_hit": g["margin_interval_hit"],
            }
        )

    hist_path = Path("data/webapp/publish_history/2026_w1.jsonl")
    snap = None
    for line in hist_path.open(encoding="utf-8"):
        rec = json.loads(line)
        if rec.get("published_at") == TARGET_PUB and rec.get("refresh_kind") == TARGET_KIND:
            snap = rec
            break
    print("===3 PUBLISH HISTORY===")
    print("found", snap is not None, "week", None if snap is None else snap.get("week"))
    pub_by_id: dict = {}
    if snap:
        for g in snap["games"]:
            pub_by_id[str(g["game_id"])] = g
        for gid in IDS:
            g = pub_by_id.get(gid)
            if g is None:
                print({"game_id": gid, "status": "ABSENT"})
                continue
            print(
                {
                    "game_id": gid,
                    "home_team": g.get("home_team"),
                    "away_team": g.get("away_team"),
                    "mu_margin": g.get("mu_margin"),
                    "p_win_home": g.get("p_win_home"),
                    "conviction_label": g.get("conviction_label"),
                    "conviction_team": g.get("conviction_team"),
                }
            )

    wp = _get_json(client, bucket=cfg.webapp.r2_bucket, key="latest/week_predictions.json")
    print("===3b LATEST week_predictions===")
    print(
        {
            "week": wp.get("week"),
            "published_at": wp.get("published_at"),
            "refresh_kind": wp.get("refresh_kind"),
            "ids_present": {gid: any(str(g["game_id"]) == gid for g in wp.get("games", [])) for gid in IDS},
        }
    )

    print("===4 ORIENTATION===")
    for gid in IDS:
        staged = sub[sub["game_id"] == gid].iloc[0].to_dict()
        res = graded[gid]
        pub = pub_by_id.get(gid, {})
        pts_margin = int(staged["home_points"]) - int(staged["away_points"])
        print(
            {
                "game_id": gid,
                "staged_home_minus_away": pts_margin,
                "results_actual_margin": res["actual_margin"],
                "actual_equals_home_minus_away": res["actual_margin"] == pts_margin,
                "home_away_match_pub_vs_results": (
                    pub.get("home_team") == res["home_team"]
                    and pub.get("away_team") == res["away_team"]
                ),
                "mu_equal_results_vs_pub": res["mu_margin"] == pub.get("mu_margin"),
                "results_mu": res["mu_margin"],
                "pub_mu": pub.get("mu_margin"),
                "abs_err_as_is": abs(float(res["mu_margin"]) - float(res["actual_margin"])),
                "abs_err_if_flip_mu": abs((-float(res["mu_margin"])) - float(res["actual_margin"])),
            }
        )

    all_graded = [g for g in results["games"] if g.get("grade_status") == "graded"]
    print("===5 FULL SLATE===")

    def metrics(flip_mu: bool) -> dict:
        abs_errs = []
        hits = 0
        n_int = 0
        for g in all_graded:
            mu = float(g["mu_margin"])
            if flip_mu:
                mu = -mu
            act = float(g["actual_margin"])
            abs_errs.append(abs(mu - act))
            lo = g.get("margin_interval_lo")
            hi = g.get("margin_interval_hi")
            if lo is not None and hi is not None:
                n_int += 1
                flo = float(lo)
                fhi = float(hi)
                if flip_mu:
                    flo, fhi = -float(hi), -float(lo)
                if flo <= act <= fhi:
                    hits += 1
        return {
            "MAE": sum(abs_errs) / len(abs_errs),
            "median_AE": median(abs_errs),
            "interval_hits": hits,
            "n_interval": n_int,
            "hit_rate": hits / n_int if n_int else None,
        }

    print("as_is", metrics(False))
    print("flip_mu_and_interval", metrics(True))

    disagree = 0
    for g in all_graded:
        mu = g.get("mu_margin")
        p = g.get("p_win_home")
        if mu is None or p is None:
            continue
        if (float(mu) > 0 and float(p) < 0.5) or (float(mu) < 0 and float(p) > 0.5):
            disagree += 1
    print("n_mu_sign_vs_p_win_home_disagree", disagree)

    mismatches = []
    ha_mismatches = []
    if snap:
        for g in all_graded:
            pub = pub_by_id.get(str(g["game_id"]))
            if pub is None:
                mismatches.append((g["game_id"], "ABSENT"))
                continue
            if pub.get("mu_margin") != g.get("mu_margin"):
                mismatches.append((g["game_id"], pub.get("mu_margin"), g.get("mu_margin")))
            if pub.get("home_team") != g.get("home_team") or pub.get("away_team") != g.get(
                "away_team"
            ):
                ha_mismatches.append(
                    (
                        g["game_id"],
                        pub.get("home_team"),
                        pub.get("away_team"),
                        g.get("home_team"),
                        g.get("away_team"),
                    )
                )
    print("pub_vs_results_mu_mismatches", len(mismatches))
    print("pub_vs_results_ha_mismatches", len(ha_mismatches))
    for m in ha_mismatches[:10]:
        print(" HA", m)

    bad_conv = []
    for g in all_graded:
        ct = g.get("conviction_team")
        mu = g.get("mu_margin")
        if ct is None or mu is None:
            continue
        if ct == g["home_team"] and float(mu) < 0:
            bad_conv.append(g["game_id"])
        if ct == g["away_team"] and float(mu) > 0:
            bad_conv.append(g["game_id"])
    print("n_conviction_team_vs_mu_sign_inconsistent", len(bad_conv))


if __name__ == "__main__":
    main()
