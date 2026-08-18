"""Cloudflare R2 push via S3-compatible API (docs/webapp/DESIGN.md §3)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from typing import Any, Literal, Protocol

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ncaa_quant.config import AppConfig, load_config, load_secrets
from ncaa_quant.pipelines.notifications import AlertKind, Notifier, build_notifier, notify
from ncaa_quant.utils.logging import get_logger
from ncaa_quant.webapp.export import (
    PUBLISHED_GAME_PREDICTION_KEYS,
    PublishedKeyAllowlistError,
)

log = get_logger(__name__)

META_FILENAME = "meta.json"

PublishScope = Literal["live", "sandbox"]

# CFBD ``game_id`` values are numeric identifiers (e.g. ``401628373``).
# Synthetic test stubs (``g-fix-1``, ``g-chaos-1``) must not reach live prefixes.
CFBD_GAME_ID_PATTERN = re.compile(r"^[0-9]{6,12}$")


def is_cfbd_game_id(game_id: str) -> bool:
    """Return True when ``game_id`` matches the CFBD numeric id shape."""
    return bool(CFBD_GAME_ID_PATTERN.fullmatch(str(game_id)))


def collect_game_ids_from_artifacts(artifacts: Mapping[str, str | bytes]) -> list[str]:
    """Extract ``game_id`` values from push-bound JSON artifacts."""
    ids: list[str] = []
    for filename, content in artifacts.items():
        if not filename.endswith(".json"):
            continue
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        payload = json.loads(text)
        if filename == "week_predictions.json":
            for game in payload.get("games") or []:
                if isinstance(game, dict) and game.get("game_id") is not None:
                    ids.append(str(game["game_id"]))
        elif filename.startswith("results_") and isinstance(payload.get("games"), list):
            for game in payload["games"]:
                if isinstance(game, dict) and game.get("game_id") is not None:
                    ids.append(str(game["game_id"]))
    return ids


def validate_live_publish_game_ids(artifacts: Mapping[str, str | bytes]) -> None:
    """Refuse live-prefix push when any artifact carries a non-CFBD ``game_id``."""
    bad = [gid for gid in collect_game_ids_from_artifacts(artifacts) if not is_cfbd_game_id(gid)]
    if bad:
        sample = bad[:5]
        suffix = f" (+{len(bad) - len(sample)} more)" if len(bad) > len(sample) else ""
        msg = (
            "refused live publish: game_id(s) "
            f"{sample!r}{suffix} do not match CFBD shape "
            "(decimal digits only, length 6–12); "
            "synthetic ids cannot write to latest/ or v*/ prefixes"
        )
        raise R2PushError(msg)


class S3Client(Protocol):
    """Minimal S3 client surface for R2 uploads."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> Any: ...
    def head_object(self, *, Bucket: str, Key: str) -> Any: ...


class R2PushError(RuntimeError):
    """Raised when artifact push to R2 fails."""


_RESULTS_FILENAME = re.compile(r"^results_[0-9]{4}\.json$")
_RATINGS_FILENAME = re.compile(r"^team_ratings_[0-9]{4}\.json$")
_TEAM_ID_KEY = re.compile(r"^[0-9]+$")

_OPTIONAL_FIXTURE: frozenset[str] = frozenset({"fixture"})

_META_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "published_at",
        "season",
        "week",
        "refresh_kind",
        "next_expected_publish_utc",
        "champion_model",
        "publish_schedule",
        "artifact_pointers",
        "feature_time_label",
        "ensemble_scope_label",
        "vintage_label",
    }
)
_CHAMPION_MODEL_KEYS: frozenset[str] = frozenset(
    {"registry_name", "champion_version", "model_version", "registered_at"}
)
_PUBLISH_SCHEDULE_KEYS: frozenset[str] = frozenset({"primary", "refresh", "postgame_ratings"})
_ARTIFACT_POINTER_KEYS: frozenset[str] = frozenset(
    {"week_predictions", "track_record", "results_current_season", "team_ratings"}
)

