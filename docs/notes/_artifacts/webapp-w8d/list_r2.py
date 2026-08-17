"""Read-only R2 inventory for W8-D D5.4. No puts, no deletes."""

from __future__ import annotations

import json
from collections import defaultdict

import boto3

from ncaa_quant.config import load_config, load_secrets

cfg = load_config()
sec = load_secrets()
s3 = boto3.client(
    "s3",
    endpoint_url=cfg.webapp.r2_endpoint_url or None,
    aws_access_key_id=sec.r2_access_key_id.get_secret_value(),
    aws_secret_access_key=sec.r2_secret_access_key.get_secret_value(),
    region_name="auto",
)
bucket = cfg.webapp.r2_bucket
keys: list[dict[str, object]] = []
token = None
while True:
    kwargs: dict[str, object] = {"Bucket": bucket, "MaxKeys": 1000}
    if token:
        kwargs["ContinuationToken"] = token
    resp = s3.list_objects_v2(**kwargs)
    for obj in resp.get("Contents", []):
        keys.append(
            {
                "Key": obj["Key"],
                "Size": obj["Size"],
                "LastModified": obj["LastModified"].isoformat(),
            }
        )
    if not resp.get("IsTruncated"):
        break
    token = resp.get("NextContinuationToken")

print(f"bucket={bucket}")
print(f"key_count={len(keys)}")
prefixes: dict[str, int] = defaultdict(int)
for k in keys:
    prefixes[str(k["Key"]).split("/")[0]] += 1
print("top_prefixes=" + json.dumps(dict(sorted(prefixes.items()))))
print("---ALL KEYS---")
for k in sorted(keys, key=lambda x: str(x["Key"])):
    print(f"{k['Size']:8d}  {k['LastModified']}  {k['Key']}")

print("---LATEST JSON FIXTURE FLAGS---")
latest = [k for k in keys if str(k["Key"]).startswith("latest/") and str(k["Key"]).endswith(".json")]
for k in sorted(latest, key=lambda x: str(x["Key"])):
    body = s3.get_object(Bucket=bucket, Key=k["Key"])["Body"].read()
    data = json.loads(body)
    season = data.get("season")
    week = data.get("week")
    fixture = data.get("fixture")
    published = data.get("published_at")
    n_games = len(data.get("games", [])) if isinstance(data.get("games"), list) else None
    n_metrics = len(data.get("metrics", [])) if isinstance(data.get("metrics"), list) else None
    print(
        f"{k['Key']} fixture={fixture!r} season={season} week={week} "
        f"published_at={published} games={n_games} metrics={n_metrics} bytes={k['Size']}"
    )
