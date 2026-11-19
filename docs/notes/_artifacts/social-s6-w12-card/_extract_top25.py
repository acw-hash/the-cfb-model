"""Extract W12-3 top-25 / QB list / parity helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ET = ZoneInfo("America/New_York")
ART = Path(__file__).resolve().parent
BOOK = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "williamhill_us": "Caesars",
    "espnbet": "theScore Bet",
    "fanatics": "Fanatics",
    "hardrockbet": "Hard Rock Bet",
    "ballybet": "Bally Bet",
    "betrivers": "BetRivers",
}


def _kick_lbl(kickoff_utc: str) -> tuple[str, bool]:
    ko = datetime.fromisoformat(str(kickoff_utc).replace("Z", "+00:00")).astimezone(ET)
    h12 = ko.hour % 12 or 12
    ap = "AM" if ko.hour < 12 else "PM"
    noon = ko.hour == 12 and ko.minute == 0
    return f"Sat {h12}:{ko.minute:02d} {ap} ET", noon


def main() -> None:
    payload = json.loads((ART / "analysis.json").read_text(encoding="utf-8"))
    surv = list(payload["gate"]["pre_qb_survivors"])
    surv.sort(key=lambda r: float(r["edge"]), reverse=True)

    # Enrich matchup from betting_rows if needed
    by_gid = {str(r["game_id"]): r for r in payload.get("betting_rows") or []}

    top25 = []
    for i, r in enumerate(surv[:25], 1):
        gid = str(r["game_id"])
        br = by_gid.get(gid, {})
        away = br.get("away_team") or r.get("away_team")
        home = br.get("home_team") or r.get("home_team")
        matchup = r.get("matchup") or f"{away} @ {home}"
        if away is None and " @ " in str(matchup):
            away, home = [x.strip() for x in str(matchup).split(" @ ", 1)]
        ko_utc = r.get("kickoff_utc") or br.get("kickoff_utc")
        ko_lbl, noon = _kick_lbl(str(ko_utc))
        # Ridge number for display/parity = home-perspective mu_margin (site).
        # model_line is side-perspective (used for residual vs market line).
        mu = br.get("mu_margin")
        if mu is None:
            mu = r.get("mu_margin")
        if mu is None:
            # recover from side-perspective model_line
            ml = float(r["model_line"])
            mu = ml if r.get("side_is") == "away" else -ml
        ridge = float(mu)
        gap = float(r["residual"]) if r.get("residual") is not None else abs(
            float(r["model_line"]) - float(r["line"])
        )
        price = float(r["price"])
        price_out: int | float = int(price) if price == int(price) else price
        top25.append(
            {
                "rank": i,
                "game_id": gid,
                "away": away,
                "home": home,
                "matchup": matchup,
                "kickoff_et": ko_lbl,
                "kickoff_utc": ko_utc,
                "side": r["side"],
                "book": BOOK.get(str(r["book"]), str(r["book"])),
                "book_key": r["book"],
                "line": float(r["line"]),
                "price": price_out,
                "ridge": round(ridge, 2),
                "ridge_raw": ridge,
                "gap": round(gap, 2),
                "edge": round(float(r["edge"]), 4),
                "noon": noon,
            }
        )

    (ART / "top25.json").write_text(json.dumps(top25, indent=2) + "\n", encoding="utf-8")

    print("| rank | game_id | game | kickoff ET | side | book | line | price | Ridge | gap | edge | noon |")
    print("|-----:|--------:|------|------------|------|------|-----:|------:|------:|----:|-----:|:----:|")
    for r in top25:
        print(
            f"| {r['rank']} | {r['game_id']} | {r['away']} @ {r['home']} | {r['kickoff_et']} | "
            f"{r['side']} | {r['book']} | {r['line']} | {r['price']:+g} | {r['ridge']} | "
            f"{r['gap']} | {r['edge']:.4f} | {'Y' if r['noon'] else ''} |"
        )

    print("\n## Noon in top 25")
    noons = [r for r in top25 if r["noon"]]
    for r in noons:
        print(f"- {r['game_id']} {r['away']} @ {r['home']}")

    print("\n## QB-check list (top 20)")
    for r in top25[:20]:
        print(f"{r['game_id']} | {r['away']} @ {r['home']} ({r['kickoff_et']})")

    # Parity vs www for first 5 of top 20
    print("\n## Parity (5 of top 20)")
    rsc = httpx.get(
        "https://www.ridgecfb.com/",
        follow_redirects=True,
        timeout=60,
        headers={"User-Agent": "ridge-w12-3", "RSC": "1"},
    ).text
    parity = []
    for r in top25[:5]:
        gid = r["game_id"]
        import re

        m = re.search(rf'game_id":"{gid}".{{0,500}}?mu_margin":([-0-9.eE+]+)', rsc)
        site = float(m.group(1)) if m else None
        site_1 = None if site is None else round(site, 2)
        card_1 = round(r["ridge_raw"], 2)
        ok = site_1 == card_1
        parity.append(
            {
                "game_id": gid,
                "card_ridge": card_1,
                "site_mu_1dec": site_1,
                "ok": ok,
            }
        )
        print(f"{gid} card={card_1} site={site_1} ok={ok}")
    (ART / "parity5.json").write_text(json.dumps(parity, indent=2) + "\n", encoding="utf-8")
    if not all(p["ok"] for p in parity):
        raise SystemExit("STOP: parity mismatch")


if __name__ == "__main__":
    main()
