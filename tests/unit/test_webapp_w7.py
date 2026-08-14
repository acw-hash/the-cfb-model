"""W7 — revalidation hook, tier-change instrumentation, push best-effort."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from ncaa_quant.config import AppConfig, WebappConfig, load_config
from ncaa_quant.pipelines.notifications import AlertKind, RecordingNotifier
from ncaa_quant.webapp.export import (
    TierStateStore,
    append_tier_change_records,
    build_week_predictions,
)
from ncaa_quant.webapp.push import (
    META_FILENAME,
    push_artifacts_to_r2,
    trigger_on_demand_revalidation,
)


class FakeS3:
    def __init__(self) -> None:
        self.put_calls: list[str] = []
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> dict[str, Any]:
        del Bucket, ContentType
        self.put_calls.append(Key)
        self.objects[Key] = Body
        return {}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        del Bucket
        if Key not in self.objects:
            raise RuntimeError("404")
        return {"ContentLength": len(self.objects[Key])}


def test_trigger_revalidation_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert "x-vercel-protection-bypass" not in request.headers
        return httpx.Response(200, json={"ok": True, "revalidated": True})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = trigger_on_demand_revalidation(
            url="https://ridge.example/api/revalidate",
            secret="test-secret",
            client=client,
        )
    assert result["ok"] is True
    assert result["status_code"] == 200


def test_trigger_revalidation_with_protection_bypass() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert request.headers["x-vercel-protection-bypass"] == "bypass-token"
        return httpx.Response(200, json={"ok": True, "revalidated": True})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = trigger_on_demand_revalidation(
            url="https://ridge.example/api/revalidate",
            secret="test-secret",
            protection_bypass_secret="bypass-token",
            client=client,
        )
    assert result["ok"] is True
    assert result["status_code"] == 200


def test_maybe_revalidate_uses_bypass_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEBAPP_REVALIDATE_SECRET", "hook-secret")
    monkeypatch.setenv("VERCEL_AUTOMATION_BYPASS_SECRET", "bypass-from-env")

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["bypass"] = request.headers.get("x-vercel-protection-bypass", "")
        return httpx.Response(200, json={"ok": True})

    cfg = AppConfig(
        webapp=WebappConfig(
            revalidate_url="https://ridge.example/api/revalidate",
        )
    )
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http:
        from ncaa_quant.webapp.push import _maybe_revalidate

        result = _maybe_revalidate(config=cfg, notifier=RecordingNotifier(), http_client=http)

    assert result is not None
    assert result["ok"] is True
    assert seen["bypass"] == "bypass-from-env"


def test_trigger_revalidation_wrong_secret_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"ok": False, "error": "unauthorized"})

    transport = httpx.MockTransport(handler)
    with (
        httpx.Client(transport=transport) as client,
        pytest.raises(RuntimeError, match="revalidation refused: HTTP 401"),
    ):
        trigger_on_demand_revalidation(
            url="https://ridge.example/api/revalidate",
            secret="wrong",
            client=client,
        )


def test_push_revalidation_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("WEBAPP_REVALIDATE_SECRET", "hook-secret")
    monkeypatch.delenv("VERCEL_AUTOMATION_BYPASS_SECRET", raising=False)

    notifier = RecordingNotifier()
    cfg = AppConfig(
        webapp=WebappConfig(
            export_enabled=True,
            r2_bucket="ridge-test",
            r2_endpoint_url="https://example.r2.cloudflarestorage.com",
            revalidate_url="https://ridge.example/api/revalidate",
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, json={"ok": False, "error": "boom"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http:
        result = push_artifacts_to_r2(
            {META_FILENAME: '{"schema_version":"1.1.0"}\n'},
            season=2026,
            week=1,
            refresh_kind="tuesday_primary",
            schema_version="1.1.0",
            config=cfg,
            client=FakeS3(),
            notifier=notifier,
            http_client=http,
        )

    assert result["meta_last"] is True
    assert result["revalidation"] is not None
    assert result["revalidation"]["ok"] is False
    assert any(a.kind == AlertKind.WEBAPP_EXPORT_FAILURE for a in notifier.sent)
    assert any("revalidation" in a.title.lower() for a in notifier.sent)


def test_push_skips_revalidation_when_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    cfg = AppConfig(
        webapp=WebappConfig(
            r2_bucket="ridge-test",
            r2_endpoint_url="https://example.r2.cloudflarestorage.com",
            revalidate_url="",
        )
    )
    result = push_artifacts_to_r2(
        {META_FILENAME: "{}\n"},
        season=2026,
        week=1,
        refresh_kind="tuesday_primary",
        config=cfg,
        client=FakeS3(),
        notifier=RecordingNotifier(),
    )
    assert result["revalidation"] is None


def test_tier_change_records_jsonl(tmp_path: Path) -> None:
    """W1A-FIX successor: per publish / per game instrumentation on disk."""
    store = TierStateStore(tmp_path / "tier_state.json")
    store.save(
        tiers={"2026:g1": "lean"},
        refresh_kind="tuesday_primary",
    )
    changes = tmp_path / "tier_changes.jsonl"
    rows = [
        {
            "game_id": "g1",
            "pred_margin": 3.0,
            "sigma_m": 14.0,
            "sigma_m_is_missing": False,
            "p_ml_home": 0.58,
            "p_ml_home_is_missing": False,
            "p_ats_home": 0.5,
            "p_ats_home_is_missing": False,
            "p_ou_over": 0.5,
            "p_ou_over_is_missing": False,
            "pred_total": 50.0,
            "sigma_t": 14.0,
            "sigma_t_is_missing": False,
            "null_reason": None,
            "is_stale": False,
            "stale_stamp": None,
            "stale_sources": [],
        }
    ]
    schedule = {
        "g1": {
            "game_id": "g1",
            "home_team": "Michigan",
            "away_team": "Ohio State",
            "home_team_id": 1,
            "away_team_id": 2,
            "kickoff_utc": "2026-09-05T19:00:00Z",
            "neutral_site": False,
            "conference_game": True,
        }
    }

    build_week_predictions(
        season=2026,
        week=1,
        refresh_kind="daily_refresh",
        published_at=datetime(2026, 9, 4, 6, 0, tzinfo=UTC),
        prediction_rows=rows,
        schedule_by_game=schedule,
        tier_store=store,
        tier_changes_path=changes,
        record_tier_changes=True,
    )
    lines = changes.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["game_id"] == "g1"
    assert record["prior_tier"] == "lean"
    assert record["new_tier"] in {"lean", "toss_up", "clear_lean", "strong_lean", None}
    assert "hysteresis_applied" in record
    assert "p_favored" in record


def test_append_tier_change_records_empty_noop(tmp_path: Path) -> None:
    path = tmp_path / "tier_changes.jsonl"
    append_tier_change_records([], path=path)
    assert not path.exists()


def test_push_calls_revalidation_after_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("WEBAPP_REVALIDATE_SECRET", "hook-secret")
    monkeypatch.delenv("VERCEL_AUTOMATION_BYPASS_SECRET", raising=False)

    order: list[str] = []
    s3 = FakeS3()
    original_put = s3.put_object

    def tracking_put(**kwargs: Any) -> dict[str, Any]:
        order.append(f"put:{kwargs['Key']}")
        return original_put(**kwargs)

    s3.put_object = tracking_put  # type: ignore[method-assign]

    def handler(request: httpx.Request) -> httpx.Response:
        order.append("revalidate")
        assert str(request.url).endswith("/api/revalidate")
        return httpx.Response(200, json={"ok": True})

    cfg = AppConfig(
        webapp=WebappConfig(
            r2_bucket="ridge-test",
            r2_endpoint_url="https://example.r2.cloudflarestorage.com",
            revalidate_url="https://ridge.example/api/revalidate",
        )
    )
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http:
        result = push_artifacts_to_r2(
            {
                "week_predictions.json": "{}\n",
                META_FILENAME: "{}\n",
            },
            season=2026,
            week=1,
            refresh_kind="tuesday_primary",
            config=cfg,
            client=s3,
            notifier=RecordingNotifier(),
            http_client=http,
        )

    assert result["revalidation"]["ok"] is True
    assert order[-1] == "revalidate"
    meta_puts = [i for i, step in enumerate(order) if step.endswith(META_FILENAME)]
    assert meta_puts
    assert max(meta_puts) < order.index("revalidate")


_DOTENV_BUCKET = "ridge-from-dotenv"
_DOTENV_ENDPOINT = "https://example.r2.cloudflarestorage.com"
_SHELL_BUCKET = "ridge-from-shell"

_WEBAPP_ENV_KEYS = (
    "NCAA_QUANT_WEBAPP__R2_BUCKET",
    "NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL",
    "NCAA_QUANT_WEBAPP__EXPORT_ENABLED",
    "NCAA_QUANT_WEBAPP__REVALIDATE_URL",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)


def _write_webapp_dotenv(path: Path, *, bucket: str = _DOTENV_BUCKET) -> None:
    path.write_text(
        f"NCAA_QUANT_WEBAPP__R2_BUCKET={bucket}\n"
        f"NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL={_DOTENV_ENDPOINT}\n"
        "R2_ACCESS_KEY_ID=test-key\n"
        "R2_SECRET_ACCESS_KEY=test-secret\n"
        "CFBD_API_KEY=not-an-appconfig-field\n",
        encoding="utf-8",
    )


def test_load_config_resolves_webapp_from_dotenv_without_shell_exports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AppConfig must read NCAA_QUANT_WEBAPP__* from .env like SecretsSettings.

    Failure mode: YAML/class defaults leave bucket and endpoint empty. Without
    env_file on AppConfig, scheduled predict_publish would then call push.py
    with an empty bucket/endpoint even when .env has the values.
    """
    assert WebappConfig().r2_bucket == ""
    assert WebappConfig().r2_endpoint_url == ""

    _write_webapp_dotenv(tmp_path / ".env")
    monkeypatch.chdir(tmp_path)
    for key in _WEBAPP_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    cfg = load_config()
    assert cfg.webapp.r2_bucket == _DOTENV_BUCKET
    assert cfg.webapp.r2_endpoint_url == _DOTENV_ENDPOINT

    # Stock push path: config=None → load_config(). Empty bucket raises R2PushError.
    result = push_artifacts_to_r2(
        {META_FILENAME: "{}\n"},
        season=2026,
        week=1,
        refresh_kind="tuesday_primary",
        client=FakeS3(),
        notifier=RecordingNotifier(),
        skip_revalidation=True,
    )
    assert result["bucket"] == _DOTENV_BUCKET


def test_shell_env_overrides_dotenv_webapp_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real environment variables still win over .env (precedence unchanged)."""
    _write_webapp_dotenv(tmp_path / ".env")
    monkeypatch.chdir(tmp_path)
    for key in _WEBAPP_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NCAA_QUANT_WEBAPP__R2_BUCKET", _SHELL_BUCKET)

    cfg = load_config()
    assert cfg.webapp.r2_bucket == _SHELL_BUCKET
    assert cfg.webapp.r2_endpoint_url == _DOTENV_ENDPOINT