_WEEK_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "season",
        "week",
        "refresh_kind",
        "published_at",
        "feature_time_label",
        "ensemble_scope_label",
        "vintage_label",
        "model_identity",
        "publish_stale",
        "games",
    }
)
_MODEL_IDENTITY_KEYS: frozenset[str] = frozenset(
    {"registry_name", "champion_version", "model_version", "run_id"}
)
_PUBLISH_STALE_KEYS: frozenset[str] = frozenset({"is_stale", "combined_stamp", "sources"})
_STALE_SOURCE_KEYS: frozenset[str] = frozenset({"source", "age_hours", "last_good_at"})
_CONVICTION_BASIS_KEYS: frozenset[str] = frozenset(
    {
        "p_favored",
        "p_win_home",
        "mu_margin",
        "sigma_margin",
        "mu_sigma_ratio",
        "favored_side",
        "hysteresis_applied",
        "previous_tier",
        "raw_tier",
    }
)

_TRACK_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "published_at",
        "source_memo",
        "verdict",
        "metrics",
        "ensemble_scope_label",
        "vintage_labels",
    }
)
_VERDICT_KEYS: frozenset[str] = frozenset({"label", "plain_language"})
_METRIC_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "label",
        "value",
        "unit",
        "ci_lower",
        "ci_upper",
        "ci_kind",
        "n",
        "notes",
        "regime",
        "vintage",
        "run",
    }
)

_RESULTS_KEYS: frozenset[str] = frozenset(
    {"schema_version", "season", "published_at", "grading_rule", "games"}
)
_GRADED_GAME_KEYS: frozenset[str] = frozenset(
    {
        "game_id",
        "week",
        "kickoff_utc",
        "home_team",
        "away_team",
        "home_points",
        "away_points",
        "actual_margin",
        "actual_total",
        "graded_from",
        "mu_margin",
        "sigma_margin",
        "margin_interval_lo",
        "margin_interval_hi",
        "margin_interval_nominal",
        "mu_total",
        "total_interval_lo",
        "total_interval_hi",
        "total_interval_nominal",
        "p_win_home",
        "conviction_tier",
        "conviction_team",
        "conviction_label",
        "margin_interval_hit",
        "total_interval_hit",
        "home_win",
        "p_win_home_realized",
        "grade_status",
    }
)
_GRADED_FROM_KEYS: frozenset[str] = frozenset({"refresh_kind", "published_at"})

_RATINGS_KEYS: frozenset[str] = frozenset({"schema_version", "season", "published_at", "teams"})
_TEAM_ENTRY_KEYS: frozenset[str] = frozenset({"school", "weeks"})
_TEAM_WEEK_KEYS: frozenset[str] = frozenset(
    {"week", "as_of_utc", "off_epa", "def_epa", "pace", "off_sd", "def_sd"}
)


def _assert_exact_keys(
    obj: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    path: str,
) -> None:
    present = set(obj.keys())
    extra = present - required - optional
    missing = required - present
    if extra:
        msg = f"unpublished keys in {path}: {sorted(extra)}"
        raise PublishedKeyAllowlistError(msg)
    if missing:
        msg = f"published {path} missing required keys: {sorted(missing)}"
        raise PublishedKeyAllowlistError(msg)


def _require_object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        msg = f"{path} is not an object"
        raise PublishedKeyAllowlistError(msg)
    return value


def _assert_stale_sources(value: Any, path: str) -> None:
    if not isinstance(value, list):
        msg = f"{path} is not an array"
        raise PublishedKeyAllowlistError(msg)
    for i, item in enumerate(value):
        src = _require_object(item, f"{path}[{i}]")
        _assert_exact_keys(src, required=_STALE_SOURCE_KEYS, path=f"{path}[{i}]")


def _assert_game_prediction_object(game: Mapping[str, Any], path: str) -> None:
    _assert_exact_keys(game, required=PUBLISHED_GAME_PREDICTION_KEYS, path=path)
    basis = game.get("conviction_basis")
    if basis is not None:
        nested = _require_object(basis, f"{path}.conviction_basis")
        _assert_exact_keys(nested, required=_CONVICTION_BASIS_KEYS, path=f"{path}.conviction_basis")
    _assert_stale_sources(game.get("stale_sources"), f"{path}.stale_sources")


