"""Extract W11-4 accepted card + next-two from post-QB analysis.json."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ART = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
payload = json.loads((ART / "analysis.json").read_text(encoding="utf-8"))

expected = [
    401864537,
    401856861,
    401856887,
    401862826,
    401856858,
    401871061,
    401862827,
]
expected_next = [401856864, 401856859]


def _kick(r: dict) -> str:
    raw = r.get("kickoff_utc") or r.get("kickoff_et")
    if not raw:
        return ""
    try:
        if "T" in str(raw):
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(ET)
        else:
            dt = datetime.strptime(str(raw)[:16], "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    except Exception:  # noqa: BLE001
        return str(raw)
    h12 = dt.hour % 12 or 12
    ap = "AM" if dt.hour < 12 else "PM"
    return f"Sat {h12}:{dt.minute:02d} {ap} ET"


def _gap(r: dict) -> float | None:
    if r.get("residual") is not None:
        return float(r["residual"])
    if r.get("model_market_residual_points") is not None:
        return float(r["model_market_residual_points"])
    line = r.get("line", r.get("shopped_line"))
    ridge = r.get("model_line")
    if line is None or ridge is None:
        return None
    return abs(float(ridge) - float(line))


def _row_out(r: dict, rank: int) -> dict:
    line = r.get("line", r.get("shopped_line"))
    return {
        "rank": rank,
        "game_id": int(r["game_id"]),
        "matchup": r.get("matchup"),
        "kickoff_et": _kick(r),
        "side": r.get("side"),
        "book": r.get("book"),
        "line": line,
        "price": r.get("price"),
        "ridge": r.get("model_line"),
        "gap": _gap(r),
        "edge": r.get("edge"),
    }


by_bet = {str(r["game_id"]): r for r in payload.get("betting_rows") or []}
card = []
for gid in payload["gate"]["accepted_game_ids"]:
    # Prefer qb_known_passing (has line/residual filled)
    hit = next(
        (r for r in payload["gate"]["qb_known_passing"] if str(r["game_id"]) == str(gid)),
        None,
    )
    if hit is None:
        hit = by_bet[str(gid)]
    card.append(hit)
card = sorted(card, key=lambda r: float(r.get("edge") or 0), reverse=True)
card_out = [_row_out(r, i) for i, r in enumerate(card, 1)]

# Next two: exposure-only rejects after max7, both QB starter (loop_trace)
next_ids: list[int] = []
for t in payload["gate"]["loop_trace"]:
    rs = set(t.get("reasons") or [])
    if rs and rs.issubset({"max_bets_per_week", "max_weekly_exposure", "max_team_exposure"}):
        next_ids.append(int(t["game_id"]))
next_ids = next_ids[:2]
next_out = []
for i, gid in enumerate(next_ids, 1):
    r = by_bet[str(gid)]
    next_out.append(_row_out(r, i))

actual_ids = [int(r["game_id"]) for r in card_out]
print("=== CARD ===")
for r in card_out:
    print(
        f"{r['rank']} {r['matchup']} | {r['kickoff_et']} | {r['side']} "
        f"{r['book']} {r['line']} {r['price']} | ridge={r['ridge']} gap={r['gap']} edge={r['edge']}"
    )
print("actual", actual_ids, "expected", expected, "match", actual_ids == expected)
print("=== NEXT TWO ===")
for r in next_out:
    print(
        f"{r['rank']} {r['matchup']} | {r['kickoff_et']} | {r['side']} "
        f"{r['book']} {r['line']} {r['price']} | ridge={r['ridge']} gap={r['gap']} edge={r['edge']}"
    )
print("next", next_ids, "expected", expected_next, "match", next_ids == expected_next)

out = {
    "accepted_count": payload["gate"]["accepted_count"],
    "accepted_game_ids": [int(x) for x in payload["gate"]["accepted_game_ids"]],
    "card": card_out,
    "next_two": next_out,
    "match_expected_card": actual_ids == expected,
    "match_expected_next": next_ids == expected_next,
    "note_marked_card_rows": (
        "marked_card_rows is NOT the accept-loop card; use accepted_game_ids / "
        "qb_known_passing. marked_card_rows still ranks pre-QB top edges."
    ),
}
(ART / "w11_4_card.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
print("wrote", ART / "w11_4_card.json")
