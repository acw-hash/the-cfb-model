"""Patch scripts/_s6_w10_card.py constants for week 10."""

from __future__ import annotations

from pathlib import Path

p = Path("scripts/_s6_w10_card.py")
t = p.read_text(encoding="utf-8")
repls = [
    (
        '"""S6-W9-CARD — week-9 card probe (W9-2).',
        '"""S6-W10-CARD — week-10 card probe (W10-2).',
    ),
    (
        '"""S6-W9-CARD - week-9 card probe (W9-2).',
        '"""S6-W10-CARD - week-10 card probe (W10-2).',
    ),
    ("social-s6-w9-card", "social-s6-w10-card"),
    ("2026_w9.jsonl", "2026_w10.jsonl"),
    ("replies_w9.md", "replies_w10.md"),
    (
        "datetime(2026, 10, 28, 12, 0, 0, tzinfo=ET)",
        "datetime(2026, 11, 4, 12, 0, 0, tzinfo=ET)",
    ),
]
for a, b in repls:
    if a not in t:
        print("MISSING", repr(a[:70]))
    else:
        t = t.replace(a, b)
        print("OK", a[:50])
p.write_text(t, encoding="utf-8", newline="\n")
text = p.read_text(encoding="utf-8")
for needle in [
    "social-s6-w10-card",
    "2026_w10.jsonl",
    "replies_w10.md",
    'HFA_MODE: str = "NONE"',
    'SITE_URL = "https://www.ridgecfb.com"',
    "social-s6-w9",
    "2026_w9",
    "replies_w9",
]:
    print(needle, needle in text)
