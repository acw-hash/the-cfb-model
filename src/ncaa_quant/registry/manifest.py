"""Run manifest binding git SHA + DVC hash + config hash + seed manifest (§8.8)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ncaa_quant.utils.seeding import SeedManifest


class ManifestError(ValueError):
    """Invalid or incomplete run manifest."""


@dataclass(frozen=True)
class RunManifest:
    """Immutable pin of code + data + config + seeds for one MLflow run.

    Units: hashes are lowercase hex SHA-256 (or git SHA-1 for ``git_sha``);
    ``created_at`` is an ISO-8601 UTC timestamp.

    ``git_dirty`` records whether the working tree carried uncommitted changes
    when the run started. A dirty run is NOT regenerable from ``git_sha`` alone,
    so DESIGN §1.4's artifact anchor does not hold for it — see
    :func:`verify_provenance`.
    """

    git_sha: str
    dvc_hash: str
    config_hash: str
    seed_manifest: dict[str, Any]
    environment_lockfile_hash: str
    created_at: str
    extra: dict[str, str] = field(default_factory=dict)
    git_dirty: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict for ``mlflow.log_dict`` / ``manifest.json``."""
        return asdict(self)

    def to_json(self) -> str:
        """Canonical JSON (sorted keys) for byte-stable artifact writes."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RunManifest:
        """Rehydrate a manifest from a mapping (artifact or index)."""
        required = (
            "git_sha",
            "dvc_hash",
            "config_hash",
            "seed_manifest",
            "environment_lockfile_hash",
            "created_at",
        )
        missing = [k for k in required if k not in payload]
        if missing:
            raise ManifestError(f"manifest missing keys: {missing}")
        seeds = payload["seed_manifest"]
        if not isinstance(seeds, Mapping):
            raise ManifestError("seed_manifest must be a mapping")
        extra_raw = payload.get("extra", {})
        if not isinstance(extra_raw, Mapping):
            raise ManifestError("extra must be a mapping")
        return cls(
            git_sha=str(payload["git_sha"]),
            dvc_hash=str(payload["dvc_hash"]),
            config_hash=str(payload["config_hash"]),
            seed_manifest=dict(seeds),
            environment_lockfile_hash=str(payload["environment_lockfile_hash"]),
            created_at=str(payload["created_at"]),
            extra={str(k): str(v) for k, v in extra_raw.items()},
            git_dirty=bool(payload.get("git_dirty", False)),
        )


def sha256_bytes(data: bytes) -> str:
    """Return lowercase hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """SHA-256 a file in chunks (empty file → hash of empty bytes)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_json(payload: Mapping[str, Any] | list[Any]) -> str:
    """Canonical JSON SHA-256 (sorted keys, no whitespace variance)."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_bytes(raw.encode("utf-8"))


def resolve_git_sha(*, repo_root: Path | None = None) -> str:
    """Best-effort ``git rev-parse HEAD``; returns ``unknown`` if unavailable."""
    cwd = repo_root if repo_root is not None else Path.cwd()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    sha = proc.stdout.strip()
    return sha if sha else "unknown"


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str] | None:
    """Run a git subcommand; ``None`` when git is unavailable or errored out."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def resolve_git_dirty(*, repo_root: Path | None = None) -> bool:
    """True when the working tree has uncommitted tracked changes.

    A dirty tree means the run cannot be regenerated from ``git_sha`` alone.
    Returns True when git is unavailable, because an unverifiable tree must not
    be recorded as clean.
    """
    cwd = repo_root if repo_root is not None else Path.cwd()
    proc = _git(["status", "--porcelain", "--untracked-files=no"], cwd=cwd)
    if proc is None or proc.returncode != 0:
        return True
    return bool(proc.stdout.strip())


def git_sha_resolvable(sha: str, *, repo_root: Path | None = None) -> bool:
    """True when ``sha`` names a commit object reachable in this repository."""
    if not sha or sha == "unknown":
        return False
    cwd = repo_root if repo_root is not None else Path.cwd()
    proc = _git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd=cwd)
    return proc is not None and proc.returncode == 0


@dataclass(frozen=True)
class ProvenanceReport:
    """Whether a manifest's code pin can actually be resolved and trusted."""

    git_sha: str
    sha_recorded: bool
    sha_resolvable: bool
    tree_clean: bool
    problems: tuple[str, ...]

    @property
    def citable(self) -> bool:
        """True when results from this run may be cited as evidence (§1.4)."""
        return not self.problems


