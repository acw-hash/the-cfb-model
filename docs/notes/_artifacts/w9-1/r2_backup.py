"""W9-1: copy R2 latest/* → backup_latest_20261027/ (read latest, write backup only)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import boto3

from ncaa_quant.config import load_config, load_secrets

DEST_PREFIX = "backup_latest_20261027/"


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
    resp = s3.list_objects_v2(Bucket=bucket, Prefix="latest/")
    keys = [o["Key"] for o in (resp.get("Contents") or []) if o["Key"] != "latest/"]
    print("source_keys", len(keys))
    for key in sorted(keys):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        sha = hashlib.sha256(body).hexdigest()
        dest = DEST_PREFIX + key.removeprefix("latest/")
        s3.put_object(Bucket=bucket, Key=dest, Body=body)
        print(f"{key} -> {dest} bytes={len(body)} sha256={sha}")
    print("done", datetime.now(tz=UTC).isoformat())


if __name__ == "__main__":
    main()
