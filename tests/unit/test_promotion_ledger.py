"""Promotion ledger + Bonferroni α (audit A-11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ncaa_quant.registry.promote import PROMOTION_ALPHA, promote
from ncaa_quant.registry.promotion_ledger import (
    LedgerError,
    PromotionLedger,
    bonferroni_alpha,
)
from ncaa_quant.registry.stages import ModelStage
from tests.unit.test_registry import _metric_bundle, _register, _registry


def test_bonferroni_is_alpha_over_k() -> None:
    assert bonferroni_alpha(0.10, 1) == pytest.approx(0.10)
    assert bonferroni_alpha(0.10, 2) == pytest.approx(0.05)
    assert bonferroni_alpha(0.10, 5) == pytest.approx(0.02)


def test_bonferroni_rejects_bad_inputs() -> None:
    with pytest.raises(LedgerError):
        bonferroni_alpha(0.0, 1)
    with pytest.raises(LedgerError):
        bonferroni_alpha(0.10, 0)


def test_ledger_increments_and_records(tmp_path: Path) -> None:
    ledger = PromotionLedger(tmp_path)
    assert ledger.next_attempt_index(year=2026) == 1
    idx, alpha = ledger.planned_alpha(base_alpha=0.10, year=2026)
    assert idx == 1 and alpha == pytest.approx(0.10)

    ledger.record(
        candidate_version=1,
        champion_version=None,
        alpha_base=0.10,
        alpha_adjusted=0.10,
        attempt_index=1,
        passed=True,
        year=2026,
    )
    assert ledger.attempts_this_year(year=2026) == 1
    idx2, alpha2 = ledger.planned_alpha(base_alpha=0.10, year=2026)
    assert idx2 == 2 and alpha2 == pytest.approx(0.05)


def test_ledger_refuses_mismatched_adjusted_alpha(tmp_path: Path) -> None:
    ledger = PromotionLedger(tmp_path)
    with pytest.raises(LedgerError, match="Bonferroni"):
        ledger.record(
            candidate_version=1,
            champion_version=None,
            alpha_base=0.10,
            alpha_adjusted=0.09,  # wrong
            attempt_index=1,
            passed=True,
            year=2026,
        )


def test_promote_tightens_alpha_across_attempts(tmp_path: Path) -> None:
    """Second look this year must use α₀/2, and both attempts land in the ledger."""
    registry = _registry(tmp_path)
    v1 = _register(registry, predictions=b"c1", run_id="c1")
    r1 = promote(
        registry,
        v1,
        _metric_bundle(candidate_better=True, seed=20),
        seasons=[2023],
        calibration_slope=1.0,
        leakage_gate_passed=True,
        n_boot=299,
        seed=0,
    )
    assert r1.promoted
    assert r1.report.attempt_index == 1
    assert r1.report.alpha == pytest.approx(PROMOTION_ALPHA)
    assert r1.report.alpha_base == pytest.approx(PROMOTION_ALPHA)

    v2 = _register(registry, predictions=b"c2", run_id="c2")
    r2 = promote(
        registry,
        v2,
        _metric_bundle(candidate_better=True, seed=21),
        seasons=[2023],
        calibration_slope=1.0,
        leakage_gate_passed=True,
        n_boot=299,
        seed=1,
    )
    assert r2.promoted
    assert r2.report.attempt_index == 2
    assert r2.report.prior_attempts_this_year == 1
    assert r2.report.alpha == pytest.approx(PROMOTION_ALPHA / 2)
    assert "Bonferroni" in r2.report.reason
    assert "attempt=2" in r2.report.to_html()

    ledger = PromotionLedger(registry.root)
    entries = ledger.entries()
    assert len(entries) == 2
    assert [e.attempt_index for e in entries] == [1, 2]
    assert registry.resolve_champion().version == v2
    assert registry.get_version(v1).stage_enum is ModelStage.ARCHIVED


def test_p_between_base_and_adjusted_alpha_is_blocked(tmp_path: Path) -> None:
    """A look with p=0.06 passes at α₀=0.10 but fails at α₀/2=0.05.

    Pins the bootstrap p-value so the test is about the multiplicity rule,
    not about whether a particular synthetic edge happened to clear a threshold.
    """
    from unittest.mock import patch

    from ncaa_quant.registry.promote import evaluate_gate

    metrics = _metric_bundle(candidate_better=True, seed=40)
    # Advantage positive, p=0.06 — between 0.05 and 0.10.
    fixed = (8.0, 7.0, 1.0, 0.06)

    with patch("ncaa_quant.registry.promote.paired_block_pvalue", return_value=fixed):
        at_base = evaluate_gate(
            metrics,
            seasons=[2023],
            calibration_slope=1.0,
            leakage_gate_passed=True,
            candidate_version=2,
            champion_version=1,
            alpha=0.10,
            alpha_base=0.10,
            attempt_index=1,
            n_boot=99,
        )
        at_adjusted = evaluate_gate(
            metrics,
            seasons=[2023],
            calibration_slope=1.0,
            leakage_gate_passed=True,
            candidate_version=2,
            champion_version=1,
            alpha=0.05,
            alpha_base=0.10,
            attempt_index=2,
            prior_attempts_this_year=1,
            n_boot=99,
        )

    assert at_base.passed is True
    assert all(r.beats_incumbent for r in at_base.metric_results)
    assert at_adjusted.passed is False
    assert all(not r.beats_incumbent for r in at_adjusted.metric_results)
    assert "Bonferroni" in at_adjusted.reason
    del tmp_path
