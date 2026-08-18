"""GET sandbox artifacts from R2 and verify W9-D acceptance checks. Read-only."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3

from ncaa_quant.config import load_config, load_secrets

ART = Path(__file__).resolve().parent
CFBD_ID = re.compile(r"^[0-9]{6,12}$")
WITHDRAWN = (
    "p_cover_home",
    "p_over",
    "p_ats_home",
    "p_ou_over",
    "p_cover_home_credible",
    "p_over_credible",
)
AS_OF = datetime.fromisoformat("2026-09-01T10:00:00+00:00")
FILES = (
    "week_predictions.json",
    "track_record.json",
    "meta.json",
    "team_ratings_2026.json",
)


def _walk_keys(obj: Any, found: list[str]) -> None:
    if isinstance(obj, dict):
        found.extend(obj.keys())
        for v in obj.values():
            _walk_keys(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk_keys(item, found)


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
    prefixes = (
        "sandbox/latest/",
        "sandbox/v1/2026/w1/tuesday_primary/",
        "latest/",
    )
    listing: dict[str, list[str]] = {}
    for prefix in prefixes:
        keys: list[str] = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents") or []:
                keys.append(str(obj["Key"]))
        listing[prefix] = sorted(keys)

    out_dir = ART / "sandbox_roundtrip"
    out_dir.mkdir(parents=True, exist_ok=True)
    checks: dict[str, Any] = {"listing": listing, "objects": {}}

    for filename in FILES:
        key = f"sandbox/latest/{filename}"
        response = s3.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        (out_dir / filename).write_bytes(body)
        obj = json.loads(body.decode("utf-8"))
        checks["objects"][filename] = {
            "bytes": len(body),
            "schema_version": obj.get("schema_version"),
            "fixture": obj.get("fixture", False),
            "fixture_key_present": "fixture" in obj,
        }
        if filename == "week_predictions.json":
            games = obj.get("games") or []
            ids = [str(g.get("game_id")) for g in games]
            kicks = []
            for g in games:
                raw = g.get("kickoff_utc")
                kicks.append(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
            withdrawn_counts = {k: 0 for k in WITHDRAWN}
            for g in games:
                for wk in WITHDRAWN:
                    if wk in g:
                        withdrawn_counts[wk] += 1
            all_keys: list[str] = []
            _walk_keys(obj, all_keys)
            checks["objects"][filename].update(
                {
                    "n_games": len(games),
                    "all_ids_cfbd_shape": all(bool(CFBD_ID.fullmatch(i)) for i in ids),
                    "n_ids_failing_shape": sum(1 for i in ids if not CFBD_ID.fullmatch(i)),
                    "withdrawn_key_counts": withdrawn_counts,
                    "kickoff_min": min(kicks).isoformat() if kicks else None,
                    "kickoff_max": max(kicks).isoformat() if kicks else None,
                    "as_of_precedes_every_kickoff": all(AS_OF < k for k in kicks) if kicks else False,
                    "n_kickoff_before_as_of": sum(1 for k in kicks if k <= AS_OF),
                    "published_at": obj.get("published_at"),
                    "feature_time_label": obj.get("feature_time_label"),
                    "model_identity": obj.get("model_identity"),
                    "sample_ids": ids[:5],
                }
            )
        if filename == "meta.json":
            checks["objects"][filename].update(
                {
                    "season": obj.get("season"),
                    "week": obj.get("week"),
                    "published_at": obj.get("published_at"),
                    "next_expected_publish_utc": obj.get("next_expected_publish_utc"),
                    "refresh_kind": obj.get("refresh_kind"),
                }
            )

    # results_2026 should be absent (empty live season).
    results_key = "sandbox/latest/results_2026.json"
    try:
        s3.get_object(Bucket=bucket, Key=results_key)
        checks["results_2026"] = {"present": True}
    except Exception as exc:  # noqa: BLE001
        checks["results_2026"] = {"present": False, "error": type(exc).__name__}

    live_latest = listing.get("latest/") or []
    checks["production_latest_untouched_listing"] = live_latest
    (ART / "r2_verify.json").write_text(json.dumps(checks, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(checks, indent=2, default=str))


if __name__ == "__main__":
    main()
