"""W9-PUSH operator: download current R2 latest/* to a local rollback copy.

Local-only backup. Does not write any bucket prefix.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import boto3

from ncaa_quant.config import load_config, load_secrets

OUT_DIR = Path("docs/notes/_artifacts/webapp-w9push/latest-pre")
MANIFEST_PATH = Path("docs/notes/_artifacts/webapp-w9push/backup_manifest.json")


def main() -> None:
    cfg = load_config()
    secrets = load_secrets()
    bucket = cfg.webapp.r2_bucket
    if not bucket:
        raise SystemExit("webapp.r2_bucket is not configured")

    client = boto3.client(
        "s3",
        endpoint_url=cfg.webapp.r2_endpoint_url or None,
        aws_access_key_id=secrets.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=secrets.r2_secret_access_key.get_secret_value(),
        region_name="auto",
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paginator = client.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix="latest/"):
        for obj in page.get("Contents") or []:
            key = str(obj["Key"])
            if key.endswith("/"):
                continue
            keys.append(key)
    keys.sort()

    entries: list[dict[str, object]] = []
    for key in keys:
        filename = key.split("/", 1)[1]
        dest = OUT_DIR / filename
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        dest.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        last_modified = response.get("LastModified")
        last_modified_utc = (
            last_modified.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if last_modified is not None
            else None
        )
        entries.append(
            {
                "key": key,
                "local_path": str(dest).replace("\\", "/"),
                "bytes": len(body),
                "sha256": digest,
                "last_modified_utc": last_modified_utc,
            }
        )
        print(f"{key}\t{len(body)}\t{digest}")

    manifest = {
        "captured_at_utc": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bucket": bucket,
        "prefix": "latest/",
        "object_count": len(entries),
        "objects": entries,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST_PATH.as_posix()} n={len(entries)}")


if __name__ == "__main__":
    main()
