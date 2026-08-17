"""W9-A: isolated v3 YAML, lockbox, week-5 causal check, tree hash."""

from __future__ import annotations

from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]
import pytest

from ncaa_quant.evaluation.backtest_runner import load_backtest_config
from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON
from ncaa_quant.pipelines.gates import evaluate_promotion_gate
from ncaa_quant.registry.w9a_revalidate import (
    A2_CONFIG_NAME,
    ADR_0014_OOD_BLOCKS,
    CHAMPION3_ROOT,
    FORBIDDEN_RUN_ID,
    FUND_CONFIG_NAME,
    FUND_MODEL_VERSION,
    FUND_RUN_ID,
    W9AStop,
    assert_lockbox_absent_from_yaml,
    assert_quality_gate_attributable,
    hash_tree,
    n_season,
    ungradable_blocks,
    week5_crosscheck,
    yaml_season_lists,
)


def test_fundamental_v3_yaml_isolated_and_lockbox_free() -> None:
    payload = load_backtest_config(FUND_CONFIG_NAME)
    assert payload["run_id"] == FUND_RUN_ID
    assert payload["run_id"] != FORBIDDEN_RUN_ID
    assert payload["walkforward"]["run_id"] == FUND_RUN_ID
    assert payload["walkforward"]["model_version"] == FUND_MODEL_VERSION
    assert payload["walkforward"]["test_seasons"] == [2019, 2021, 2022, 2023, 2024]
    assert payload["walkforward"]["continuity_seasons"] == [2020]
    seasons = yaml_season_lists(payload)
    for vals in seasons.values():
        assert LOCKBOX_SEASON not in vals
        assert 2025 not in vals
    assert_lockbox_absent_from_yaml(payload, name=FUND_CONFIG_NAME)
    output = Path("data/backtests") / payload["run_id"] / payload["ablation_id"]
    assert CHAMPION3_ROOT.resolve() not in output.resolve().parents
    assert output.resolve() != CHAMPION3_ROOT.resolve()


def test_a2_v2_yaml_isolated_and_lockbox_free() -> None:
    payload = load_backtest_config(A2_CONFIG_NAME)
    assert payload["run_id"] == "task23_a2_reduced_v2"
    assert payload["run_id"] != FORBIDDEN_RUN_ID
    assert payload["walkforward"]["rating_updates"] == "frozen_after_week_1"
    assert payload["walkforward"]["test_seasons"] == [2019, 2021, 2022, 2023, 2024]
    assert payload["walkforward"]["continuity_seasons"] == [2020]
    assert_lockbox_absent_from_yaml(payload, name=A2_CONFIG_NAME)


def test_lockbox_yaml_guard_raises() -> None:
    with pytest.raises(W9AStop, match="lockbox"):
        assert_lockbox_absent_from_yaml(
            {"walkforward": {"test_seasons": [2019, 2025]}},
            name="poison",
        )


def test_week5_crosscheck_zero_and_nonzero() -> None:
    old = pd.DataFrame(
        {
            "game_id": [1, 2, 3],
            "pred_margin": [1.0, -2.5, 0.0],
            "sigma_m": [12.0, 11.0, 10.0],
            "p_ml_home": [0.6, 0.4, 0.5],
        }
    )
    same = old.copy()
    report = week5_crosscheck(same, old)
    assert report["all_zero"] is True
    assert report["n"] == 3
    assert report["fields"]["mu_margin"]["max_abs"] == 0.0
    assert report["fields"]["sigma_margin"]["max_abs"] == 0.0
    assert report["fields"]["p_ml_home"]["max_abs"] == 0.0

    shifted = old.copy()
    shifted.loc[0, "pred_margin"] = 1.5
    bad = week5_crosscheck(shifted, old)
    assert bad["all_zero"] is False
    assert bad["fields"]["mu_margin"]["max_abs"] == 0.5


def test_ungradable_only_adr_0014_blocks() -> None:
    preds = pd.DataFrame(
        {
            "season": [2019, 2019, 2019, 2024],
            "week": [2, 3, 4, 5],
            "null_reason": ["ood", "ood", "ood", None],
        }
    )
    blocks = ungradable_blocks(preds)
    assert blocks == ADR_0014_OOD_BLOCKS
    assert_quality_gate_attributable({"n_null_mu": 0, "n_ungradable": 3}, preds)
    poison = preds.copy()
    poison.loc[3, "null_reason"] = "unexpected"
    with pytest.raises(W9AStop, match="outside ADR 0014"):
        assert_quality_gate_attributable({"n_null_mu": 0, "n_ungradable": 4}, poison)
    with pytest.raises(W9AStop, match="n_null_mu"):
        assert_quality_gate_attributable({"n_null_mu": 1, "n_ungradable": 0}, preds)


def test_n_season_zero_for_lockbox() -> None:
    frame = pd.DataFrame({"season": [2019, 2024], "x": [1, 2]})
    assert n_season(frame, 2025) == 0
    assert n_season(frame, 2024) == 1


def test_hash_tree_stable(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / "a").mkdir(parents=True)
    (root / "a" / "f.txt").write_text("hello\n", encoding="utf-8")
    (root / "b.bin").write_bytes(b"\x00\x01")
    first = hash_tree(root)
    second = hash_tree(root)
    assert first == second
    assert len(first) == 64
    (root / "a" / "f.txt").write_text("hello\n!", encoding="utf-8")
    assert hash_tree(root) != first
    assert hash_tree(tmp_path / "missing") == "ABSENT"


def test_promotion_force_false_still_required() -> None:
    blocked = evaluate_promotion_gate(
        candidate_version="2",
        gate_passed=True,
        manual_approve=False,
        force=False,
    )
    assert blocked.approved is False
    assert blocked.force is False
    ok = evaluate_promotion_gate(
        candidate_version="2",
        gate_passed=True,
        manual_approve=True,
        force=False,
    )
    assert ok.approved is True
    assert ok.force is False
    assert ok.reason == "gate passed and manually approved"
