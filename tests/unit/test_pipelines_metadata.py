"""Unit tests for raw-archive API-key scrub verification."""

from __future__ import annotations

import json

from ncaa_quant.pipelines.metadata import assert_raw_archive_scrubbed, verify_raw_archive_scrub


def test_scrub_clean_archive(tmp_path) -> None:
    (tmp_path / "2024-09-01").mkdir()
    body = json.dumps([{"id": "evt1"}]).encode()
    (tmp_path / "2024-09-01" / "20240901T120000000000Z.json").write_bytes(body)
    assert verify_raw_archive_scrub(tmp_path) == []
    assert_raw_archive_scrubbed(tmp_path)


def test_scrub_detects_api_key(tmp_path) -> None:
    (tmp_path / "bad.json").write_bytes(b'{"apiKey": "SECRET"}')
    violations = verify_raw_archive_scrub(tmp_path)
    assert len(violations) == 1
    assert "apiKey" in violations[0].pattern


def test_scrub_detects_literal_secret(tmp_path) -> None:
    secret = "SUPER_SECRET_ODDS_KEY"
    (tmp_path / "leak.json").write_bytes(secret.encode())
    violations = verify_raw_archive_scrub(tmp_path, extra_secrets=(secret,))
    assert any(v.pattern == "literal_secret" for v in violations)
