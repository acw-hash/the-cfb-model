"""W11-4: set-qb for week-11 card probe games (operator QB research).

Same path as W9-2 / W10-4: ``ncaa-quant roster set-qb``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

# game_id -> (away, home, away_status, home_status)
# status values: starter | backup | unknown
ROWS: list[tuple[str, str, str, str, str]] = [
    ("401858520", "Wisconsin", "Maryland", "backup", "unknown"),
    ("401856859", "Cincinnati", "Iowa State", "starter", "starter"),
    ("401862829", "UAB", "Temple", "starter", "unknown"),
    ("401856862", "Kansas State", "TCU", "unknown", "starter"),
    ("401862826", "Florida Atlantic", "Tulsa", "starter", "starter"),
    ("401856858", "Baylor", "BYU", "starter", "starter"),
    ("401871061", "Middle Tennessee", "Liberty", "starter", "starter"),
    ("401870752", "Jacksonville State", "Western Kentucky", "starter", "unknown"),
    ("401856887", "Ole Miss", "Oklahoma", "starter", "starter"),
    ("401871099", "Southern Miss", "Louisiana Tech", "backup", "unknown"),
    ("401856861", "Kansas", "West Virginia", "starter", "starter"),
    ("401869928", "App State", "Marshall", "starter", "unknown"),
    ("401856884", "Florida", "Kentucky", "backup", "starter"),
    ("401871100", "Troy", "South Alabama", "starter", "backup"),
    ("401856864", "Utah", "Arizona", "starter", "starter"),
    ("401864539", "UNLV", "New Mexico", "unknown", "starter"),
    ("401862827", "North Texas", "UTSA", "starter", "starter"),
    ("401856863", "Texas Tech", "Oklahoma State", "backup", "starter"),
    ("401860915", "Utah State", "San Diego State", "backup", "backup"),
    ("401864537", "North Dakota State", "Hawai'i", "starter", "starter"),
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
    (art / "w11_4_set_qb.out").write_text("\n".join(out_lines), encoding="utf-8")
    import json

    (art / "w11_4_set_qb_writes.json").write_text(
        json.dumps({"n_calls": len(calls), "fails": fails, "writes": writes}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print("wrote", art / "w11_4_set_qb.out", "n_calls", len(calls), "fails", fails)
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
