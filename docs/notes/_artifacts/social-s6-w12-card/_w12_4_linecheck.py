"""W12-4 — line check + final card + ledger (NO PUBLISH).

One Odds API pull (spreads × us,us2) for the 6 QB-eligible games plus the
W12-3 pre-QB survivors ranked 26–29. Hard stop if credits used > 10.
Stage snapshots. Re-price each W12-3 side at the best regulated current
line through the production MC path (seed 42 + game_id, 20_000 draws,
default key-number kernel — same as ``build_candidates_from_odds``).

Eligible plays that pass are the card and the ledger. Failures go to
``superseded_calls``. Ranks 26–29 are report-only.
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
TRACKED = ROOT / "docs" / "notes" / "_artifacts" / "w12-posted-calls"
SOCIAL = ROOT / "data" / "social" / "posted_calls_2026_w12.json"
HIST = ROOT / "data" / "webapp" / "publish_history" / "2026_w12.jsonl"
ANALYSIS = ART / "analysis.json"

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_ncaaf"
EST_CREDITS = 2  # spreads (1) × regions us,us2 (2)
CREDIT_HARD_STOP = 10
MAX_AGE_H = 6.0
MIN_EDGE = 0.045
MAX_GAP = 7.0
STAKE = 0.014
UNITS = round(STAKE / 0.005, 1)
N_DRAWS = 20_000
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

# Operator-reviewed both-STARTER games. Sides/books are the W12-3 card side.
ELIGIBLE: list[dict[str, Any]] = [
    {
        "game_id": 401856867,
        "side": "Colorado",
        "w12_3_book": "betrivers",
        "w12_3_line": 3.0,
        "w12_3_price": -105.0,
        "role": "eligible",
    },
    {
        "game_id": 401858531,
        "side": "Nebraska",
        "w12_3_book": "draftkings",
        "w12_3_line": 3.0,
        "w12_3_price": 100.0,
        "role": "eligible",
    },
    {
        "game_id": 401856866,
        "side": "Kansas",
        "w12_3_book": "betrivers",
        "w12_3_line": 6.5,
        "w12_3_price": -113.0,
        "role": "eligible",
    },
    {
        "game_id": 401856753,
        "side": "South Carolina",
        "w12_3_book": "betrivers",
        "w12_3_line": 16.0,
        "w12_3_price": -112.0,
        "role": "eligible",
        "qb_note": "operator_override_sc_sellers",
    },
    {
        "game_id": 401858307,
        "side": "Notre Dame",
        "w12_3_book": "fanduel",
        "w12_3_line": -20.5,
        "w12_3_price": -110.0,
        "role": "eligible",
    },
    {
        "game_id": 401858308,
        "side": "Stanford",
        "w12_3_book": "ballybet",
        "w12_3_line": -2.5,
        "w12_3_price": -109.0,
        "role": "eligible",
    },
]


def _watch_plays() -> list[dict[str, Any]]:
    """W12-3 pre-QB survivors ranked 26–29. Not on the card."""
    payload = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    surv = payload["gate"]["pre_qb_survivors"]
    plays: list[dict[str, Any]] = []
    for rank, row in enumerate(surv, 1):
        if rank < 26 or rank > 29:
            continue
        plays.append(
            {
                "game_id": int(row["game_id"]),
                "side": str(row["side"]),
                "w12_3_book": str(row["book"]),
                "w12_3_line": float(row["line"]),
                "w12_3_price": float(row["price"]),
                "role": "watch",
                "w12_3_rank": rank,
            }
        )
    if len(plays) != 4:
        raise RuntimeError(f"expected 4 watch plays, got {len(plays)}")
    return plays


def _load_wp() -> tuple[dict[str, Any], str]:
    lines = [ln for ln in HIST.read_text(encoding="utf-8").splitlines() if ln.strip()]
    wp = json.loads(lines[-1])
    return wp, f"data/webapp/publish_history/2026_w12.jsonl (record {len(lines)})"


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
    if not event_ids:
        raise RuntimeError("no event ids to pull")
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
    out_path = ART / f"odds_w12_4_{ts}.json"
    meta = {
        "pulled_at_utc": datetime.now(tz=UTC).isoformat(),
        "event_ids": event_ids,
        "params": {k: v for k, v in params.items() if k != "apiKey"},
        "headers": headers,
        "est_credits": EST_CREDITS,
        "n_events": len(payload) if isinstance(payload, list) else None,
        "task": "W12-4",
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
    """Same MC as ``build_candidates_from_odds``: seed 42+game_id, 20k draws."""
    gid = int(game["game_id"])
    home = str(game["home_team"])
    away = str(game["away_team"])
    mu_m = float(game["mu_margin"])
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

    rho_raw = game.get("rho")
    rho = float(rho_raw) if rho_raw is not None else 0.0
    kernel = KeyNumberKernel(offset_weights={}, n=0)
    draws = _sample_game_draws(
        mu_m=mu_m,
        sigma_m=float(game["sigma_margin"]),
        mu_t=float(game.get("mu_total") or 50.0),
        sigma_t=float(game.get("sigma_total") or 16.0),
        rho=rho,
        kernel=kernel,
        n_draws=N_DRAWS,
        seed=42 + gid,
    )
    p_model = two_way_side_prob(
        spread_cover_probs(draws, float(home_line), game_index=0, side=bet_on)  # type: ignore[arg-type]
    )
    edge_res = compute_edge(p_model, [BookPrice(book, side_px, other_px)])
    model_line_home = -mu_m
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
        "edge_raw": edge,
        "gap": round(float(residual), 2),
        "gap_raw": float(residual),
        "model_line": float(model_line),
        "age_hours": round(age_h, 3),
        "p_model": round(float(edge_res.p_model), 6),
        "gate_pass": not fail,
        "gate_fail": ",".join(fail),
        "snapshot_event_time": to_utc(last_update).isoformat(),
    }


def _pick_best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Card rule on the same side: gated pool if any, else all; max edge; book order."""
    book_rank = {b: i for i, b in enumerate(REGULATED)}
    gated = [r for r in rows if r.get("gate_pass")]
    pool = gated if gated else rows
    if not pool:
        return None
    best_edge = max(float(r["edge_raw"]) for r in pool)
    top = [r for r in pool if abs(float(r["edge_raw"]) - best_edge) < 1e-12]
    top.sort(key=lambda r: book_rank.get(str(r["book"]), 99))
    return top[0]


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
    card_book = str(play["w12_3_book"])
    at_card_book = next((r for r in rows if r["book"] == card_book), None)
    best = _pick_best(rows)
    gate_ok = bool(best is not None and best.get("gate_pass"))
    return {
        "play": play,
        "matchup": f"{game['away_team']} @ {game['home_team']}",
        "kickoff_et": _kick_et(str(game["kickoff_utc"])),
        "kickoff_utc": game["kickoff_utc"],
        "side": side,
        "w12_3": {
            "book": card_book,
            "book_display": BOOK_DISPLAY.get(card_book, card_book),
            "line": float(play["w12_3_line"]),
            "price": float(play["w12_3_price"]),
        },
        "at_w12_3_book_now": at_card_book,
        "best_regulated": best,
        "best_regulated_passes": gate_ok,
        "all_books": sorted(rows, key=lambda r: float(r["edge_raw"]), reverse=True),
        "n_quotes": len(rows),
    }


