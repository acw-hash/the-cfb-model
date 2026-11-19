"""W12-4: set-qb for operator-reviewed week-12 eligible games.

Same path as W11-4 / W9-2 / W10-4: ``ncaa-quant roster set-qb``.

Operator (Nov 19, reviewed): both QBs STARTER on the six eligible games.
Every other W12-3 top-25 game has at least one BACKUP or UNKNOWN and is
not given a starter row here (no per-team split was supplied).
Georgia @ South Carolina is both STARTER; the ledger play carries
qb_note ``operator_override_sc_sellers`` (Sellers: starter 3 of 4, no
Nov 15+ source naming him the starter).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

# game_id, away, home, away_status, home_status
ROWS: list[tuple[str, str, str, str, str]] = [
    ("401856867", "Colorado", "Cincinnati", "starter", "starter"),
    ("401858531", "Ohio State", "Nebraska", "starter", "starter"),
    ("401856866", "BYU", "Kansas", "starter", "starter"),
    ("401856753", "Georgia", "South Carolina", "starter", "starter"),
    ("401858307", "SMU", "Notre Dame", "starter", "starter"),
    ("401858308", "Stanford", "California", "starter", "starter"),
]


def main() -> None:
    calls: list[tuple[str, str, str]] = []
    for gid, away, home, a_st, h_st in ROWS:
        calls.append((gid, away, a_st))
        calls.append((gid, home, h_st))

    out_lines: list[str] = []
    fails = 0
    writes: list[dict[str, object]] = []
    for game, team, status in calls:
        cmd = [
            "uv",
            "run",
            "ncaa-quant",
            "roster",
            "set-qb",
            "--game",
            game,
            "--team",
            team,
            "--status",
            status,
        ]
        print("CMD", " ".join(cmd), flush=True)
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        block = (
            f"$ {' '.join(cmd)}\n"
            f"exit={proc.returncode}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}\n"
        )
        print(block, flush=True)
        out_lines.append(block)
        writes.append(
            {
                "game_id": game,
                "team": team,
                "status": status,
                "exit": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }
        )
        if proc.returncode != 0:
            fails += 1
            print("FAIL", game, team, status, file=sys.stderr)

    art = Path(__file__).resolve().parent
    (art / "w12_4_set_qb.out").write_text("\n".join(out_lines), encoding="utf-8")
    (art / "w12_4_set_qb_writes.json").write_text(
        json.dumps({"n_calls": len(calls), "fails": fails, "writes": writes}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print("wrote", art / "w12_4_set_qb.out", "n_calls", len(calls), "fails", fails)
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
