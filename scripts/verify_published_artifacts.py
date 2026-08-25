#!/usr/bin/env python3
"""Read-only verifier for published Ridge artifacts under an R2 prefix.

Usage:
  uv run python scripts/verify_published_artifacts.py sandbox/latest/
  uv run python scripts/verify_published_artifacts.py latest/

Exits 0 on all-pass, 1 otherwise. No writes, no publish, no R2 mutation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from typing import Any

from ncaa_quant.config import load_config, load_secrets

GID_RE = re.compile(r"^[0-9]{6,12}$")

EARLY_IDS: dict[str, str] = {
    "401856766": "2026-08-29T16:00:00Z",
    "401864494": "2026-08-29T19:00:00Z",
    "401858202": "2026-08-29T19:30:00Z",
    "401864577": "2026-08-29T21:30:00Z",
    "401866408": "2026-08-29T22:30:00Z",
    "401858201": "2026-08-29T23:00:00Z",
    "401864570": "2026-08-29T23:00:00Z",
    "401862693": "2026-08-30T02:00:00Z",
}

# Phase A operator baseline — tomorrow's comparison target.
EXPECTED_IDENTITY: dict[str, Any] = {
    "vintage_label": "W9A_REVAL",
    "ensemble_scope_label": "REDUCED_PER_ADR_0013",
    "feature_time_label": "FEATURE_TIME=TUESDAY_DECISION",
    "champion_version": 2,
    "model_version": "production-v0_reduced_v3",
    "meta.champion_model": {
        "registry_name": "ncaa-quant",
        "champion_version": 2,
        "model_version": "production-v0_reduced_v3",
        "registered_at": "2026-08-17T20:41:49Z",
    },
}


def _normalize_prefix(prefix: str) -> str:
    p = prefix.strip().lstrip("/")
    if not p.endswith("/"):
        p += "/"
    return p


def _s3_client() -> Any:
    import boto3

    cfg = load_config()
    secrets = load_secrets()
    return boto3.client(
        "s3",
        endpoint_url=cfg.webapp.r2_endpoint_url or None,
        aws_access_key_id=secrets.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=secrets.r2_secret_access_key.get_secret_value(),
        region_name="auto",
    )


def _get_json(client: Any, *, bucket: str, key: str) -> dict[str, Any]:
    obj = client.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read().decode("utf-8")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        msg = f"{key}: expected JSON object"
        raise TypeError(msg)
    return payload


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _check(name: str, ok: bool, observed: Any, failures: list[str]) -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: {observed}")
    if not ok:
        failures.append(f"{name}: {observed}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prefix",
        help="R2 key prefix, e.g. sandbox/latest/ or latest/",
    )
    args = parser.parse_args(argv)
    prefix = _normalize_prefix(args.prefix)

    cfg = load_config()
    bucket = cfg.webapp.r2_bucket
    if not bucket:
        print("[FAIL] webapp.r2_bucket is not configured")
        return 1

    client = _s3_client()
    failures: list[str] = []

    week_key = f"{prefix}week_predictions.json"
    meta_key = f"{prefix}meta.json"
    print(f"prefix={prefix!r} bucket={bucket!r}")
    print(f"fetching {week_key} and {meta_key}")

    week = _get_json(client, bucket=bucket, key=week_key)
    meta = _get_json(client, bucket=bucket, key=meta_key)
    games = list(week.get("games") or [])

    # --- item 3: eight early kickoffs ---
    by_id = {str(g.get("game_id")): g for g in games if isinstance(g, dict)}
    early_obs = {
        gid: (by_id[gid].get("kickoff_utc") if gid in by_id else None) for gid in EARLY_IDS
    }
    early_ok = all(
        gid in by_id and str(by_id[gid].get("kickoff_utc")) == expected
        for gid, expected in EARLY_IDS.items()
    )
    _check("3_early_kickoffs", early_ok, early_obs, failures)

    # --- item 7: game_id shape ---
    bad_gids = [str(g.get("game_id")) for g in games if not GID_RE.match(str(g.get("game_id")))]
    _check("7_game_id_shape", bad_gids == [], {"failures": bad_gids, "n": len(games)}, failures)

    # --- item 8: no kickoff <= as_of ---
    as_of_raw = week.get("as_of") or meta.get("as_of")
    past: list[str] = []
    if as_of_raw:
        as_of = _parse_utc(str(as_of_raw))
        for g in games:
            ko = g.get("kickoff_utc")
            if ko is None:
                continue
            if _parse_utc(str(ko)) <= as_of:
                past.append(str(g.get("game_id")))
    else:
        past = ["<missing as_of>"]
    _check(
        "8_kickoff_gt_as_of",
        past == [],
        {"as_of": as_of_raw, "violations": past},
        failures,
    )

    # --- item 9: schema 1.3.0; fixture absent/false ---
    schema_week = week.get("schema_version")
    schema_meta = meta.get("schema_version")
    fixture = week.get("fixture", meta.get("fixture"))
    schema_ok = schema_week == "1.3.0" and schema_meta == "1.3.0"
    fixture_ok = fixture is None or fixture is False
    _check(
        "9_schema_and_fixture",
        schema_ok and fixture_ok,
        {
            "week.schema_version": schema_week,
            "meta.schema_version": schema_meta,
            "fixture": fixture,
        },
        failures,
    )

    # --- item 10: identity stamps ---
    identity = week.get("model_identity") or {}
    champ = meta.get("champion_model") or {}
    observed_identity = {
        "vintage_label": week.get("vintage_label") or meta.get("vintage_label"),
        "ensemble_scope_label": week.get("ensemble_scope_label")
        or meta.get("ensemble_scope_label"),
        "feature_time_label": week.get("feature_time_label") or meta.get("feature_time_label"),
        "champion_version": identity.get("champion_version"),
        "model_version": identity.get("model_version"),
        "meta.champion_model": champ,
    }
    identity_ok = observed_identity == EXPECTED_IDENTITY
    _check("10_identity_stamps", identity_ok, observed_identity, failures)

    # --- meta_last + schema major ---
    # meta_last is a push-audit property; from objects we assert meta exists and
    # is readable after data files (presence of both week + meta under prefix).
    meta_present = bool(meta)
    major = str(schema_meta or "").split(".", 1)[0]
    _check(
        "meta_last_readable",
        meta_present,
        {"meta_keys": sorted(meta.keys()), "meta_key": meta_key},
        failures,
    )
    _check("schema_major", major == "1", {"schema_version": schema_meta, "major": major}, failures)

    if failures:
        print(f"\nFAILED ({len(failures)} check(s))")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