def _fmt_px(price: float) -> str:
    n = int(price) if float(price).is_integer() else price
    return f"{n:+g}" if isinstance(n, (int, float)) else str(price)


def _fail_reason(res: dict[str, Any]) -> str:
    best = res["best_regulated"]
    if best is None:
        return "no regulated quote for the W12-3 side"
    bits = []
    if "edge" in str(best.get("gate_fail")):
        bits.append(f"edge {float(best['edge']):.4f} < {MIN_EDGE}")
    if "gap" in str(best.get("gate_fail")):
        bits.append(f"gap {float(best['gap']):.2f} > {MAX_GAP}")
    if "stale" in str(best.get("gate_fail")):
        bits.append(f"stale age {best.get('age_hours')}h > {MAX_AGE_H}")
    why = "; ".join(bits) if bits else str(best.get("gate_fail") or "gate_fail")
    return (
        f"W12-4 FAIL at best regulated {best['book_display']} "
        f"{float(best['line']):+g}/{_fmt_px(float(best['price']))}: {why}. "
        "Same side kept; no swap."
    )


def _print_result(label: str, res: dict[str, Any]) -> None:
    w3 = res["w12_3"]
    now_book = res["at_w12_3_book_now"]
    best = res["best_regulated"]
    print(f"\n-- {label}: {res['matchup']} side={res['side']} {res['kickoff_et']} --")
    print(f"  W12-3: {w3['book_display']} {w3['line']:+g}/{_fmt_px(w3['price'])}")
    if now_book:
        print(
            f"  now @ W12-3 book: {now_book['book_display']} "
            f"{now_book['line']:+g}/{_fmt_px(now_book['price'])} "
            f"edge={now_book['edge']:.4f} gap={now_book['gap']:.2f} "
            f"{'PASS' if now_book['gate_pass'] else 'FAIL:' + now_book['gate_fail']}"
        )
    else:
        print("  now @ W12-3 book: MISSING")
    if best:
        print(
            f"  best regulated: {best['book_display']} "
            f"{best['line']:+g}/{_fmt_px(best['price'])} "
            f"ridge={best['model_line']:.2f} gap={best['gap']:.2f} "
            f"edge={best['edge']:.4f} "
            f"{'PASS' if best['gate_pass'] else 'FAIL:' + best['gate_fail']}"
        )
    else:
        print("  best regulated: NONE")


