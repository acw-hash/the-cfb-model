"""Check both public hosts show week 10, Market, Odds as of."""

from __future__ import annotations

import re

import httpx


def main() -> None:
    for url in ["https://www.ridgecfb.com", "https://the-cfb-model.vercel.app"]:
        r = httpx.get(url, timeout=60, follow_redirects=True)
        text = r.text
        print("URL", url, "status", r.status_code, "final", str(r.url))
        checks = {
            "week_10": bool(re.search(r"week\s*10", text, re.I)),
            "Market": "Market" in text or "market" in text.lower(),
            "Odds as of": bool(re.search(r"odds\s+as\s+of", text, re.I)),
        }
        for k, v in checks.items():
            print(" ", k, v)
        i = text.lower().find("odds as of")
        if i >= 0:
            print("  odds_snip", text[i : i + 90].replace("\n", " "))
        m = re.search(r"week\s*10", text, re.I)
        if m:
            print("  week_snip", text[m.start() : m.start() + 60].replace("\n", " "))


if __name__ == "__main__":
    main()
