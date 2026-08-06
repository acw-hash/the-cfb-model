"""Global RNG seeding and seed manifests for reproducible runs.

All randomness in the system must flow through :func:`set_global_seed` so that
every seed used in a run is recorded in a :class:`SeedManifest` suitable for
MLflow logging.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

# Env vars consulted by training code / native libs for deterministic GBDT fits.
_LGBM_SEED_ENV = "LIGHTGBM_RANDOM_SEED"
_XGB_SEED_ENV = "XGBOOST_RANDOM_SEED"
_HASH_SEED_ENV = "PYTHONHASHSEED"


@dataclass
class SeedManifest:
    """Record of every seed used in a run (JSON-serializable for MLflow)."""

    global_seed: int
    python_hash_seed: str
    lightgbm_seed: int
    xgboost_seed: int
    numpy_seed: int
    extra: dict[str, int] = field(default_factory=dict)

    def record(self, name: str, seed: int) -> None:
        """Attach an additional named seed (e.g. Optuna trial seed)."""
        self.extra[name] = seed

    def to_dict(self) -> dict[str, Any]:
        """Plain dict suitable for ``mlflow.log_dict`` / JSON artifacts."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize the manifest to a JSON string."""
        return json.dumps(self.to_dict(), sort_keys=True)


def set_global_seed(seed: int) -> SeedManifest:
    """Seed Python ``random``, NumPy, and GBDT-related environment variables.

    Sets ``PYTHONHASHSEED``, ``LIGHTGBM_RANDOM_SEED``, and
    ``XGBOOST_RANDOM_SEED`` so subsequent LightGBM/XGBoost fits can read a
    deterministic seed from the environment. Returns a :class:`SeedManifest`
    capturing every seed applied.
    """
    if seed < 0:
        msg = f"seed must be non-negative, got {seed}"
        raise ValueError(msg)

    seed_str = str(seed)
    os.environ[_HASH_SEED_ENV] = seed_str
    os.environ[_LGBM_SEED_ENV] = seed_str
    os.environ[_XGB_SEED_ENV] = seed_str

    random.seed(seed)
    np.random.seed(seed)

    return SeedManifest(
        global_seed=seed,
        python_hash_seed=seed_str,
        lightgbm_seed=seed,
        xgboost_seed=seed,
        numpy_seed=seed,
    )
