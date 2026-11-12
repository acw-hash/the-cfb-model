"""Extract B3 top-25 and B4 QB checklist from analysis.json."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ART = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
payload = json.loads((ART / "analysis.json").read_text(encoding="utf-8"))
rows = payload.get("gate", {}).get("pre_qb_survivors") or []
rows = sorted(rows, key=lambda r: float(r.get("edge") or 0), reverse=True)
print("n_pre_qb", len(rows), "slate", payload.get("slate_count"))


def _kick_parts(r: dict) -> tuple[str, bool]:
    """Return ('Sat H:MM PM ET', noon_flag)."""
    raw = r.get("kickoff_utc") or r.get("kickoff_et")
    if not raw:
        return "", False
    try:
        if "T" in str(raw) or str(raw).endswith("Z") or "+" in str(raw)[10:]:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(ET)
        else:
            # '2026-11-14 12:00 EST'
            dt = datetime.strptime(str(raw)[:16], "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    except Exception:  # noqa: BLE001
        return str(raw), False
    noon = dt.hour == 12
    label = dt.strftime("Sat %-I:%M %p ET") if False else dt.strftime("Sat %I:%M %p ET").lstrip("0").replace(" 0", " ")
    # Windows strftime lacks %-I; normalize manually
    h12 = dt.hour % 12 or 12
    ap = "AM" if dt.hour < 12 else "PM"
    label = f"Sat {h12}:{dt.minute:02d} {ap} ET"
    return label, noon


top25 = []
for i, r in enumerate(rows[:25], 1):
    ko_lbl, noon = _kick_parts(r)
    top25.append(
        {
            "rank": i,
            "game_id": int(r["game_id"]) if str(r.get("game_id", "")).isdigit() else r.get("game_id"),
            "kickoff_et": ko_lbl,
            "matchup": r.get("matchup"),
            "side": r.get("side"),
            "book": r.get("book"),
            "line": r.get("line"),
            "price": r.get("price"),
            "ridge": r.get("model_line"),
            "gap": r.get("residual"),
            "edge": r.get("edge"),
            "noon_flag": noon,
        }
    )
    flag = " NOON" if noon else ""
    print(
        f"{i:2d} {r.get('game_id')} {ko_lbl} | {r.get('side')} @ "
        f"{r.get('book')} {r.get('line')} {r.get('price')} | ridge={r.get('model_line')} "
        f"gap={r.get('residual')} edge={r.get('edge')}{flag}"
    )

(ART / "b3_top25.json").write_text(json.dumps(top25, indent=2) + "\n", encoding="utf-8")

qb_lines: list[str] = []
for r in rows[:20]:
    ko_lbl, _ = _kick_parts(r)
    qb_lines.append(f"{r.get('game_id')} | {r.get('matchup')} ({ko_lbl})")
(ART / "b4_qb_checklist.txt").write_text("\n".join(qb_lines) + "\n", encoding="utf-8")
md_lines = [
    "# B3 top 25 (non-QB gates)",
    "",
    "| rank | game_id | kickoff ET | side | book | line | price | Ridge | gap | edge | noon |",
    "|-----:|--------:|------------|------|------|-----:|------:|------:|----:|-----:|:----:|",
]
for r in top25:
    md_lines.append(
        f"| {r['rank']} | {r['game_id']} | {r['kickoff_et']} | {r['side']} | {r['book']} | "
        f"{r['line']} | {r['price']} | {r['ridge']} | {r['gap']} | {r['edge']} | "
        f"{'Y' if r['noon_flag'] else ''} |"
    )
md_lines.extend(["", "# B4 QB-check list (top 20)", ""] + [f"- {x}" for x in qb_lines] + [""])
(ART / "B3_B4.md").write_text("\n".join(md_lines), encoding="utf-8")
print("--- QB checklist ---")
print("\n".join(qb_lines))
