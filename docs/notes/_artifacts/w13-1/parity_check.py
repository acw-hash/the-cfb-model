"""Parity: latest μ vs www.ridgecfb.com Model line (favored magnitude, 1 decimal)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
import httpx
import pandas as pd

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.webapp.export import load_teams_frame

OUT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")


def favored_from_mu(
    away: str, home: str, mu: float | None
) -> tuple[str | None, float | None]:
    if mu is None:
        return None, None
    if mu > 0:
        return home, round(float(mu), 1)
    if mu < 0:
        return away, round(abs(float(mu)), 1)
    return home, 0.0


def main() -> None:
    cfg = load_config()
    sec = load_secrets()
    client_s3 = boto3.client(
        "s3",
        endpoint_url=cfg.webapp.r2_endpoint_url or None,
        aws_access_key_id=sec.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=sec.r2_secret_access_key.get_secret_value(),
        region_name="auto",
    )
    wp = json.loads(
        client_s3.get_object(
            Bucket=cfg.webapp.r2_bucket, Key="latest/week_predictions.json"
        )["Body"].read()
    )
    (OUT / "r2_week_predictions.json").write_text(
        json.dumps(wp, indent=2), encoding="utf-8"
    )
    meta = json.loads(
        client_s3.get_object(Bucket=cfg.webapp.r2_bucket, Key="latest/meta.json")[
            "Body"
        ].read()
    )
    print("meta week", meta.get("week"), "published_at", meta.get("published_at"))

    games = {str(g["game_id"]): g for g in (wp.get("games") or [])}
    store = ParquetStore(Path(cfg.paths.staged_dir))
    g13 = store.read("games", filters={"season": 2026, "week": 13}).copy()
    g13["start_date"] = pd.to_datetime(g13["start_date"], utc=True)
    teams = load_teams_frame(season=2026, config=cfg)
    names = {int(r.team_id): str(r.school) for r in teams.itertuples()}

    thu = datetime(2026, 11, 26, 0, 0, tzinfo=ET).astimezone(UTC)
    fri = datetime(2026, 11, 27, 0, 0, tzinfo=ET).astimezone(UTC)
    sat = datetime(2026, 11, 28, 0, 0, tzinfo=ET).astimezone(UTC)
    sun = datetime(2026, 11, 29, 0, 0, tzinfo=ET).astimezone(UTC)

    thu_games = g13.loc[
        (g13["start_date"] >= pd.Timestamp(thu))
        & (g13["start_date"] < pd.Timestamp(fri))
    ].sort_values("start_date")
    fri_games = g13.loc[
        (g13["start_date"] >= pd.Timestamp(fri))
        & (g13["start_date"] < pd.Timestamp(sat))
    ].sort_values("start_date")
    sat_games = g13.loc[
        (g13["start_date"] >= pd.Timestamp(sat))
        & (g13["start_date"] < pd.Timestamp(sun))
    ].sort_values("start_date")

    parity_ids: list[str] = []
    for _, r in thu_games.iterrows():
        parity_ids.append(str(int(r.game_id)))
    for _, r in fri_games.iterrows():
        parity_ids.append(str(int(r.game_id)))
    n_sat = 0
    for _, r in sat_games.iterrows():
        gid = str(int(r.game_id))
        g = games.get(gid)
        if not g or g.get("null_reason"):
            continue
        parity_ids.append(gid)
        n_sat += 1
        if n_sat >= 5:
            break

    targets = []
    for gid in parity_ids:
        g = games.get(gid)
        hit = g13.loc[g13.game_id.astype(str) == gid].iloc[0]
        home = names.get(int(hit.home_team_id), str(hit.home_team_id))
        away = names.get(int(hit.away_team_id), str(hit.away_team_id))
        mu = None if g is None else g.get("mu_margin")
        fav, mag = favored_from_mu(away, home, None if mu is None else float(mu))
        targets.append(
            {
                "game_id": gid,
                "away": away,
                "home": home,
                "mu": mu,
                "fav": fav,
                "mag": mag,
                "null_reason": None if g is None else g.get("null_reason"),
                "kickoff": str(hit.start_date),
            }
        )
        print(f"TARGET {gid} | {away} @ {home} | fav={fav} by {mag}")
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
        for path, name in (
            ("/", "site__rsc.txt"),
            ("/results", "site_results_rsc.txt"),
        ):
            r = client.get(
                f"https://www.ridgecfb.com{path}",
                headers={
                    "RSC": "1",
                    "Next-Router-State-Tree": "%5B%22%22%2C%7B%7D%5D",
                },
            )
            (OUT / name).write_text(r.text, encoding="utf-8")
            print(path, "rsc", r.status_code, "len", len(r.text))

    blob = (
        (OUT / "site_home.html").read_text(encoding="utf-8")
        + "\n"
        + (OUT / "site__rsc.txt").read_text(encoding="utf-8")
    )
    # Flatten escaped sequences that appear in RSC payloads
    blob_flat = blob.replace("\\n", " ").replace('\\"', '"')

    matches = []
    mismatches = []
    for t in targets:
        fav, mag = t["fav"], t["mag"]
        # Find a window containing both team names, then the first X.X number near them
        window = ""
        for name in (t["away"], t["home"]):
            for mm in re.finditer(re.escape(name), blob_flat):
                start = max(0, mm.start() - 60)
                end = min(len(blob_flat), mm.end() + 220)
                window += blob_flat[start:end] + " || "
        site_mag = None
        # Prefer pattern: Away Home <mag> <pct>%
        m = re.search(
            rf"{re.escape(t['away'])}\s+{re.escape(t['home'])}\s+([0-9]+(?:\.[0-9]+)?)\s+\d+%",
            window,
        )
        if m:
            site_mag = float(m.group(1))
        else:
            # Fallback: mag appears near favored team
            if fav and mag is not None:
                if f"{mag:.1f}" in window or str(mag) in window:
                    site_mag = float(mag)
        ok = (
            site_mag is not None
            and mag is not None
            and abs(float(site_mag) - float(mag)) < 0.05
            and (fav is None or fav in window)
        )
        row = {**t, "site_mag": site_mag, "ok": ok, "window": window[:500]}
        if ok:
            matches.append(row)
            print(f"MATCH {t['game_id']} {fav} by {mag} == site {site_mag}")
        else:
            mismatches.append(row)
            print(
                f"MISMATCH {t['game_id']} latest {fav} by {mag} vs site {site_mag}"
            )

    print("n_targets", len(targets))
    print("n_match", len(matches))
    print("n_mismatch", len(mismatches))

    home_html = (OUT / "site_home.html").read_text(encoding="utf-8")
    print("home_week13_text", bool(re.search(r"2026 Week 13|Week 13", home_html, re.I)))
    print("meta_week_is_13", meta.get("week") == 13)
    res = (OUT / "site_results.html").read_text(encoding="utf-8") + "\n" + (
        OUT / "site_results_rsc.txt"
    ).read_text(encoding="utf-8")
    print("results_week12", bool(re.search(r"Week\s*12", res, re.I)))
    for needle in ("Notre Dame", "Nebraska", "Stanford", "Colorado", "Florida State"):
        print(f"results_has[{needle}]", needle in res)

    (OUT / "parity_result.json").write_text(
        json.dumps(
            {
                "matches": [{k: v for k, v in m.items() if k != "window"} for m in matches],
                "mismatches": mismatches,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if mismatches:
        raise SystemExit(f"STOP parity mismatch n={len(mismatches)}")
    print("PARITY_OK")


if __name__ == "__main__":
    main()
