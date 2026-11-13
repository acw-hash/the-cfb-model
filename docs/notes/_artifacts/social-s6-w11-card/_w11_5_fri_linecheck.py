"""W11-5 — Friday line check + final card + ledger (NO PUBLISH).

One Odds API pull (spreads × us,us2) for the 7 card games + 2 alternates.
Hard stop if credits used > 10. Stage snapshots. Re-price each W11-4 side at
the best regulated current line (seed 42 + game_id). Write posted_calls only.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from ncaa_quant.betting.edges import BookPrice, compute_edge
from ncaa_quant.betting.provider import _sample_game_draws
from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.distribution.key_numbers import KeyNumberKernel
from ncaa_quant.distribution.simulate import spread_cover_probs, two_way_side_prob
from ncaa_quant.ingestion.odds_api import normalize_odds_payload, write_odds_snapshots
from ncaa_quant.ingestion.teams import load_team_name_map, normalize_team_name
from ncaa_quant.utils.timeutils import to_utc

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent
ART.mkdir(parents=True, exist_ok=True)
TRACKED = ROOT / "docs" / "notes" / "_artifacts" / "w11-posted-calls"
SOCIAL = ROOT / "data" / "social" / "posted_calls_2026_w11.json"
HIST = ROOT / "data" / "webapp" / "publish_history" / "2026_w11.jsonl"
W11_4_CARD = ART / "w11_4_card.json"

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_ncaaf"
EST_CREDITS = 2
CREDIT_HARD_STOP = 10
MAX_AGE_H = 6.0
MIN_EDGE = 0.045
MAX_GAP = 7.0
STAKE = 0.014
UNITS = round(STAKE / 0.005, 1)
ET = ZoneInfo("America/New_York")

REGULATED = (
    "draftkings",
    "fanduel",
    "betmgm",
    "williamhill_us",
    "espnbet",
    "fanatics",
    "hardrockbet",
    "ballybet",
    "betrivers",
)
BOOK_DISPLAY = {
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

# W11-4 card (edge order) + alternates (Utah first, then Cincinnati).
CARD_PLAYS: list[dict[str, Any]] = [
    {
        "game_id": 401864537,
        "side": "Hawai'i",
        "w11_4_book": "espnbet",
        "w11_4_line": 6.5,
        "w11_4_price": 100.0,
        "role": "card",
    },
    {
        "game_id": 401856861,
        "side": "West Virginia",
        "w11_4_book": "betrivers",
        "w11_4_line": -3.0,
        "w11_4_price": 100.0,
        "role": "card",
    },
    {
        "game_id": 401856887,
        "side": "Oklahoma",
        "w11_4_book": "betmgm",
        "w11_4_line": 1.0,
        "w11_4_price": -112.0,
        "role": "card",
    },
    {
        "game_id": 401862826,
        "side": "Tulsa",
        "w11_4_book": "betrivers",
        "w11_4_line": -5.5,
        "w11_4_price": -110.0,
        "role": "card",
    },
    {
        "game_id": 401856858,
        "side": "Baylor",
        "w11_4_book": "hardrockbet",
        "w11_4_line": 7.5,
        "w11_4_price": -110.0,
        "role": "card",
    },
    {
        "game_id": 401871061,
        "side": "Liberty",
        "w11_4_book": "fanduel",
        "w11_4_line": -21.5,
        "w11_4_price": 100.0,
        "role": "card",
    },
    {
        "game_id": 401862827,
        "side": "North Texas",
        "w11_4_book": "betrivers",
        "w11_4_line": -2.5,
        "w11_4_price": -109.0,
        "role": "card",
    },
]
ALTERNATES: list[dict[str, Any]] = [
    {
        "game_id": 401856864,
        "side": "Arizona",
        "w11_4_book": "espnbet",
        "w11_4_line": 2.5,
        "w11_4_price": 100.0,
        "role": "alternate",
    },
    {
        "game_id": 401856859,
        "side": "Iowa State",
        "w11_4_book": "fanduel",
        "w11_4_line": -3.5,
        "w11_4_price": 110.0,
        "role": "alternate",
    },
]


def _load_wp() -> tuple[dict[str, Any], str]:
    lines = [ln for ln in HIST.read_text(encoding="utf-8").splitlines() if ln.strip()]
    wp = json.loads(lines[-1])
    return wp, f"data/webapp/publish_history/2026_w11.jsonl (record {len(lines)})"


def _kick_et(kickoff_utc: str) -> str:
    dt = datetime.fromisoformat(str(kickoff_utc).replace("Z", "+00:00")).astimezone(ET)
    h12 = dt.hour % 12 or 12
    ap = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%a')} {h12}:{dt.minute:02d} {ap} ET"


def _side_is(game: dict[str, Any], side: str) -> str:
    if bool(game.get("neutral_site")):
        return "neutral"
    if side == game.get("home_team"):
        return "home"
    if side == game.get("away_team"):
        return "away"
    return "?"


def _qb_statuses(
    gids: list[int], *, as_of: datetime
) -> list[dict[str, Any]]:
    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        qb = store.read("qb_status", filters={"season": 2026})
        teams = store.read("teams", filters={"season": 2026})
        games = store.read("games", filters={"season": 2026, "week": 11})
    tid_to_name = {int(r["team_id"]): str(r["school"]) for _, r in teams.iterrows()}
    qb = qb.copy()
    qb["event_time"] = pd.to_datetime(qb["event_time"], utc=True)
    as_ts = pd.Timestamp(to_utc(as_of))
    out: list[dict[str, Any]] = []
    for gid in gids:
        gr = games.loc[games["game_id"].astype("Int64") == gid]
        if gr.empty:
            out.append({"game_id": gid, "error": "game_missing"})
            continue
        row = gr.iloc[0]
        for tid in (int(row["away_team_id"]), int(row["home_team_id"])):
            school = tid_to_name.get(tid, str(tid))
            sub = qb.loc[
                (qb["game_id"].astype("Int64") == gid)
                & (qb["team_id"].astype(int) == tid)
                & (qb["event_time"] <= as_ts)
            ]
            if sub.empty:
                out.append(
                    {
                        "game_id": gid,
                        "team_id": tid,
                        "team": school,
                        "status": None,
                        "ok": False,
                    }
                )
                continue
            latest = sub.sort_values("event_time").iloc[-1]
            status = str(latest["status"])
            out.append(
                {
                    "game_id": gid,
                    "team_id": tid,
                    "team": school,
                    "status": status,
                    "event_time": latest["event_time"].isoformat(),
                    "ok": status == "starter",
                }
            )
    return out


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
    out_path = ART / f"odds_w11_5_{ts}.json"
    meta = {
        "pulled_at_utc": datetime.now(tz=UTC).isoformat(),
        "event_ids": event_ids,
        "params": {k: v for k, v in params.items() if k != "apiKey"},
        "headers": headers,
        "est_credits": EST_CREDITS,
        "n_events": len(payload) if isinstance(payload, list) else None,
        "task": "W11-5",
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


def _parse_quotes(
    events: list[Any],
    *,
    event_to_gid: dict[str, int],
    team_map: dict[str, str],
    as_of: datetime,
) -> dict[int, dict[str, dict[str, Any]]]:
    cutoff = to_utc(as_of) - timedelta(hours=MAX_AGE_H)
    by_gid: dict[int, dict[str, dict[str, Any]]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        eid = str(event.get("id", ""))
        gid = event_to_gid.get(eid)
        if gid is None:
            continue
        home = normalize_team_name(str(event.get("home_team", "")), team_map)
        away = normalize_team_name(str(event.get("away_team", "")), team_map)
        for bm in event.get("bookmakers") or []:
            if not isinstance(bm, dict):
                continue
            book = str(bm.get("key", ""))
            if book not in REGULATED:
                continue
            last_raw = bm.get("last_update")
            if not isinstance(last_raw, str):
                continue
            last_update = to_utc(datetime.fromisoformat(last_raw.replace("Z", "+00:00")))
            if last_update < cutoff:
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
                continue
            sides: dict[str, tuple[float, float]] = {}
            for o in spread.get("outcomes") or []:
                if not isinstance(o, dict) or o.get("point") is None or o.get("price") is None:
                    continue
                name = normalize_team_name(str(o.get("name", "")), team_map)
                sides[name] = (float(o["point"]), float(o["price"]))
            if home not in sides or away not in sides:
                continue
            by_gid.setdefault(gid, {})[book] = {
                "home_line": sides[home][0],
                "home_px": sides[home][1],
                "away_line": sides[away][0],
                "away_px": sides[away][1],
                "last_update": last_update,
                "home": home,
                "away": away,
            }
    return by_gid


def _better_price_key(price: float) -> float:
    if price >= 100:
        return 1.0 + price / 100.0
    return 1.0 + 100.0 / abs(price)


def _price_side(
    *,
    game: dict[str, Any],
    side_team: str,
    book: str,
    home_line: float,
    home_px: float,
    away_px: float,
    last_update: datetime,
    as_of: datetime,
) -> dict[str, Any]:
    gid = int(game["game_id"])
    home = str(game["home_team"])
    away = str(game["away_team"])
    if side_team == home:
        bet_on = "home"
        side_px = float(home_px)
        other_px = float(away_px)
        market_line = float(home_line)
    elif side_team == away:
        bet_on = "away"
        side_px = float(away_px)
        other_px = float(home_px)
        market_line = -float(home_line)
    else:
        raise ValueError(f"{gid}: side {side_team!r} not in {away} @ {home}")

    kernel = KeyNumberKernel(offset_weights={}, n=0)
    draws = _sample_game_draws(
        mu_m=float(game["mu_margin"]),
        sigma_m=float(game["sigma_margin"]),
        mu_t=float(game.get("mu_total") or 50.0),
        sigma_t=float(game.get("sigma_total") or 16.0),
        rho=float(game["rho"]) if game.get("rho") is not None else 0.0,
        kernel=kernel,
        n_draws=20_000,
        seed=42 + gid,
    )
    p_model = two_way_side_prob(
        spread_cover_probs(draws, float(home_line), game_index=0, side=bet_on)  # type: ignore[arg-type]
    )
    edge_res = compute_edge(p_model, [BookPrice(book, side_px, other_px)])
    model_line_home = -float(game["mu_margin"])
    residual = abs(model_line_home - float(home_line))
    model_line = model_line_home if bet_on == "home" else -model_line_home
    age_h = (to_utc(as_of) - to_utc(last_update)).total_seconds() / 3600.0
    edge = float(edge_res.edge)
    fail: list[str] = []
    if edge < MIN_EDGE:
        fail.append("edge")
    if residual > MAX_GAP:
        fail.append("gap")
    if age_h > MAX_AGE_H:
        fail.append("stale")
    return {
        "book": book,
        "book_display": BOOK_DISPLAY.get(book, book),
        "side": side_team,
        "side_is": _side_is(game, side_team),
        "line": market_line,
        "price": float(side_px),
        "last_update": to_utc(last_update).isoformat(),
        "edge": round(edge, 4),
        "gap": round(float(residual), 2),
        "model_line": float(model_line),
        "age_hours": round(age_h, 3),
        "p_model": round(float(edge_res.p_model), 6),
        "gate_pass": not fail,
        "gate_fail": ",".join(fail),
        "snapshot_event_time": to_utc(last_update).isoformat(),
    }


def _pick_best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Best regulated quote: prefer gate-pass; else highest edge among all."""
    book_rank = {b: i for i, b in enumerate(REGULATED)}
    elig = [r for r in rows if r.get("gate_pass")]
    pool = elig if elig else rows
    if not pool:
        return None
    pool = sorted(
        pool,
        key=lambda r: (
            float(r["edge"]),
            float(r["line"]),
            _better_price_key(float(r["price"])),
            -book_rank.get(str(r["book"]), 99),
        ),
        reverse=True,
    )
    return pool[0]