def _assert_meta(payload: Mapping[str, Any], filename: str) -> None:
    _assert_exact_keys(payload, required=_META_KEYS, optional=_OPTIONAL_FIXTURE, path=filename)
    champion = _require_object(payload.get("champion_model"), f"{filename}.champion_model")
    _assert_exact_keys(champion, required=_CHAMPION_MODEL_KEYS, path=f"{filename}.champion_model")
    schedule = _require_object(payload.get("publish_schedule"), f"{filename}.publish_schedule")
    _assert_exact_keys(
        schedule, required=_PUBLISH_SCHEDULE_KEYS, path=f"{filename}.publish_schedule"
    )
    pointers = _require_object(payload.get("artifact_pointers"), f"{filename}.artifact_pointers")
    _assert_exact_keys(
        pointers, required=_ARTIFACT_POINTER_KEYS, path=f"{filename}.artifact_pointers"
    )


def _assert_week_predictions(payload: Mapping[str, Any], filename: str) -> None:
    _assert_exact_keys(payload, required=_WEEK_KEYS, optional=_OPTIONAL_FIXTURE, path=filename)
    identity = _require_object(payload.get("model_identity"), f"{filename}.model_identity")
    _assert_exact_keys(identity, required=_MODEL_IDENTITY_KEYS, path=f"{filename}.model_identity")
    stale = _require_object(payload.get("publish_stale"), f"{filename}.publish_stale")
    _assert_exact_keys(stale, required=_PUBLISH_STALE_KEYS, path=f"{filename}.publish_stale")
    _assert_stale_sources(stale.get("sources"), f"{filename}.publish_stale.sources")
    games = payload.get("games")
    if not isinstance(games, list):
        msg = f"{filename}.games is not an array"
        raise PublishedKeyAllowlistError(msg)
    for i, game in enumerate(games):
        row = _require_object(game, f"{filename}.games[{i}]")
        _assert_game_prediction_object(row, f"{filename}.games[{i}]")


def _assert_track_record(payload: Mapping[str, Any], filename: str) -> None:
    _assert_exact_keys(payload, required=_TRACK_KEYS, optional=_OPTIONAL_FIXTURE, path=filename)
    verdict = _require_object(payload.get("verdict"), f"{filename}.verdict")
    _assert_exact_keys(verdict, required=_VERDICT_KEYS, path=f"{filename}.verdict")
    metrics = payload.get("metrics")
    if not isinstance(metrics, list):
        msg = f"{filename}.metrics is not an array"
        raise PublishedKeyAllowlistError(msg)
    for i, metric in enumerate(metrics):
        row = _require_object(metric, f"{filename}.metrics[{i}]")
        _assert_exact_keys(row, required=_METRIC_KEYS, path=f"{filename}.metrics[{i}]")


def _assert_results(payload: Mapping[str, Any], filename: str) -> None:
    _assert_exact_keys(payload, required=_RESULTS_KEYS, optional=_OPTIONAL_FIXTURE, path=filename)
    games = payload.get("games")
    if not isinstance(games, list):
        msg = f"{filename}.games is not an array"
        raise PublishedKeyAllowlistError(msg)
    for i, game in enumerate(games):
        row = _require_object(game, f"{filename}.games[{i}]")
        _assert_exact_keys(row, required=_GRADED_GAME_KEYS, path=f"{filename}.games[{i}]")
        graded_from = row.get("graded_from")
        if graded_from is not None:
            nested = _require_object(graded_from, f"{filename}.games[{i}].graded_from")
            _assert_exact_keys(
                nested, required=_GRADED_FROM_KEYS, path=f"{filename}.games[{i}].graded_from"
            )


def _assert_team_ratings(payload: Mapping[str, Any], filename: str) -> None:
    _assert_exact_keys(payload, required=_RATINGS_KEYS, optional=_OPTIONAL_FIXTURE, path=filename)
    teams = _require_object(payload.get("teams"), f"{filename}.teams")
    for team_id, entry in teams.items():
        if not _TEAM_ID_KEY.fullmatch(str(team_id)):
            msg = f"unpublished keys in {filename}.teams: {[str(team_id)]}"
            raise PublishedKeyAllowlistError(msg)
        team_path = f"{filename}.teams.{team_id}"
        team = _require_object(entry, team_path)
        _assert_exact_keys(team, required=_TEAM_ENTRY_KEYS, path=team_path)
        weeks = team.get("weeks")
        if not isinstance(weeks, list):
            msg = f"{team_path}.weeks is not an array"
            raise PublishedKeyAllowlistError(msg)
        for i, week in enumerate(weeks):
            snap = _require_object(week, f"{team_path}.weeks[{i}]")
            _assert_exact_keys(snap, required=_TEAM_WEEK_KEYS, path=f"{team_path}.weeks[{i}]")


