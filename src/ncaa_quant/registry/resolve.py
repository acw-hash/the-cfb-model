"""Inference-time champion resolution (DESIGN §10).

Inference **always** resolves the ``champion`` stage at runtime — never a
hardcoded version id. Missing champion fails loudly via
:class:`NoChampionError`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ncaa_quant.registry.manifest import RunManifest
from ncaa_quant.registry.store import (
    ModelRegistry,
    ModelVersionRecord,
    NoChampionError,
    RegistryError,
)


def resolve_champion(registry: ModelRegistry) -> ModelVersionRecord:
    """Return the pinned champion record (raises :class:`NoChampionError`)."""
    return registry.resolve_champion()


def load_champion_predictions(registry: ModelRegistry) -> bytes:
    """Load golden prediction bytes for the current champion."""
    return registry.load_champion_predictions()


def load_champion_manifest(registry: ModelRegistry) -> RunManifest:
    """Load the run manifest bound to the current champion."""
    champ = registry.resolve_champion()
    return registry.load_manifest(champ.version)


def load_champion_feature_signature(registry: ModelRegistry) -> dict[str, Any] | None:
    """Return the champion feature-signature contract, if present.

    Inference callers should refuse mismatched schemas (§8.8).
    """
    champ = registry.resolve_champion()
    return champ.feature_signature


def open_registry(
    root: Path | str,
    *,
    tracking_uri: str | None = None,
    model_name: str = "ncaa-quant",
) -> ModelRegistry:
    """Construct a :class:`ModelRegistry` for inference or CLI use."""
    return ModelRegistry(root, model_name=model_name, tracking_uri=tracking_uri)


__all__ = [
    "NoChampionError",
    "RegistryError",
    "load_champion_feature_signature",
    "load_champion_manifest",
    "load_champion_predictions",
    "open_registry",
    "resolve_champion",
]