def _reprice_play(
    play: dict[str, Any],
    *,
    games_by: dict[int, dict[str, Any]],
    quotes: dict[int, dict[str, dict[str, Any]]],
    as_of: datetime,
) -> dict[str, Any]:
    gid = int(play["game_id"])
    side = str(play["side"])
    game = games_by[gid]
    book_map = quotes.get(gid, {})
    rows: list[dict[str, Any]] = []
    for book, q in book_map.items():
        rows.append(
            _price_side(
                game=game,
                side_team=side,
                book=book,
                home_line=float(q["home_line"]),
                home_px=float(q["home_px"]),
                away_px=float(q["away_px"]),
                last_update=q["last_update"],
                as_of=as_of,
            )
        )
    card_book = str(play["w11_4_book"])
    at_card_book = next((r for r in rows if r["book"] == card_book), None)
    best = _pick_best(rows)
    # Informational: best among gate-pass only (may be None even if best exists).
    best_pass = _pick_best([r for r in rows if r.get("gate_pass")]) if rows else None
    gate_ok = bool(best_pass is not None and best_pass.get("gate_pass"))
    return {
        "play": play,
        "matchup": f"{game['away_team']} @ {game['home_team']}",
        "kickoff_et": _kick_et(str(game["kickoff_utc"])),
        "kickoff_utc": game["kickoff_utc"],
        "side": side,
        "w11_4": {
            "book": card_book,
            "book_display": BOOK_DISPLAY.get(card_book, card_book),
            "line": float(play["w11_4_line"]),
            "price": float(play["w11_4_price"]),
        },
        "at_w11_4_book_now": at_card_book,
        "best_regulated": best_pass if gate_ok else best,
        "best_regulated_passes": gate_ok,
        "all_books": sorted(rows, key=lambda r: float(r["edge"]), reverse=True),
        "n_quotes": len(rows),
    }


