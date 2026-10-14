#!/usr/bin/env python3
"""Render Ridge weekly social charts from published artifacts.

Usage:
  uv run python scripts/make_social_charts.py --season 2026
  uv run python scripts/make_social_charts.py --season 2026 --source local --artifact-dir webapp/fixtures --allow-fixture --allow-stale
"""

from __future__ import annotations

from ncaa_quant.social.cli import main

if __name__ == "__main__":
    main()
