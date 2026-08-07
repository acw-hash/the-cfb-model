"""Code-level lockbox enforcement (DESIGN §7.2 item 9, audit A-11).

A documented convention is not enough: the promotion gate re-tests candidates
against the same seasons at p < 0.10 over the project's life, so evaluation-set
reuse accumulates whether or not anyone intends it. These tests pin that a
development run cannot reach 2025 by accident.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ncaa_quant.evaluation.lockbox import (
    HPO_TIEBREAK_SEASON,
    LOCKBOX_SEASON,
    LockboxViolation,
    assert_lockbox_excluded,
    assert_tiebreak_differs_from_lockbox,
    contains_lockbox,
    lockbox_free,
)
from ncaa_quant.evaluation.walkforward import (
    DEFAULT_TEST_SEASONS,
    HISTORICAL_CANONICAL_SEASONS,
    WalkForwardConfig,
)


def test_default_test_seasons_exclude_the_lockbox() -> None:
    assert LOCKBOX_SEASON not in DEFAULT_TEST_SEASONS
    assert DEFAULT_TEST_SEASONS == (2019, 2021, 2022, 2023, 2024)


def test_historical_canonical_seasons_are_kept_separate_and_labelled() -> None:
    """The frozen D2-D7 frames did include 2025; that constant must stay distinct."""
    assert LOCKBOX_SEASON in HISTORICAL_CANONICAL_SEASONS
    assert HISTORICAL_CANONICAL_SEASONS != DEFAULT_TEST_SEASONS


def test_tiebreak_season_differs_from_lockbox() -> None:
    assert HPO_TIEBREAK_SEASON != LOCKBOX_SEASON
    assert_tiebreak_differs_from_lockbox()


def test_helpers_identify_and_strip_the_lockbox() -> None:
    assert contains_lockbox([2023, 2025]) is True
    assert contains_lockbox([2023, 2024]) is False
    assert lockbox_free([2024, 2025, 2023, 2023]) == (2023, 2024)


def test_assert_lockbox_excluded_passes_on_clean_seasons() -> None:
    assert_lockbox_excluded([2019, 2021, 2022], context="unit test")


def test_assert_lockbox_excluded_raises_with_an_actionable_message() -> None:
    with pytest.raises(LockboxViolation) as exc:
        assert_lockbox_excluded([2023, 2025], context="walk-forward run abc/full")

    message = str(exc.value)
    assert "abc/full" in message
    assert "2025" in message
    assert "(2023,)" in message
    assert "docs/lockbox_access.md" in message


def test_confirmatory_read_is_permitted_explicitly() -> None:
    assert_lockbox_excluded([2025], context="annual confirmatory read", confirmatory_read=True)


def test_walkforward_config_refuses_the_lockbox_in_any_season_role() -> None:
    """Test, continuity and warm-up seasons all read data, so all are guarded."""
    for field in ("test_seasons", "continuity_seasons", "warmup_seasons"):
        cfg = WalkForwardConfig(**{field: (LOCKBOX_SEASON,)})  # type: ignore[arg-type]
        with pytest.raises(LockboxViolation, match="lockbox season 2025"):
            cfg.validate_ablations()


def test_walkforward_config_allows_a_declared_confirmatory_read() -> None:
    cfg = WalkForwardConfig(
        test_seasons=(LOCKBOX_SEASON,),
        lockbox_confirmatory_read=True,
    )
    cfg.validate_ablations()


def test_default_walkforward_config_validates() -> None:
    WalkForwardConfig().validate_ablations()


def test_encompassing_config_excludes_the_lockbox() -> None:
    """The powered encompassing test is development work, so 2025 is out.

    The odds purchase spec still includes 2025: the lockbox restricts reading the
    season, not buying its data.
    """
    payload = yaml.safe_load(Path("configs/eval/encompassing.yaml").read_text(encoding="utf-8"))

    assert LOCKBOX_SEASON not in payload["encompassing"]["seasons"]
    assert LOCKBOX_SEASON in payload["odds_purchase_spec"]["seasons"]


def test_shipped_ablation_configs_exclude_the_lockbox() -> None:
    for path in sorted(Path("configs/ablations").glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        runs = payload.get("runs") or [payload]
        for run in runs:
            wf = run.get("walkforward", run)
            for key in ("test_seasons", "continuity_seasons", "warmup_seasons"):
                seasons = wf.get(key) or []
                assert LOCKBOX_SEASON not in seasons, f"{path.name}:{key} reads the lockbox"
