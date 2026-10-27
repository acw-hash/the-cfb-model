#!/usr/bin/env python3
"""Fetch + normalize CFBD team logos for Ridge social charts.

Usage:
  uv run python scripts/fetch_team_logos.py --season 2026
  uv run python scripts/fetch_team_logos.py --season 2026 --refresh
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ncaa_quant.social.logos import DEFAULT_LOGO_DIR, fetch_team_logos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--logo-dir", type=Path, default=DEFAULT_LOGO_DIR)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download even when a cached file exists",
    )
    args = parser.parse_args(argv)

    records, missing = fetch_team_logos(
        season=args.season,
        logo_dir=args.logo_dir,
        refresh=args.refresh,
    )
    print(f"Wrote/kept {len(records)} logo files under {args.logo_dir}")
    print(f"FBS teams missing a logo: {len(missing)}")
    if missing:
        print(f"{'team_id':>8}  {'school':<28}  override_path")
        for row in missing:
            print(
                f"{row['team_id']:>8}  {row['school']:<28}  {row['override_path']}"
            )
    return 0 if not missing else 0  # missing logos reported; fetch itself succeeds


if __name__ == "__main__":
    raise SystemExit(main())