def _artifact_kind(filename: str) -> str:
    if filename == "week_predictions.json":
        return "week_predictions"
    if filename == "track_record.json":
        return "track_record"
    if filename == META_FILENAME:
        return "meta"
    if _RESULTS_FILENAME.fullmatch(filename):
        return "results"
    if _RATINGS_FILENAME.fullmatch(filename):
        return "team_ratings"
    msg = f"unpublished artifact filename: {filename}"
    raise PublishedKeyAllowlistError(msg)


def assert_push_artifact_allowlists(artifacts: Mapping[str, str | bytes]) -> None:
    """Refuse any write whose objects carry keys outside the sanctioned set.

    Exact allowlist per artifact type — unknown keys fail. Runs on every
    ``push_artifacts_to_r2`` call, including sandbox and operator restore.
    """
    for filename, content in artifacts.items():
        kind = _artifact_kind(filename)
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"invalid JSON in {filename}: {exc}"
            raise PublishedKeyAllowlistError(msg) from exc
        obj = _require_object(payload, filename)
        if kind == "week_predictions":
            _assert_week_predictions(obj, filename)
        elif kind == "track_record":
            _assert_track_record(obj, filename)
        elif kind == "meta":
            _assert_meta(obj, filename)
        elif kind == "results":
            _assert_results(obj, filename)
        else:
            _assert_team_ratings(obj, filename)


def artifact_object_keys(
    *,
    filename: str,
    season: int,
    week: int,
    refresh_kind: str,
    schema_version: str,
    publish_scope: PublishScope = "live",
) -> tuple[str, str]:
    major = schema_version.split(".", 1)[0]
    versioned = f"v{major}/{season}/w{week}/{refresh_kind}/{filename}"
    latest = f"latest/{filename}"
    if publish_scope == "sandbox":
        return f"sandbox/{versioned}", f"sandbox/{latest}"
    return versioned, latest


def _upload_order(filenames: list[str]) -> list[str]:
    """``meta.json`` uploads last for reader-side atomicity."""
    data_files = sorted(name for name in filenames if name != META_FILENAME)
    if META_FILENAME in filenames:
        data_files.append(META_FILENAME)
    return data_files


def _body_bytes(content: str | bytes) -> bytes:
    return content.encode("utf-8") if isinstance(content, str) else content


def _content_sha256(content: str | bytes) -> str:
    return hashlib.sha256(_body_bytes(content)).hexdigest()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def _put_with_retry(
    client: S3Client,
    *,
    bucket: str,
    key: str,
    body: bytes,
) -> None:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
    )


def trigger_on_demand_revalidation(
    *,
    url: str,
    secret: str,
    protection_bypass_secret: str | None = None,
    timeout_s: float = 15.0,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """POST the Vercel revalidation endpoint with the shared secret.

    When ``protection_bypass_secret`` is set, also sends
    ``x-vercel-protection-bypass`` for Deployment Protection.

    Returns a result dict. Raises on HTTP/transport failure so callers can
    treat the hook as best-effort.
    """
    if not url:
        msg = "revalidate_url is empty"
        raise ValueError(msg)
    if not secret:
        msg = "WEBAPP_REVALIDATE_SECRET is empty"
        raise ValueError(msg)

    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }
    if protection_bypass_secret:
        headers["x-vercel-protection-bypass"] = protection_bypass_secret
    body = {"source": "ridge_r2_push"}
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout_s)
    try:
        response = http.post(url, headers=headers, json=body)
        payload: dict[str, Any]
        try:
            parsed = response.json()
            payload = parsed if isinstance(parsed, dict) else {"body": parsed}
        except Exception:
            payload = {"body": response.text}
        if response.status_code >= 400:
            msg = f"revalidation refused: HTTP {response.status_code} {payload}"
            raise RuntimeError(msg)
        return {
            "ok": True,
            "status_code": response.status_code,
            "response": payload,
        }
    finally:
        if owns_client:
            http.close()


