"""Parity: latest μ vs www.ridgecfb.com Model line (1 decimal)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.webapp.export import load_teams_frame

OUT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")


def _fmt_mu(mu: float) -> float:
    return round(float(mu), 1)


def main() -> None:
    cfg = load_config()
    wp = json.loads((OUT / "r2_week_predictions.json").read_text(encoding="utf-8"))
    games = {str(g["game_id"]): g for g in (wp.get("games") or [])}
    store = ParquetStore(Path(cfg.paths.staged_dir))
    g12 = store.read("games", filters={"season": 2026, "week": 12}).copy()
    g12["start_date"] = pd.to_datetime(g12["start_date"], utc=True)
    teams = load_teams_frame(season=2026, config=cfg)
    names = {int(r.team_id): str(r.school) for r in teams.itertuples()}

    sat_start = datetime(2026, 11, 21, 0, 0, tzinfo=ET).astimezone(UTC)
    sat_end = datetime(2026, 11, 22, 0, 0, tzinfo=ET).astimezone(UTC)
    before = g12.loc[g12["start_date"] < pd.Timestamp(sat_start)].sort_values(
        "start_date"
    )
    not_final = before.loc[~before["completed"].fillna(False).astype(bool)]
    sat = g12.loc[
        (g12["start_date"] >= pd.Timestamp(sat_start))
        & (g12["start_date"] < pd.Timestamp(sat_end))
    ]
    sat_open = sat.loc[~sat["completed"].fillna(False).astype(bool)].sort_values(
        "start_date"
    )

    chosen: list[str] = []
    for _, r in sat_open.iterrows():
        gid = str(int(r.game_id))
        g = games.get(gid)
        if not g or g.get("null_reason"):
            continue
        chosen.append(gid)
        if len(chosen) >= 5:
            break

    parity_ids = list(not_final["game_id"].astype(int).astype(str)) + chosen
    targets = []
    for gid in parity_ids:
        g = games.get(gid)
        hit = g12.loc[g12.game_id.astype(str) == gid].iloc[0]
        home = names.get(int(hit.home_team_id), str(hit.home_team_id))
        away = names.get(int(hit.away_team_id), str(hit.away_team_id))
        mu = None if g is None else g.get("mu_margin")
        targets.append(
            {
                "game_id": gid,
                "away": away,
                "home": home,
                "mu": mu,
                "mu_1dec": None if mu is None else _fmt_mu(mu),
                "null_reason": None if g is None else g.get("null_reason"),
            }
        )
        print(
            f"TARGET {gid} | {away} @ {home} | mu={mu} "
            f"1dec={None if mu is None else _fmt_mu(mu)}"
        )
    (OUT / "parity_targets.json").write_text(
        json.dumps(targets, indent=2), encoding="utf-8"
    )

    with httpx.Client(
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": "ridge-parity/1.0"},
    ) as client:
        home_r = client.get("https://www.ridgecfb.com/")
        results_r = client.get("https://www.ridgecfb.com/results")
        print("home", home_r.status_code, "len", len(home_r.text))
        print("results", results_r.status_code, "len", len(results_r.text))
        (OUT / "site_home.html").write_text(home_r.text, encoding="utf-8")
        (OUT / "site_results.html").write_text(results_r.text, encoding="utf-8")

        # Try RSC / flight payloads that embed JSON
        for path in ("/", "/results"):
            r = client.get(
                f"https://www.ridgecfb.com{path}",
                headers={"RSC": "1", "User-Agent": "ridge-parity/1.0"},
            )
            (OUT / f"site{path.replace('/', '_') or '_home'}_rsc.txt").write_text(
                r.text, encoding="utf-8"
            )
            print("rsc", path, r.status_code, "len", len(r.text))

    # Week markers
    html = (OUT / "site_home.html").read_text(encoding="utf-8")
    print("home_has_Week_12", bool(re.search(r"Week\s*12", html, re.I)))
    print("home_has_week_12_json", '"week":12' in html or '"week\":12' in html)
    res = (OUT / "site_results.html").read_text(encoding="utf-8")
    print("results_has_Week_11", bool(re.search(r"Week\s*11", res, re.I)))
    print("results_has_graded", "graded" in res.lower() or "MAE" in res)


if __name__ == "__main__":
    main()
