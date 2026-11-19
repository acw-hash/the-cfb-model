"""Compare parity targets to scraped homepage HTML/RSC."""

from __future__ import annotations

import json
import re
from pathlib import Path

OUT = Path(__file__).resolve().parent


def favored_from_mu(away: str, home: str, mu: float | None) -> tuple[str | None, float | None]:
    if mu is None:
        return None, None
    if mu > 0:
        return home, round(mu, 1)
    if mu < 0:
        return away, round(abs(mu), 1)
    return home, 0.0


def main() -> None:
    targets = json.loads((OUT / "parity_targets.json").read_text(encoding="utf-8"))
    html = (OUT / "site_home.html").read_text(encoding="utf-8")
    rsc = (OUT / "site_home_rsc.txt").read_text(encoding="utf-8")
    blob = html + "\n" + rsc

    mismatches = []
    for t in targets:
        fav, mag = favored_from_mu(t["away"], t["home"], t["mu"])
        window = ""
        for name in (t["away"], t["home"]):
            for mm in re.finditer(re.escape(name), blob):
                start = max(0, mm.start() - 40)
                end = min(len(blob), mm.end() + 180)
                window += blob[start:end] + " || "
        alts = {f"{mag:.1f}", str(mag)}
        has_mag = any(a in window for a in alts)
        # also require fav team near the number for stronger match
        has_fav_near = fav is not None and fav in window
        status = "MATCH" if has_mag and has_fav_near else "MISMATCH"
        print(
            f"{status} {t['game_id']} {t['away']}@{t['home']} "
            f"latest={fav} by {mag}"
        )
        if status != "MATCH":
            mismatches.append({**t, "fav": fav, "mag": mag, "window": window[:400]})

    print("n_targets", len(targets))
    print("n_mismatches", len(mismatches))
    (OUT / "parity_mismatches.json").write_text(
        json.dumps(mismatches, indent=2), encoding="utf-8"
    )

    # Week badge / results graded
    print("home_week12_text", bool(re.search(r"2026 Week 12|Week 12", html, re.I)))
    res = (OUT / "site_results.html").read_text(encoding="utf-8")
    print("results_len", len(res))
    print("results_week11", bool(re.search(r"Week\s*11", res, re.I)))
    # graded games count hints
    for pat in [r"graded", r"Week 11", r"401856", r"margin_interval"]:
        print(pat, len(re.findall(pat, res, re.I)))


if __name__ == "__main__":
    main()
