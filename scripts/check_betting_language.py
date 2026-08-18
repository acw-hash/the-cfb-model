"""Repo-wide W0∪W8-D betting-language grep (W9-1).

Canonical union (do not narrow):

    best bet|yes bet|\\bplay\\b|edge vs market|\\bunits\\b|lock it in|must bet|recommended bet

Scans tracked files (``git ls-files``). Exit 1 on any match.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

UNION = re.compile(
    r"best bet|yes bet|\bplay\b|edge vs market|\bunits\b|lock it in|must bet|recommended bet",
    re.IGNORECASE,
)

BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".parquet",
    ".pkl",
    ".pickle",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _tracked_files(root: Path) -> list[Path]:
    out = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=root,
        stderr=subprocess.STDOUT,
    )
    names = [n for n in out.decode("utf-8").split("\0") if n]
    return [root / n for n in names]


def main() -> int:
    root = _repo_root()
    hits: list[str] = []
    files_with_hits = 0
    for path in _tracked_files(root):
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(root).as_posix()
        file_hit = False
        for i, line in enumerate(text.splitlines(), start=1):
            if UNION.search(line):
                hits.append(f"{rel}:{i}:{line}")
                file_hit = True
        if file_hit:
            files_with_hits += 1
    for hit in hits:
        print(hit)
    print(
        f"union_grep matches={len(hits)} files={files_with_hits} pattern={UNION.pattern!r}",
        file=sys.stderr,
    )
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
