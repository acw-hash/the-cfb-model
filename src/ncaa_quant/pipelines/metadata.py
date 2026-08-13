"""Raw-archive metadata hygiene (DESIGN §10 API-key scrub verification)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Patterns that must never appear in archived response bodies.
_FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"apiKey", re.IGNORECASE),
    re.compile(r"api_key", re.IGNORECASE),
    re.compile(r"Authorization\s*:", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class ScrubViolation:
    """One file that failed the scrub check."""

    path: str
    pattern: str


def verify_raw_archive_scrub(
    raw_root: Path | str,
    *,
    extra_secrets: tuple[str, ...] = (),
) -> list[ScrubViolation]:
    """Scan ``raw_root`` for forbidden API-key patterns in file contents.

    Returns an empty list when clean. Used by tests and the weekly integrity
    check to enforce DESIGN §10.
    """
    root = Path(raw_root)
    if not root.is_dir():
        return []
    violations: list[ScrubViolation] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in _FORBIDDEN_PATTERNS:
            if pattern.search(text):
                violations.append(
                    ScrubViolation(
                        path=str(path.relative_to(root)),
                        pattern=pattern.pattern,
                    )
                )
                break
        for secret in extra_secrets:
            if secret and secret in text:
                violations.append(
                    ScrubViolation(path=str(path.relative_to(root)), pattern="literal_secret")
                )
                break
    return violations


def assert_raw_archive_scrubbed(
    raw_root: Path | str,
    *,
    extra_secrets: tuple[str, ...] = (),
) -> None:
    """Raise when any scrub violation is found."""
    violations = verify_raw_archive_scrub(raw_root, extra_secrets=extra_secrets)
    if violations:
        sample = violations[:5]
        detail = "; ".join(f"{v.path} ({v.pattern})" for v in sample)
        msg = f"raw archive scrub check failed: {detail}"
        raise ValueError(msg)
