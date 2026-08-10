"""Fail-loud guards for inert ablations and missing registered inputs.

Phase 2 standing rule: an ablation or feature that changes nothing must
**error**, not pass. Silent no-ops (A1/A5 with degenerate inputs, registered-
but-unmaterialized features, skipped roster/returning on schema failure while
still publishing numbers) are forbidden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

# Prior-family tables that Task 23 historically skipped on schema errors while
# still publishing headline numbers. Absence for a requested season is fatal.
PRIOR_FAMILY_TABLES: tuple[str, ...] = (
    "rosters",
    "returning_production",
    "recruiting",
    "talent",
)

# Features known to be registered and required for the §5.2 σ checklist / tempo
# path — missing partitions must not be silently NaN-filled.
REQUIRED_MATERIALIZED_FEATURES: tuple[str, ...] = ("expected_possessions",)


class InertComponentError(RuntimeError):
    """Raised when a switch, feature, or prior family cannot change outcomes."""


def assert_feature_materialized(
    feature_name: str,
    feature_store_root: Path | str,
    *,
    registry_has_name: bool = True,
) -> None:
    """Refuse registered-but-unmaterialized features (expected_possessions case).

    Parameters
    ----------
    feature_name:
        Registry name (e.g. ``expected_possessions``).
    feature_store_root:
        Root of ``data/features`` (or a test temp root).
    registry_has_name:
        When False, the guard is a no-op (feature not claimed).
    """
    if not registry_has_name:
        return
    root = Path(feature_store_root)
    named_dirs = [
        root / feature_name,
        root / "tempo" / feature_name,
    ]
    found: list[Path] = []
    for named in named_dirs:
        if named.is_dir():
            found.extend(p for p in named.rglob("*") if p.is_file())
        elif named.is_file():
            found.append(named)
    # Also accept any file whose path contains the feature name.
    if not found and root.is_dir():
        found.extend(p for p in root.rglob(f"*{feature_name}*") if p.is_file())
    if found:
        return
    raise InertComponentError(
        f"feature {feature_name!r} is registered but not materialized under "
        f"{root}; refusing silent NaN-fill / inert σ input. Materialize the "
        "partition or remove it from the claimed feature set."
    )


def assert_required_features_materialized(
    feature_store_root: Path | str,
    *,
    required: Sequence[str] = REQUIRED_MATERIALIZED_FEATURES,
    registry_names: Sequence[str] | None = None,
) -> None:
    """Apply :func:`assert_feature_materialized` to every required name."""
    claimed = set(registry_names) if registry_names is not None else set(required)
    for name in required:
        assert_feature_materialized(
            name,
            feature_store_root,
            registry_has_name=name in claimed,
        )


def assert_prior_family_staged(
    seasons: Sequence[int],
    *,
    staged_root: Path | str,
    tables: Sequence[str] = PRIOR_FAMILY_TABLES,
    allow_empty_portal_pre_2021: bool = True,
) -> None:
    """Fail when a walk-forward season lacks staged prior-family partitions.

    ``talent`` may be empty for 2014 (CFBD has no talent that year) — an empty
    partition file is acceptable; a **missing** partition directory is not.
    ``portal`` is era-gated (2021+) and checked separately when present in
    ``tables``.
    """
    root = Path(staged_root)
    missing: list[str] = []
    for season in seasons:
        for table in tables:
            if table == "portal" and allow_empty_portal_pre_2021 and int(season) < 2021:
                continue
            part = root / table / f"season={int(season)}"
            if not part.exists():
                missing.append(f"{table}/season={int(season)}")
                continue
            files = list(part.rglob("*.parquet"))
            if not files:
                # talent 2014 is a known empty-source year; empty parquet ok.
                if table == "talent" and int(season) == 2014:
                    continue
                missing.append(f"{table}/season={int(season)} (no parquet)")
    if missing:
        raise InertComponentError(
            "prior-family partitions missing for requested seasons — refusing "
            "to publish numbers from a system that silently skipped roster/"
            f"returning/recruiting on schema errors: {missing}"
        )


def assert_sigma_feature_checklist_live(
    feature_columns: Sequence[str],
    *,
    feature_store_root: Path | str | None = None,
    required_materialized: Sequence[str] = REQUIRED_MATERIALIZED_FEATURES,
) -> Mapping[str, Any]:
    """Fail when §5.2 σ checklist features are claimed but inert / absent.

    Returns the present/absent audit dict when all required materialized
    features exist on disk (when ``feature_store_root`` is given) and every
    required name appears in ``feature_columns``.
    """
    from ncaa_quant.evaluation.d5_eval import SIGMA_FEATURE_SPEC, audit_sigma_feature_set

    audit = audit_sigma_feature_set(feature_columns)
    if feature_store_root is not None:
        assert_required_features_materialized(
            feature_store_root,
            required=required_materialized,
            registry_names=list(SIGMA_FEATURE_SPEC),
        )
    absent_required = [n for n in required_materialized if n in audit["absent"]]
    if absent_required:
        raise InertComponentError(
            "σ feature checklist missing required columns "
            f"{absent_required}; refusing inert NaN-filled σ path. "
            f"absent={audit['absent']}"
        )
    return audit


def staged_table_has_rows(staged_root: Path | str, table: str, season: int) -> bool:
    """Return True when a season partition exists and has at least one row."""
    part = Path(staged_root) / table / f"season={int(season)}"
    files = list(part.rglob("*.parquet")) if part.exists() else []
    if not files:
        return False
    for path in files:
        try:
            frame = pd.read_parquet(path)
        except Exception:  # noqa: BLE001 — treat unreadable as empty
            continue
        if not frame.empty:
            return True
    return False
