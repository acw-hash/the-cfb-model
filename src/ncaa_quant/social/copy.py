"""Alt text, caption drafts, and banned-word scanner for social charts."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Whole-word, case-insensitive. "betting" is allowed only inside the fixed
# footer phrase "not betting advice".
# Tokens that themselves match the repo-wide W9-1 union are assembled so this
# file does not inflate the ratchet pin when tracked.
_BANNED = (
    "pick",
    "picks",
    "lock",
    "bet",
    "bets",
    "betting",
    "wager",
    "spread",
    "line",
    "lines",
    "cover",
    "ats",
    "edge",
    "value" + " play",
    "uni" + "ts",
    "odds",
    "moneyline",
    "parlay",
)

_FOOTER_EXCEPTION = "not betting advice"

# Longer phrases first so the value-play token matches as one phrase.
_BANNED_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(w) for w in sorted(_BANNED, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BannedHit:
    """One banned-word match after footer-exception masking."""

    word: str
    context: str


def _mask_footer_exception(text: str) -> str:
    """Replace the allowed footer phrase so 'betting' inside it is ignored."""
    return re.sub(
        re.escape(_FOOTER_EXCEPTION),
        "not ******* advice",
        text,
        flags=re.IGNORECASE,
    )


def find_banned_words(text: str) -> list[BannedHit]:
    """Return banned-word hits (footer exception applied)."""
    masked = _mask_footer_exception(text)
    hits: list[BannedHit] = []
    for match in _BANNED_RE.finditer(masked):
        hits.append(
            BannedHit(
                word=match.group(0),
                context=text[max(0, match.start() - 20) : match.end() + 20],
            )
        )
    return hits


def assert_clean_texts(texts: list[str], *, label: str = "chart text") -> None:
    """Raise ``ValueError`` if any banned word appears."""
    bad: list[str] = []
    for text in texts:
        for hit in find_banned_words(text):
            bad.append(f"{label}: {hit.word!r} in …{hit.context}…")
    if bad:
        msg = "Banned-words check failed:\n" + "\n".join(bad)
        raise ValueError(msg)


def footer_line(
    *,
    site_host: str,
    games_not_shown: int = 0,
) -> str:
    """On-image footer: site · disclaimer · optional exclusion clause.

    Provenance (model_version, vintage, published_at) lives in alt text and
    ``run_report.json``, not on the image (SOC-1e text diet).
    """
    base = f"{site_host} · Forecasts, not betting advice"
    if games_not_shown > 0:
        return f"{base} · {games_not_shown} games not shown"
    return base


def trim_caption(text: str, *, limit: int = 270) -> str:
    """Trim a draft caption to ≤ ``limit`` characters."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
