"""Parse homepage a11y snapshot link names for Model line parity."""

from __future__ import annotations

import json
import re
from pathlib import Path

OUT = Path(__file__).resolve().parent
SNAP = Path(
    r"C:\Users\alecw\.cursor\browser-logs\snapshot-2026-11-19T13-44-02-543Z-5fnkpa.log"
)


def favored_from_mu(away: str, home: str, mu: float | None) -> tuple[str | None, float | None]:
    if mu is None:
        return None, None
    if mu > 0:
        return home, round(float(mu), 1)
    if mu < 0:
        return away, round(abs(float(mu)), 1)
    return home, 0.0


def main() -> None:
    targets = json.loads((OUT / "parity_targets.json").read_text(encoding="utf-8"))
    text = SNAP.read_text(encoding="utf-8")
    # link names like: "7:30 PM Rice Temple 2.2 63% Lean 2.5 56%"
    link_names = re.findall(r'name: "([^"]+)"', text)
    model_rows = [n for n in link_names if "%" in n and "PM" in n]

    mismatches = []
    matches = []
    for t in targets:
        fav, mag = favored_from_mu(t["away"], t["home"], t["mu"])
        hit = None
        for n in model_rows:
            if t["away"] in n and t["home"] in n:
                hit = n
                break
        if hit is None:
            # try truncated / alternate names
            for n in model_rows:
                away_tok = t["away"].split()[0]
                home_tok = t["home"].split()[0]
                if away_tok in n and home_tok in n:
                    hit = n
                    break
        if hit is None:
            mismatches.append({**t, "reason": "not_found_on_site", "fav": fav, "mag": mag})
            print(f"MISS_NOT_FOUND {t['game_id']} {t['away']}@{t['home']}")
            continue
        # first number after team names is model wins-by
        # pattern: TIME Away Home MODEL% ... 
        # e.g. "7:30 PM Rice Temple 2.2 63% Lean 2.5 56%"
        m = re.search(
            rf"{re.escape(t['away'])}\s+{re.escape(t['home'])}\s+([0-9]+(?:\.[0-9]+)?)",
            hit,
        )
        if not m:
            # order may be as displayed without @ 
            m = re.search(r"PM\s+.+\s+([0-9]+(?:\.[0-9]+)?)\s+\d+%", hit)
        site_mag = float(m.group(1)) if m else None
        ok = site_mag is not None and mag is not None and abs(site_mag - mag) < 0.05
        row = {
            **t,
            "fav": fav,
            "mag": mag,
            "site_link": hit,
            "site_mag": site_mag,
            "ok": ok,
        }
        if ok:
            matches.append(row)
            print(f"MATCH {t['game_id']} {fav} by {mag} == site {site_mag} | {hit}")
        else:
            mismatches.append(row)
            print(
                f"MISMATCH {t['game_id']} latest {fav} by {mag} vs site {site_mag} | {hit}"
            )

    print("n_targets", len(targets))
    print("n_match", len(matches))
    print("n_mismatch", len(mismatches))
    (OUT / "parity_result.json").write_text(
        json.dumps({"matches": matches, "mismatches": mismatches}, indent=2),
        encoding="utf-8",
    )
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
