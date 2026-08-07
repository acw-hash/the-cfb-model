"""Provenance verification for run manifests (ADR 0005).

DESIGN §1.4 anchors reproducibility on a run's recorded commit. These tests pin
the two ways that anchor silently fails: the commit no longer exists in the
repository, or the tree was dirty when the run executed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ncaa_quant.registry.manifest import (
    ManifestError,
    RunManifest,
    build_manifest,
    git_sha_resolvable,
    require_citable_provenance,
    resolve_git_dirty,
    resolve_git_sha,
    verify_provenance,
)
from ncaa_quant.utils.seeding import set_global_seed

# The SHA every data/backtests/task23_* manifest pins, which does not exist in
# this repository. ADR 0005 records why.
ORPHANED_TASK23_SHA = "b81cb536f894a5bcfce5472dfb98615907f18265"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repository with one commit."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "--initial-branch=main"], root)
    _git(["config", "user.email", "test@example.com"], root)
    _git(["config", "user.name", "Test"], root)
    (root / "tracked.txt").write_text("original\n", encoding="utf-8")
    _git(["add", "tracked.txt"], root)
    _git(["commit", "-m", "initial"], root)
    return root


def _manifest_for(repo_root: Path) -> RunManifest:
    return build_manifest(
        config={"seed": 7},
        seed_manifest=set_global_seed(7),
        dvc_hash="none",
        environment_lockfile_hash="none",
        repo_root=repo_root,
    )


def test_clean_tree_produces_a_citable_manifest(repo: Path) -> None:
    manifest = _manifest_for(repo)

    assert manifest.git_sha == resolve_git_sha(repo_root=repo)
    assert manifest.git_dirty is False

    report = verify_provenance(manifest, repo_root=repo)
    assert report.citable
    assert report.problems == ()
    require_citable_provenance(manifest, repo_root=repo)


def test_dirty_tree_is_recorded_and_rejected(repo: Path) -> None:
    (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
    assert resolve_git_dirty(repo_root=repo) is True

    manifest = _manifest_for(repo)
    assert manifest.git_dirty is True

    report = verify_provenance(manifest, repo_root=repo)
    assert not report.citable
    assert report.sha_resolvable is True
    assert any("dirty" in problem for problem in report.problems)


def test_untracked_files_alone_do_not_make_a_run_uncitable(repo: Path) -> None:
    (repo / "scratch.log").write_text("noise\n", encoding="utf-8")

    assert resolve_git_dirty(repo_root=repo) is False
    assert verify_provenance(_manifest_for(repo), repo_root=repo).citable


def test_orphaned_sha_is_rejected(repo: Path) -> None:
    """The Task 23 failure mode: history replaced, so the pin dangles."""
    assert git_sha_resolvable(ORPHANED_TASK23_SHA, repo_root=repo) is False

    manifest = _manifest_for(repo)
    orphaned = RunManifest.from_dict({**manifest.to_dict(), "git_sha": ORPHANED_TASK23_SHA})

    report = verify_provenance(orphaned, repo_root=repo)
    assert not report.citable
    assert report.sha_recorded is True
    assert report.sha_resolvable is False
    assert any(ORPHANED_TASK23_SHA in problem for problem in report.problems)

    with pytest.raises(ManifestError, match="not citable"):
        require_citable_provenance(orphaned, repo_root=repo)


def test_unknown_sha_is_rejected(repo: Path) -> None:
    manifest = _manifest_for(repo)
    unknown = RunManifest.from_dict({**manifest.to_dict(), "git_sha": "unknown"})

    report = verify_provenance(unknown, repo_root=repo)
    assert report.sha_recorded is False
    assert any("not recorded" in problem for problem in report.problems)


def test_git_dirty_defaults_to_true_outside_a_repository(tmp_path: Path) -> None:
    """An unverifiable tree must never be recorded as clean."""
    assert resolve_git_dirty(repo_root=tmp_path) is True


def test_manifest_round_trip_preserves_git_dirty(repo: Path) -> None:
    (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
    manifest = _manifest_for(repo)

    assert RunManifest.from_dict(manifest.to_dict()).git_dirty is True


def test_legacy_manifest_without_git_dirty_reads_as_clean(repo: Path) -> None:
    """Manifests written before this field must stay loadable."""
    payload = _manifest_for(repo).to_dict()
    del payload["git_dirty"]

    assert RunManifest.from_dict(payload).git_dirty is False


def test_verify_cli_rejects_an_orphaned_manifest(repo: Path, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ncaa_quant.cli import app
    from ncaa_quant.registry.manifest import write_manifest

    run_dir = tmp_path / "backtests" / "run_a" / "full"
    manifest = _manifest_for(repo)
    write_manifest(
        run_dir / "manifest.json",
        RunManifest.from_dict({**manifest.to_dict(), "git_sha": ORPHANED_TASK23_SHA}),
    )

    result = CliRunner().invoke(app, ["backtest", "verify", "--output-root", str(tmp_path)])

    assert result.exit_code == 1
    assert "REJECTED" in result.stdout
    assert "0/1 runs citable" in result.stdout
