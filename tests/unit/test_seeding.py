"""Tests for seed manifest and global seeding."""

from __future__ import annotations

import json
import os
import random

import numpy as np

from ncaa_quant.utils.seeding import SeedManifest, set_global_seed


def test_set_global_seed_reproducible() -> None:
    set_global_seed(123)
    py_a = [random.random() for _ in range(5)]
    np_a = np.random.rand(5).tolist()

    set_global_seed(123)
    py_b = [random.random() for _ in range(5)]
    np_b = np.random.rand(5).tolist()

    assert py_a == py_b
    assert np_a == np_b


def test_seed_manifest_json_roundtrip() -> None:
    manifest = set_global_seed(7)
    assert isinstance(manifest, SeedManifest)
    assert manifest.global_seed == 7
    assert os.environ["PYTHONHASHSEED"] == "7"
    assert os.environ["LIGHTGBM_RANDOM_SEED"] == "7"
    assert os.environ["XGBOOST_RANDOM_SEED"] == "7"

    manifest.record("optuna_trial_0", 700)
    payload = json.loads(manifest.to_json())
    assert payload["global_seed"] == 7
    assert payload["extra"]["optuna_trial_0"] == 700
    assert payload == manifest.to_dict()


def test_different_seeds_diverge() -> None:
    set_global_seed(1)
    a = random.random()
    set_global_seed(2)
    b = random.random()
    assert a != b
