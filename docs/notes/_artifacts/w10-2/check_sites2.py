"""Deeper week-10 presence check on both hosts."""

from __future__ import annotations

import json
import re

import httpx


def inspect(url: str) -> None:
    r = httpx.get(url, timeout=60, follow_redirects=True)
    text = r.text
    print("URL", url, "status", r.status_code, "final", str(r.url))
    weeks = sorted(
        set(re.findall(r"week\s*\d+", text, re.I)),
        key=lambda s: int(re.search(r"\d+", s).group()),  # type: ignore[union-attr]
    )
    print("  weeks_in_html", weeks)
    for pat in [r'"week"\s*:\s*10', r"Week 10", r"week 10", r"W10"]:
        print(" ", pat, bool(re.search(pat, text)))
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S
    )
    if m:
        data = json.loads(m.group(1))
        s = json.dumps(data)
        print("  nextdata weeks", sorted(set(re.findall(r'"week":\s*(\d+)', s))))
        print("  nextdata has 10", bool(re.search(r'"week":\s*10\b', s)))
    else:
        print("  no __NEXT_DATA__ len", len(text))
        # flight/RSC payloads
        print("  has flight week", bool(re.search(r"week.{0,5}10", text, re.I)))
        # dump nearby published stamp
        for needle in ["Nov 4", "published", "Tuesday primary", "week"]:
            i = text.lower().find(needle.lower())
            if i >= 0:
                snip = text[max(0, i - 20) : i + 80].replace("\n", " ")
                print("  near", needle, repr(snip[:120]))


def main() -> None:
    for url in [
        "https://www.ridgecfb.com",
        "https://the-cfb-model.vercel.app",
        "https://www.ridgecfb.com/results",
    ]:
        inspect(url)
        print("---")


if __name__ == "__main__":
    main()
