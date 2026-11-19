"""W12-R — fetch CFBD historical lines (one GET /lines?year= per season).

Scratch only: writes under docs/notes/_artifacts/w12-r/. No staged/R2 writes.
Never prints secret values.
"""

from __future__ import annotations

import json
from pathlib import Path

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.ingestion.cfbd import CFBDClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "notes" / "_artifacts" / "w12-r"
SEASONS = [2019, 2021, 2022, 2023, 2024, 2026]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    key = load_secrets().cfbd_api_key.get_secret_value()
    if not key:
        raise SystemExit("CFBD_API_KEY missing")

    remaining_after_each: list[int | None] = []
    with CFBDClient(key, requests_per_second=cfg.data.cfbd_requests_per_second) as client:
        for season in SEASONS:
            raw = client.get("/lines", {"year": int(season)})
            path = OUT / f"cfbd_lines_{season}.json"
            path.write_bytes(raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode())
            n = len(json.loads(path.read_text(encoding="utf-8")))
            rem = client.remaining_requests
            remaining_after_each.append(rem)
            print(f"season={season} games={n} remaining={rem}", flush=True)
        credits_after = client.remaining_requests
    # Header is only known after the first response; reconstruct pre-run credits.
    first_after = remaining_after_each[0]
    credits_before = None if first_after is None else int(first_after) + 1

    (OUT / "cfbd_credits.json").write_text(
        json.dumps(
            {
                "endpoint": "GET /lines?year={season}",
                "seasons": SEASONS,
                "n_calls": len(SEASONS),
                "credits_before": credits_before,
                "credits_after": credits_after,
                "remaining_after_each_call": remaining_after_each,
                "line_field": (
                    "CFBD provider `spread` (closing home spread); "
                    "open available as `spreadOpen` but unused. "
                    "Per-game line_margin := median_books(-spread) "
                    "(market implied home margin)."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"credits_before={credits_before} credits_after={credits_after}",
        flush=True,
    )


if __name__ == "__main__":
    main()
