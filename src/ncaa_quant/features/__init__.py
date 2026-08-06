"""Feature registry and as-of feature materialization."""

from ncaa_quant.features.builder import FeatureBuilder, FeatureBuildError
from ncaa_quant.features.epa import (
    CONNELLY_MARGIN_BY_PERIOD,
    DEFAULT_WEIGHTING,
    SUCCESS_FRAC_BY_DOWN,
    UniformWeighting,
    aggregate_efficiency,
    apply_garbage_time,
    filter_garbage_time,
    garbage_time_summary,
    is_successful_play,
    load_season_plays_from_cfbd_raw,
    normalize_epa_plays,
)
from ncaa_quant.features.materialize import (
    MaterializeError,
    MaterializeResult,
    PartitionRef,
    duckdb_asof_join,
    dvc_add_partition,
    materialize_partition,
    materialize_registry,
    read_partition,
)
from ncaa_quant.features.pit_audit import (
    PitAuditError,
    PitAuditResult,
    assert_partition_pit_clean,
    audit_partition,
)
from ncaa_quant.features.registry import (
    DependencyCycleError,
    FeatureRegistry,
    FeatureSpec,
    RegistryError,
    load_registry,
    resolve_build_order,
)

__all__ = [
    "CONNELLY_MARGIN_BY_PERIOD",
    "DEFAULT_WEIGHTING",
    "SUCCESS_FRAC_BY_DOWN",
    "DependencyCycleError",
    "FeatureBuildError",
    "FeatureBuilder",
    "FeatureRegistry",
    "FeatureSpec",
    "MaterializeError",
    "MaterializeResult",
    "PartitionRef",
    "PitAuditError",
    "PitAuditResult",
    "RegistryError",
    "UniformWeighting",
    "aggregate_efficiency",
    "apply_garbage_time",
    "assert_partition_pit_clean",
    "audit_partition",
    "dvc_add_partition",
    "duckdb_asof_join",
    "filter_garbage_time",
    "garbage_time_summary",
    "is_successful_play",
    "load_registry",
    "load_season_plays_from_cfbd_raw",
    "materialize_partition",
    "materialize_registry",
    "normalize_epa_plays",
    "read_partition",
    "resolve_build_order",
]
