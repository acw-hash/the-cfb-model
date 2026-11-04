import httpx
import re

for url in ["https://www.ridgecfb.com", "https://the-cfb-model.vercel.app"]:
    r = httpx.get(url, timeout=60, follow_redirects=True)
    text = r.text
    print("====", url, "====")
    # header region
    i = text.find("ThisWeekHeader")
    print("header_idx", i)
    if i >= 0:
        print(re.sub(r"\s+", " ", text[i : i + 500]))
    # known week-9 matchups
    for name in [
        "Kennesaw",
        "Middle Tennessee",
        "James Madison",
        "North Texas",
        "Charlotte",
        "Sacramento State",
        "Delaware",
        "Western Kentucky",
    ]:
        print(name, text.find(name) >= 0)
    # week number near This week
    for m in re.finditer(r"This week.{0,80}|Week\s+\d|w0?9|season.{0,40}week", text, re.I | re.S):
        print("hit", re.sub(r"\s+", " ", m.group(0))[:100])
    # Market column
    print("Market col", "Market" in text)
    print("Odds as of", "Odds as of" in text)
    print("Tuesday primary", "Tuesday primary" in text)
