"""Phase 2 fail-loud inert-component guards."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ncaa_quant.evaluation.inert import (
    InertComponentError,
    assert_feature_materialized,
    assert_prior_family_staged,
    assert_required_features_materialized,
    assert_sigma_feature_checklist_live,
)


def test_assert_feature_materialized_errors_when_missing(tmp_path: Path) -> None:
    with pytest.raises(InertComponentError, match="expected_possessions"):
        assert_feature_materialized("expected_possessions", tmp_path)


def test_assert_feature_materialized_ok_when_present(tmp_path: Path) -> None:
    part = tmp_path / "expected_possessions" / "season=2023"
    part.mkdir(parents=True)
    (part / "part.parquet").write_bytes(b"ok")
    assert_feature_materialized("expected_possessions", tmp_path)


def test_assert_required_features_respects_registry_claim(tmp_path: Path) -> None:
    # Not claimed → no-op.
    assert_required_features_materialized(
        tmp_path,
        required=("expected_possessions",),
        registry_names=("week",),
    )
    with pytest.raises(InertComponentError):
        assert_required_features_materialized(
            tmp_path,
            required=("expected_possessions",),
            registry_names=("expected_possessions",),
        )


def test_assert_prior_family_staged_errors_when_missing(tmp_path: Path) -> None:
    with pytest.raises(InertComponentError, match="prior-family"):
        assert_prior_family_staged([2023], staged_root=tmp_path)


def test_assert_prior_family_staged_ok(tmp_path: Path) -> None:
    for table in ("rosters", "returning_production", "recruiting", "talent"):
        part = tmp_path / table / "season=2023"
        part.mkdir(parents=True)
        pd.DataFrame({"x": [1]}).to_parquet(part / "part.parquet")
    assert_prior_family_staged([2023], staged_root=tmp_path)


def test_assert_sigma_feature_checklist_live_errors() -> None:
    with pytest.raises(InertComponentError, match="expected_possessions"):
        assert_sigma_feature_checklist_live(
            ["week", "rating_diff_magnitude"],
            feature_store_root=None,
        )
