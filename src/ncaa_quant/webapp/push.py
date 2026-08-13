"""Cloudflare R2 push via S3-compatible API (docs/webapp/DESIGN.md §3)."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any, Protocol

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ncaa_quant.config import AppConfig, load_config, load_secrets
from ncaa_quant.pipelines.notifications import AlertKind, Notifier, build_notifier, notify
from ncaa_quant.utils.logging import get_logger

log = get_logger(__name__)

META_FILENAME = "meta.json"


class S3Client(Protocol):
    """Minimal S3 client surface for R2 uploads."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> Any: ...
    def head_object(self, *, Bucket: str, Key: str) -> Any: ...


class R2PushError(RuntimeError):
    """Raised when artifact push to R2 fails."""


def artifact_object_keys(
    *,
    filename: str,
    season: int,
    week: int,
    refresh_kind: str,
    schema_version: str,
) -> tuple[str, str]:
    major = schema_version.split(".", 1)[0]
    versioned = f"v{major}/{season}/w{week}/{refresh_kind}/{filename}"
    latest = f"latest/{filename}"
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
    timeout_s: float = 15.0,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """POST the Vercel revalidation endpoint with the shared secret.

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
    try:
        result = trigger_on_demand_revalidation(url=url, secret=secret, client=http_client)
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
    """
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
    if not skip_revalidation:
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