def _maybe_revalidate(
    *,
    config: AppConfig,
    notifier: Notifier | None,
    http_client: httpx.Client | None = None,
) -> dict[str, Any] | None:
    """Best-effort revalidation after meta.json lands. Never raises."""
    url = (config.webapp.revalidate_url or "").strip()
    if not url:
        return None

    secrets = load_secrets()
    secret = secrets.webapp_revalidate_secret.get_secret_value()
    bypass = secrets.vercel_automation_bypass_secret.get_secret_value().strip() or None
    try:
        result = trigger_on_demand_revalidation(
            url=url,
            secret=secret,
            protection_bypass_secret=bypass,
            client=http_client,
        )
        log.info("webapp_revalidate_ok", status_code=result.get("status_code"))
        return result
    except Exception as exc:
        log.warning("webapp_revalidate_failed", error=str(exc))
        notify(
            AlertKind.WEBAPP_EXPORT_FAILURE,
            "Ridge on-demand revalidation failed",
            str(exc),
            config=config,
            notifier=notifier,
        )
        return {"ok": False, "error": str(exc)}


def push_artifacts_to_r2(
    artifacts: Mapping[str, str | bytes],
    *,
    season: int,
    week: int,
    refresh_kind: str,
    schema_version: str = "1.0.0",
    publish_scope: PublishScope = "live",
    config: AppConfig | None = None,
    client: S3Client | None = None,
    notifier: Notifier | None = None,
    http_client: httpx.Client | None = None,
    skip_revalidation: bool = False,
) -> dict[str, Any]:
    """Upload artifacts to R2; return upload audit trail.

    After ``meta.json`` lands (last), triggers on-demand revalidation when
    ``webapp.revalidate_url`` is configured. Revalidation failure is best-effort:
    it alerts via the notifier and does not fail the push.

    Key allowlist runs on every call (live, sandbox, restore) before any upload.
    """
    assert_push_artifact_allowlists(artifacts)
    cfg = config or load_config()
    secrets = load_secrets()
    bucket = cfg.webapp.r2_bucket
    if not bucket:
        msg = "webapp.r2_bucket is not configured"
        raise R2PushError(msg)

    access_key = secrets.r2_access_key_id.get_secret_value()
    secret_key = secrets.r2_secret_access_key.get_secret_value()
    if not access_key or not secret_key:
        msg = "R2 credentials missing from environment"
        raise R2PushError(msg)

    if publish_scope == "live":
        validate_live_publish_game_ids(artifacts)

    s3 = client
    if s3 is None:
        import boto3  # type: ignore[import-untyped]

        s3 = boto3.client(
            "s3",
            endpoint_url=cfg.webapp.r2_endpoint_url or None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )

    ordered = _upload_order(list(artifacts.keys()))
    uploads: list[dict[str, Any]] = []
    content_hashes: dict[str, str] = {}

    for filename in ordered:
        content = artifacts[filename]
        body = _body_bytes(content)
        digest = _content_sha256(content)
        content_hashes[filename] = digest
        versioned_key, latest_key = artifact_object_keys(
            filename=filename,
            season=season,
            week=week,
            refresh_kind=refresh_kind,
            schema_version=schema_version,
            publish_scope=publish_scope,
        )
        for key in (versioned_key, latest_key):
            started = time.monotonic()
            _put_with_retry(s3, bucket=bucket, key=key, body=body)
            uploads.append(
                {
                    "key": key,
                    "sha256": digest,
                    "bytes": len(body),
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            )

    # meta-last ordering: revalidation runs only after the upload loop completes.
    n = notifier if notifier is not None else build_notifier(cfg)
    revalidation: dict[str, Any] | None = None
    if not skip_revalidation and publish_scope == "live":
        revalidation = _maybe_revalidate(config=cfg, notifier=n, http_client=http_client)

    return {
        "bucket": bucket,
        "upload_order": ordered,
        "uploads": uploads,
        "content_hashes": content_hashes,
        "meta_last": ordered[-1] == META_FILENAME if ordered else False,
        "revalidation": revalidation,
    }


def push_audit_json(result: Mapping[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True) + "\n"
