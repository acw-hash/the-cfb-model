"""Tests for structlog setup and secret redaction."""

from __future__ import annotations

import io
import json
import logging

from ncaa_quant.utils.logging import configure_logging, get_logger, redact_secrets


def test_redact_secrets_processor() -> None:
    event = {
        "event": "login",
        "api_key": "should-not-leak",
        "token": "tok",
        "password": "pw",
        "secret_value": "sv",
        "team": "Alabama",
        "nested": {"cfbd_api_key": "nested-secret", "ok": 1},
    }
    out = redact_secrets(None, "info", event)
    assert out["api_key"] == "***REDACTED***"
    assert out["token"] == "***REDACTED***"
    assert out["password"] == "***REDACTED***"
    assert out["secret_value"] == "***REDACTED***"
    assert out["team"] == "Alabama"
    assert out["nested"]["cfbd_api_key"] == "***REDACTED***"
    assert out["nested"]["ok"] == 1


def test_logged_output_redacts_secrets() -> None:
    stream = io.StringIO()
    run_id = configure_logging(level="INFO", run_id="test-run-1", stream=stream)
    assert run_id == "test-run-1"

    log = get_logger("test")
    log.info("fetching", cfbd_api_key="LEAK_ME_CFBD", odds_api_token="LEAK_ME_ODDS", week=3)
    # Ensure stdlib handlers flush.
    for handler in logging.root.handlers:
        handler.flush()

    raw = stream.getvalue()
    assert "LEAK_ME_CFBD" not in raw
    assert "LEAK_ME_ODDS" not in raw
    assert "***REDACTED***" in raw

    # Last non-empty line should be JSON with run_id.
    line = [ln for ln in raw.splitlines() if ln.strip()][-1]
    payload = json.loads(line)
    assert payload["run_id"] == "test-run-1"
    assert payload["cfbd_api_key"] == "***REDACTED***"
    assert payload["odds_api_token"] == "***REDACTED***"
    assert payload["week"] == 3
