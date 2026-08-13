"""Cloudflare R2 push via S3-compatible API (docs/webapp/DESIGN.md §3)."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any, Protocol

from tenacity import retry, stop_after_attempt, wait_exponential

from ncaa_quant.config import AppConfig, load_config, load_secrets

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


def push_artifacts_to_r2(
    artifacts: Mapping[str, str | bytes],
    *,
    season: int,
    week: int,
    refresh_kind: str,
    schema_version: str = "1.0.0",
    config: AppConfig | None = None,
    client: S3Client | None = None,
) -> dict[str, Any]:
    """Upload artifacts to R2; return upload audit trail."""
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

    return {
        "bucket": bucket,
        "upload_order": ordered,
        "uploads": uploads,
        "content_hashes": content_hashes,
        "meta_last": ordered[-1] == META_FILENAME if ordered else False,
    }


def push_audit_json(result: Mapping[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True) + "\n"
