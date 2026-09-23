"""W2-3 deliverable helpers — read-only analysis of rendered social files."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

QB_PHRASE = "QB situation is unclear and we don't bet through that"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    wp = json.loads(Path("out/scratch/w2_3a/week_predictions.json").read_text(encoding="utf-8"))
    replies = Path("out/social/replies_w2.md").read_text(encoding="utf-8")
    thread = Path("out/social/thread_w2.md").read_text(encoding="utf-8")

    sections: list[tuple[str, str]] = []
    cur: str | None = None
    body: list[str] = []
    for line in replies.splitlines():
        if line.startswith("## "):
            if cur is not None:
                sections.append((cur, "\n".join(body)))
            cur = line[3:].strip()
            body = []
        else:
            body.append(line)
    if cur is not None:
        sections.append((cur, "\n".join(body)))

    qb_matchups = [m for m, b in sections if QB_PHRASE in b]
    print(f"qb_status_unknown_reply_count={len(qb_matchups)}")

    id_by = {f"{g['away_team']} @ {g['home_team']}": str(g["game_id"]) for g in wp["games"]}
    ids: list[str] = []
    missing: list[str] = []
    for m in qb_matchups:
        gid = id_by.get(m)
        if gid is None:
            missing.append(m)
        else:
            ids.append(gid)
    print("game_ids=" + ",".join(ids))
    print(f"unmapped={missing}")
    print(f"FIXTURE_BANNER_thread={'FIXTURE_BANNER' in thread}")
    print(f"FIXTURE_BANNER_replies={'FIXTURE_BANNER' in replies}")
    print(f"example_com_thread={'example.com' in thread}")
    print(f"example_com_replies={'example.com' in replies}")
    for m in re.finditer(r"--- POST (\d+)/(\d+) \((\d+) chars\) ---", thread):
        n, tot, c = m.groups()
        flag = " OVER" if int(c) > 280 else ""
        print(f"post {n}/{tot}: {c}{flag}")


if __name__ == "__main__":
    main()
