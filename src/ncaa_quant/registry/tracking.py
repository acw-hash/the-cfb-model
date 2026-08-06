"""MLflow tracking helpers for training and evaluation runs (§8.7–8.8).

Callers (training / evaluation / registry) use :class:`TrackingSession` to log
params, per-season metrics, artifacts, and the run :class:`RunManifest`.
Training and evaluation modules are not modified here — they import this API.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ncaa_quant.registry.manifest import RunManifest, write_manifest
from ncaa_quant.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_EXPERIMENT: str = "ncaa-quant"


class TrackingError(RuntimeError):
    """MLflow tracking misconfiguration or I/O failure."""


@dataclass
class TrackingSession(AbstractContextManager["TrackingSession"]):
    """Context-managed MLflow run that always attaches a :class:`RunManifest`.

    Parameters
    ----------
    tracking_uri:
        MLflow tracking URI (``file:…`` for local / tests).
    experiment_name:
        MLflow experiment name.
    run_name:
        Optional human-readable run name.
    manifest:
        Required pin of git/DVC/config/seeds for this run.
    tags:
        Extra MLflow tags (string values).
    """

    tracking_uri: str
    experiment_name: str = DEFAULT_EXPERIMENT
    run_name: str | None = None
    manifest: RunManifest | None = None
    tags: dict[str, str] = field(default_factory=dict)
    _run_id: str | None = field(default=None, init=False, repr=False)
    _active: bool = field(default=False, init=False, repr=False)

    @property
    def run_id(self) -> str:
        """Active MLflow run id (raises if not started)."""
        if self._run_id is None:
            raise TrackingError("TrackingSession has no active run")
        return self._run_id

    def __enter__(self) -> TrackingSession:
        if self.manifest is None:
            raise TrackingError("TrackingSession requires a RunManifest")
        import mlflow
        from mlflow.tracking import MlflowClient

        mlflow.set_tracking_uri(self.tracking_uri)
        mlflow.set_experiment(self.experiment_name)
        run = mlflow.start_run(run_name=self.run_name)
        self._run_id = run.info.run_id
        self._active = True

        client = MlflowClient(tracking_uri=self.tracking_uri)
        base_tags = {
            "git_sha": self.manifest.git_sha,
            "dvc_hash": self.manifest.dvc_hash,
            "config_hash": self.manifest.config_hash,
            "environment_lockfile_hash": self.manifest.environment_lockfile_hash,
            **self.tags,
        }
        for key, value in base_tags.items():
            client.set_tag(self._run_id, key, value)

        mlflow.log_dict(self.manifest.to_dict(), "manifest.json")
        mlflow.log_params(
            {
                "git_sha": self.manifest.git_sha,
                "dvc_hash": self.manifest.dvc_hash,
                "config_hash": self.manifest.config_hash,
                "environment_lockfile_hash": self.manifest.environment_lockfile_hash,
            }
        )
        log.info(
            "mlflow_run_started",
            run_id=self._run_id,
            experiment=self.experiment_name,
            git_sha=self.manifest.git_sha,
        )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if not self._active:
            return
        import mlflow

        status = "FINISHED" if exc_type is None else "FAILED"
        mlflow.end_run(status=status)
        self._active = False
        log.info("mlflow_run_ended", run_id=self._run_id, status=status)

    def log_params(self, params: Mapping[str, Any]) -> None:
        """Log flat parameters (values coerced to str for MLflow)."""
        import mlflow

        self._require_active()
        cleaned: dict[str, str] = {}
        for key, value in params.items():
            cleaned[str(key)] = _param_str(value)
        if cleaned:
            mlflow.log_params(cleaned)

    def log_metrics(self, metrics: Mapping[str, float], *, step: int | None = None) -> None:
        """Log scalar metrics (optionally at ``step``)."""
        import mlflow

        self._require_active()
        for key, value in metrics.items():
            if step is None:
                mlflow.log_metric(str(key), float(value))
            else:
                mlflow.log_metric(str(key), float(value), step=step)

    def log_metrics_per_season(
        self,
        metrics_by_season: Mapping[int, Mapping[str, float]],
    ) -> None:
        """Log metrics keyed by season (``metric_season_{YYYY}`` + step=season)."""
        import mlflow

        self._require_active()
        for season, metrics in sorted(metrics_by_season.items()):
            for name, value in metrics.items():
                mlflow.log_metric(f"{name}_season_{season}", float(value), step=int(season))
                mlflow.log_metric(str(name), float(value), step=int(season))

    def log_artifact(self, path: Path | str, *, artifact_path: str | None = None) -> None:
        """Log a single file artifact."""
        import mlflow

        self._require_active()
        mlflow.log_artifact(str(path), artifact_path=artifact_path)

    def log_artifacts(self, dir_path: Path | str, *, artifact_path: str | None = None) -> None:
        """Log all files under a directory."""
        import mlflow

        self._require_active()
        mlflow.log_artifacts(str(dir_path), artifact_path=artifact_path)

    def log_dict_artifact(self, payload: Mapping[str, Any], artifact_file: str) -> None:
        """Log an in-memory mapping as a JSON artifact."""
        import mlflow

        self._require_active()
        mlflow.log_dict(dict(payload), artifact_file)

    def _require_active(self) -> None:
        if not self._active or self._run_id is None:
            raise TrackingError("no active MLflow run — use TrackingSession as a context manager")


def log_training_run(
    *,
    tracking_uri: str,
    manifest: RunManifest,
    params: Mapping[str, Any],
    metrics_by_season: Mapping[int, Mapping[str, float]],
    artifact_paths: Sequence[Path | str] = (),
    experiment_name: str = DEFAULT_EXPERIMENT,
    run_name: str | None = None,
    tags: Mapping[str, str] | None = None,
) -> str:
    """Convenience: one-shot training run logging. Returns the MLflow run id."""
    with TrackingSession(
        tracking_uri=tracking_uri,
        experiment_name=experiment_name,
        run_name=run_name or "train",
        manifest=manifest,
        tags={"phase": "training", **dict(tags or {})},
    ) as session:
        session.log_params(params)
        session.log_metrics_per_season(metrics_by_season)
        for path in artifact_paths:
            p = Path(path)
            if p.is_dir():
                session.log_artifacts(p)
            elif p.is_file():
                session.log_artifact(p)
        return session.run_id


def log_evaluation_run(
    *,
    tracking_uri: str,
    manifest: RunManifest,
    metrics: Mapping[str, float],
    metrics_by_season: Mapping[int, Mapping[str, float]] | None = None,
    report_paths: Sequence[Path | str] = (),
    experiment_name: str = DEFAULT_EXPERIMENT,
    run_name: str | None = None,
    tags: Mapping[str, str] | None = None,
) -> str:
    """Convenience: one-shot evaluation / backtest logging. Returns run id."""
    with TrackingSession(
        tracking_uri=tracking_uri,
        experiment_name=experiment_name,
        run_name=run_name or "evaluate",
        manifest=manifest,
        tags={"phase": "evaluation", **dict(tags or {})},
    ) as session:
        session.log_metrics(metrics)
        if metrics_by_season:
            session.log_metrics_per_season(metrics_by_season)
        for path in report_paths:
            p = Path(path)
            if p.is_dir():
                session.log_artifacts(p, artifact_path="reports")
            elif p.is_file():
                session.log_artifact(p, artifact_path="reports")
        return session.run_id


def staging_dir_with_manifest(parent: Path, manifest: RunManifest) -> Path:
    """Create a temp-ish dir containing ``manifest.json`` for artifact uploads."""
    parent.mkdir(parents=True, exist_ok=True)
    write_manifest(parent / "manifest.json", manifest)
    return parent


def _param_str(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return str(value)
