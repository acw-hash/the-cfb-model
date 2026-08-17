"""Serialize / load a fitted :class:`ProductionEnsemblePredictor` for inference.

The walk-forward harness never pickled the mapping layer. This bundle is the
registry artifact ``predict()`` needs: fitted heads, NNLS, σ/CQR/PIT state,
key-number kernel, ADR 0014 member status, and the config/seed used at fit.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

from ncaa_quant.evaluation.production_stack import ProductionEnsemblePredictor

BUNDLE_FORMAT_VERSION: int = 1
ENSEMBLE_FILENAME: str = "production_ensemble.pkl"
FEATURES_FILENAME: str = "predict_features.parquet"
RATING_SNAPSHOT_FILENAME: str = "rating_snapshot.json"
POSSESSIONS_FILENAME: str = "possessions_artifacts.pkl"
INVENTORY_FILENAME: str = "state_inventory.json"
WEEK_PREDICTIONS_FILENAME: str = "week_predictions.parquet"
PROMOTION_FILENAME: str = "promotion_gate.json"
FIT_PROCESS_FILENAME: str = "last_fit_process.json"


class BundleError(RuntimeError):
    """Raised when a champion bundle is missing or unreadable."""


def save_production_ensemble(predictor: ProductionEnsemblePredictor, path: Path | str) -> Path:
    """Pickle a fitted production ensemble. Returns the written path.

    Units: the file is a Python pickle (protocol HIGHEST). Time semantics: this
    captures mapping-layer state at the last ``fit()``, not rating history.
    """
    if not predictor.is_fitted:
        raise BundleError("cannot serialize an unfitted ProductionEnsemblePredictor")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "format_version": BUNDLE_FORMAT_VERSION,
        "class_name": type(predictor).__name__,
        "predictor": predictor,
    }
    with out.open("wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return out


def load_production_ensemble(path: Path | str) -> ProductionEnsemblePredictor:
    """Load a bundle written by :func:`save_production_ensemble`."""
    target = Path(path)
    if not target.is_file():
        raise BundleError(f"ensemble bundle missing: {target}")
    with target.open("rb") as fh:
        payload = pickle.load(fh)  # noqa: S301 — registry artifact we wrote
    if not isinstance(payload, dict):
        raise BundleError("ensemble bundle root must be a mapping")
    version = int(payload.get("format_version", -1))
    if version != BUNDLE_FORMAT_VERSION:
        raise BundleError(f"unsupported ensemble bundle format_version={version}")
    predictor = payload.get("predictor")
    if not isinstance(predictor, ProductionEnsemblePredictor):
        raise BundleError(
            f"bundle predictor is {type(predictor).__name__}, expected ProductionEnsemblePredictor"
        )
    if not predictor.is_fitted:
        raise BundleError("loaded ensemble is not fitted")
    return predictor
