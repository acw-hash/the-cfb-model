"""TASK 23-RERUN-R1 — reduced ensemble config labels (regression only)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ncaa_quant.evaluation.backtest_runner import load_backtest_config
from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON

RUN_SET = Path("configs/ablations/task23_run_set.yaml")
REDUCED_SUFFIX = "_reduced_v1"
ENSEMBLE_SCOPE = "REDUCED_PER_ADR_0013"
MODEL_VERSION = "production-v0_reduced_v1"


def _load_run_set() -> dict:
    payload = yaml.safe_load(RUN_SET.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_run_set_is_reduced_v1_labeled() -> None:
    payload = _load_run_set()
    assert payload.get("run_set") == "task23_reduced_v1"
    assert payload.get("ensemble_scope") == ENSEMBLE_SCOPE
    runs = payload["runs"]
    assert len(runs) == 8
    for run in runs:
        run_id = str(run["run_id"])
        assert run_id.endswith(REDUCED_SUFFIX), run_id
        assert run.get("ensemble_scope") == ENSEMBLE_SCOPE
        wf = run["walkforward"]
        assert wf["model_version"] == MODEL_VERSION
        assert wf["run_id"] == run_id
        assert wf["ablation_id"] == run["ablation_id"]
        seasons = set(int(s) for s in wf["test_seasons"])
        assert LOCKBOX_SEASON not in seasons
        assert int(LOCKBOX_SEASON) not in set(int(s) for s in wf.get("continuity_seasons", ()))


def test_per_run_reduced_yaml_files_load() -> None:
    payload = _load_run_set()
    fname_by_name = {
        "fundamental_full": "task23_fundamental_full_reduced_v1",
        "market_aware_full": "task23_market_aware_full_reduced_v1",
        "A1_priors_off": "task23_A1_priors_off_reduced_v1",
        "A2_rating_updates_frozen": "task23_A2_rating_updates_frozen_reduced_v1",
        "A3_market_features_off": "task23_A3_market_features_off_reduced_v1",
        "A4_single_lgbm": "task23_A4_single_lgbm_reduced_v1",
        "A5_garbage_time_filter_off": "task23_A5_garbage_time_filter_off_reduced_v1",
        "A6_cfbd_open_close": "task23_A6_cfbd_open_close_reduced_v1",
    }
    for run in payload["runs"]:
        name = run["name"]
        stem = fname_by_name[name]
        loaded = load_backtest_config(stem)
        assert loaded["run_id"].endswith(REDUCED_SUFFIX)
        assert loaded.get("ensemble_scope") == ENSEMBLE_SCOPE
        assert loaded["walkforward"]["model_version"] == MODEL_VERSION


@pytest.mark.parametrize(
    "cfg_stem",
    [
        "task23_fundamental_full_reduced_v1",
        "task23_market_aware_full_reduced_v1",
        "task23_A1_priors_off_reduced_v1",
        "task23_A2_rating_updates_frozen_reduced_v1",
        "task23_A3_market_features_off_reduced_v1",
        "task23_A4_single_lgbm_reduced_v1",
        "task23_A5_garbage_time_filter_off_reduced_v1",
        "task23_A6_cfbd_open_close_reduced_v1",
    ],
)
def test_reduced_config_lockbox_absent(cfg_stem: str) -> None:
    loaded = load_backtest_config(cfg_stem)
    wf = loaded["walkforward"]
    all_seasons = set(int(s) for s in wf["test_seasons"]) | set(
        int(s) for s in wf.get("continuity_seasons", ())
    )
    assert LOCKBOX_SEASON not in all_seasons
