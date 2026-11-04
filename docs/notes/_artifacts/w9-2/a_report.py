"""W9-2 section A post-publish checks (read-only except printing)."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import boto3

from ncaa_quant.config import load_config, load_secrets

ROOT = Path(__file__).resolve().parents[3]


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
    ):
        if k in meta:
            print(k, meta[k])
    print("model_identity", meta.get("model_identity"))
    print("meta_keys", sorted(meta.keys()))

    hist_path = Path(cfg.webapp.publish_history_path) / "2026_w9.jsonl"
    lines = hist_path.read_text(encoding="utf-8").strip().splitlines()
    print("\n=== publish_history 2026_w9.jsonl ===")
    print("n_lines", len(lines), "path", hist_path)
    o = json.loads(lines[-1])
    print(
        "week",
        o.get("week"),
        "refresh",
        o.get("refresh_kind"),
        "as_of_source",
        o.get("as_of_source"),
        "n_games",
        len(o.get("games") or []),
        "vintage",
        o.get("vintage_label"),
        "identity",
        o.get("model_identity"),
    )

    # fixture flags on latest objects
    print("\n=== fixture flags ===")
    for key in keys:
        body = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        if isinstance(body, dict):
            print(key, "fixture=", body.get("fixture"))

    # ratings weeks 1-8 + week 9 current
    ratings = json.loads(
        s3.get_object(Bucket=bucket, Key="latest/team_ratings_2026.json")["Body"].read()
    )
    teams = ratings.get("teams") or {}
    print("\n=== team_ratings_2026 ===")
    print("team_count", len(teams))
    n9 = sum(
        1
        for t in teams.values()
        if any(int(w["week"]) == 9 for w in (t.get("weeks") or []))
    )
    print("teams_with_week9", n9)
    missing_by_week: dict[int, int] = Counter()
    for tid, entry in teams.items():
        weeks = {int(w["week"]) for w in (entry.get("weeks") or [])}
        for week in range(1, 9):
            if week not in weeks:
                missing_by_week[week] += 1
    print("missing_counts_weeks_1_8", dict(missing_by_week))

    # results card / season
    results = json.loads(
        s3.get_object(Bucket=bucket, Key="latest/results_2026.json")["Body"].read()
    )
    print("\n=== results_2026 top keys ===", list(results.keys())[:20])
    tr = results.get("track_record") or results.get("season_record") or {}
    print("trackish", tr if not isinstance(tr, dict) else {k: tr.get(k) for k in list(tr)[:20]})
    # also local track_record
    track = json.loads(
        s3.get_object(Bucket=bucket, Key="latest/track_record.json")["Body"].read()
    )
    print("track_record", json.dumps(track)[:800])

    # week predictions n + kicked check
    wp = json.loads(
        s3.get_object(Bucket=bucket, Key="latest/week_predictions.json")["Body"].read()
    )
    games = wp.get("games") or []
    print("\nweek_predictions n_games", len(games), "week", wp.get("week"))
    print(
        "as_of_source",
        wp.get("as_of_source"),
        "vintage",
        wp.get("vintage_label"),
        "identity",
        wp.get("model_identity"),
    )


if __name__ == "__main__":
    main()
