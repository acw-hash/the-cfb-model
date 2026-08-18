"""W9-D Phase 1: production predict_publish path, sandbox destination only.

Uses execute_predict_publish (not the idempotent wrapper, not isolated export).
Redirects hysteresis/ledger writes so a real week-1 publish is not contaminated.
Pushes only under sandbox/. Never writes latest/ or v1/*.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent
STATE = ART / "rehearsal_state"
LOCAL = ART / "sandbox_export"

ISO_PATHS = {
    "tier_state": ROOT / "data" / "webapp" / "tier_state.json",
    "tier_changes": ROOT / "data" / "webapp" / "tier_changes.jsonl",
    "idempotency": ROOT / "data" / "pipeline_state" / "idempotency.json",
    "possessions": ROOT / "data" / "artifacts" / "expected_possessions" / "live.json",
}
CFBD_ID = re.compile(r"^[0-9]{6,12}$")
WITHDRAWN = (
    "p_cover_home",
    "p_over",
    "p_ats_home",
    "p_ou_over",
    "p_cover_home_credible",
    "p_over_credible",
)


def _log(msg: str) -> None:
    now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"W9-D {now} {msg}", flush=True)


def sha256_file(path: Path) -> str:
    if not path.is_file():
        return "ABSENT"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hash_isolation() -> dict[str, str]:
    return {name: sha256_file(path) for name, path in ISO_PATHS.items()}


def main() -> None:
    from ncaa_quant.config import load_config
    from ncaa_quant.evaluation.production_stack import StateSpaceRatingEngine
    from ncaa_quant.pipelines.predict import (
        RefreshKind,
        _isolated_publish_config,
        execute_predict_publish,
    )
    from ncaa_quant.webapp.export import SCHEMA_VERSION, export_publish_artifacts
    from ncaa_quant.webapp.push import PublishedKeyAllowlistError, push_artifacts_to_r2

    STATE.mkdir(parents=True, exist_ok=True)
    LOCAL.mkdir(parents=True, exist_ok=True)

    hashes_before = hash_isolation()
    _log("isolation_before=" + json.dumps(hashes_before, sort_keys=True))

    base = load_config()
    _log(f"workstation_export_enabled={base.webapp.export_enabled}")
    if not base.webapp.r2_bucket:
        raise SystemExit("r2_bucket is not configured; refusing to guess a destination")

    # Redirect hysteresis + ledger. Keep R2 settings from the workstation.
    inner = _isolated_publish_config(base, STATE)
    inner = inner.model_copy(
        update={
            "webapp": inner.webapp.model_copy(
                update={
                    "r2_bucket": base.webapp.r2_bucket,
                    "r2_endpoint_url": base.webapp.r2_endpoint_url,
                    "revalidate_url": base.webapp.revalidate_url,
                }
            )
        }
    )
    _log(f"inner_export_enabled={inner.webapp.export_enabled}")
    _log(f"tier_state_path={inner.webapp.tier_state_path}")
    _log(f"idempotency_dir={inner.pipeline.idempotency_dir}")

    timings: dict[str, float] = {}
    orig_init = StateSpaceRatingEngine.initialize_season

    def timed_init(self: Any, season: int, as_of: datetime) -> None:
        t0 = time.perf_counter()
        orig_init(self, season, as_of)
        timings["rating_filter_sec"] = time.perf_counter() - t0

    StateSpaceRatingEngine.initialize_season = timed_init  # type: ignore[method-assign]

    try:
        _log("execute_predict_publish 2026 week 1 (export suppressed)")
        t0 = time.perf_counter()
        result = execute_predict_publish(
            season=2026,
            week=1,
            refresh_kind=RefreshKind.TUESDAY_PRIMARY,
            config=inner,
        )
        timings["execute_predict_publish_sec"] = time.perf_counter() - t0
    finally:
        StateSpaceRatingEngine.initialize_season = orig_init  # type: ignore[method-assign]

    n_rows = len(result.get("prediction_rows") or [])
    _log(f"n_prediction_rows={n_rows}")
    if "webapp_export" in result:
        _log("UNEXPECTED inner export: " + json.dumps(result["webapp_export"], default=str))
        raise RuntimeError("inner execute_predict_publish tried to export/push")

    timings["predict_after_ratings_sec"] = timings.get("execute_predict_publish_sec", 0.0) - timings.get(
        "rating_filter_sec", 0.0
    )

    _log("export_publish_artifacts push=False")
    t0 = time.perf_counter()
    export_out = export_publish_artifacts(result, config=inner, push=False)
    timings["export_sec"] = time.perf_counter() - t0
    artifacts = dict(export_out["artifacts"])
    for name, body in artifacts.items():
        dest = LOCAL / str(name)
        dest.write_text(str(body), encoding="utf-8")
        _log(f"wrote_local {dest.as_posix()}")

    _log("push_artifacts_to_r2 publish_scope=sandbox skip_revalidation=True")
    t0 = time.perf_counter()
    push_result = push_artifacts_to_r2(
        artifacts,
        season=2026,
        week=1,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        schema_version=SCHEMA_VERSION,
        publish_scope="sandbox",
        config=base,
        skip_revalidation=True,
    )
    timings["push_sec"] = time.perf_counter() - t0
    _log("push_result=" + json.dumps(push_result, default=str)[:2000])

    keys = [u["key"] for u in push_result.get("uploads") or []]
    forbidden = [k for k in keys if k.startswith("latest/") or k.startswith("v1/") or k.startswith("v2/")]
    if forbidden:
        raise RuntimeError(f"push wrote non-sandbox keys: {forbidden}")
    if any(not k.startswith("sandbox/") for k in keys):
        raise RuntimeError(f"non-sandbox key in uploads: {keys}")

    hashes_after = hash_isolation()
    changed = [k for k, v in hashes_before.items() if hashes_after.get(k) != v]
    _log("isolation_after=" + json.dumps(hashes_after, sort_keys=True))
    _log(f"isolation_changed={changed}")

    # Deliberate allowlist failure drill — fake S3, unsanctioned key, revert in-memory.
    class _FakeS3:
        def __init__(self) -> None:
            self.put_calls: list[dict[str, Any]] = []

        def put_object(self, **kwargs: Any) -> dict[str, Any]:
            self.put_calls.append(kwargs)
            return {}

    poisoned = dict(artifacts)
    week_obj = json.loads(str(poisoned["week_predictions.json"]))
    week_obj["games"][0]["unsanctioned_edge"] = 0.42
    poisoned["week_predictions.json"] = json.dumps(week_obj, indent=2) + "\n"
    fake = _FakeS3()
    allowlist_drill: dict[str, Any]
    try:
        push_artifacts_to_r2(
            poisoned,
            season=2026,
            week=1,
            refresh_kind=RefreshKind.TUESDAY_PRIMARY,
            schema_version=SCHEMA_VERSION,
            publish_scope="sandbox",
            config=base,
            client=fake,  # type: ignore[arg-type]
            skip_revalidation=True,
        )
        allowlist_drill = {
            "raised": False,
            "put_calls": len(fake.put_calls),
            "error": None,
        }
    except PublishedKeyAllowlistError as exc:
        allowlist_drill = {
            "raised": True,
            "exception": type(exc).__name__,
            "message": str(exc),
            "put_calls": len(fake.put_calls),
        }
    _log("allowlist_drill=" + json.dumps(allowlist_drill))
    if not allowlist_drill.get("raised") or allowlist_drill.get("put_calls"):
        raise RuntimeError("allowlist drill did not refuse before put_object")

    report = {
        "timings": timings,
        "n_prediction_rows": n_rows,
        "push": {
            "bucket": push_result.get("bucket"),
            "upload_order": push_result.get("upload_order"),
            "keys": keys,
            "content_hashes": push_result.get("content_hashes"),
            "meta_last": push_result.get("meta_last"),
            "revalidation": push_result.get("revalidation"),
        },
        "isolation_before": hashes_before,
        "isolation_after": hashes_after,
        "isolation_changed": changed,
        "redirected_tier_state": inner.webapp.tier_state_path,
        "redirected_tier_changes": inner.webapp.tier_changes_path,
        "redirected_idempotency_dir": inner.pipeline.idempotency_dir,
        "allowlist_drill": allowlist_drill,
        "inner_export_enabled": inner.webapp.export_enabled,
        "workstation_export_enabled": base.webapp.export_enabled,
    }
    (ART / "rehearsal.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    _log("timings=" + json.dumps(timings))
    _log("rehearsal done")


if __name__ == "__main__":
    main()
