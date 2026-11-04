"""W10-1: grade_export + W9 call grades + season record + EPA gaps (week 9)."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pandas as pd

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ratings.state_space import build_game_observations_from_plays
from ncaa_quant.webapp.export import load_schedule_frame, load_teams_frame
from ncaa_quant.webapp.grade import grade_export

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]


def _load_partition(table: str, season: int, week: int) -> pd.DataFrame:
    cfg = load_config()
    path = (
        Path(cfg.paths.staged_dir)
        / table
        / f"season={season}"
        / f"week={week}"
        / "part.parquet"
    )
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _classification(teams: pd.DataFrame) -> dict[int, str]:
    id_col = "team_id" if "team_id" in teams.columns else "id"
    out: dict[int, str] = {}
    for _, r in teams.iterrows():
        tid = int(r[id_col])
        cls = str(r.get("classification", "") or "").upper() or "?"
        out[tid] = cls
    return out


def _school(teams: pd.DataFrame) -> dict[int, str]:
    id_col = "team_id" if "team_id" in teams.columns else "id"
    name_col = next(
        c for c in ("school", "team", "name", "display_name") if c in teams.columns
    )
    return {int(r[id_col]): str(r[name_col]) for _, r in teams.iterrows()}


def grade_call(
    c: dict,
    games_by_id: dict[str, dict],
) -> tuple[str, str] | tuple[None, None]:
    g = games_by_id.get(str(c["game_id"]))
    if g is None:
        return None, None
    side = str(c["side"])
    line = float(c["line"])
    home_name, away_name = g["home_name"], g["away_name"]
    home_pts, away_pts = g["home_points"], g["away_points"]
    if side == home_name:
        side_pts, opp_pts = home_pts, away_pts
        side_bucket = "neutral" if g["neutral"] else "home"
    elif side == away_name:
        side_pts, opp_pts = away_pts, home_pts
        side_bucket = "neutral" if g["neutral"] else "away"
    else:
        raise ValueError(f"side {side!r} not in {home_name}/{away_name}")
    margin_vs_line = (side_pts - opp_pts) + line
    if margin_vs_line == 0:
        result = "P"
    elif margin_vs_line > 0:
        result = "W"
    else:
        result = "L"
    print(
        f"{c['matchup']}: {side} {line:+g} @ {c.get('book')} | "
        f"final {away_name} {away_pts:.0f}-{home_pts:.0f} {home_name} | {result}"
    )
    return result, side_bucket


def _games_by_id(week: int, names: dict[int, str], cfg: object) -> dict[str, dict]:
    gf = load_schedule_frame(season=2026, week=week, config=cfg)
    gbi: dict[str, dict] = {}
    for _, r in gf.iterrows():
        hid = int(r["home_team_id"])
        aid = int(r["away_team_id"])
        hp = r.get("home_points")
        ap = r.get("away_points")
        if pd.isna(hp) or pd.isna(ap):
            continue
        gbi[str(r["game_id"])] = {
            "home_points": float(hp),
            "away_points": float(ap),
            "home_name": names.get(hid, str(hid)),
            "away_name": names.get(aid, str(aid)),
            "neutral": bool(r.get("neutral_site", False)),
        }
    return gbi


def _closing_line(
    snaps: pd.DataFrame,
    *,
    game_id: str,
    book: str,
    side: str,
    home_name: str,
    kickoff: pd.Timestamp | None,
) -> tuple[float | None, float | None, str]:
    sub = snaps.loc[snaps["game_id"].astype("Int64") == int(game_id)].copy()
    if sub.empty:
        return None, None, "no_snaps"
    sub = sub.loc[
        (sub["market"].astype(str) == "spread") & (sub["book"].astype(str) == book)
    ]
    if sub.empty:
        return None, None, "book_missing"
    if kickoff is not None and pd.notna(kickoff):
        before = sub.loc[sub["event_time"] <= pd.Timestamp(kickoff)]
        if not before.empty:
            sub = before
    last_et = sub["event_time"].max()
    last = sub.loc[sub["event_time"] == last_et]
    pool = last.loc[last["side"].astype(str).str.casefold() == side.casefold()]
    if pool.empty:
        if side.casefold() == home_name.casefold():
            pool = last.loc[
                last["side"].astype(str).str.casefold().isin({home_name.casefold(), "home"})
            ]
        else:
            pool = last.loc[
                last["side"].astype(str).str.casefold() != home_name.casefold()
            ]
    if pool.empty:
        return None, None, f"side_missing@{last_et.isoformat()}"
    row = pool.iloc[0]
    return float(row["line"]), float(row["price"]), last_et.isoformat()


def main() -> None:
    cfg = load_config()
    teams = load_teams_frame(season=2026, config=cfg)
    cls = _classification(teams)
    names = _school(teams)

    print("=== grade_export(season=2026) ===")
    results = grade_export(season=2026)
    games = results["games"]
    statuses = Counter(g.get("grade_status") for g in games)
    print("GRADE_STATUS", dict(statuses))

    fbs_fbs_ids: set[str] = set()
    for week in range(1, 10):
        gf = _load_partition("games", 2026, week)
        for _, r in gf.iterrows():
            hid, aid = int(r["home_team_id"]), int(r["away_team_id"])
            if cls.get(hid, "?").upper() == "FBS" and cls.get(aid, "?").upper() == "FBS":
                fbs_fbs_ids.add(str(int(r["game_id"])))

    print("\n=== WEEK 5-9 FBS-FBS MAE / coverage ===")
    for week in range(5, 10):
        wg = [
            g
            for g in games
            if int(g.get("week") or 0) == week
            and g.get("grade_status") == "graded"
            and str(g["game_id"]) in fbs_fbs_ids
        ]
        abs_err = [
            abs(g["mu_margin"] - g["actual_margin"])
            for g in wg
            if g.get("mu_margin") is not None and g.get("actual_margin") is not None
        ]
        mae = sum(abs_err) / len(abs_err) if abs_err else float("nan")
        hits = [g for g in wg if g.get("margin_interval_hit") is not None]
        n_hit = sum(1 for g in hits if g["margin_interval_hit"])
        cov = n_hit / len(hits) if hits else float("nan")
        print(
            f"w{week} n={len(wg)} MAE={mae:.4f} cov80={cov:.4f} ({n_hit}/{len(hits)})"
        )

    print("\n=== WEEK-9 POSTED CALLS ===")
    payload = json.loads((ROOT / "data/social/posted_calls_2026_w9.json").read_text())
    gbi9 = _games_by_id(9, names, cfg)
    store = ParquetStore(Path(cfg.paths.staged_dir))
    snaps = store.read("odds_snapshots", filters={"season": 2026}).copy()
    snaps["event_time"] = pd.to_datetime(snaps["event_time"], utc=True)
    gf9 = load_schedule_frame(season=2026, week=9, config=cfg)
    kick_by_gid = {
        str(r["game_id"]): pd.to_datetime(r.get("start_date"), utc=True, errors="coerce")
        for _, r in gf9.iterrows()
    }
    home_by_gid = {
        str(r["game_id"]): names.get(int(r["home_team_id"]), str(r["home_team_id"]))
        for _, r in gf9.iterrows()
    }

    w = l = p = 0
    for c in payload["posted_calls"]:
        result, _bucket = grade_call(c, gbi9)
        if result is None:
            print("NO_FINAL", c["matchup"])
            continue
        cl, cp, note = _closing_line(
            snaps,
            game_id=str(c["game_id"]),
            book=str(c["book"]),
            side=str(c["side"]),
            home_name=home_by_gid.get(str(c["game_id"]), ""),
            kickoff=kick_by_gid.get(str(c["game_id"])),
        )
        print(
            f"  close@{c['book']}: line={cl} price={cp} ({note}) | "
            f"posted {c['line']}/{c['price']}"
        )
        if result == "W":
            w += 1
        elif result == "L":
            l += 1
        else:
            p += 1
    print(f"w9 W-L-P = {w}-{l}-{p}")

    print("\n=== SEASON RECORD (graded calls only, w1-w9) ===")
    season_w = season_l = season_p = 0
    side_stats = {"home": [0, 0, 0], "away": [0, 0, 0], "neutral": [0, 0, 0]}
    for week in range(1, 10):
        path = ROOT / "data/social" / f"posted_calls_2026_w{week}.json"
        if not path.is_file():
            print(f"w{week} MISSING ledger")
            continue
        pl = json.loads(path.read_text(encoding="utf-8"))
        gbi = _games_by_id(week, names, cfg)
        ww = ll = pp = 0
        for c in pl["posted_calls"]:
            result, bucket = grade_call(c, gbi)
            if result is None:
                continue
            if result == "W":
                ww += 1
                side_stats[bucket][0] += 1
            elif result == "L":
                ll += 1
                side_stats[bucket][1] += 1
            else:
                pp += 1
                side_stats[bucket][2] += 1
        print(f"w{week} W-L-P = {ww}-{ll}-{pp}  (math: {ww}+{ll}+{pp}={ww + ll + pp})")
        season_w += ww
        season_l += ll
        season_p += pp
    print(f"SEASON TOTAL W-L-P = {season_w}-{season_l}-{season_p}")
    print(
        "HOME/AWAY/NEUTRAL",
        {k: f"{v[0]}-{v[1]}-{v[2]}" for k, v in side_stats.items()},
    )

    print("\n=== EPA GAPS (teams that played week 9 vs latest publish) ===")
    secrets = load_secrets()
    s3 = boto3.client(
        "s3",
        endpoint_url=cfg.webapp.r2_endpoint_url or None,
        aws_access_key_id=secrets.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=secrets.r2_secret_access_key.get_secret_value(),
        region_name="auto",
    )
    meta = json.loads(
        s3.get_object(Bucket=cfg.webapp.r2_bucket, Key="latest/meta.json")["Body"].read()
    )
    ratings = json.loads(
        s3.get_object(Bucket=cfg.webapp.r2_bucket, Key="latest/team_ratings_2026.json")[
            "Body"
        ].read()
    )
    publish_as_of = datetime.fromisoformat(
        str(meta["published_at"]).replace("Z", "+00:00")
    )
    teams_art = ratings.get("teams") or {}
    print(
        "publish_as_of",
        publish_as_of.isoformat(),
        "week",
        meta.get("week"),
        "team_count",
        len(teams_art),
        "fixture",
        meta.get("fixture"),
    )

    gdf = _load_partition("games", 2026, 9)
    plays = _load_partition("plays", 2026, 9)
    obs = (
        build_game_observations_from_plays(plays, gdf)
        if not gdf.empty and not plays.empty
        else pd.DataFrame()
    )
    obs_gids = set(obs["game_id"].astype(str)) if not obs.empty else set()
    completed = gdf[gdf["completed"].astype(bool)].copy() if not gdf.empty else gdf
    play_by_game_side: dict[tuple[str, int], int] = defaultdict(int)
    if not plays.empty:
        snaps_p = plays.dropna(subset=["game_id", "offense_id"])
        for (gid, oid), n in snaps_p.groupby(["game_id", "offense_id"]).size().items():
            play_by_game_side[(str(int(gid)), int(oid))] = int(n)

    played: set[int] = set()
    for _, r in completed.iterrows():
        played.add(int(r["home_team_id"]))
        played.add(int(r["away_team_id"]))

    gaps: list[dict] = []
    counts: Counter[str] = Counter()
    for tid in sorted(played):
        rating_weeks = {
            int(w["week"])
            for w in (teams_art.get(str(tid), {}) or {}).get("weeks", [])
        }
        if 9 in rating_weeks:
            klass = "HAS-WEEK9-RATING"
            game_id = None
            opp = None
        else:
            tg = completed[
                (completed["home_team_id"].astype(int) == tid)
                | (completed["away_team_id"].astype(int) == tid)
            ]
            r = tg.iloc[0]
            gid = str(int(r["game_id"]))
            is_home = int(r["home_team_id"]) == tid
            opp_id = int(r["away_team_id"] if is_home else r["home_team_id"])
            opp = names.get(opp_id, str(opp_id))
            game_id = gid
            n_plays = play_by_game_side.get((gid, tid), 0)
            if gid not in obs_gids and n_plays == 0:
                klass = "NO-PBP"
            elif gid not in obs_gids:
                klass = "FILTER-MISSED"
            else:
                klass = "PENDING"
        counts[klass] += 1
        gaps.append(
            {
                "team_id": tid,
                "school": names.get(tid, str(tid)),
                "class": klass,
                "game_id": game_id,
                "opp": opp,
            }
        )

    print("COUNTS", dict(counts))
    if counts.get("FILTER-MISSED"):
        print("STOP FILTER-MISSED:")
        for g in gaps:
            if g["class"] == "FILTER-MISSED":
                print(g)
    pending = [g for g in gaps if g["class"] == "PENDING"]
    print(f"PENDING n={len(pending)}")
    for g in pending[:5]:
        print(" sample", g)
    (OUT / "audit_summary.json").write_text(
        json.dumps(
            {
                "grade_status": dict(statuses),
                "season_wlp": [season_w, season_l, season_p],
                "side_stats": side_stats,
                "epa_counts": dict(counts),
                "gaps": gaps,
                "generated_at": datetime.now(tz=UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