def main() -> None:
    cfg = load_config()
    assert cfg.webapp.export_enabled is False, "export must stay False"
    print("export_enabled", cfg.webapp.export_enabled)

    now = datetime.now(tz=UTC)
    all_plays = CARD_PLAYS + ALTERNATES
    gids = [int(p["game_id"]) for p in all_plays]
    assert len(set(gids)) == 9

    # --- 0. QB recheck (read-only) ---
    qb_rows = _qb_statuses(gids, as_of=now)
    qb_ok = all(r.get("ok") for r in qb_rows if "error" not in r) and len(qb_rows) == 18
    print("\n=== 0. QB RECHECK ===")
    for r in qb_rows:
        print(
            f"  {r.get('game_id')} {r.get('team')}: {r.get('status')} "
            f"{'OK' if r.get('ok') else 'FAIL'}"
        )
    print("all_18_starter", qb_ok, "n_rows", len(qb_rows))
    if not qb_ok:
        raise SystemExit("QB recheck failed — expected all 18 STARTER; no set-qb writes")

    # --- 1. Odds pull + stage ---
    print("\n=== 1. ODDS PULL ===")
    gid_to_eid = _event_ids(gids)
    event_ids = sorted(set(gid_to_eid.values()))
    events, meta, raw_path = _pull(event_ids)
    as_of = to_utc(datetime.fromisoformat(str(meta["pulled_at_utc"]).replace("Z", "+00:00")))

    team_map = load_team_name_map(Path(cfg.data.team_names_path))
    cleaned, drops = _drop_stale_and_onesided(events, as_of=as_of, team_map=team_map)
    print("n_drop_records", len(drops))
    rc = Counter(d["reason"] if "stale" not in d["reason"] else "stale_>6h" for d in drops)
    print("drop_reason_counts", dict(rc))

    event_game_ids = {eid: gid for gid, eid in gid_to_eid.items()}
    captured = to_utc(as_of)
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

    stage_report = {
        "raw_path": str(raw_path),
        "credits": meta.get("headers"),
        "n_games": len(gids),
        "n_event_ids": len(event_ids),
        "n_drop_records": len(drops),
        "drop_reason_counts": dict(rc),
        "rows_fetched": len(frame),
        "rows_written": added,
        "quarantined": quarantined,
        "gids": gids,
    }
    (ART / "w11_5_stage_report.json").write_text(
        json.dumps(stage_report, indent=2, default=str) + "\n", encoding="utf-8"
    )

    # --- 2. Reprice ---
    print("\n=== 2. REPRICE ===")
    wp, wp_src = _load_wp()
    games_by = {int(g["game_id"]): g for g in wp["games"]}
    for g in games_by.values():
        g.setdefault("season", 2026)
        g.setdefault("week", 11)
    event_to_gid = {eid: gid for gid, eid in gid_to_eid.items()}
    quotes = _parse_quotes(
        cleaned, event_to_gid=event_to_gid, team_map=team_map, as_of=as_of
    )

    card_results = [
        _reprice_play(p, games_by=games_by, quotes=quotes, as_of=as_of) for p in CARD_PLAYS
    ]
    alt_results = [
        _reprice_play(p, games_by=games_by, quotes=quotes, as_of=as_of) for p in ALTERNATES
    ]

    def _print_result(label: str, res: dict[str, Any]) -> None:
        w4 = res["w11_4"]
        now_book = res["at_w11_4_book_now"]
        best = res["best_regulated"]
        print(f"\n-- {label}: {res['matchup']} side={res['side']} --")
        print(
            f"  W11-4: {w4['book_display']} {w4['line']:+g}/{w4['price']:+g}"
        )
        if now_book:
            print(
                f"  now @ W11-4 book: {now_book['book_display']} "
                f"{now_book['line']:+g}/{now_book['price']:+g} "
                f"edge={now_book['edge']:.4f} gap={now_book['gap']:.2f} "
                f"{'PASS' if now_book['gate_pass'] else 'FAIL:'+now_book['gate_fail']}"
            )
        else:
            print("  now @ W11-4 book: MISSING")
        if best:
            print(
                f"  best regulated: {best['book_display']} "
                f"{best['line']:+g}/{best['price']:+g} "
                f"ridge={best['model_line']:.2f} gap={best['gap']:.2f} "
                f"edge={best['edge']:.4f} "
                f"{'PASS' if best['gate_pass'] else 'FAIL:'+best['gate_fail']}"
            )
        else:
            print("  best regulated: NONE")

    for res in card_results:
        _print_result("CARD", res)
    for res in alt_results:
        _print_result("ALT", res)

    # Build final card: keep card plays that pass at best regulated; fill
    # from alternates (Utah then Cincy) without inventing new sides.
    final: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for res in card_results:
        best = res["best_regulated"]
        if best is not None and best.get("gate_pass"):
            final.append({**res, "selected": best, "source": "card"})
        else:
            failed.append(res)
            print(
                f"\nFAIL card play {res['matchup']} {res['side']} — "
                f"will consider alternates (no side-swap)"
            )

    alt_iter = iter(alt_results)
    while len(final) < 7:
        try:
            alt = next(alt_iter)
        except StopIteration:
            break
        best = alt["best_regulated"]
        if best is not None and best.get("gate_pass"):
            print(
                f"\nALT FILL: {alt['matchup']} {alt['side']} "
                f"{best['book_display']} {best['line']:+g}/{best['price']:+g} "
                f"edge={best['edge']:.4f} gap={best['gap']:.2f}"
            )
            final.append({**alt, "selected": best, "source": "alternate"})
        else:
            print(
                f"\nALT ALSO FAILS: {alt['matchup']} {alt['side']} "
                f"{best['gate_fail'] if best else 'no_quote'}"
            )

    final.sort(key=lambda r: float(r["selected"]["edge"]), reverse=True)

    # --- 3. Final card table ---
    print("\n=== 3. FINAL CARD (by edge) ===")
    print(
        f"{'#':>2}  {'game':40}  {'kick':16}  {'side':16}  {'book':14}  "
        f"{'line':>6}  {'W11-4':>6}  {'price':>6}  {'ridge':>7}  {'gap':>5}  {'edge':>7}"
    )
    card_table: list[dict[str, Any]] = []
    for i, res in enumerate(final, 1):
        s = res["selected"]
        w4 = res["w11_4"]
        row = {
            "rank": i,
            "game_id": int(res["play"]["game_id"]),
            "matchup": res["matchup"],
            "kickoff_et": res["kickoff_et"],
            "side": res["side"],
            "side_is": s["side_is"],
            "book": s["book"],
            "book_display": s["book_display"],
            "line": float(s["line"]),
            "w11_4_line": float(w4["line"]),
            "price": float(s["price"]),
            "ridge": float(s["model_line"]),
            "gap": float(s["gap"]),
            "edge": float(s["edge"]),
            "snapshot_event_time": s["snapshot_event_time"],
            "source": res["source"],
        }
        card_table.append(row)
        print(
            f"{i:>2}  {row['matchup'][:40]:40}  {row['kickoff_et'][:16]:16}  "
            f"{row['side'][:16]:16}  {row['book_display'][:14]:14}  "
            f"{row['line']:+6g}  {row['w11_4_line']:+6g}  {row['price']:+6g}  "
            f"{row['ridge']:+7.2f}  {row['gap']:5.2f}  {row['edge']:7.4f}"
        )

    # --- 4. Ledger ---
    print("\n=== 4. LEDGER ===")
    assert cfg.webapp.export_enabled is False
    calls = []
    for row in card_table:
        calls.append(
            {
                "game_id": str(row["game_id"]),
                "matchup": row["matchup"],
                "side": row["side"],
                "side_is": row["side_is"],
                "book": row["book"],
                "book_display": row["book_display"],
                "line": row["line"],
                "price": row["price"],
                "snapshot_event_time": row["snapshot_event_time"],
                "posted_at_utc": None,
                "edge": row["edge"],
                "model_line": row["ridge"],
                "model_market_residual_points": row["gap"],
                "stake": STAKE,
                "units": UNITS,
                "ladder_rung": "odds_api_snapshot+per_book_latest",
                "w11_4_line": row["w11_4_line"],
            }
        )
    ledger = {
        "season": 2026,
        "week": 11,
        "label": "posted_calls",
        "n": len(calls),
        "probe_book": "regulated_mixed",
        "pricing_note": (
            "W11-5 Fri line check: HFA=NONE; stake 0.014; max 7; regulated books "
            "(draftkings/fanduel/betmgm/williamhill_us/espnbet/fanatics/"
            "hardrockbet/ballybet/betrivers); best regulated line for W11-4 side; "
            "production MC seed 42+game_id; espnbet displayed as theScore Bet; "
            "posted_at_utc null (not stamped). No publish."
        ),
        "decisions": {
            "hfa": "NONE",
            "stake": STAKE,
            "max_bets": 7,
            "weekly_cap": 0.10,
            "min_edge": MIN_EDGE,
            "max_gap": MAX_GAP,
        },
        "prior_snapshot_note": "W11-4 card (2026-11-12 staged allbooks) qb_known_passing",
        "posted_calls": calls,
    }
    text = json.dumps(ledger, indent=2) + "\n"
    TRACKED.mkdir(parents=True, exist_ok=True)
    SOCIAL.parent.mkdir(parents=True, exist_ok=True)
    SOCIAL.write_text(text, encoding="utf-8")
    tracked_path = TRACKED / "posted_calls_2026_w11.json"
    tracked_path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    print("wrote", SOCIAL)
    print("wrote", tracked_path)
    print("ledger_sha256", digest)

    report = {
        "task": "W11-5",
        "export_enabled": False,
        "qb_all_starter": qb_ok,
        "qb_rows": qb_rows,
        "stage": stage_report,
        "week_predictions_source": wp_src,
        "publish_as_of": wp.get("as_of"),
        "pulled_at_utc": meta.get("pulled_at_utc"),
        "card_results": [
            {
                "matchup": r["matchup"],
                "side": r["side"],
                "w11_4": r["w11_4"],
                "at_w11_4_book_now": r["at_w11_4_book_now"],
                "best_regulated": r["best_regulated"],
                "best_regulated_passes": r["best_regulated_passes"],
                "n_quotes": r["n_quotes"],
            }
            for r in card_results
        ],
        "alternate_results": [
            {
                "matchup": r["matchup"],
                "side": r["side"],
                "w11_4": r["w11_4"],
                "at_w11_4_book_now": r["at_w11_4_book_now"],
                "best_regulated": r["best_regulated"],
                "best_regulated_passes": r["best_regulated_passes"],
                "n_quotes": r["n_quotes"],
            }
            for r in alt_results
        ],
        "failed_card_plays": [
            {"matchup": r["matchup"], "side": r["side"], "best": r["best_regulated"]}
            for r in failed
        ],
        "final_card": card_table,
        "ledger_sha256": digest,
        "ledger_paths": [
            str(SOCIAL.relative_to(ROOT)).replace("\\", "/"),
            str(tracked_path.relative_to(ROOT)).replace("\\", "/"),
        ],
    }
    (ART / "w11_5_report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print("wrote", ART / "w11_5_report.json")
    print("final_n", len(final), "failed_card", len(failed))
    assert cfg.webapp.export_enabled is False
    print("DONE export_enabled still", cfg.webapp.export_enabled)


if __name__ == "__main__":
    main()
