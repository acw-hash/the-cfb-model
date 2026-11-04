import json
from pathlib import Path
from collections import Counter
import httpx

hist = Path("data/webapp/publish_history/2026_w9.jsonl")
o = json.loads(hist.read_text(encoding="utf-8").strip().splitlines()[0])
games = o.get("games") or []
reasons = Counter()
n_suppressed = 0
for g in games:
    nr = g.get("null_reason") or g.get("published_null_reason")
    if nr:
        n_suppressed += 1
        reasons[str(nr)] += 1
    # also conviction suppressed?
    if g.get("conviction_tier") in (None, "suppressed") and g.get("tier_suppressed"):
        pass
print("n_games", len(games))
print("null_reason_count", n_suppressed, dict(reasons))
# check margin intervals null
n_margin_null = sum(1 for g in games if g.get("margin_interval_lo") is None)
print("margin_interval_null", n_margin_null)

# results page
r = httpx.get("https://www.ridgecfb.com/results", timeout=60, follow_redirects=True)
text = r.text
for needle in ["21-17-1", "21–17–1", "2-5", "2–5", "Week 8", "posted", "Best"]:
    print("results", needle, needle in text)
# snippet around 2-5 if any
i = text.find("2-5")
if i < 0:
    i = text.find("2–5")
print("snip", repr(text[max(0,i-80):i+80]) if i>=0 else None)
