"""W9-PUSH operator: attended fixture push (sandbox / live / rollback-sandbox).

Reads committed ``webapp/fixtures/*.json`` (or the local ``latest-pre`` backup)
and calls stock ``push_artifacts_to_r2``. Does not import or call
``run_fixture_week_publish`` / ``run_chaos_stale_publish``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.webapp.export import (
    WITHDRAWN_FIELDS,
    assert_game_prediction_allowlist,
)
from ncaa_quant.webapp.push import (
    CFBD_GAME_ID_PATTERN,
    push_artifacts_to_r2,
    validate_live_publish_game_ids,
)

FIXTURE_DIR = Path("webapp/fixtures")
BACKUP_DIR = Path("docs/notes/_artifacts/webapp-w9push/latest-pre")
OUT_DIR = Path("docs/notes/_artifacts/webapp-w9push")
FIXTURE_NAMES = (
    "week_predictions.json",
    "track_record.json",
    "results_2024.json",
    "team_ratings_2024.json",
    "meta.json",
)
WITHDRAWN_NAMES = tuple(sorted(WITHDRAWN_FIELDS)) + ("p_ats_home", "p_ou_over")


def _load_dir(source: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for name in FIXTURE_NAMES:
        path = source / name
        artifacts[name] = path.read_text(encoding="utf-8")
    return artifacts


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _s3_client():
    cfg = load_config()
    secrets = load_secrets()
    return boto3.client(
        "s3",
        endpoint_url=cfg.webapp.r2_endpoint_url or None,
        aws_access_key_id=secrets.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=secrets.r2_secret_access_key.get_secret_value(),
        region_name="auto",
    ), cfg


def _run_allowlist(week_payload: dict[str, Any]) -> dict[str, Any]:
    games = week_payload.get("games") or []
    for game in games:
        if not isinstance(game, dict):
            raise SystemExit("week_predictions.games entry is not an object")
        assert_game_prediction_allowlist(game)
    return {"ran": True, "passed": True, "n_games": len(games)}


def _inspect_week(payload: dict[str, Any]) -> dict[str, Any]:
    games = payload.get("games") or []
    withdrawn_hits: dict[str, int] = {}
    for name in WITHDRAWN_NAMES:
        withdrawn_hits[name] = sum(1 for g in games if isinstance(g, dict) and name in g)
    as_of = payload.get("published_at")
    if games and isinstance(games[0], dict) and games[0].get("published_at"):
        as_of = games[0]["published_at"]
    return {
        "schema_version": payload.get("schema_version"),
        "fixture": payload.get("fixture"),
        "published_at": payload.get("published_at"),
        "as_of": as_of,
        "n_games": len(games),
        "withdrawn_key_counts": withdrawn_hits,
        "game0_id": games[0]["game_id"] if games and isinstance(games[0], dict) else None,
        "game0_kickoff_utc": (
            games[0].get("kickoff_utc") if games and isinstance(games[0], dict) else None
        ),
        "game0_published_at": (
            games[0].get("published_at") if games and isinstance(games[0], dict) else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=("sandbox", "live", "rollback-sandbox", "rollback-live"),
    )
    args = parser.parse_args()

    source = BACKUP_DIR if args.mode.startswith("rollback") else FIXTURE_DIR
    publish_scope = "live" if args.mode in {"live", "rollback-live"} else "sandbox"
    artifacts = _load_dir(source)

    fixture_flags = {
        name: json.loads(text).get("fixture") for name, text in artifacts.items()
    }
    week = json.loads(artifacts["week_predictions.json"])
    meta = json.loads(artifacts["meta.json"])

    allowlist_report: dict[str, Any]
    if args.mode == "live":
        allowlist_report = _run_allowlist(week)
    else:
        allowlist_report = {
            "ran": False,
            "passed": None,
            "reason": (
                "not invoked on sandbox/rollback; push.py does not call it. "
                "rollback-live must skip the 1.2.0 allowlist because the backup "
                "is schema 1.1.0 and still carries withdrawn keys"
            ),
        }

    cfbd_calls: list[dict[str, Any]] = []
    original = validate_live_publish_game_ids

    def traced_validate(payload: Any) -> None:
        started = time.monotonic()
        original(payload)
        ids = []
        week_obj = json.loads(
            payload["week_predictions.json"]
            if isinstance(payload["week_predictions.json"], str)
            else payload["week_predictions.json"].decode("utf-8")
        )
        for game in week_obj.get("games") or []:
            if isinstance(game, dict) and game.get("game_id") is not None:
                ids.append(str(game["game_id"]))
        cfbd_calls.append(
            {
                "ran": True,
                "passed": True,
                "pattern": CFBD_GAME_ID_PATTERN.pattern,
                "n_ids": len(ids),
                "all_match": all(CFBD_GAME_ID_PATTERN.fullmatch(gid) for gid in ids),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        )

    import ncaa_quant.webapp.push as push_mod

    push_mod.validate_live_publish_game_ids = traced_validate  # type: ignore[method-assign]

    started = time.monotonic()
    push_started_utc = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = push_artifacts_to_r2(
        artifacts,
        season=int(meta["season"]),
        week=int(meta["week"]),
        refresh_kind=str(meta["refresh_kind"]),
        schema_version=str(meta["schema_version"]),
        publish_scope=publish_scope,  # type: ignore[arg-type]
    )
    elapsed_s = time.monotonic() - started

    client, cfg = _s3_client()
    get_key = (
        "sandbox/latest/week_predictions.json"
        if publish_scope == "sandbox"
        else "latest/week_predictions.json"
    )
    fetched = client.get_object(Bucket=cfg.webapp.r2_bucket, Key=get_key)["Body"].read()
    round_trip = json.loads(fetched.decode("utf-8"))
    inspect = _inspect_week(round_trip)
    round_trip_sha = hashlib.sha256(fetched).hexdigest()

    report = {
        "mode": args.mode,
        "source_dir": str(source).replace("\\", "/"),
        "publish_scope": publish_scope,
        "push_started_utc": push_started_utc,
        "elapsed_s": round(elapsed_s, 3),
        "bucket": result.get("bucket"),
        "upload_order": result.get("upload_order"),
        "meta_last": result.get("meta_last"),
        "content_hashes": result.get("content_hashes"),
        "uploads": result.get("uploads"),
        "revalidation": result.get("revalidation"),
        "fixture_flags": fixture_flags,
        "source_sha256": {name: _sha256_text(text) for name, text in artifacts.items()},
        "allowlist": allowlist_report,
        "cfbd_id_guard": cfbd_calls[0]
        if cfbd_calls
        else {
            "ran": False,
            "passed": None,
            "reason": "validate_live_publish_game_ids not called (publish_scope != live)",
            "pattern": CFBD_GAME_ID_PATTERN.pattern,
        },
        "round_trip": {
            "key": get_key,
            "bytes": len(fetched),
            "sha256": round_trip_sha,
            **inspect,
        },
    }
    out_name = {
        "sandbox": "sandbox_rehearsal.json",
        "live": "live_push.json",
        "rollback-sandbox": "rollback_sandbox_dryrun.json",
        "rollback-live": "rollback_live.json",
    }[args.mode]
    out_path = OUT_DIR / out_name
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"wrote {out_path.as_posix()}")


if __name__ == "__main__":
    main()
