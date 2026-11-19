"""W12-5: set-qb for NC State @ Florida State.

Same path as W12-4 / W11-4: ``ncaa-quant roster set-qb``.

Operator: both QBs STARTER. East Carolina @ Army (401862831) is excluded
(ECU UNKNOWN) and is not written.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

# game_id, away, home, away_status, home_status
ROWS: list[tuple[str, str, str, str, str]] = [
    ("401858304", "NC State", "Florida State", "starter", "starter"),
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
    (art / "w12_5_set_qb.out").write_text("\n".join(out_lines), encoding="utf-8")
    (art / "w12_5_set_qb_writes.json").write_text(
        json.dumps({"n_calls": len(calls), "fails": fails, "writes": writes}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print("wrote", art / "w12_5_set_qb.out", "n_calls", len(calls), "fails", fails)
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
