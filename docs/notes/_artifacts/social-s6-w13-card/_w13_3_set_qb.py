"""W13-3: set-qb for operator-reviewed week-13 eligible Saturday games.

Copied from docs/notes/_artifacts/social-s6-w12-card/_w12_5_set_qb.py.
Same path: ``ncaa-quant roster set-qb``.

Operator research dated Nov 25 (reviewed): both QBs STARTER on the ten
eligible games below (W13-1 QB-list order). The other ten W13-1 QB-list
games have at least one BACKUP or UNKNOWN and are not written.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

# game_id, away, home, away_status, home_status
ROWS: list[tuple[str, str, str, str, str]] = [
    ("401856873", "Arizona State", "Arizona", "starter", "starter"),
    ("401856761", "Louisville", "Kentucky", "starter", "starter"),
    ("401862839", "Tulane", "South Florida", "starter", "starter"),
    ("401856757", "Auburn", "Alabama", "starter", "starter"),
    ("401858541", "Washington", "Oregon", "starter", "starter"),
    ("401866474", "Central Michigan", "Ball State", "starter", "starter"),
    ("401856877", "Kansas", "Oklahoma State", "starter", "starter"),
    ("401864550", "UTEP", "Northern Illinois", "starter", "starter"),
    ("401869959", "Louisiana", "Georgia State", "starter", "starter"),
    ("401856763", "Oklahoma", "Missouri", "starter", "starter"),
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
    (art / "w13_3_set_qb.out").write_text("\n".join(out_lines), encoding="utf-8")
    (art / "w13_3_set_qb_writes.json").write_text(
        json.dumps({"n_calls": len(calls), "fails": fails, "writes": writes}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print("wrote", art / "w13_3_set_qb.out", "n_calls", len(calls), "fails", fails)
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
