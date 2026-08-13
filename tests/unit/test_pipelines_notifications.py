"""Unit tests for pipeline notifications."""

from __future__ import annotations

import httpx

from ncaa_quant.config import AppConfig, NotificationConfig, PipelineConfig
from ncaa_quant.pipelines.notifications import (
    Alert,
    AlertKind,
    NtfyNotifier,
    RecordingNotifier,
    build_notifier,
    notify,
)


def test_null_notifier_records_suppressed() -> None:
    rec = RecordingNotifier()
    sent = notify(
        AlertKind.FLOW_FAILURE,
        "test",
        "body",
        config=AppConfig(
            pipeline=PipelineConfig(notifications=NotificationConfig(provider="null"))
        ),
        notifier=rec,
    )
    assert sent is True
    assert len(rec.sent) == 1
    assert rec.sent[0].kind == AlertKind.FLOW_FAILURE


def test_ntfy_notifier_posts(monkeypatch) -> None:
    calls: list[dict] = []

    class _Resp:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def post(self, url, content, headers):
            calls.append({"url": url, "content": content, "headers": headers})
            return _Resp()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(httpx, "Client", lambda **kw: _Client())
    n = NtfyNotifier(server="https://ntfy.sh", topic="cfb-test")
    assert n.send(Alert(kind=AlertKind.CLV_WEEKLY_SUMMARY, title="t", body="b"))
    assert calls[0]["url"] == "https://ntfy.sh/cfb-test"


def test_build_notifier_null_by_default() -> None:
    n = build_notifier(AppConfig())
    from ncaa_quant.pipelines.notifications import NullNotifier

    assert isinstance(n, NullNotifier)
