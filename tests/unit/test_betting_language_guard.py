"""W9-1 Amendment 1: published-copy union grep and repo-wide ratchet."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_betting_language.py"


def _load_guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_betting_language", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_published_copy_surfaces_are_clean() -> None:
    guard = _load_guard()
    assert guard.check_published(REPO_ROOT) == 0


def test_unlisted_copy_module_fails(tmp_path: Path) -> None:
    guard = _load_guard()
    src = tmp_path / "webapp" / "site" / "src" / "lib" / "this-week"
    src.mkdir(parents=True)
    (src / "copy.ts").write_text('export const X = "hello";\n', encoding="utf-8")
    for rel in guard.PUBLISHED_COPY_SURFACES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("ok\n", encoding="utf-8")
    assert guard.check_published(tmp_path) == 1


def test_betting_language_in_copy_module_fails(tmp_path: Path) -> None:
    guard = _load_guard()
    for rel in guard.PUBLISHED_COPY_SURFACES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("ok\n", encoding="utf-8")
    copy = tmp_path / "webapp" / "site" / "src" / "lib" / "about" / "copy.ts"
    copy.write_text('export const X = "this is the best bet tonight";\n', encoding="utf-8")
    assert guard.check_published(tmp_path) == 1


def test_ratchet_existing_counts_do_not_fail() -> None:
    guard = _load_guard()
    result = guard.scan_paths(REPO_ROOT, guard.tracked_files(REPO_ROOT))
    assert result.matches <= guard.BASELINE_MATCHES
    assert result.lines <= guard.BASELINE_LINES
    assert result.files <= guard.BASELINE_FILES
    assert guard.check_ratchet(REPO_ROOT) == 0


def test_ratchet_new_docs_play_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_guard()
    docs = tmp_path / "docs" / "notes"
    docs.mkdir(parents=True)
    (docs / "poison.md").write_text("a new play in a docs file\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        guard,
        "tracked_files",
        lambda _root: [docs / "poison.md", tmp_path / "ok.py"],
    )
    monkeypatch.setattr(guard, "BASELINE_MATCHES", 0)
    monkeypatch.setattr(guard, "BASELINE_LINES", 0)
    monkeypatch.setattr(guard, "BASELINE_FILES", 0)
    assert guard.check_ratchet(tmp_path) == 1
