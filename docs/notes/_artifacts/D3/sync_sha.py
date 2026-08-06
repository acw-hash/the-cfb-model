import hashlib
import json
import re
from pathlib import Path

d3_text = Path("docs/notes/_artifacts/D3/d3_results.json").read_text(encoding="utf-8")
d3_sha = hashlib.sha256(d3_text.encode()).hexdigest()
v2p = Path("docs/notes/_artifacts/D3/canonical_v2.json")
v2 = json.loads(v2p.read_text(encoding="utf-8"))
v2["d3_results_sha256"] = d3_sha
text = json.dumps(v2, indent=2, sort_keys=True, default=str) + "\n"
v2p.write_text(text, encoding="utf-8")
v2_sha = hashlib.sha256(text.encode()).hexdigest()
md_path = Path("docs/notes/D3.md")
md = md_path.read_text(encoding="utf-8")
md = re.sub(
    r"(canonical_v2\.json` sha256 `)[a-f0-9]+",
    rf"\g<1>{v2_sha}",
    md,
    count=1,
)
md_path.write_text(md, encoding="utf-8")
print(d3_sha)
print(v2_sha)
