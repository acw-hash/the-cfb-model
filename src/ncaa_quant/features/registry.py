"""Feature registry YAML schema, loader, and dependency resolution.

DESIGN §4.1 / §4.7 / §15 item 9. A feature with an empty ``hypothesis`` is a
registry validation error — the primary defense against p-hacking.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from omegaconf import OmegaConf

NullPolicy = Literal["forbid", "allow", "indicator"]

_VALID_NULL_POLICIES: frozenset[str] = frozenset({"forbid", "allow", "indicator"})
_DEFAULT_REGISTRY_PATH = Path(__file__).with_name("registry.yaml")


class RegistryError(ValueError):
    """Invalid feature registry contents or structure."""


class DependencyCycleError(RegistryError):
    """Feature dependency graph contains a cycle."""


@dataclass(frozen=True)
class FeatureSpec:
    """One registered feature (production card fields from DESIGN §4.1)."""

    name: str
    version: str
    dtype: str
    builder: str
    dependencies: tuple[str, ...]
    as_of_semantics: str
    null_policy: NullPolicy
    lookback_window: str
    hypothesis: str

    def spec_hash(self) -> str:
        """Stable content hash of the registry entry (change detection)."""
        payload = {
            "name": self.name,
            "version": self.version,
            "dtype": self.dtype,
            "builder": self.builder,
            "dependencies": list(self.dependencies),
            "as_of_semantics": self.as_of_semantics,
            "null_policy": self.null_policy,
            "lookback_window": self.lookback_window,
            "hypothesis": self.hypothesis,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class FeatureRegistry:
    """Loaded registry: specs keyed by feature name."""

    specs: dict[str, FeatureSpec]
    source_path: Path | None = None

    def get(self, name: str) -> FeatureSpec:
        """Return a feature spec or raise :class:`KeyError`."""
        if name not in self.specs:
            msg = f"feature {name!r} not in registry"
            raise KeyError(msg)
        return self.specs[name]

    def names(self) -> list[str]:
        """Feature names in registry file order (dict insertion order)."""
        return list(self.specs)


def load_registry(path: Path | str | None = None) -> FeatureRegistry:
    """Load and validate ``registry.yaml``.

    Parameters
    ----------
    path:
        Registry YAML path. Defaults to the package ``registry.yaml``.
    """
    registry_path = Path(path) if path is not None else _DEFAULT_REGISTRY_PATH
    if not registry_path.is_file():
        msg = f"feature registry not found: {registry_path}"
        raise RegistryError(msg)

    raw = OmegaConf.to_container(OmegaConf.load(registry_path), resolve=True)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        msg = "registry root must be a mapping"
        raise RegistryError(msg)

    features_raw = raw.get("features", [])
    if features_raw is None:
        features_raw = []
    if not isinstance(features_raw, list):
        msg = "registry 'features' must be a list"
        raise RegistryError(msg)

    specs: dict[str, FeatureSpec] = {}
    for index, entry in enumerate(features_raw):
        if not isinstance(entry, dict):
            msg = f"features[{index}] must be a mapping"
            raise RegistryError(msg)
        spec = _parse_feature_entry(entry, index=index)
        if spec.name in specs:
            msg = f"duplicate feature name {spec.name!r}"
            raise RegistryError(msg)
        specs[spec.name] = spec

    _validate_dependency_refs(specs)
    return FeatureRegistry(specs=specs, source_path=registry_path)


def resolve_build_order(registry: FeatureRegistry) -> list[str]:
    """Topological order of feature names (dependencies before dependents).

    Raises
    ------
    DependencyCycleError
        If the feature-to-feature dependency graph has a cycle.
    """
    feature_names = set(registry.specs)
    # Edges: dep -> dependent (only among registered features).
    dependents: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = {name: 0 for name in feature_names}

    for name, spec in registry.specs.items():
        for dep in spec.dependencies:
            if _is_raw_dependency(dep):
                continue
            if dep not in feature_names:
                # Should have been caught at load; keep defensive.
                msg = f"feature {name!r} depends on unknown feature {dep!r}"
                raise RegistryError(msg)
            dependents[dep].append(name)
            indegree[name] += 1

    queue: deque[str] = deque(sorted(n for n, d in indegree.items() if d == 0))
    ordered: list[str] = []
    while queue:
        node = queue.popleft()
        ordered.append(node)
        for child in sorted(dependents[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if len(ordered) != len(feature_names):
        remaining = sorted(n for n, d in indegree.items() if d > 0)
        msg = f"feature dependency cycle involving: {remaining}"
        raise DependencyCycleError(msg)
    return ordered


def _parse_feature_entry(entry: dict[str, Any], *, index: int) -> FeatureSpec:
    required = (
        "name",
        "version",
        "dtype",
        "builder",
        "dependencies",
        "as_of_semantics",
        "null_policy",
        "lookback_window",
        "hypothesis",
    )
    missing = [key for key in required if key not in entry]
    if missing:
        msg = f"features[{index}] missing required fields: {missing}"
        raise RegistryError(msg)

    name = _require_nonempty_str(entry["name"], field="name", index=index)
    version = _require_nonempty_str(entry["version"], field="version", index=index)
    dtype = _require_nonempty_str(entry["dtype"], field="dtype", index=index)
    builder = _require_nonempty_str(entry["builder"], field="builder", index=index)
    as_of_semantics = _require_nonempty_str(
        entry["as_of_semantics"], field="as_of_semantics", index=index
    )
    lookback_window = _require_nonempty_str(
        entry["lookback_window"], field="lookback_window", index=index
    )
    hypothesis = entry["hypothesis"]
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        msg = (
            f"features[{index}] ({name!r}): empty hypothesis is a registry "
            "validation error (DESIGN §4.1)"
        )
        raise RegistryError(msg)

    null_policy_raw = entry["null_policy"]
    if not isinstance(null_policy_raw, str) or null_policy_raw not in _VALID_NULL_POLICIES:
        msg = (
            f"features[{index}] ({name!r}): null_policy must be one of "
            f"{sorted(_VALID_NULL_POLICIES)}, got {null_policy_raw!r}"
        )
        raise RegistryError(msg)
    null_policy: NullPolicy = null_policy_raw  # type: ignore[assignment]

    deps_raw = entry["dependencies"]
    if deps_raw is None:
        deps_raw = []
    if not isinstance(deps_raw, list):
        msg = f"features[{index}] ({name!r}): dependencies must be a list"
        raise RegistryError(msg)
    dependencies: list[str] = []
    for dep in deps_raw:
        if not isinstance(dep, str) or not dep.strip():
            msg = f"features[{index}] ({name!r}): dependency entries must be non-empty strings"
            raise RegistryError(msg)
        dependencies.append(dep.strip())

    return FeatureSpec(
        name=name,
        version=str(version),
        dtype=dtype,
        builder=builder,
        dependencies=tuple(dependencies),
        as_of_semantics=as_of_semantics,
        null_policy=null_policy,
        lookback_window=lookback_window,
        hypothesis=hypothesis.strip(),
    )


def _require_nonempty_str(value: Any, *, field: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"features[{index}]: {field} must be a non-empty string"
        raise RegistryError(msg)
    return value.strip()


def _is_raw_dependency(dep: str) -> bool:
    return dep.startswith("raw:")


def _validate_dependency_refs(specs: dict[str, FeatureSpec]) -> None:
    known = set(specs)
    for name, spec in specs.items():
        for dep in spec.dependencies:
            if _is_raw_dependency(dep):
                table = dep.removeprefix("raw:").strip()
                if not table:
                    msg = f"feature {name!r}: empty raw dependency"
                    raise RegistryError(msg)
                continue
            if dep not in known:
                msg = f"feature {name!r} depends on unknown feature {dep!r}"
                raise RegistryError(msg)
