"""W12-2: label + Model-line parity on 5 open Saturday games."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ET = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parent


def main() -> None:
    wp = json.loads(
        Path("docs/notes/_artifacts/w12-1/r2_week_predictions.json").read_text(
            encoding="utf-8"
        )
    )
    sat0 = datetime(2026, 11, 21, 0, 0, tzinfo=ET).astimezone(UTC)
    sat1 = datetime(2026, 11, 22, 0, 0, tzinfo=ET).astimezone(UTC)
    open_sat = []
    for g in wp["games"]:
        ko = datetime.fromisoformat(g["kickoff_utc"].replace("Z", "+00:00"))
        if sat0 <= ko < sat1:
            open_sat.append(g)
    open_sat.sort(key=lambda g: g["kickoff_utc"])
    chosen = open_sat[:5]
    print(
        "chosen",
        [
            (g["game_id"], g["away_team"], g["home_team"], round(g["mu_margin"], 1))
            for g in chosen
        ],
    )

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        html = client.get(
            "https://www.ridgecfb.com/",
            headers={"User-Agent": "ridge-w12-2"},
        ).text
        (OUT / "site_home.html").write_text(html, encoding="utf-8")
        print("home Higher is better", len(re.findall(r"Higher is better", html, re.I)))

        rsc = client.get(
            "https://www.ridgecfb.com/",
            headers={"User-Agent": "ridge-w12-2", "RSC": "1"},
        ).text
        (OUT / "site__rsc.txt").write_text(rsc, encoding="utf-8")

        gid0 = chosen[0]["game_id"]
        gd = client.get(
            f"https://www.ridgecfb.com/game/{gid0}",
            headers={"User-Agent": "ridge-w12-2"},
        ).text
        (OUT / f"site_game_{gid0}.html").write_text(gd, encoding="utf-8")
        print(
            "game detail Higher",
            len(re.findall(r"Higher is better", gd, re.I)),
        )

    results = []
    for g in chosen:
        gid = g["game_id"]
        mu1 = round(float(g["mu_margin"]), 1)
        m = re.search(
            rf'game_id":"{gid}".{{0,500}}?mu_margin":([-0-9.eE+]+)',
            rsc,
        )
        site_mu = float(m.group(1)) if m else None
        site_1dec = None if site_mu is None else round(site_mu, 1)
        ok = site_1dec == mu1
        results.append(
            {
                "game_id": gid,
                "away": g["away_team"],
                "home": g["home_team"],
                "mu_1dec": mu1,
                "site_1dec": site_1dec,
                "ok": ok,
            }
        )
        print(
            f"{gid} {g['away_team']}@{g['home_team']} "
            f"artifact={mu1} site={site_1dec} ok={ok}"
        )

    (OUT / "parity_sat5.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print("parity", sum(1 for x in results if x["ok"]), "/", len(results))


if __name__ == "__main__":
    main()
