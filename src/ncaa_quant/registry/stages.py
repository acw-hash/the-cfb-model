"""Model registry stages (DESIGN §10).

Lifecycle: ``candidate → challenger → champion → archived``.
"""

from __future__ import annotations

from enum import StrEnum


class ModelStage(StrEnum):
    """Immutable stage labels used by the local registry index and MLflow tags."""

    CANDIDATE = "candidate"
    CHALLENGER = "challenger"
    CHAMPION = "champion"
    ARCHIVED = "archived"


# Forward promotion path (not including archive, which is a terminal side-exit).
PROMOTION_PATH: tuple[ModelStage, ...] = (
    ModelStage.CANDIDATE,
    ModelStage.CHALLENGER,
    ModelStage.CHAMPION,
)

ALLOWED_TRANSITIONS: dict[ModelStage, frozenset[ModelStage]] = {
    ModelStage.CANDIDATE: frozenset({ModelStage.CHALLENGER, ModelStage.ARCHIVED}),
    ModelStage.CHALLENGER: frozenset({ModelStage.CHAMPION, ModelStage.ARCHIVED}),
    ModelStage.CHAMPION: frozenset({ModelStage.ARCHIVED}),
    ModelStage.ARCHIVED: frozenset(),
}


def assert_transition_allowed(current: ModelStage, target: ModelStage) -> None:
    """Raise ``ValueError`` if ``current → target`` is not a legal stage move."""
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        msg = f"illegal stage transition: {current.value} → {target.value}"
        raise ValueError(msg)
