"""W9-2: set-qb for top-20 probe games (operator QB research + overrides).

Overrides vs Batch 1 research:
- Notre Dame: STARTER (CJ Carr confirmed)
- Ole Miss: STARTER (Trinidad Chambliss confirmed)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

# game_id -> (away, home)
GAMES: dict[str, tuple[str, str]] = {
    "401856735": ("Auburn", "Ole Miss"),
    "401859185": ("Notre Dame", "Navy"),
    "401858501": ("Minnesota", "Indiana"),
    "401858280": ("Clemson", "Florida State"),
    "401856736": ("South Carolina", "Oklahoma"),
    "401862813": ("Tulane", "Charlotte"),
    "401869944": ("Coastal Carolina", "Georgia State"),
    "401862814": ("Army", "Memphis"),
    "401856737": ("Mississippi State", "Texas"),
    "401856844": ("Baylor", "UCF"),
    "401858282": ("Miami", "North Carolina"),
    "401858279": ("Boston College", "Duke"),
    "401858281": ("Georgia Tech", "Pittsburgh"),
    "401862816": ("UAB", "South Florida"),
    "401866451": ("Kent State", "Sacramento State"),
    "401858505": ("Northwestern", "Oregon"),
    "401858509": ("Ohio State", "USC"),
    "401860909": ("Washington State", "San Diego State"),
    "401856847": ("Oklahoma State", "Iowa State"),
    "401869846": ("App State", "Georgia Southern"),
}

STARTER = {
    "Auburn",
    "Ole Miss",  # override: Chambliss confirmed
    "Notre Dame",  # override: Carr confirmed
    "Navy",
    "Minnesota",
    "Indiana",
    "Clemson",
    "Florida State",
    "South Carolina",
    "Oklahoma",
    "Coastal Carolina",  # betting-preview caveat retained as STARTER
    "Army",
    "Memphis",
    "Texas",
    "Baylor",
    "UCF",
    "Miami",
    "North Carolina",
    "Boston College",
    "Georgia Tech",
    "UAB",
    "South Florida",
    "Kent State",
    "Sacramento State",
    "Northwestern",
    "USC",
    "Oklahoma State",
    "Iowa State",
}
BACKUP = {
    "Tulane",
    "Charlotte",
    "Pittsburgh",
    "Washington State",
    "San Diego State",
}
UNKNOWN = {
    "Georgia State",
    "Mississippi State",
    "Duke",
    "Oregon",
    "Ohio State",
    "App State",
    "Georgia Southern",
}


def status_for(team: str) -> str:
    if team in STARTER:
        return "starter"
    if team in BACKUP:
        return "backup"
    if team in UNKNOWN:
        return "unknown"
    raise KeyError(f"team not classified: {team!r}")


def main() -> None:
    all_teams: set[str] = set()
    for away, home in GAMES.values():
        all_teams.add(away)
        all_teams.add(home)
    classified = STARTER | BACKUP | UNKNOWN
    missing = all_teams - classified
    extra = classified - all_teams
    if missing or extra:
        print("MISSING", sorted(missing))
        print("EXTRA", sorted(extra))
        raise SystemExit(1)
    if len(classified) != 40:
        print("n_classified", len(classified), "expected 40")
        raise SystemExit(1)

    calls: list[tuple[str, str, str]] = []
    for gid, (away, home) in GAMES.items():
        calls.append((gid, away, status_for(away)))
        calls.append((gid, home, status_for(home)))

    out_lines: list[str] = []
    fails = 0
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
        if proc.returncode != 0:
            fails += 1
            print("FAIL", game, team, status, file=sys.stderr)

    path = Path(__file__).resolve().parent / "w9_2_set_qb.out"
    path.write_text("\n".join(out_lines), encoding="utf-8")
    print("wrote", path, "n_calls", len(calls), "fails", fails)
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
