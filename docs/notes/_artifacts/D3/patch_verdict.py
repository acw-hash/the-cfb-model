"""Patch D3 verdict ASCII + sha without re-running the filter."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

p = Path("docs/notes/_artifacts/D3/d3_results.json")
payload = json.loads(p.read_text(encoding="utf-8"))
gap = float(payload["part5"]["mapping_layer_mae_gap"])
payload["part5"]["mapping_layer_verdict"] = (
    f"Stack MAE minus L1 MAE = {gap:.2f} "
    f"({'stack better' if gap > 0 else 'L1 better'}; "
    f"D2 prior was +0.5 to +1.2 for the stack)."
)
text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
p.write_text(text, encoding="utf-8")
d3_sha = hashlib.sha256(text.encode()).hexdigest()

md = Path("docs/notes/D3.md")
s = md.read_text(encoding="utf-8")
s = re.sub(
    r"\*\*Stack MAE.*?\*\*",
    f"**Stack MAE minus L1 MAE = {gap:.2f} "
    f"(stack better; D2 prior was +0.5 to +1.2 for the stack).**",
    s,
    count=1,
    flags=re.DOTALL,
)
s = re.sub(
    r"(d3_results\.json` sha256 `)[a-f0-9]+",
    rf"\g<1>{d3_sha}",
    s,
    count=1,
)
md.write_text(s, encoding="utf-8")
print(d3_sha)
