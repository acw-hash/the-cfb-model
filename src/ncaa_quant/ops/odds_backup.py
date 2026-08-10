"""Off-machine replication of unbackfillable raw Odds API archives (E-1).

DESIGN §10 / Task 24: replicate a source tree (live ``data/raw/odds_api`` or
historical ``data/raw/odds_api_historical``) to a versioned off-machine target
within 24h of capture, and support a quarterly restore drill.

Live and historical must use **separate** dest roots — each has its own
``current/`` mirror. The CLI requires ``--dest`` so a forgotten flag cannot
silently write into the wrong tree. Library callers may still resolve via
``ODDS_RAW_BACKUP_ROOT`` or :data:`DEFAULT_BACKUP_ROOT` (live path only).

This module uses the stdlib only (no new dependency).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Live archive only — never use as dest for odds_api_historical.
DEFAULT_BACKUP_ROOT = Path("D:/ncaa-quant-backups/odds_api")
MANIFEST_NAME = "backup_manifest.json"
FRESHNESS_HOURS = 24


class OddsBackupError(RuntimeError):
    """Backup or restore drill failure."""


@dataclass(frozen=True)
class FileDigest:
    """Relative path + SHA-256 of one archived snapshot."""

    relative_path: str
    sha256: str
    size_bytes: int


@dataclass
class BackupManifest:
    """Versioned record of one backup (or restore-drill) run."""

    created_at: str
    source_root: str
    dest_root: str
    n_files: int
    total_bytes: int
    files: list[FileDigest] = field(default_factory=list)
    kind: str = "backup"  # backup | restore_drill
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["files"] = [asdict(f) for f in self.files]
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> BackupManifest:
        raw_files = payload.get("files", [])
        files = [
            FileDigest(
                relative_path=str(f["relative_path"]),
                sha256=str(f["sha256"]),
                size_bytes=int(f["size_bytes"]),
            )
            for f in raw_files
        ]
        return cls(
            created_at=str(payload["created_at"]),
            source_root=str(payload["source_root"]),
            dest_root=str(payload["dest_root"]),
            n_files=int(payload["n_files"]),
            total_bytes=int(payload["total_bytes"]),
            files=files,
            kind=str(payload.get("kind", "backup")),
            notes=str(payload.get("notes", "")),
        )


def resolve_backup_root(explicit: Path | str | None = None) -> Path:
    """Resolve backup destination: explicit → env → live default.

    Prefer an explicit path. The env/default fallbacks are live-oriented;
    historical backups must pass ``explicit`` (CLI ``--dest`` is required).
    """
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("ODDS_RAW_BACKUP_ROOT", "").strip()
    if env:
        return Path(env)
    return DEFAULT_BACKUP_ROOT


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> list[FileDigest]:
    """Hash every file under ``root`` (relative paths, sorted)."""
    if not root.is_dir():
        raise OddsBackupError(f"odds archive root does not exist: {root}")
    out: list[FileDigest] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        out.append(
            FileDigest(
                relative_path=rel,
                sha256=_sha256_file(path),
                size_bytes=path.stat().st_size,
            )
        )
    return out


def replicate_odds_archive(
    source_root: Path | str,
    dest_root: Path | str | None = None,
    *,
    notes: str = "",
) -> BackupManifest:
    """Copy ``source_root`` → versioned snapshot under ``dest_root`` and write a manifest.

    Layout::

        {dest_root}/
          current/                 # mirror of live archive (idempotent sync)
          snapshots/
            {utc_ts}/              # point-in-time copy for versioning
          backup_manifest.json     # latest backup record
    """
    src = Path(source_root)
    dest = resolve_backup_root(dest_root)
    if not src.is_dir():
        raise OddsBackupError(f"source odds archive missing: {src}")

    files = inventory(src)
    if not files:
        raise OddsBackupError(f"source odds archive is empty: {src}")

    created = datetime.now(tz=UTC)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    current = dest / "current"
    snapshot = dest / "snapshots" / stamp
    current.mkdir(parents=True, exist_ok=True)
    snapshot.mkdir(parents=True, exist_ok=True)

    # Sync into current/ (replace file-by-file), then copy tree to snapshot/.
    for digest in files:
        src_file = src / digest.relative_path
        for target_root in (current, snapshot):
            target = target_root / digest.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, target)

    # Drop files in current/ that no longer exist in source.
    live_rels = {f.relative_path for f in files}
    for path in list(current.rglob("*")):
        if path.is_file():
            rel = path.relative_to(current).as_posix()
            if rel not in live_rels:
                path.unlink()

    manifest = BackupManifest(
        created_at=created.isoformat(),
        source_root=str(src.resolve()),
        dest_root=str(dest.resolve()),
        n_files=len(files),
        total_bytes=sum(f.size_bytes for f in files),
        files=files,
        kind="backup",
        notes=notes,
    )
    dest.mkdir(parents=True, exist_ok=True)
    (dest / MANIFEST_NAME).write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (snapshot / MANIFEST_NAME).write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def load_manifest(dest_root: Path | str | None = None) -> BackupManifest:
    """Load the latest backup manifest from ``dest_root``."""
    dest = resolve_backup_root(dest_root)
    path = dest / MANIFEST_NAME
    if not path.is_file():
        raise OddsBackupError(f"no backup manifest at {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BackupManifest.from_dict(payload)


def assert_backup_fresh(
    dest_root: Path | str | None = None,
    *,
    max_age: timedelta = timedelta(hours=FRESHNESS_HOURS),
    now: datetime | None = None,
) -> BackupManifest:
    """Fail when the latest backup is older than ``max_age`` (default 24h)."""
    manifest = load_manifest(dest_root)
    created = datetime.fromisoformat(manifest.created_at)
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    clock = now if now is not None else datetime.now(tz=UTC)
    age = clock - created
    if age > max_age:
        raise OddsBackupError(
            f"odds archive backup is stale: age={age} > max_age={max_age} "
            f"(created_at={manifest.created_at})"
        )
    return manifest


def restore_drill(
    dest_root: Path | str | None = None,
    *,
    restore_dir: Path | str | None = None,
    source_root: Path | str | None = None,
) -> BackupManifest:
    """Copy ``current/`` to a restore directory and verify digests match the manifest.

    When ``source_root`` is given, also assert the restored digests match the
    live archive (bit-for-bit drill). Returns a restore-drill manifest record.
    """
    dest = resolve_backup_root(dest_root)
    manifest = load_manifest(dest)
    current = dest / "current"
    if not current.is_dir():
        raise OddsBackupError(f"backup current/ missing under {dest}")

    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(restore_dir) if restore_dir is not None else dest / "restore_drills" / stamp
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(current, out)

    restored = inventory(out)
    by_rel = {f.relative_path: f for f in restored}
    mismatches: list[str] = []
    for expected in manifest.files:
        got = by_rel.get(expected.relative_path)
        if got is None:
            mismatches.append(f"missing {expected.relative_path}")
        elif got.sha256 != expected.sha256:
            mismatches.append(
                f"hash mismatch {expected.relative_path}: "
                f"manifest={expected.sha256} restored={got.sha256}"
            )
    if mismatches:
        raise OddsBackupError(
            "restore drill failed digest checks:\n  " + "\n  ".join(mismatches[:20])
        )

    if source_root is not None:
        live = {f.relative_path: f for f in inventory(Path(source_root))}
        for expected in manifest.files:
            live_f = live.get(expected.relative_path)
            if live_f is None or live_f.sha256 != expected.sha256:
                mismatches.append(
                    f"live divergence {expected.relative_path} "
                    f"(re-run backup before trusting restore)"
                )
        if mismatches:
            raise OddsBackupError(
                "restore drill diverged from live archive:\n  " + "\n  ".join(mismatches[:20])
            )

    drill = BackupManifest(
        created_at=datetime.now(tz=UTC).isoformat(),
        source_root=str(current.resolve()),
        dest_root=str(out.resolve()),
        n_files=len(restored),
        total_bytes=sum(f.size_bytes for f in restored),
        files=restored,
        kind="restore_drill",
        notes=f"verified against manifest created_at={manifest.created_at}",
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "restore_drill_manifest.json").write_text(
        json.dumps(drill.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return drill
