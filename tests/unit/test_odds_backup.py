"""Tests for off-machine odds archive backup (E-1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ncaa_quant.ops.odds_backup import (
    OddsBackupError,
    assert_backup_fresh,
    inventory,
    load_manifest,
    replicate_odds_archive,
    restore_drill,
)


def _seed_archive(root: Path) -> None:
    day = root / "2026-08-07"
    day.mkdir(parents=True)
    (day / "snap1.json").write_text('{"ok": true}\n', encoding="utf-8")
    (day / "snap2.json").write_text('{"ok": false}\n', encoding="utf-8")


def test_replicate_and_restore_drill(tmp_path: Path) -> None:
    src = tmp_path / "odds_api"
    dest = tmp_path / "backup"
    _seed_archive(src)

    manifest = replicate_odds_archive(src, dest, notes="unit")
    assert manifest.n_files == 2
    assert (dest / "current" / "2026-08-07" / "snap1.json").is_file()
    assert (dest / "backup_manifest.json").is_file()
    loaded = load_manifest(dest)
    assert loaded.n_files == 2

    assert_backup_fresh(dest)
    drill = restore_drill(dest, source_root=src)
    assert drill.kind == "restore_drill"
    assert drill.n_files == 2


def test_assert_backup_fresh_stale(tmp_path: Path) -> None:
    src = tmp_path / "odds_api"
    dest = tmp_path / "backup"
    _seed_archive(src)
    replicate_odds_archive(src, dest)
    path = dest / "backup_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["created_at"] = (datetime.now(tz=UTC) - timedelta(hours=48)).isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OddsBackupError, match="stale"):
        assert_backup_fresh(dest)


def test_inventory_hashes(tmp_path: Path) -> None:
    src = tmp_path / "odds_api"
    _seed_archive(src)
    files = inventory(src)
    assert {f.relative_path for f in files} == {
        "2026-08-07/snap1.json",
        "2026-08-07/snap2.json",
    }
    assert all(len(f.sha256) == 64 for f in files)
