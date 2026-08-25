#!/usr/bin/env python3
"""ridge_social.py — generate the weekly RidgeCFB X thread + reply bank.

Reads the public webapp artifact (week_predictions.json) plus the private
candidates sidecar from S1 (or hand-written --candidates/--rejected lists),
applies the PUBLIC Best Bet bar on top of the already-applied §12 filters,
and writes two paste-ready files:

    thread_w{W}.md   — Tuesday anchor thread (one post per block)
    replies_w{W}.md  — reply bank for every game on the slate

No network calls, no posting. A human reviews and posts. That's on purpose.

Primary input (S1 sidecar)::

    {
      "season": 2024, "week": 5, "refresh_kind": "tuesday_primary",
      "published_at": "...", "fixture": false,
      "accepted": [ /* CandidateRecord */ ],
      "rejected": [ /* CandidateRecord + reasons */ ]
    }

Fallback: separate --candidates / --rejected JSON lists (hand-written weeks).

Thresholds and unit_fraction come from SocialConfig (not module constants).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ncaa_quant.config import load_config
from ncaa_quant.social.render import render_replies, render_thread
from ncaa_quant.social.select import select_best_bets


def _load_json(path: str | None) -> Any:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        print(f"[warn] missing file: {p}", file=sys.stderr)
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _load_accepted_rejected(
    *,
    sidecar: str | None,
    candidates: str | None,
    rejected: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool | None]:
    """Load accepted/rejected lists.

    Prefers the S1 sidecar (``accepted`` / ``rejected`` arrays). Falls back to
    separate list files for hand-written weeks. Returns optional sidecar
    ``fixture`` flag (``None`` when using the fallback path).
    """
    if sidecar:
        data = _load_json(sidecar)
        if data is None:
            return [], [], None
        if not isinstance(data, dict) or "accepted" not in data:
            print(
                "[error] sidecar must be an object with 'accepted' / 'rejected' arrays",
                file=sys.stderr,
            )
            raise SystemExit(1)
        accepted = list(data.get("accepted") or [])
        rejected_list = list(data.get("rejected") or [])
        fixture_flag = bool(data["fixture"]) if "fixture" in data else None
        return accepted, rejected_list, fixture_flag

    accepted = list(_load_json(candidates) or [])
    rejected_list = list(_load_json(rejected) or [])
    return accepted, rejected_list, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--predictions", required=True, help="week_predictions.json")
    ap.add_argument(
        "--sidecar",
        help="S1 candidates.json (accepted + rejected arrays)",
    )
    ap.add_argument(
        "--candidates",
        help="fallback: accepted candidates JSON list (hand-written weeks)",
    )
    ap.add_argument(
        "--rejected",
        help="fallback: rejected candidates JSON list with reasons",
    )
    ap.add_argument("--record", help="season record JSON for the hook post")
    ap.add_argument("--site-url", default="https://ridge.example.com")
    ap.add_argument("--out", default="out/social")
    ap.add_argument(
        "--now",
        help="ISO UTC post-time override (tests / dry-runs); default: wall clock",
    )
    args = ap.parse_args(argv)

    wp = _load_json(args.predictions)
    if not wp:
        print("[error] predictions artifact required", file=sys.stderr)
        return 1

    fixture = bool(wp.get("fixture", False))
    if fixture:
        print(
            "[warn] FIXTURE artifact — do not post these numbers",
            file=sys.stderr,
        )

    week = int(wp.get("week", 0))
    games = [dict(g) for g in wp.get("games", [])]
    games_by_id = {str(g.get("game_id")): g for g in games}

    try:
        accepted, rejected, sidecar_fixture = _load_accepted_rejected(
            sidecar=args.sidecar,
            candidates=args.candidates,
            rejected=args.rejected,
        )
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1

    if sidecar_fixture is True:
        fixture = True
        print(
            "[warn] FIXTURE sidecar — do not post these numbers",
            file=sys.stderr,
        )

    record = _load_json(args.record)
    cfg = load_config()

    if args.now:
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
    else:
        now = datetime.now(tz=UTC)

    bets, skipped = select_best_bets(
        games_by_id,
        accepted,
        now,
        social=cfg.social,
        betting=cfg.betting,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    thread_path = out_dir / f"thread_w{week}.md"
    thread_path.write_text(
        render_thread(
            week,
            bets,
            record,
            args.site_url,
            fixture=fixture,
            rejected=rejected,
        ),
        encoding="utf-8",
    )

    replies_path = out_dir / f"replies_w{week}.md"
    replies_path.write_text(
        render_replies(games, bets, rejected, args.site_url, fixture=fixture),
        encoding="utf-8",
    )

    print(
        f"week {week}: {len(bets)} Best Bets "
        f"({len(skipped)} accepted candidates held back from public card)"
    )
    for cand, why in skipped:
        print(f"  held: game {cand.get('game_id')} — {why}")
    print(f"wrote {thread_path}\nwrote {replies_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
