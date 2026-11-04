"""W10-2: top-20 non-QB gate passers + QB prompt batches."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
payload = json.loads(
    Path("docs/notes/_artifacts/social-s6-w10-card/analysis.json").read_text(
        encoding="utf-8"
    )
)
rows = payload["betting_rows"]


def kick_et(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)


cands = []
for r in rows:
    edge = float(r.get("edge") or 0)
    gap = float(r.get("model_market_residual_points") or 0)
    if not r.get("passes_pricing_gates"):
        reasons = set(r.get("filter_reasons_firing") or [])
        non_qb_fail = reasons - {"qb_status_unknown", "pass"}
        if non_qb_fail:
            continue
    if edge + 1e-12 < 0.045 or gap > 7.0 + 1e-9:
        continue
    cands.append(r)

cands = sorted(cands, key=lambda r: -float(r["edge"]))
seen: set[str] = set()
uniq = []
for r in cands:
    gid = str(r["game_id"])
    if gid in seen:
        continue
    seen.add(gid)
    uniq.append(r)
top = uniq[:20]

print("pre_qb_survivors_n", len(uniq))
print("accepted_with_qb", sum(1 for r in payload["betting_rows"] if r.get("accepted")))

print("=== TOP 20 (non-QB gates) ===")
print(
    "rank | game_id | kickoff ET | game | side | book | line | price | Ridge | gap | "
    "edge | HFA-adj | venue | flags"
)
out_rows = []
wed_thu = []
for i, r in enumerate(top, 1):
    ko = kick_et(str(r["kickoff_utc"]))
    gap = float(r["model_market_residual_points"])
    edge = float(r["edge"])
    line = r.get("shopped_line") if r.get("shopped_line") is not None else r.get("line")
    flags = []
    if gap >= 6.5:
        flags.append("GAP~7")
    if abs(edge - 0.045) <= 0.005:
        flags.append("EDGE~4.5%")
    if ko.weekday() in (2, 3):  # Wed=2, Thu=3
        flags.append("WED/THU")
        wed_thu.append((i, r, ko))
    book = r.get("book_display") or r.get("book")
    print(
        f"{i} | {r['game_id']} | {ko.strftime('%a %b %d %I:%M %p ET')} | {r['matchup']} | "
        f"{r['side']} | {book} | {line} | {r['price']} | {float(r['model_line']):.2f} | "
        f"{gap:.2f} | {edge:.4f} | {float(r.get('edge_hfa_adj') or 0):.4f} | {r['side_is']} | "
        f"{','.join(flags) or '-'}"
    )
    out_rows.append((i, r, ko, line, gap, edge, flags))

print("\n=== WED/THU IN TOP 20 (flagged first) ===")
if not wed_thu:
    print("none")
else:
    for i, r, ko in wed_thu:
        print(
            f"rank {i} | {r['game_id']} | {r['matchup']} | "
            f"{ko.strftime('%a %b %d %I:%M %p ET')}"
        )

# Prompt order: Wed/Thu first in Batch 1, then remaining by edge rank
prompt_rows = []
seen_p: set[str] = set()
for i, r, ko in wed_thu:
    gid = str(r["game_id"])
    if gid in seen_p:
        continue
    seen_p.add(gid)
    prompt_rows.append(
        f"{r['game_id']} | {r['matchup']} ({ko.strftime('%a %I:%M %p ET')})"
    )
for i, r, ko, line, gap, edge, flags in out_rows:
    gid = str(r["game_id"])
    if gid in seen_p:
        continue
    seen_p.add(gid)
    prompt_rows.append(
        f"{r['game_id']} | {r['matchup']} ({ko.strftime('%a %I:%M %p ET')})"
    )

print("\n=== BATCH 1 (1-10) ===")
for line in prompt_rows[:10]:
    print(line)
print("\n=== BATCH 2 (11-20) ===")
for line in prompt_rows[10:20]:
    print(line)

print("\nWed/Thu gate-passers outside top20:")
for r in uniq[20:]:
    ko = kick_et(str(r["kickoff_utc"]))
    if ko.weekday() in (2, 3):
        print(
            f"  {r['game_id']} | {r['matchup']} ({ko.strftime('%a %I:%M %p ET')}) "
            f"edge={float(r['edge']):.4f}"
        )
