"""W2-3a — GET-only fetch of latest/week_predictions.json from R2."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3

from ncaa_quant.config import load_config, load_secrets

TARGET_PUBLISHED_AT = "2026-09-08T15:34:11Z"
OUT = Path("out/scratch/w2_3a/week_predictions.json")
HISTORY = Path("data/webapp/publish_history/2026_w2.jsonl")
SIDECAR = Path("data/social/2026/w2/tuesday_primary/candidates.json")
R2_KEY = "latest/week_predictions.json"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    cfg = load_config()
    secrets = load_secrets()
    assert cfg.webapp.export_enabled is False
    bucket = cfg.webapp.r2_bucket
    if not bucket:
        raise SystemExit("webapp.r2_bucket not configured")
    access = secrets.r2_access_key_id.get_secret_value()
    secret = secrets.r2_secret_access_key.get_secret_value()
    if not access or not secret:
        raise SystemExit("R2 credentials missing")

    s3 = boto3.client(
        "s3",
        endpoint_url=cfg.webapp.r2_endpoint_url or None,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="auto",
    )
    print(f"GET s3://{bucket}/{R2_KEY} (read-only)")
    resp = s3.get_object(Bucket=bucket, Key=R2_KEY)
    body = resp["Body"].read()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(body)
    print(f"local_path={OUT.resolve()}")
    print(f"bytes={len(body)}")

    wp = json.loads(body.decode("utf-8"))
    published_at = wp.get("published_at")
    season = wp.get("season")
    week = wp.get("week")
    schema = wp.get("schema_version")
    games = wp.get("games") or []
    n_games = len(games)
    fixture = wp.get("fixture", None)

    print("\n=== verification ===")
    print(f"published_at={published_at} (want {TARGET_PUBLISHED_AT})")
    print(f"season={season} (want 2026)")
    print(f"week={week} (want 2)")
    print(f"n_games={n_games} (want 86)")
    print(f"schema_version={schema} (want 1.3.0)")
    print(f"fixture={fixture!r}")

    if published_at != TARGET_PUBLISHED_AT:
        print("STOP: published_at mismatch — do not use this file")
        raise SystemExit(2)

    # publish_history game_id set
    hist_rows = [
        json.loads(l) for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    hist = next(r for r in hist_rows if r.get("published_at") == TARGET_PUBLISHED_AT)
    hist_ids = {str(g["game_id"]) for g in hist["games"]}
    wp_ids = {str(g["game_id"]) for g in games}
    only_hist = sorted(hist_ids - wp_ids)
    only_wp = sorted(wp_ids - hist_ids)
    print(f"\nhist_n={len(hist_ids)} wp_n={len(wp_ids)}")
    print(f"only_in_history={only_hist or '[]'}")
    print(f"only_in_week_predictions={only_wp or '[]'}")
    if only_hist or only_wp:
        print("STOP: game_id set symmetric difference non-empty")
        raise SystemExit(3)

    sc = json.loads(SIDECAR.read_text(encoding="utf-8"))
    sidecar_ids = [str(r["game_id"]) for r in sc.get("accepted") or []]
    missing = [g for g in sidecar_ids if g not in wp_ids]
    print(f"\nsidecar_accepted={sidecar_ids}")
    print(f"missing_from_predictions={missing or '[]'}")
    if missing:
        print("STOP: sidecar game_ids missing from predictions")
        raise SystemExit(4)

    if fixture is True:
        print("STOP: fixture=True")
        raise SystemExit(5)

    print("\nOK: artifact matches 15:34:11Z generation")


if __name__ == "__main__":
    main()
