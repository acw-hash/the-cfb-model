import httpx
import json
import re

r = httpx.get("https://www.ridgecfb.com", timeout=60, follow_redirects=True)
text = r.text
print("status", r.status_code, "len", len(text))
m = re.search(r"<script id=\"__NEXT_DATA__\"[^>]*>(.*?)</script>", text, re.S)
if not m:
    print("no NEXT_DATA")
    i = text.lower().find("odds as of")
    print(repr(text[max(0, i - 250) : i + 350]))
    # look for week in visible text
    for pat in ["Week 9", "week 9", "W9", '"week":9', "week&quot;:9"]:
        print(pat, text.find(pat))
else:
    data = json.loads(m.group(1))

    def find(obj, path=""):
        if isinstance(obj, dict):
            if "week" in obj:
                print("week@", path, obj.get("week"), "season", obj.get("season"))
            for k, v in obj.items():
                find(v, f"{path}.{k}")
        elif isinstance(obj, list) and len(obj) < 400:
            for i, v in enumerate(obj):
                find(v, f"{path}[{i}]")

    find(data)
