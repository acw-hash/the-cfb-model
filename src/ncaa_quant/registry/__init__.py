"""Model registry, MLflow tracking, promotion gate, and rollback (Task 22).

DESIGN §8 items 7–8, §10, §15 item 22.
"""

from __future__ import annotations

from ncaa_quant.registry.manifest import (
    ManifestError,
    ProvenanceReport,
    RunManifest,
    build_manifest,
    read_manifest,
    require_citable_provenance,
    verify_provenance,
    write_manifest,
)
from ncaa_quant.registry.promote import (
    PROMOTION_ALPHA,
    REQUIRED_METRICS,
    ComparisonReport,
    MetricComparisonInput,
    MetricTestResult,
    PromotionError,
    PromotionResult,
    evaluate_gate,
    load_metric_comparisons_from_mlflow_runs,
    promote,
    promote_from_mlflow_runs,
    rollback,
)
from ncaa_quant.registry.promotion_ledger import (
    LedgerEntry,
    LedgerError,
    PromotionLedger,
    bonferroni_alpha,
)
from ncaa_quant.registry.resolve import (
    load_champion_feature_signature,
    load_champion_manifest,
    load_champion_predictions,
    open_registry,
    resolve_champion,
)
from ncaa_quant.registry.stages import ModelStage
from ncaa_quant.registry.store import (
    ModelRegistry,
    ModelVersionRecord,
    NoChampionError,
    RegistryError,
)
from ncaa_quant.registry.tracking import (
    TrackingSession,
    log_evaluation_run,
    log_training_run,
)

__all__ = [
    "REQUIRED_METRICS",
    "PROMOTION_ALPHA",
    "ComparisonReport",
    "ManifestError",
    "MetricComparisonInput",
    "MetricTestResult",
    "ModelRegistry",
    "ModelStage",
    "ModelVersionRecord",
    "NoChampionError",
    "LedgerEntry",
    "LedgerError",
    "PromotionError",
    "PromotionLedger",
    "PromotionResult",
    "ProvenanceReport",
    "RegistryError",
    "RunManifest",
    "TrackingSession",
    "bonferroni_alpha",
    "build_manifest",
    "evaluate_gate",
    "load_champion_feature_signature",
    "load_champion_manifest",
    "load_champion_predictions",
    "load_metric_comparisons_from_mlflow_runs",
    "log_evaluation_run",
    "log_training_run",
    "open_registry",
    "promote",
    "promote_from_mlflow_runs",
    "read_manifest",
    "require_citable_provenance",
    "resolve_champion",
    "rollback",
    "verify_provenance",
    "write_manifest",
]