def verify_provenance(manifest: RunManifest, *, repo_root: Path | None = None) -> ProvenanceReport:
    """Check a manifest's code pin against the current repository.

    DESIGN §1.4 anchors reproducibility on the artifact's recorded commit. That
    only holds if the commit exists here and the tree was clean when it ran.
    ADR 0005 records the case this guards against: the Task 23 manifests pin a
    SHA that no longer exists in this repository, so their numbers cannot be
    regenerated and must not be cited.
    """
    sha = manifest.git_sha
    recorded = bool(sha) and sha != "unknown"
    resolvable = git_sha_resolvable(sha, repo_root=repo_root)
    clean = not manifest.git_dirty

    problems: list[str] = []
    if not recorded:
        problems.append("git_sha not recorded")
    elif not resolvable:
        problems.append(f"git_sha {sha} does not resolve to a commit in this repository")
    if not clean:
        problems.append("run executed from a dirty working tree")

    return ProvenanceReport(
        git_sha=sha,
        sha_recorded=recorded,
        sha_resolvable=resolvable,
        tree_clean=clean,
        problems=tuple(problems),
    )


def require_citable_provenance(manifest: RunManifest, *, repo_root: Path | None = None) -> None:
    """Raise unless the manifest's results may be cited as evidence.

    Call this before publishing or promoting on a run's numbers, not before
    writing them: a run whose provenance is broken should still record what it
    did, it just may not be used to make claims.
    """
    report = verify_provenance(manifest, repo_root=repo_root)
    if not report.citable:
        raise ManifestError("run provenance is not citable: " + "; ".join(report.problems))


def resolve_dvc_hash(*, data_dir: Path | None = None, repo_root: Path | None = None) -> str:
    """Hash DVC lock / data pointer files when present; else ``none``.

    Prefers ``dvc.lock`` at the repo root. If absent, hashes a sorted listing of
    ``*.dvc`` files under ``data_dir`` (content hashes concatenated). This keeps
    the manifest field populated for local/dev trees without a DVC remote.
    """
    root = repo_root if repo_root is not None else Path.cwd()
    lock = root / "dvc.lock"
    if lock.is_file():
        return sha256_file(lock)

    search_root = data_dir if data_dir is not None else root / "data"
    if not search_root.is_dir():
        return "none"
    dvc_files = sorted(search_root.rglob("*.dvc"))
    if not dvc_files:
        return "none"
    h = hashlib.sha256()
    for path in dvc_files:
        rel = path.relative_to(search_root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(sha256_file(path).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def resolve_lockfile_hash(*, repo_root: Path | None = None) -> str:
    """SHA-256 of ``uv.lock`` (preferred) or ``poetry.lock``; else ``none``."""
    root = repo_root if repo_root is not None else Path.cwd()
    for name in ("uv.lock", "poetry.lock"):
        path = root / name
        if path.is_file():
            return sha256_file(path)
    return "none"


def build_manifest(
    *,
    config: Mapping[str, Any] | None = None,
    config_hash_value: str | None = None,
    seed_manifest: SeedManifest | Mapping[str, Any] | None = None,
    git_sha: str | None = None,
    git_dirty: bool | None = None,
    dvc_hash: str | None = None,
    environment_lockfile_hash: str | None = None,
    repo_root: Path | None = None,
    data_dir: Path | None = None,
    extra: Mapping[str, str] | None = None,
    created_at: str | None = None,
) -> RunManifest:
    """Assemble a :class:`RunManifest` from live environment + optional overrides.

    Parameters
    ----------
    config:
        Serialized app config (e.g. ``dump_config``). Used when
        ``config_hash_value`` is not supplied.
    seed_manifest:
        :class:`SeedManifest` or plain dict. Defaults to an empty seed record
        with ``global_seed=-1`` when omitted (caller should normally pass the
        real manifest from ``set_global_seed``).
    """
    if config_hash_value is None:
        if config is None:
            raise ManifestError("provide config or config_hash_value")
        cfg_hash = sha256_json(dict(config))
    else:
        cfg_hash = config_hash_value

    if seed_manifest is None:
        seeds: dict[str, Any] = {
            "global_seed": -1,
            "python_hash_seed": "",
            "lightgbm_seed": -1,
            "xgboost_seed": -1,
            "numpy_seed": -1,
            "extra": {},
        }
    elif isinstance(seed_manifest, SeedManifest):
        seeds = seed_manifest.to_dict()
    else:
        seeds = dict(seed_manifest)

    root = repo_root
    return RunManifest(
        git_sha=git_sha if git_sha is not None else resolve_git_sha(repo_root=root),
        dvc_hash=(
            dvc_hash
            if dvc_hash is not None
            else resolve_dvc_hash(data_dir=data_dir, repo_root=root)
        ),
        config_hash=cfg_hash,
        seed_manifest=seeds,
        environment_lockfile_hash=(
            environment_lockfile_hash
            if environment_lockfile_hash is not None
            else resolve_lockfile_hash(repo_root=root)
        ),
        created_at=created_at
        if created_at is not None
        else datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        extra=dict(extra) if extra else {},
        git_dirty=(git_dirty if git_dirty is not None else resolve_git_dirty(repo_root=root)),
    )


def write_manifest(path: Path, manifest: RunManifest) -> Path:
    """Write ``manifest.json`` atomically; return the path written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(manifest.to_json() + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def read_manifest(path: Path) -> RunManifest:
    """Load a ``manifest.json`` written by :func:`write_manifest`."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ManifestError("manifest.json root must be a mapping")
    return RunManifest.from_dict(payload)
