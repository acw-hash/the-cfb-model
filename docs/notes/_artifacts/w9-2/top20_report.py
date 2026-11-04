import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
payload = json.loads(
    Path("docs/notes/_artifacts/social-s6-w9-card/analysis.json").read_text(encoding="utf-8")
)
rows = payload.get("betting_rows") or []
print("n_betting_rows", len(rows))
# keep rows that pass non-QB gates: typically marked pre_qb or passes
# analysis structure: look for accepted/pre_qb fields
sample = rows[0] if rows else {}
print("sample keys", list(sample.keys())[:40])

def kick_et(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)

# Prefer pre_qb survivors sorted by edge desc
cands = []
for r in rows:
    # filter: passes non-QB (edge/gap) — use flags if present
    if r.get("excluded_timing"):
        continue
    if r.get("qb_only_fail") or r.get("pre_qb") or r.get("passes_non_qb") or True:
        edge = float(r.get("edge") or 0)
        gap = float(r.get("residual") or r.get("model_market_residual_points") or 0)
        if edge + 1e-12 < 0.045:
            continue
        if gap > 7.0 + 1e-9:
            continue
        cands.append(r)

# if betting_rows already are the best-per-game survivors, just sort
cands = sorted(cands, key=lambda r: -float(r.get("edge") or 0))
# dedupe by game_id keeping best edge
seen = set()
uniq = []
for r in cands:
    gid = str(r.get("game_id"))
    if gid in seen:
        continue
    seen.add(gid)
    uniq.append(r)
top = uniq[:20]
print("n_uniq_pass", len(uniq), "showing", len(top))

wed_thu = []
for i, r in enumerate(top, 1):
    ko = kick_et(str(r["kickoff_utc"]))
    flag = ""
    if ko.weekday() in (2, 3):  # Wed=2 Thu=3
        flag = " WED/THU"
        wed_thu.append((i, r, ko))
    gap = float(r.get("residual") or r.get("model_market_residual_points") or 0)
    edge = float(r["edge"])
    near = []
    if abs(gap - 7.0) <= 0.5 or gap >= 6.5:
        near.append(f"GAP_NEAR_7({gap:.2f})")
    if abs(edge - 0.045) <= 0.005:
        near.append(f"EDGE_NEAR_FLOOR({edge:.4f})")
    print(
        f"{i:02d}{flag} | {r['game_id']} | {ko.strftime('%a %b %d %I:%M %p ET')} | "
        f"{r.get('matchup') or r.get('game')} | side={r.get('side')} | "
        f"book={r.get('book')} line={r.get('line')} price={r.get('price')} | "
        f"ridge={r.get('model_line')} gap={gap:.2f} edge={edge:.4f} "
        f"hfa_adj={r.get('edge_hfa_adj')} | {r.get('side_is')} | {' '.join(near)}"
    )

print("\nWED_THU_IN_TOP20", len(wed_thu))
for i, r, ko in wed_thu:
    print(i, r["game_id"], ko.isoformat(), r.get("matchup"))

# also list any Wed/Thu that pass gates even outside top20
print("\nALL_WED_THU_PASSING")
for r in uniq:
    ko = kick_et(str(r["kickoff_utc"]))
    if ko.weekday() in (2, 3):
        print(
            f"edge={float(r['edge']):.4f} | {r['game_id']} | "
            f"{ko.strftime('%a %b %d %I:%M %p ET')} | {r.get('matchup')} | {r.get('side')}"
        )
