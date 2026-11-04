"""Pull latest team_ratings_2026.json from R2 into social artifacts cache."""

from __future__ import annotations

import json
from pathlib import Path

import boto3

from ncaa_quant.config import load_config, load_secrets

OUT = Path("data/social/artifacts_cache/latest/team_ratings_2026.json")
CARD = [
    "Auburn",
    "Navy",
    "Minnesota",
    "Florida State",
    "South Carolina",
    "Army",
    "UCF",
]


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
    raw = s3.get_object(Bucket=cfg.webapp.r2_bucket, Key="latest/team_ratings_2026.json")[
        "Body"
    ].read()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(raw)
    data = json.loads(raw)
    print("wrote", OUT.resolve(), "bytes", len(raw))
    print("published_at", data.get("published_at"), "n_teams", len(data["teams"]))
    by_school = {t["school"]: t for t in data["teams"].values()}
    for school in CARD:
        t = by_school[school]
        w9 = next(w for w in t["weeks"] if int(w["week"]) == 9)
        print(
            f"{school}: off={w9['off_epa']:+.4f} def={w9['def_epa']:+.4f} "
            f"pace={w9['pace']:+.4f} weeks={[int(w['week']) for w in t['weeks']]}"
        )


if __name__ == "__main__":
    main()
