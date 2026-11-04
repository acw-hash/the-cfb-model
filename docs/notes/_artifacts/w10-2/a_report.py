"""W10-2 section A post-publish checks."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pandas as pd

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ratings.state_space import build_game_observations_from_plays
from ncaa_quant.webapp.export import load_teams_frame

ROOT = Path(__file__).resolve().parents[3]


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


def main() -> None:
    cfg = load_config()
    secrets = load_secrets()
    s3 = boto3.client(
        "s3",
        endpoint_url=cfg.webapp.r2_endpoint_url or None,
        aws_access_key_id=secrets.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=secrets.r2_secret_access_key.get_secret_value(),
        region_name="auto",
    )
    bucket = cfg.webapp.r2_bucket

    print("=== latest/ keys ===")
    resp = s3.list_objects_v2(Bucket=bucket, Prefix="latest/")
    keys = sorted(o["Key"] for o in (resp.get("Contents") or []))
    for k in keys:
        head = s3.head_object(Bucket=bucket, Key=k)
        print(k, head["LastModified"].isoformat(), head["ContentLength"])

    # versioned week-10 keys
    print("\n=== v1/2026/w10/tuesday_primary/ ===")
    vresp = s3.list_objects_v2(Bucket=bucket, Prefix="v1/2026/w10/tuesday_primary/")
    vkeys = sorted(o["Key"] for o in (vresp.get("Contents") or []))
    for k in vkeys:
        print(k)

    meta = json.loads(
        s3.get_object(Bucket=bucket, Key="latest/meta.json")["Body"].read()
    )
    print("\n=== meta.json ===")
    for k in (
        "published_at",
        "week",
        "season",
        "refresh_kind",
        "vintage_label",
        "as_of",
        "as_of_source",
        "fixture",
        "schema_version",
        "feature_time_label",
        "ensemble_scope_label",
    ):
        if k in meta:
            print(k, meta[k])
    print("champion_model", meta.get("champion_model"))
    print("model_identity", meta.get("model_identity"))
    print("artifact_pointers", meta.get("artifact_pointers"))
    print("meta_last_key_order_ok", keys and keys[-1].endswith("meta.json"))

    hist_path = Path("data/webapp/publish_history/2026_w10.jsonl")
    lines = hist_path.read_text(encoding="utf-8").strip().splitlines()
    print("\n=== publish_history 2026_w10.jsonl ===")
    print("n_lines", len(lines), "path", hist_path)
    o = json.loads(lines[-1])
    print(
        "week",
        o.get("week"),
        "refresh",
        o.get("refresh_kind"),
        "as_of",
        o.get("as_of"),
        "as_of_source",
        o.get("as_of_source"),
        "n_games",
        len(o.get("games") or []),
        "vintage",
        o.get("vintage_label"),
        "identity",
        o.get("model_identity"),
        "published_at",
        o.get("published_at"),
    )
    # coherence / null reasons
    reasons: Counter[str] = Counter()
    n_null = 0
    kicked = []
    as_of = datetime.fromisoformat(str(o["as_of"]).replace("Z", "+00:00"))
    for g in o.get("games") or []:
        nr = g.get("null_reason")
        if nr:
            n_null += 1
            reasons[str(nr)] += 1
        ko = g.get("kickoff_utc")
        if ko:
            kdt = datetime.fromisoformat(str(ko).replace("Z", "+00:00"))
            if kdt <= as_of:
                kicked.append((g.get("game_id"), g.get("away_team"), g.get("home_team"), ko))
    print("coherence_or_null_reason_rows", n_null, dict(reasons))
    print("kicked_off_rows_in_publish", len(kicked), kicked[:5])

    print("\n=== fixture flags ===")
    for key in keys:
        body = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        if isinstance(body, dict):
            print(key, "fixture=", body.get("fixture"))

    # ratings
    ratings = json.loads(
        s3.get_object(Bucket=bucket, Key="latest/team_ratings_2026.json")["Body"].read()
    )
    teams = ratings.get("teams") or {}
    print("\n=== team_ratings_2026 ===")
    print("team_count", len(teams))
    n10 = sum(
        1
        for t in teams.values()
        if any(int(w["week"]) == 10 for w in (t.get("weeks") or []))
    )
    n9 = sum(
        1
        for t in teams.values()
        if any(int(w["week"]) == 9 for w in (t.get("weeks") or []))
    )
    print("teams_with_week9", n9, "teams_with_week10", n10)

    teams_frame = load_teams_frame(season=2026, config=cfg)
    names = {int(r.team_id): str(r.school) for r in teams_frame.itertuples()}
    # classify missing weeks 1-9
    gdfs = {w: _load_partition("games", 2026, w) for w in range(1, 10)}
    plays = {w: _load_partition("plays", 2026, w) for w in range(1, 10)}
    played_weeks: dict[int, set[int]] = {}
    for week, gdf in gdfs.items():
        if gdf.empty:
            continue
        completed = gdf[gdf["completed"].astype(bool)]
        for _, r in completed.iterrows():
            for tid in (int(r["home_team_id"]), int(r["away_team_id"])):
                played_weeks.setdefault(tid, set()).add(week)

    obs_gids_by_week: dict[int, set[str]] = {}
    for week in range(1, 10):
        gdf = gdfs[week]
        pl = plays[week]
        if gdf.empty or pl.empty:
            obs_gids_by_week[week] = set()
            continue
        obs = build_game_observations_from_plays(pl, gdf)
        obs_gids_by_week[week] = (
            set(obs["game_id"].astype(str)) if not obs.empty else set()
        )

    missing_class: Counter[str] = Counter()
    missing_detail: list[tuple[int, int, str]] = []
    for tid_s, entry in teams.items():
        tid = int(tid_s)
        weeks = {int(w["week"]) for w in (entry.get("weeks") or [])}
        for week in range(1, 10):
            if week in weeks:
                continue
            # did they play?
            if week not in played_weeks.get(tid, set()):
                missing_class["BYE"] += 1
                continue
            # find their game
            gdf = gdfs[week]
            tg = gdf[
                (gdf["home_team_id"].astype(int) == tid)
                | (gdf["away_team_id"].astype(int) == tid)
            ]
            if tg.empty:
                missing_class["BYE"] += 1
                continue
            gid = str(int(tg.iloc[0]["game_id"]))
            if gid not in obs_gids_by_week[week]:
                missing_class["NO-PBP"] += 1
            else:
                missing_class["PLAYED-MISSING"] += 1
                missing_detail.append((tid, week, names.get(tid, str(tid))))
    print("missing_weeks_1_9_class", dict(missing_class))
    if missing_detail:
        print("PLAYED-MISSING samples", missing_detail[:20])

    # week-9 players should have week-10 current
    g9 = gdfs[9]
    completed9 = g9[g9["completed"].astype(bool)] if not g9.empty else g9
    played9: set[int] = set()
    for _, r in completed9.iterrows():
        played9.add(int(r["home_team_id"]))
        played9.add(int(r["away_team_id"]))
    have10 = [
        tid
        for tid in played9
        if any(
            int(w["week"]) == 10
            for w in (teams.get(str(tid), {}) or {}).get("weeks", [])
        )
    ]
    miss10 = sorted(played9 - set(have10))
    print("week9_played", len(played9), "have_week10", len(have10), "missing_week10", len(miss10))
    if miss10:
        print("missing_week10_teams", [(t, names.get(t)) for t in miss10[:20]])

    # Confirm week-9 results folded into week-10 EPA (not just label)
    # Compare week-9 vs week-10 off_epa for a sample of week-9 participants
    changed = 0
    unchanged = 0
    samples = []
    for tid in sorted(played9)[:]:
        entry = teams.get(str(tid)) or {}
        by_week = {int(w["week"]): w for w in (entry.get("weeks") or [])}
        w9 = by_week.get(9)
        w10 = by_week.get(10)
        if not w9 or not w10:
            continue
        delta = abs(float(w10["off_epa"]) - float(w9["off_epa"])) + abs(
            float(w10["def_epa"]) - float(w9["def_epa"])
        )
        if delta > 1e-9:
            changed += 1
        else:
            unchanged += 1
            if len(samples) < 8:
                samples.append((tid, names.get(tid), w9["off_epa"], w10["off_epa"]))
    print(
        "week9_vs_week10_epa_changed",
        changed,
        "unchanged",
        unchanged,
        "unchanged_samples",
        samples,
    )

    # results / track record for card
    results = json.loads(
        s3.get_object(Bucket=bucket, Key="latest/results_2026.json")["Body"].read()
    )
    print("\n=== results_2026 ===")
    print("top_keys", list(results.keys())[:25])
    track = json.loads(
        s3.get_object(Bucket=bucket, Key="latest/track_record.json")["Body"].read()
    )
    print("track_record_keys", list(track.keys())[:30])
    # search for ATS / card record fields
    blob = json.dumps(results)
    for needle in ["24-21", "3-4", "21-17", "ATS", "posted", "card"]:
        print("results_contains", needle, needle in blob)
    blob2 = json.dumps(track)
    for needle in ["24-21", "3-4", "21-17", "ats", "record"]:
        print("track_contains", needle, needle.lower() in blob2.lower())

    # local ledger grade echo
    print("\n(ledger echo from W10-1: week9 card 3-4-0, season 24-21-1)")

    wp = json.loads(
        s3.get_object(Bucket=bucket, Key="latest/week_predictions.json")["Body"].read()
    )
    print(
        "\nweek_predictions n_games",
        len(wp.get("games") or []),
        "week",
        wp.get("week"),
        "as_of_source",
        wp.get("as_of_source"),
        "vintage",
        wp.get("vintage_label"),
        "identity",
        wp.get("model_identity"),
    )


if __name__ == "__main__":
    main()
