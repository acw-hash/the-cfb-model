"""S18 read-only week-1 error structure. Not product code."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path
from statistics import median

import duckdb

from ncaa_quant.config import load_config

_v = SourceFileLoader("verify_pub", "scripts/verify_published_artifacts.py").load_module()
_get_json = _v._get_json
_s3_client = _v._s3_client

GRADED_FROM_PUB = "2026-08-27T11:33:22Z"
FOCUS = {
    "401858423": ("Rutgers", "Massachusetts"),
    "401860880": ("Utah State", "Idaho State"),
}


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def main() -> None:
    cfg = load_config()
    client = _s3_client()
    results = _get_json(client, bucket=cfg.webapp.r2_bucket, key="latest/results_2026.json")
    graded = [g for g in results["games"] if g.get("grade_status") == "graded"]
    print("n_graded", len(graded))

    con = duckdb.connect()
    teams = con.execute(
        "SELECT * FROM read_parquet('data/staged/teams/season=2026/part.parquet')"
    ).fetchdf()
    print("teams_cols", sorted(teams.columns.tolist()))
    print("classification_counts", teams["classification"].value_counts().to_dict())

    school_to_class = {str(r["school"]): str(r["classification"]) for _, r in teams.iterrows()}
    # also by team_id
    id_to_class = {int(r["team_id"]): str(r["classification"]) for _, r in teams.iterrows()}
    id_to_school = {int(r["team_id"]): str(r["school"]) for _, r in teams.iterrows()}

    games = con.execute(
        """
        SELECT game_id, home_team_id, away_team_id, home_points, away_points, start_date, week
        FROM read_parquet('data/staged/games/season=2026/week=1/part.parquet')
        """
    ).fetchdf()
    games["game_id"] = games["game_id"].astype(str)
    staged_by_id = {r["game_id"]: r for _, r in games.iterrows()}

    def side_class(team_name: str, team_id: int | None) -> str:
        if team_name in school_to_class:
            return school_to_class[team_name]
        if team_id is not None and int(team_id) in id_to_class:
            return id_to_class[int(team_id)]
        return "UNKNOWN"

    rows = []
    for g in graded:
        gid = str(g["game_id"])
        st = staged_by_id.get(gid)
        home_id = int(st["home_team_id"]) if st is not None else None
        away_id = int(st["away_team_id"]) if st is not None else None
        hc = side_class(g["home_team"], home_id)
        ac = side_class(g["away_team"], away_id)
        both_fbs = hc == "fbs" and ac == "fbs"
        any_fcs = (hc != "fbs") or (ac != "fbs")
        # user asked: both teams FBS vs any FCS opponent
        bucket = "both_fbs" if both_fbs else "any_fcs"
        mu = float(g["mu_margin"])
        act = float(g["actual_margin"])
        err = mu - act  # signed: positive => model too high for home (overforecast home margin)
        ae = abs(err)
        lo, hi = g.get("margin_interval_lo"), g.get("margin_interval_hi")
        has_int = lo is not None and hi is not None
        hit = bool(g.get("margin_interval_hit")) if has_int else None
        kick = g.get("kickoff_utc")
        rows.append(
            {
                "g": g,
                "gid": gid,
                "home_class": hc,
                "away_class": ac,
                "bucket": bucket,
                "mu": mu,
                "act": act,
                "err": err,
                "ae": ae,
                "has_int": has_int,
                "hit": hit,
                "kickoff_utc": kick,
                "start_date": None if st is None else st["start_date"],
            }
        )

    unknown = [r for r in rows if r["home_class"] == "UNKNOWN" or r["away_class"] == "UNKNOWN"]
    print("n_unknown_class", len(unknown))
    for r in unknown[:5]:
        print(" unknown", r["g"]["home_team"], r["g"]["away_team"], r["home_class"], r["away_class"])

    print("classification_values_sample", sorted(set(school_to_class.values()))[:20])

    def bucket_metrics(label: str, subset: list) -> None:
        aes = [r["ae"] for r in subset]
        signed = [r["err"] for r in subset]
        ints = [r for r in subset if r["has_int"]]
        hits = sum(1 for r in ints if r["hit"])
        print(
            {
                "bucket": label,
                "n": len(subset),
                "MAE": mean(aes) if aes else None,
                "median_AE": median(aes) if aes else None,
                "signed_mean_error": mean(signed) if signed else None,
                "n_interval": len(ints),
                "interval_hits": hits,
                "interval_hit_rate": hits / len(ints) if ints else None,
            }
        )

    print("===1/2/4 BUCKETS===")
    bucket_metrics("overall", rows)
    bucket_metrics("both_fbs", [r for r in rows if r["bucket"] == "both_fbs"])
    bucket_metrics("any_fcs", [r for r in rows if r["bucket"] == "any_fcs"])

    # also show if any non-fbs is not fcs
    non_fbs = []
    for r in rows:
        for side, cls in (("home", r["home_class"]), ("away", r["away_class"])):
            if cls not in ("fbs", "fcs"):
                non_fbs.append((r["gid"], side, cls, r["g"][f"{side}_team"]))
    print("non_fbs_fcs_labels", Counter := __import__("collections").Counter(x[2] for x in non_fbs))
    print("non_fbs_detail_n", len(non_fbs))

    print("===3 TOP 10 ABS ERROR===")
    top = sorted(rows, key=lambda r: r["ae"], reverse=True)[:10]
    for i, r in enumerate(top, 1):
        g = r["g"]
        print(
            {
                "rank": i,
                "home": g["home_team"],
                "away": g["away_team"],
                "home_class": r["home_class"],
                "away_class": r["away_class"],
                "actual_margin": r["act"],
                "mu_margin": r["mu"],
                "abs_err": r["ae"],
                "signed_err": r["err"],
                "bucket": r["bucket"],
            }
        )

    # 4 extra: bias toward favorites?
    # favorite = side with mu favoring them: if mu>0 home favored, if mu<0 away favored
    # signed error mu-actual: if model overstates favorite's margin...
    # When home favored (mu>0): positive err = predicted home margin too large (overconfident favorite)
    # When away favored (mu<0): redefine favorite-margin error
    fav_over = []
    for r in rows:
        mu, act = r["mu"], r["act"]
        if mu >= 0:
            # home favorite: favorite_margin_error = mu - act (positive = overstated home/fav margin)
            fav_over.append(mu - act)
        else:
            # away favorite: model favorite margin is -mu; actual favorite margin is -act
            # favorite_margin_error = (-mu) - (-act) = act - mu = -(mu - act)
            fav_over.append(act - mu)
    print("===4 FAVORITE-MARGIN BIAS===")
    print(
        {
            "mean_signed_error_home_minus_away": mean([r["err"] for r in rows]),
            "mean_favorite_margin_error": mean(fav_over),
            "note": "favorite_margin_error>0 means model overstated the favorite's margin",
        }
    )
    for label, subset in (
        ("both_fbs", [r for r in rows if r["bucket"] == "both_fbs"]),
        ("any_fcs", [r for r in rows if r["bucket"] == "any_fcs"]),
    ):
        fo = []
        for r in subset:
            mu, act = r["mu"], r["act"]
            fo.append(mu - act if mu >= 0 else act - mu)
        print(
            {
                "bucket": label,
                "mean_signed_error_hma": mean([r["err"] for r in subset]),
                "mean_favorite_margin_error": mean(fo),
            }
        )

    # 6. kickoff date split
    print("===6 KICKOFF SPLIT===")
    early = []
    late = []
    other = []
    for r in rows:
        ko = r["kickoff_utc"]
        if ko is None:
            other.append(r)
            continue
        # parse date UTC
        dt = datetime.fromisoformat(str(ko).replace("Z", "+00:00"))
        d = dt.astimezone(timezone.utc).date().isoformat()
        if d in ("2026-08-29", "2026-08-30"):
            early.append(r)
        elif d.startswith("2026-09-05") or d == "2026-09-05":
            late.append(r)
        else:
            # also sept 4/6?
            other.append((d, r))

    def mae_only(subset: list, label: str) -> None:
        if not subset or isinstance(subset[0], tuple):
            print(label, "n", len(subset), "sample_dates", sorted({x[0] for x in subset})[:20] if subset else None)
            # if other has tuples, compute MAE on the r parts
            if subset and isinstance(subset[0], tuple):
                aes = [x[1]["ae"] for x in subset]
                print(label, "MAE", mean(aes) if aes else None, "dates", sorted({x[0] for x in subset}))
            return
        aes = [r["ae"] for r in subset]
        print({"bucket": label, "n": len(subset), "MAE": mean(aes) if aes else None, "median_AE": median(aes) if aes else None})

    mae_only(early, "aug_29_30")
    mae_only(late, "sept_5")
    mae_only(other, "other_kickoffs")

    # verify graded_from same for both
    pubs = {(g.get("graded_from") or {}).get("published_at") for g in graded}
    kinds = {(g.get("graded_from") or {}).get("refresh_kind") for g in graded}
    print("graded_from_published_at_set", pubs)
    print("graded_from_refresh_kind_set", kinds)

    # date distribution of all kickoffs
    from collections import Counter

    dates = Counter()
    for r in rows:
        dt = datetime.fromisoformat(str(r["kickoff_utc"]).replace("Z", "+00:00"))
        dates[dt.astimezone(timezone.utc).date().isoformat()] += 1
    print("kickoff_utc_date_counts", dict(sorted(dates.items())))


if __name__ == "__main__":
    main()
