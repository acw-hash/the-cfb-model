"""Scan agent transcripts for W8 post time mentions."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(
    r"C:\Users\alecw\.cursor\projects\c-Users-alecw-Projects-the-cfb-model\agent-transcripts"
)


def main() -> None:
    pat = re.compile(
        r".{0,100}(W8-4|7-play|posted_at|Thread posted|NIU posted|"
        r"Wed Oct 21.{0,40}(AM|PM)|Tue Oct 21.{0,40}(AM|PM)|"
        r"4:\d{2} PM ET|5:\d{2} PM ET).{0,160}",
        re.I | re.S,
    )
    for f in sorted(ROOT.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        text = f.read_text(encoding="utf-8", errors="replace")
        if "401869967" not in text and "W8-4" not in text:
            continue
        print("FILE", f.parent.name)
        n = 0
        for m in pat.finditer(text):
            s = re.sub(r"\s+", " ", m.group(0))[:260]
            if any(
                k in s.lower()
                for k in ("posted", "thread", "niu", "w8-4", "oct 21", "pm et")
            ):
                print(" ", s)
                n += 1
                if n >= 25:
                    break


if __name__ == "__main__":
    main()
