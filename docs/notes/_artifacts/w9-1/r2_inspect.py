"""W9-1: read R2 latest meta + odds age (read-only)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import boto3

from ncaa_quant.config import load_config, load_secrets


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
    now = datetime.now(tz=UTC)
    for key in [
        "latest/meta.json",
        "latest/odds_snapshot.json",
        "latest/week_predictions.json",
        "latest/team_ratings_2026.json",
        "latest/results_2026.json",
        "latest/track_record.json",
    ]:
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
            lm = head["LastModified"]
            age_h = (now - lm).total_seconds() / 3600.0
            size = head["ContentLength"]
            print(f"{key} LastModified={lm.isoformat()} age_h={age_h:.2f} size={size}")
        except Exception as exc:  # noqa: BLE001
            print(f"{key} ERR {type(exc).__name__}: {exc}")

    meta = json.loads(
        s3.get_object(Bucket=bucket, Key="latest/meta.json")["Body"].read()
    )
    print("meta.published_at", meta.get("published_at"))
    print("meta.week", meta.get("week"))
    print("meta.refresh_kind", meta.get("refresh_kind"))
    print("meta.season", meta.get("season"))
    print("meta.fixture", meta.get("fixture"))
    print("meta.model_identity", meta.get("model_identity") or meta.get("champion"))
    odds = json.loads(
        s3.get_object(Bucket=bucket, Key="latest/odds_snapshot.json")["Body"].read()
    )
    if isinstance(odds, dict):
        print("odds_top_keys", list(odds.keys())[:25])
        for k in (
            "captured_at",
            "as_of",
            "generated_at",
            "fetched_at",
            "snapshot_at",
            "event_time",
            "written_at",
            "as_of_utc",
        ):
            if k in odds:
                print("odds", k, odds[k])
        # nested common
        for nest in ("meta", "header", "snapshot"):
            if isinstance(odds.get(nest), dict):
                print(f"odds.{nest} keys", list(odds[nest].keys())[:20])


if __name__ == "__main__":
    main()
