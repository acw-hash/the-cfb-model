"""W9-1: push.py exact-key allowlist on every write path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ncaa_quant.config import AppConfig, WebappConfig
from ncaa_quant.webapp.export import PublishedKeyAllowlistError
from ncaa_quant.webapp.push import (
    META_FILENAME,
    assert_push_artifact_allowlists,
    push_artifacts_to_r2,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "webapp" / "fixtures"

RESTORE_FILES = (
    "week_predictions.json",
    "track_record.json",
    "results_2024.json",
    "team_ratings_2024.json",
    "meta.json",
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


def _text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _restore_artifacts() -> dict[str, str]:
    return {name: _text(name) for name in RESTORE_FILES}


def _push_cfg() -> AppConfig:
    return AppConfig(
        webapp=WebappConfig(
            r2_bucket="ridge-test",
            r2_endpoint_url="https://example.r2.cloudflarestorage.com",
        )
    )


def test_committed_fixtures_pass_push_allowlist() -> None:
    assert_push_artifact_allowlists(_restore_artifacts())


def test_restore_path_uploads_when_keys_are_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    s3 = FakeS3()
    result = push_artifacts_to_r2(
        _restore_artifacts(),
        season=2024,
        week=5,
        refresh_kind="tuesday_primary",
        schema_version="1.2.0",
        publish_scope="live",
        config=_push_cfg(),
        client=s3,
        skip_revalidation=True,
    )
    assert result["uploads"]
    assert any(u["key"] == "latest/week_predictions.json" for u in result["uploads"])


def test_unknown_filename_fails_before_put(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    s3 = FakeS3()
    with pytest.raises(PublishedKeyAllowlistError, match="unpublished artifact filename"):
        push_artifacts_to_r2(
            {"odds.json": '{"spread": -3.5}\n'},
            season=2024,
            week=5,
            refresh_kind="tuesday_primary",
            config=_push_cfg(),
            client=s3,
            skip_revalidation=True,
        )
    assert s3.put_calls == []


def test_extra_game_key_fails_on_sandbox_restore() -> None:
    week = json.loads(_text("week_predictions.json"))
    week["games"][0]["p_cover_home"] = 0.42
    poisoned = {
        "week_predictions.json": json.dumps(week),
        META_FILENAME: _text("meta.json"),
    }
    s3 = FakeS3()
    with pytest.raises(PublishedKeyAllowlistError, match="p_cover_home"):
        push_artifacts_to_r2(
            poisoned,
            season=2024,
            week=5,
            refresh_kind="tuesday_primary",
            publish_scope="sandbox",
            config=_push_cfg(),
            client=s3,
            skip_revalidation=True,
        )
    assert s3.put_calls == []


def test_nested_conviction_basis_unknown_key_fails() -> None:
    week = json.loads(_text("week_predictions.json"))
    basis = week["games"][0]["conviction_basis"]
    assert isinstance(basis, dict)
    basis["unsanctioned_edge"] = 0.03
    with pytest.raises(PublishedKeyAllowlistError, match="unsanctioned_edge"):
        assert_push_artifact_allowlists({"week_predictions.json": json.dumps(week)})


def test_meta_unknown_key_fails() -> None:
    meta = json.loads(_text("meta.json"))
    meta["p_cover_home"] = 0.5
    with pytest.raises(PublishedKeyAllowlistError, match="p_cover_home"):
        assert_push_artifact_allowlists({META_FILENAME: json.dumps(meta)})


def test_team_ratings_rejects_non_id_map_key() -> None:
    payload = {
        "schema_version": "1.1.0",
        "season": 2024,
        "published_at": "2024-09-24T06:00:00Z",
        "teams": {
            "103": {
                "school": "Boston College",
                "weeks": [
                    {
                        "week": 1,
                        "as_of_utc": "2024-09-03T03:00:00Z",
                        "off_epa": 0.0,
                        "def_epa": 0.0,
                        "pace": 0.0,
                        "off_sd": 0.0,
                        "def_sd": 0.0,
                    }
                ],
            },
            "p_cover_home": {"school": "Leak", "weeks": []},
        },
    }
    with pytest.raises(PublishedKeyAllowlistError, match="p_cover_home"):
        assert_push_artifact_allowlists({"team_ratings_2024.json": json.dumps(payload)})


def test_allowlist_does_not_need_credentials_to_refuse() -> None:
    week = json.loads(_text("week_predictions.json"))
    week["games"][0]["p_over"] = 0.44
    with pytest.raises(PublishedKeyAllowlistError, match="p_over"):
        push_artifacts_to_r2(
            {"week_predictions.json": json.dumps(week)},
            season=2024,
            week=5,
            refresh_kind="tuesday_primary",
            client=FakeS3(),
            skip_revalidation=True,
        )
