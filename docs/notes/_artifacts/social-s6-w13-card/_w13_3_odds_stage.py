"""W13-3 step 1: spreads pull for eligible Saturday games after QB gate.

markets=spreads × regions=us,us2. Hard stop if credits used > 10.
Report credits remaining first; STOP if < 5. Stage with per-book event_time.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.odds_api import normalize_odds_payload, write_odds_snapshots
from ncaa_quant.ingestion.teams import load_team_name_map, normalize_team_name
from ncaa_quant.utils.timeutils import to_utc

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent
BASE_URL = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_ncaaf"
EST_CREDITS = 2
CREDIT_HARD_STOP = 10
CREDIT_FLOOR = 5
MAX_AGE_H = 6.0

# Eligible after 0b (Friday availability = no changes): all 10 STARTER games.
GIDS: list[int] = [
    401856873,  # Arizona State @ Arizona
    401856761,  # Louisville @ Kentucky
    401862839,  # Tulane @ South Florida
    401856757,  # Auburn @ Alabama
    401858541,  # Washington @ Oregon
    401866474,  # Central Michigan @ Ball State
    401856877,  # Kansas @ Oklahoma State
    401864550,  # UTEP @ Northern Illinois
    401869959,  # Louisiana @ Georgia State
    401856763,  # Oklahoma @ Missouri
]


def _credits_remaining() -> int:
    key = load_secrets().odds_api_key.get_secret_value()
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        resp = client.get("/sports", params={"apiKey": key})
        resp.raise_for_status()
        rem = resp.headers.get("x-requests-remaining")
    if rem is None:
        raise RuntimeError("missing x-requests-remaining")
    return int(rem)


def _event_ids(gids: list[int]) -> dict[int, str]:
    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        cw = store.read("odds_cfbd_game_crosswalk", filters={"season": 2026})
    sub = cw.loc[
        (cw["game_id"].astype("Int64").isin(gids))
        & (cw["match_status"].astype(str) == "matched")
    ].copy()
    if "ingested_at" in sub.columns:
        sub["ingested_at"] = pd.to_datetime(sub["ingested_at"], utc=True)
        sub = sub.sort_values("ingested_at").groupby("game_id", sort=False).tail(1)
    out = {int(r["game_id"]): str(r["odds_event_id"]) for _, r in sub.iterrows()}
    missing = set(gids) - set(out)
    if missing:
        raise RuntimeError(f"crosswalk missing for {sorted(missing)}")
    return out


def _pull(event_ids: list[str]) -> tuple[list[Any], dict[str, Any], Path]:
    if EST_CREDITS > CREDIT_HARD_STOP:
        raise RuntimeError(f"hard stop: est {EST_CREDITS} > {CREDIT_HARD_STOP}")
    key = load_secrets().odds_api_key.get_secret_value()
    params = {
        "apiKey": key,
        "regions": "us,us2",
        "markets": "spreads",
        "oddsFormat": "american",
        "eventIds": ",".join(event_ids),
    }
    print(f"EST_CREDITS={EST_CREDITS} (spreads x us,us2) hard_stop={CREDIT_HARD_STOP}")
    print(f"n_event_ids={len(event_ids)}")
    with httpx.Client(base_url=BASE_URL, timeout=90.0) as client:
        resp = client.get(f"/sports/{SPORT_KEY}/odds", params=params)
        resp.raise_for_status()
        headers = {
            "x-requests-remaining": resp.headers.get("x-requests-remaining"),
            "x-requests-used": resp.headers.get("x-requests-used"),
            "x-requests-last": resp.headers.get("x-requests-last"),
        }
        payload = resp.json()
    used = int(headers["x-requests-last"] or 0)
    print("credits_used", headers["x-requests-last"])
    print("credits_remaining", headers["x-requests-remaining"])
    if used > CREDIT_HARD_STOP:
        raise RuntimeError(f"hard stop after call: used {used} > {CREDIT_HARD_STOP}")
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = ART / f"odds_w13_3_{ts}.json"
    meta = {
        "pulled_at_utc": datetime.now(tz=UTC).isoformat(),
        "event_ids": event_ids,
        "params": {k: v for k, v in params.items() if k != "apiKey"},
        "headers": headers,
        "est_credits": EST_CREDITS,
        "n_events": len(payload) if isinstance(payload, list) else None,
        "task": "W13-3",
        "gids": GIDS,
    }
    out_path.write_text(
        json.dumps({"meta": meta, "events": payload}, indent=2) + "\n",
        encoding="utf-8",
    )
    print("saved", out_path)
    return payload, meta, out_path


def _drop_stale_and_onesided(
    events: list[Any], *, as_of: datetime, team_map: dict[str, str]
) -> tuple[list[Any], list[dict[str, Any]]]:
    cutoff = to_utc(as_of) - timedelta(hours=MAX_AGE_H)
    drops: list[dict[str, Any]] = []
    cleaned: list[Any] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        eid = str(event.get("id", ""))
        home = normalize_team_name(str(event.get("home_team", "")), team_map)
        away = normalize_team_name(str(event.get("away_team", "")), team_map)
        keep_bms: list[dict[str, Any]] = []
        for bm in event.get("bookmakers") or []:
            if not isinstance(bm, dict):
                continue
            book = str(bm.get("key", ""))
            last_raw = bm.get("last_update")
            if not isinstance(last_raw, str):
                drops.append({"event_id": eid, "book": book, "reason": "missing_last_update"})
                continue
            last_update = to_utc(datetime.fromisoformat(last_raw.replace("Z", "+00:00")))
            if last_update < cutoff:
                drops.append(
                    {
                        "event_id": eid,
                        "book": book,
                        "reason": f"stale_last_update>{MAX_AGE_H}h",
                        "last_update": last_update.isoformat(),
                    }
                )
                continue
            spread = next(
                (
                    m
                    for m in (bm.get("markets") or [])
                    if isinstance(m, dict) and m.get("key") == "spreads"
                ),
                None,
            )
            if spread is None:
                drops.append({"event_id": eid, "book": book, "reason": "no_spreads_market"})
                continue
            outcomes = [o for o in (spread.get("outcomes") or []) if isinstance(o, dict)]
            sides = {normalize_team_name(str(o.get("name", "")), team_map) for o in outcomes}
            if home not in sides or away not in sides:
                drops.append(
                    {
                        "event_id": eid,
                        "book": book,
                        "reason": "one_side_only",
                        "sides": sorted(sides),
                    }
                )
                continue
            keep_bms.append(bm)
        if keep_bms:
            cleaned.append({**event, "bookmakers": keep_bms})
    return cleaned, drops


def _frame_with_per_book_event_time(
    events: list[Any],
    *,
    event_game_ids: dict[str, int],
    team_map: dict[str, str],
    captured_at: datetime,
    ingested_at: datetime,
) -> pd.DataFrame:
    body = json.dumps(events).encode()
    frame = normalize_odds_payload(
        body,
        captured_at=captured_at,
        ingested_at=ingested_at,
        team_map=team_map,
        snapshot_source="live",
        event_time=captured_at,
        event_game_ids=event_game_ids,
    )
    if frame.empty:
        return frame
    last_map: dict[tuple[str, str], datetime] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        eid = str(event.get("id", ""))
        for bm in event.get("bookmakers") or []:
            if not isinstance(bm, dict):
                continue
            book = str(bm.get("key", ""))
            last_raw = bm.get("last_update")
            if isinstance(last_raw, str):
                last_map[(eid, book)] = to_utc(
                    datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
                )
    eid_by_gid = {int(gid): eid for eid, gid in event_game_ids.items()}
    new_ets: list[datetime] = []
    for _, r in frame.iterrows():
        gid = r.get("game_id")
        book = str(r.get("book", ""))
        eid = eid_by_gid.get(int(gid)) if pd.notna(gid) else None
        et = last_map.get((str(eid), book)) if eid else None
        new_ets.append(et if et is not None else to_utc(captured_at))
    frame = frame.copy()
    frame["event_time"] = new_ets
    return frame


def main() -> None:
    cfg = load_config()
    assert cfg.webapp.export_enabled is False
    rem = _credits_remaining()
    print("credits_remaining_before", rem)
    if rem < CREDIT_FLOOR:
        raise SystemExit(f"STOP: credits remaining {rem} < {CREDIT_FLOOR}")

    gid_to_eid = _event_ids(GIDS)
    event_ids = sorted(set(gid_to_eid.values()))
    print("n_gids", len(GIDS), "n_event_ids", len(event_ids))
    events, meta, raw_path = _pull(event_ids)

    now = datetime.now(tz=UTC)
    team_map = load_team_name_map(Path(cfg.data.team_names_path))
    cleaned, drops = _drop_stale_and_onesided(events, as_of=now, team_map=team_map)
    print("n_drop_records", len(drops))
    rc = Counter(d["reason"] if "stale" not in d["reason"] else "stale_>6h" for d in drops)
    print("drop_reason_counts", dict(rc))
    books_dropped = sorted({d["book"] for d in drops if d.get("book")})
    print("books_appearing_in_drops", books_dropped)

    event_game_ids = {eid: gid for gid, eid in gid_to_eid.items()}
    captured = to_utc(now)
    ingested = datetime.now(tz=UTC)
    frame = _frame_with_per_book_event_time(
        cleaned,
        event_game_ids=event_game_ids,
        team_map=team_map,
        captured_at=captured,
        ingested_at=ingested,
    )
    print("frame_rows", len(frame))
    with ParquetStore(cfg.paths.staged_dir) as store:
        added, quarantined = write_odds_snapshots(store, frame, raw_archive_path=raw_path)
    print("rows_written", added, "quarantined", quarantined)

    report = {
        "raw_path": str(raw_path),
        "credits_before": rem,
        "credits": meta.get("headers"),
        "n_gids": len(GIDS),
        "gids": GIDS,
        "n_event_ids": len(event_ids),
        "n_drop_records": len(drops),
        "drop_reason_counts": dict(rc),
        "books_appearing_in_drops": books_dropped,
        "rows_fetched": len(frame),
        "rows_written": added,
        "quarantined": quarantined,
        "drops_sample": drops[:80],
        "availability": "no changes",
    }
    (ART / "w13_3_odds_stage_report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print("wrote", ART / "w13_3_odds_stage_report.json")


if __name__ == "__main__":
    main()
