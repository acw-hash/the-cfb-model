#!/usr/bin/env python3
"""S5 P0-6 — thin reader for team_ids diagnosis (written by _s5_p0_4_selection)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "docs" / "notes" / "_artifacts" / "social-s5" / "p0_6_team_ids.json"


def main() -> int:
    if not ART.is_file():
        print(
            "missing p0_6_team_ids.json — run scripts/_s5_p0_4_selection.py first",
            flush=True,
        )
        return 1
    payload = json.loads(ART.read_text(encoding="utf-8"))
    print(payload["paragraph"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
