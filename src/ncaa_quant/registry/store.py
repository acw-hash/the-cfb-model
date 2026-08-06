"""Local model registry index with MLflow run binding (DESIGN §8.7 / §10).

Custom stages (``candidate → challenger → champion → archived``) live in a
JSON index under ``registry_root``. Each version points at an MLflow run and
an on-disk artifact directory (model bundle, calibration maps, predictions).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ncaa_quant.registry.manifest import RunManifest, read_manifest, write_manifest
from ncaa_quant.registry.stages import ModelStage, assert_transition_allowed
from ncaa_quant.utils.logging import get_logger

log = get_logger(__name__)

INDEX_FILENAME: str = "registry_index.json"
PREDICTIONS_FILENAME: str = "predictions.bin"
DEFAULT_MODEL_NAME: str = "ncaa-quant"


class RegistryError(RuntimeError):
    """Registry index / artifact contract violation."""


class NoChampionError(RegistryError):
    """Raised when inference asks for ``champion`` and none is pinned."""


@dataclass
class ModelVersionRecord:
    """One registered model version in the local index."""

    version: int
    stage: str
    run_id: str
    artifact_dir: str
    registered_at: str
    manifest: dict[str, Any]
    metrics: dict[str, float] = field(default_factory=dict)
    notes: str = ""
    feature_signature: dict[str, Any] | None = None
    prior_champion_version: int | None = None

    @property
    def stage_enum(self) -> ModelStage:
        """Parse ``stage`` string into :class:`ModelStage`."""
        return ModelStage(self.stage)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable record."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ModelVersionRecord:
        """Rehydrate from index JSON."""
        return cls(
            version=int(payload["version"]),
            stage=str(payload["stage"]),
            run_id=str(payload["run_id"]),
            artifact_dir=str(payload["artifact_dir"]),
            registered_at=str(payload["registered_at"]),
            manifest=dict(payload.get("manifest") or {}),
            metrics={str(k): float(v) for k, v in dict(payload.get("metrics") or {}).items()},
            notes=str(payload.get("notes") or ""),
            feature_signature=(
                dict(payload["feature_signature"])
                if payload.get("feature_signature") is not None
                else None
            ),
            prior_champion_version=(
                int(payload["prior_champion_version"])
                if payload.get("prior_champion_version") is not None
                else None
            ),
        )


@dataclass
class RegistryIndex:
    """Top-level index document."""

    model_name: str
    versions: list[ModelVersionRecord] = field(default_factory=list)
    champion_history: list[int] = field(default_factory=list)
    overrides: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize index."""
        return {
            "model_name": self.model_name,
            "versions": [v.to_dict() for v in self.versions],
            "champion_history": list(self.champion_history),
            "overrides": list(self.overrides),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RegistryIndex:
        """Load index."""
        versions = [ModelVersionRecord.from_dict(v) for v in payload.get("versions") or []]
        return cls(
            model_name=str(payload.get("model_name") or DEFAULT_MODEL_NAME),
            versions=versions,
            champion_history=[int(x) for x in payload.get("champion_history") or []],
            overrides=[dict(x) for x in payload.get("overrides") or []],
        )


class ModelRegistry:
    """Filesystem-backed registry with optional MLflow tagging.

    Inference **must** call :meth:`resolve_champion` — never hardcode a version.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        model_name: str = DEFAULT_MODEL_NAME,
        tracking_uri: str | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self.tracking_uri = tracking_uri
        self._index_path = self.root / INDEX_FILENAME
        if not self._index_path.is_file():
            self._write_index(RegistryIndex(model_name=model_name))

    # ------------------------------------------------------------------
    # Index I/O
    # ------------------------------------------------------------------
    def _read_index(self) -> RegistryIndex:
        payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise RegistryError("registry_index.json root must be a mapping")
        return RegistryIndex.from_dict(payload)

    def _write_index(self, index: RegistryIndex) -> None:
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(index.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        tmp.replace(self._index_path)

    def list_versions(self) -> list[ModelVersionRecord]:
        """All versions ascending by version number."""
        return sorted(self._read_index().versions, key=lambda v: v.version)

    def get_version(self, version: int) -> ModelVersionRecord:
        """Fetch a single version or raise."""
        for record in self._read_index().versions:
            if record.version == version:
                return record
        raise RegistryError(f"version {version} not found in registry {self.root}")

    def versions_in_stage(self, stage: ModelStage) -> list[ModelVersionRecord]:
        """All versions currently at ``stage``."""
        return [v for v in self.list_versions() if v.stage_enum is stage]

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_candidate(
        self,
        *,
        run_id: str,
        manifest: RunManifest,
        predictions: bytes,
        metrics: Mapping[str, float] | None = None,
        feature_signature: Mapping[str, Any] | None = None,
        extra_artifacts: Mapping[str, Path | bytes] | None = None,
        notes: str = "",
        stage: ModelStage = ModelStage.CANDIDATE,
    ) -> ModelVersionRecord:
        """Register a new version at ``stage`` (default ``candidate``).

        ``predictions`` bytes are the golden inference artifact used for
        rollback byte-identity checks.
        """
        if stage is ModelStage.CHAMPION:
            raise RegistryError(
                "cannot register directly as champion — use promote() or rollback()"
            )
        index = self._read_index()
        next_version = 1 + max((v.version for v in index.versions), default=0)
        artifact_dir = self.root / "artifacts" / f"v{next_version}"
        artifact_dir.mkdir(parents=True, exist_ok=False)

        write_manifest(artifact_dir / "manifest.json", manifest)
        (artifact_dir / PREDICTIONS_FILENAME).write_bytes(predictions)
        if feature_signature is not None:
            (artifact_dir / "feature_signature.json").write_text(
                json.dumps(dict(feature_signature), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if extra_artifacts:
            for name, payload in extra_artifacts.items():
                dest = artifact_dir / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(payload, (bytes, bytearray)):
                    dest.write_bytes(bytes(payload))
                else:
                    src = Path(payload)
                    if src.is_dir():
                        shutil.copytree(src, dest)
                    else:
                        shutil.copy2(src, dest)

        record = ModelVersionRecord(
            version=next_version,
            stage=stage.value,
            run_id=run_id,
            artifact_dir=str(artifact_dir),
            registered_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            manifest=manifest.to_dict(),
            metrics={str(k): float(v) for k, v in dict(metrics or {}).items()},
            notes=notes,
            feature_signature=dict(feature_signature) if feature_signature else None,
        )
        index.versions.append(record)
        self._write_index(index)
        self._sync_mlflow_stage(record)
        log.info(
            "registry_candidate_registered",
            version=next_version,
            stage=stage.value,
            run_id=run_id,
        )
        return record

    # ------------------------------------------------------------------
    # Stage transitions
    # ------------------------------------------------------------------
    def set_stage(
        self,
        version: int,
        stage: ModelStage,
        *,
        allow_champion_pin: bool = False,
        prior_champion_version: int | None = None,
    ) -> ModelVersionRecord:
        """Move ``version`` to ``stage`` (enforces allowed transitions).

        Pinning ``champion`` also demotes any existing champion to ``archived``
        and appends to ``champion_history``. Direct champion pins require
        ``allow_champion_pin=True`` (promotion / rollback paths only).
        """
        index = self._read_index()
        record = None
        for v in index.versions:
            if v.version == version:
                record = v
                break
        if record is None:
            raise RegistryError(f"version {version} not found")

        current = record.stage_enum
        if stage is ModelStage.CHAMPION:
            if not allow_champion_pin and current is not ModelStage.CHALLENGER:
                raise RegistryError(
                    f"champion pin requires promote()/rollback() (current stage={current.value})"
                )
            # Demote current champion(s).
            for other in index.versions:
                if other.stage_enum is ModelStage.CHAMPION and other.version != version:
                    other.stage = ModelStage.ARCHIVED.value
                    self._sync_mlflow_stage(other)
            if version not in index.champion_history:
                index.champion_history.append(version)
            record.prior_champion_version = prior_champion_version
            record.stage = ModelStage.CHAMPION.value
        else:
            if current is not stage:
                assert_transition_allowed(current, stage)
            record.stage = stage.value

        self._write_index(index)
        self._sync_mlflow_stage(record)
        log.info("registry_stage_set", version=version, stage=stage.value)
        return record

    def archive(
        self,
        version: int,
        *,
        comparison_report: Mapping[str, Any] | None = None,
        report_html: str | None = None,
    ) -> ModelVersionRecord:
        """Move a version to ``archived``, optionally attaching a comparison report."""
        record = self.get_version(version)
        artifact_dir = Path(record.artifact_dir)
        if comparison_report is not None:
            (artifact_dir / "comparison_report.json").write_text(
                json.dumps(dict(comparison_report), indent=2, sort_keys=True, default=str) + "\n",
                encoding="utf-8",
            )
        if report_html is not None:
            (artifact_dir / "comparison_report.html").write_text(report_html, encoding="utf-8")
        # Archive is allowed from any non-archived stage — bypass path check
        # by writing directly when coming from champion/challenger/candidate.
        index = self._read_index()
        for v in index.versions:
            if v.version == version:
                v.stage = ModelStage.ARCHIVED.value
                self._write_index(index)
                self._sync_mlflow_stage(v)
                log.info("registry_archived", version=version)
                return v
        raise RegistryError(f"version {version} not found")

    def record_force_override(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Append an immutable force-promotion override record to the index."""
        index = self._read_index()
        entry = {
            "recorded_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **dict(payload),
        }
        index.overrides.append(entry)
        overrides_dir = self.root / "overrides"
        overrides_dir.mkdir(parents=True, exist_ok=True)
        stamp = entry["recorded_at"].replace(":", "").replace("-", "")
        out = overrides_dir / f"override_{stamp}_v{payload.get('version', 'x')}.json"
        out.write_text(
            json.dumps(entry, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )
        self._write_index(index)
        log.warning("registry_force_override", path=str(out), version=payload.get("version"))
        return entry

    # ------------------------------------------------------------------
    # Resolve
    # ------------------------------------------------------------------
    def resolve_champion(self) -> ModelVersionRecord:
        """Return the current champion version — never a hardcoded version id.

        Raises
        ------
        NoChampionError
            If no version is currently staged as ``champion``.
        """
        champions = self.versions_in_stage(ModelStage.CHAMPION)
        if not champions:
            raise NoChampionError(
                f"no champion registered for model {self.model_name!r} at {self.root}"
            )
        if len(champions) > 1:
            raise RegistryError(
                f"corrupt registry: {len(champions)} champions pinned "
                f"(versions {[c.version for c in champions]})"
            )
        return champions[0]

    def load_predictions(self, version: int) -> bytes:
        """Read the prediction artifact bytes for ``version``."""
        record = self.get_version(version)
        path = Path(record.artifact_dir) / PREDICTIONS_FILENAME
        if not path.is_file():
            raise RegistryError(f"predictions missing for version {version}: {path}")
        return path.read_bytes()

    def load_champion_predictions(self) -> bytes:
        """Load prediction bytes for the current champion (inference path)."""
        champ = self.resolve_champion()
        return self.load_predictions(champ.version)

    def load_manifest(self, version: int) -> RunManifest:
        """Load the on-disk manifest for ``version``."""
        record = self.get_version(version)
        return read_manifest(Path(record.artifact_dir) / "manifest.json")

    def champion_history(self) -> list[int]:
        """Ordered list of versions that have held the champion pin."""
        return list(self._read_index().champion_history)

    # ------------------------------------------------------------------
    # MLflow sync (best-effort tags)
    # ------------------------------------------------------------------
    def _sync_mlflow_stage(self, record: ModelVersionRecord) -> None:
        if not self.tracking_uri or not record.run_id:
            return
        try:
            from mlflow.tracking import MlflowClient

            client = MlflowClient(tracking_uri=self.tracking_uri)
            client.set_tag(record.run_id, "registry.stage", record.stage)
            client.set_tag(record.run_id, "registry.version", str(record.version))
            client.set_tag(record.run_id, "registry.model_name", self.model_name)
        except Exception as exc:  # noqa: BLE001 — best-effort sync
            log.warning(
                "mlflow_stage_sync_failed",
                run_id=record.run_id,
                error=str(exc),
            )
