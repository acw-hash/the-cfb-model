"""W0∪W8-D betting-language grep (W9-1 Amendment 1).

Canonical union (do not narrow):

    best bet | yes bet | \\bplay\\b | edge vs market | \\bunits\\b
    | lock it in | must bet | recommended bet

Two runners:

* ``published`` — blocking. Full union over the explicit published-copy
  surface list. An unlisted ``copy.ts`` / ``copy.tsx`` under
  ``webapp/site/src`` is itself a failure.
* ``ratchet`` — repo-wide over ``git ls-files``. Counts must equal the
  pin in this file (not a ceiling above the live count). Any drift fails.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

UNION = re.compile(
    r"best bet|yes bet|\bplay\b|edge vs market|\bunits\b|lock it in|must bet|recommended bet",
    re.IGNORECASE,
)

# W9-1 recon (rg --pcre2, before the checker/notes landed): 283 / 217 / 67.
# git ls-files + Python finditer at W9-D: exact live counts, not a
# padded ceiling. Fail if any count differs. Re-measure after editing this
# file or notes that quote the union.
BASELINE_MATCHES = 351
BASELINE_LINES = 239
BASELINE_FILES = 76

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

#: Explicit published-copy surfaces. Adding a ``copy.ts`` without adding it
#: here is a failure. Modules whose strings reach a published artifact
#: (site copy, export verdict/metrics, committed fixtures) belong here.
PUBLISHED_COPY_SURFACES: tuple[str, ...] = (
    "webapp/site/src/lib/about/copy.ts",
    "webapp/site/src/lib/results/copy.ts",
    "webapp/site/src/lib/game-detail/absence.ts",
    "webapp/site/src/lib/game-detail/provenance.ts",
    "src/ncaa_quant/webapp/export.py",
    "webapp/fixtures/week_predictions.json",
    "webapp/fixtures/track_record.json",
    "webapp/fixtures/meta.json",
    "webapp/fixtures/results_2024.json",
    "webapp/fixtures/team_ratings_2024.json",
)


@dataclass(frozen=True)
class Hit:
    rel: str
    line_no: int
    line: str
    n_matches: int


@dataclass(frozen=True)
class ScanResult:
    hits: tuple[Hit, ...]
    matches: int
    lines: int
    files: int

    @property
    def files_with_hits(self) -> int:
        return self.files


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def tracked_files(root: Path) -> list[Path]:
    out = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=root,
        stderr=subprocess.STDOUT,
    )
    names = [n for n in out.decode("utf-8").split("\0") if n]
    return [root / n for n in names]


def discover_copy_modules(root: Path) -> list[str]:
    """Return repo-relative ``copy.ts`` / ``copy.tsx`` paths under site src."""
    src = root / "webapp" / "site" / "src"
    found: list[str] = []
    if not src.is_dir():
        return found
    for path in src.rglob("*"):
        if path.suffix.lower() in {".ts", ".tsx"} and path.stem == "copy":
            found.append(path.relative_to(root).as_posix())
    return sorted(found)


def scan_text(text: str, *, rel: str) -> list[Hit]:
    hits: list[Hit] = []
    for i, line in enumerate(text.splitlines(), start=1):
        found = list(UNION.finditer(line))
        if found:
            hits.append(Hit(rel=rel, line_no=i, line=line, n_matches=len(found)))
    return hits


def scan_paths(root: Path, paths: Iterable[Path]) -> ScanResult:
    hits: list[Hit] = []
    files_with_hits = 0
    for path in paths:
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError, OSError):
            continue
        rel = path.relative_to(root).as_posix() if path.is_absolute() else path.as_posix()
        file_hits = scan_text(text, rel=rel)
        if file_hits:
            files_with_hits += 1
            hits.extend(file_hits)
    matches = sum(h.n_matches for h in hits)
    return ScanResult(
        hits=tuple(hits),
        matches=matches,
        lines=len(hits),
        files=files_with_hits,
    )


def _print_hits(hits: Sequence[Hit]) -> None:
    for hit in hits:
        print(f"{hit.rel}:{hit.line_no}:{hit.line}")


def check_published(root: Path) -> int:
    listed = list(PUBLISHED_COPY_SURFACES)
    listed_set = set(listed)
    errors: list[str] = []

    copy_modules = discover_copy_modules(root)
    unlisted = [p for p in copy_modules if p not in listed_set]
    if unlisted:
        errors.append("copy module not on PUBLISHED_COPY_SURFACES: " + ", ".join(unlisted))

    missing = [p for p in listed if not (root / p).is_file()]
    if missing:
        errors.append("listed published-copy surface missing: " + ", ".join(missing))

    if errors:
        for msg in errors:
            print(msg, file=sys.stderr)
        return 1

    result = scan_paths(root, [root / p for p in listed])
    _print_hits(result.hits)
    print(
        f"union_grep published matches={result.matches} lines={result.lines} "
        f"files={result.files} surfaces={len(listed)}",
        file=sys.stderr,
    )
    if result.hits:
        print(
            "published-copy union grep failed: betting-language hit on a published surface",
            file=sys.stderr,
        )
        return 1
    return 0


def check_ratchet(root: Path) -> int:
    result = scan_paths(root, tracked_files(root))
    print(
        f"union_grep ratchet matches={result.matches}/{BASELINE_MATCHES} "
        f"lines={result.lines}/{BASELINE_LINES} "
        f"files={result.files}/{BASELINE_FILES}",
        file=sys.stderr,
    )
    drift: list[str] = []
    if result.matches != BASELINE_MATCHES:
        drift.append(f"matches {result.matches} != {BASELINE_MATCHES}")
    if result.lines != BASELINE_LINES:
        drift.append(f"lines {result.lines} != {BASELINE_LINES}")
    if result.files != BASELINE_FILES:
        drift.append(f"files {result.files} != {BASELINE_FILES}")
    if drift:
        _print_hits(result.hits)
        print("union-grep ratchet failed: " + "; ".join(drift), file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("published", "ratchet"),
        help="published = blocking copy surfaces; ratchet = repo-wide exact pin",
    )
    args = parser.parse_args(argv)
    root = _repo_root()
    if args.mode == "published":
        return check_published(root)
    return check_ratchet(root)


if __name__ == "__main__":
    raise SystemExit(main())