def _public_quote(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keep = (
        "book",
        "book_display",
        "side",
        "side_is",
        "line",
        "price",
        "edge",
        "gap",
        "model_line",
        "age_hours",
        "p_model",
        "gate_pass",
        "gate_fail",
        "snapshot_event_time",
    )
    return {k: row[k] for k in keep}


def main() -> None:
    cfg = load_config()
    assert cfg.webapp.export_enabled is False, "export must stay False"
    print("export_enabled", cfg.webapp.export_enabled)

    watch = _watch_plays()
    all_plays = ELIGIBLE + watch
    gids = [int(p["game_id"]) for p in all_plays]
    assert len(set(gids)) == 10, gids

    print("\n=== 1. ODDS PULL ===")
    gid_to_eid = _event_ids(gids)
    event_ids = sorted(set(gid_to_eid.values()))
    events, meta, raw_path = _pull(event_ids)
    as_of = to_utc(datetime.fromisoformat(str(meta["pulled_at_utc"]).replace("Z", "+00:00")))

    team_map = load_team_name_map(Path(cfg.data.team_names_path))
    cleaned, drops = _drop_stale_and_onesided(events, as_of=as_of, team_map=team_map)
    print("n_drop_records", len(drops))
    rc = Counter(d["reason"] if "stale" not in str(d["reason"]) else "stale_>6h" for d in drops)
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
        "raw_path": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
        "credits": meta.get("headers"),
        "n_games": len(gids),
        "n_event_ids": len(event_ids),
        "gids": gids,
        "n_drop_records": len(drops),
        "drop_reason_counts": dict(rc),
        "rows_fetched": len(frame),
        "rows_written": added,
        "quarantined": quarantined,
    }
    (ART / "w12_4_stage_report.json").write_text(
        json.dumps(stage_report, indent=2, default=str) + "\n", encoding="utf-8"
    )

    print("\n=== 2. REPRICE ===")
    wp, wp_src = _load_wp()
    mi = wp.get("model_identity") or {}
    print(
        "identity",
        mi.get("model_version"),
        wp.get("vintage_label"),
        "champion",
        mi.get("champion_version"),
    )
    games_by = {int(g["game_id"]): g for g in wp["games"]}
    missing_games = [g for g in gids if g not in games_by]
    if missing_games:
        raise RuntimeError(f"publish history missing games {missing_games}")
    for g in games_by.values():
        g.setdefault("season", 2026)
        g.setdefault("week", 12)
    event_to_gid = {eid: gid for gid, eid in gid_to_eid.items()}
    quotes = _parse_quotes(cleaned, event_to_gid=event_to_gid, team_map=team_map, as_of=as_of)

    eligible_results = [
        _reprice_play(p, games_by=games_by, quotes=quotes, as_of=as_of) for p in ELIGIBLE
    ]
    watch_results = [
        _reprice_play(p, games_by=games_by, quotes=quotes, as_of=as_of) for p in watch
    ]
    for res in eligible_results:
        _print_result("ELIGIBLE", res)

    print("\n=== 3. RANKS 26-29 (report only) ===")
    for res in watch_results:
        rank = res["play"]["w12_3_rank"]
        _print_result(f"RANK {rank}", res)
        best = res["best_regulated"]
        if best is not None and best.get("gate_pass"):
            away, _, home = res["matchup"].partition(" @ ")
            print(f"  QB-LIST: {res['play']['game_id']} | {away} @ {home} ({res['kickoff_et']})")
        else:
            print("  QB-LIST: (fails gates — no line)")

    final: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for res in eligible_results:
        best = res["best_regulated"]
        if best is not None and best.get("gate_pass"):
            final.append({**res, "selected": best})
        else:
            failed.append(res)
            print(f"\nFLAG FAIL {res['matchup']} {res['side']}: {_fail_reason(res)}")
    final.sort(key=lambda r: float(r["selected"]["edge_raw"]), reverse=True)

    print("\n=== 4. FINAL CARD (eligible, by edge) ===")
    card_table: list[dict[str, Any]] = []
    for i, res in enumerate(final, 1):
        s = res["selected"]
        w3 = res["w12_3"]
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
            "w12_3_line": float(w3["line"]),
            "price": float(s["price"]),
            "ridge": float(s["model_line"]),
            "gap": float(s["gap"]),
            "edge": float(s["edge"]),
            "snapshot_event_time": s["snapshot_event_time"],
            "qb_note": res["play"].get("qb_note"),
        }
        card_table.append(row)
        print(
            f"{i}  {row['matchup']}  {row['kickoff_et']}  {row['side']}  "
            f"{row['book_display']} {row['line']:+g} (W12-3 {row['w12_3_line']:+g})  "
            f"{_fmt_px(row['price'])}  ridge={row['ridge']:+.2f}  "
            f"gap={row['gap']:.2f}  edge={row['edge']:.4f}"
        )

    print("\n=== 5. LEDGER ===")
    assert cfg.webapp.export_enabled is False
    calls = []
    for row in card_table:
        call = {
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
            "w12_3_line": row["w12_3_line"],
        }
        if row.get("qb_note"):
            call["qb_note"] = row["qb_note"]
        calls.append(call)

    superseded: list[dict[str, Any]] = []
    for res in failed:
        best = res["best_regulated"] or {}
        entry: dict[str, Any] = {
            "game_id": str(res["play"]["game_id"]),
            "matchup": res["matchup"],
            "side": res["side"],
            "side_is": (best or {}).get("side_is"),
            "book": best.get("book"),
            "book_display": best.get("book_display"),
            "line": best.get("line"),
            "price": best.get("price"),
            "snapshot_event_time": best.get("snapshot_event_time"),
            "posted_at_utc": None,
            "edge": best.get("edge"),
            "model_line": best.get("model_line"),
            "model_market_residual_points": best.get("gap"),
            "w12_3_line": float(res["w12_3"]["line"]),
            "w12_3_book": res["w12_3"]["book"],
            "supersede_reason": _fail_reason(res),
        }
        if res["play"].get("qb_note"):
            entry["qb_note"] = res["play"]["qb_note"]
        superseded.append(entry)

    ledger: dict[str, Any] = {
        "season": 2026,
        "week": 12,
        "label": "posted_calls",
        "n": len(calls),
        "probe_book": "regulated_mixed",
        "pricing_note": (
            "W12-4 Thu line check: HFA=NONE; stake 0.014; regulated books "
            "(draftkings/fanduel/betmgm/williamhill_us/espnbet/fanatics/"
            "hardrockbet/ballybet/betrivers); best regulated line for the W12-3 "
            "side; production MC seed 42+game_id n_draws=20000; gates edge>=4.5% "
            "gap<=7.0; espnbet displayed as theScore Bet; posted_at_utc null "
            "(not stamped). Ranks 26-29 not on the card. No publish."
        ),
        "decisions": {
            "hfa": "NONE",
            "stake": STAKE,
            "max_bets": 7,
            "weekly_cap": 0.10,
            "min_edge": MIN_EDGE,
            "max_gap": MAX_GAP,
        },
        "prior_snapshot_note": "W12-3 Saturday card probe (2026-11-19 all-books) pre-QB top 25",
        "posted_calls": calls,
    }
    if superseded:
        ledger["superseded_calls"] = superseded
    text = json.dumps(ledger, indent=2) + "\n"
    TRACKED.mkdir(parents=True, exist_ok=True)
    SOCIAL.parent.mkdir(parents=True, exist_ok=True)
    SOCIAL.write_text(text, encoding="utf-8")
    tracked_path = TRACKED / "posted_calls_2026_w12.json"
    tracked_path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    print("wrote", SOCIAL)
    print("wrote", tracked_path)
    print("ledger_sha256", digest)
    print("n_posted", len(calls), "n_superseded", len(superseded))

    def _slim(res: dict[str, Any]) -> dict[str, Any]:
        return {
            "game_id": int(res["play"]["game_id"]),
            "rank": res["play"].get("w12_3_rank"),
            "matchup": res["matchup"],
            "kickoff_et": res["kickoff_et"],
            "side": res["side"],
            "w12_3": res["w12_3"],
            "at_w12_3_book_now": _public_quote(res["at_w12_3_book_now"]),
            "best_regulated": _public_quote(res["best_regulated"]),
            "best_regulated_passes": res["best_regulated_passes"],
            "n_quotes": res["n_quotes"],
            "qb_note": res["play"].get("qb_note"),
        }

    report = {
        "task": "W12-4",
        "export_enabled": False,
        "stage": stage_report,
        "week_predictions_source": wp_src,
        "publish_as_of": wp.get("as_of"),
        "model_version": mi.get("model_version"),
        "vintage_label": wp.get("vintage_label"),
        "champion_version": mi.get("champion_version"),
        "pulled_at_utc": meta.get("pulled_at_utc"),
        "eligible": [_slim(r) for r in eligible_results],
        "watch_26_29": [_slim(r) for r in watch_results],
        "final_card": card_table,
        "superseded": superseded,
        "ledger_sha256": digest,
        "ledger_paths": [
            "data/social/posted_calls_2026_w12.json",
            "docs/notes/_artifacts/w12-posted-calls/posted_calls_2026_w12.json",
        ],
    }
    (ART / "w12_4_report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print("wrote", ART / "w12_4_report.json")
    assert cfg.webapp.export_enabled is False
    print("DONE export_enabled still", cfg.webapp.export_enabled)


if __name__ == "__main__":
    main()
