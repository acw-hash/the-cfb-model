"""W7-7 — fresh-odds ATS recheck + thread rebuild (HFA=NONE, spreads only)."""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))

from ncaa_quant.betting.edges import BookPrice, compute_edge  # noqa: E402
from ncaa_quant.config import load_config, load_secrets  # noqa: E402
from ncaa_quant.data.storage import ParquetStore  # noqa: E402
from ncaa_quant.distribution.bivariate import assemble_bivariate  # noqa: E402
from ncaa_quant.distribution.key_numbers import KeyNumberKernel  # noqa: E402
from ncaa_quant.distribution.simulate import (  # noqa: E402
    sample_joint,
    spread_cover_probs,
    two_way_side_prob,
)
from ncaa_quant.ingestion.odds_api import BASE_URL, SPORT_KEY, normalize_team_name  # noqa: E402
from ncaa_quant.ingestion.teams import load_team_name_map  # noqa: E402
from ncaa_quant.utils.timeutils import to_utc  # noqa: E402
from _s6_w7_card import (  # noqa: E402
    FIXED_STAKE,
    HFA_MODE,
    MAX_BETS,
    MIN_MODEL_MARKET_AGREEMENT,
    PUBLIC_MIN_EDGE_DEFAULT,
    UNIT_FRACTION,
    _kickoff_et,
    _kickoff_ok,
    _load_slate,
    _side_is,
)

ART = Path(__file__).resolve().parent
OUT_THREAD = ROOT / "out" / "social" / "2026_w7" / "thread_w7.md"
TRACKED_THREAD = ROOT / "docs" / "notes" / "_artifacts" / "w7-posted-calls" / "thread_w7.md"
CANDIDATES = ART / "candidates_w7.json"
SOCIAL_LEDGER = ROOT / "data" / "social" / "posted_calls_2026_w7.json"
TRACKED_LEDGER = ROOT / "docs" / "notes" / "_artifacts" / "w7-posted-calls" / "posted_calls_2026_w7.json"

REGULATED = frozenset(
    {
        "draftkings",
        "fanduel",
        "betmgm",
        "williamhill_us",
        "espnbet",
        "fanatics",
        "betrivers",
        "hardrockbet",
        "ballybet",
    }
)
BOOK_LABEL = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "williamhill_us": "Caesars",
    "espnbet": "ESPN BET",
    "fanatics": "Fanatics",
    "betrivers": "BetRivers",
    "hardrockbet": "Hard Rock Bet",
    "ballybet": "Bally Bet",
}
MAX_AGE_H = 6.0
EST_CREDITS = 2
CREDIT_HARD_STOP = 4

# Posted / prior-approved ATS card + radar (from W7-5 approval).
OLD_PLAYS: list[dict[str, Any]] = [
    {
        "game_id": "401856722",
        "side": "Kentucky",
        "role": "card",
        "old_book": "fanduel",
        "old_line": 6.5,
        "old_price": 100.0,
        "old_edge": 0.1657,
    },
    {
        "game_id": "401866444",
        "side": "Eastern Michigan",
        "role": "card",
        "old_book": "espnbet",
        "old_line": 6.5,
        "old_price": -105.0,
        "old_edge": 0.1364,
    },
    {
        "game_id": "401856829",
        "side": "West Virginia",
        "role": "card",
        "old_book": "fanduel",
        "old_line": -1.5,
        "old_price": -108.0,
        "old_edge": 0.1325,
    },
    {
        "game_id": "401864521",
        "side": "New Mexico",
        "role": "card",
        "old_book": "fanduel",
        "old_line": -14.5,
        "old_price": -110.0,
        "old_edge": 0.1324,
    },
    {
        "game_id": "401856828",
        "side": "Texas Tech",
        "role": "card",
        "old_book": "espnbet",
        "old_line": -14.5,
        "old_price": -105.0,
        "old_edge": 0.1223,
    },
    {
        "game_id": "401864522",
        "side": "Wyoming",
        "role": "card",
        "old_book": "ballybet",
        "old_line": -8.0,
        "old_price": -107.0,
        "old_edge": 0.1220,
    },
    {
        "game_id": "401866442",
        "side": "Buffalo",
        "role": "radar",
        "old_book": "fanduel",
        "old_line": -6.5,
        "old_price": -105.0,
        "old_edge": 0.1503,
    },
]


def twitter_len(text: str) -> int:
    url_re = re.compile(r"https?://\S+")
    n = 0
    pos = 0
    for m in url_re.finditer(text):
        n += len(text[pos : m.start()])
        n += 23
        pos = m.end()
    n += len(text[pos:])
    # ▸ and emoji count as 2 (rough X weighting used in prior threads)
    n += text.count("\u25b8")  # extra 1 beyond len (already counted as 1)
    # emoji football / point-down already in text as single codepoints; add +1 each
    for ch in ("\U0001f3c8", "\U0001f447"):
        n += text.count(ch)
    return n


def fmt_line(side: str, line: float) -> str:
    if line > 0:
        return f"{side} +{line:g}"
    if line < 0:
        return f"{side} {line:g}"
    return f"{side} PK"


def fmt_price(price: float) -> str:
    p = float(price)
    if p > 0:
        return f"+{p:g}"
    return f"{p:g}"


def _sample_draws(game: dict[str, Any], *, n_draws: int = 20_000) -> Any:
    mu_m = float(game["mu_margin"])
    sig_m = float(game["sigma_margin"])
    mu_t = float(game.get("mu_total") or 50.0)
    sig_t = float(game.get("sigma_total") or 14.0)
    rho = float(game["rho"]) if game.get("rho") is not None else 0.0
    kernel = KeyNumberKernel(offset_weights={}, n=0)
    params = assemble_bivariate([mu_m], [sig_m], [mu_t], [sig_t], rho=rho)
    seed = 42 + int(game["game_id"])
    return sample_joint(params, kernel=kernel, n_draws=n_draws, seed=seed)


def _price_side(
    *,
    game: dict[str, Any],
    side_team: str,
    book: str,
    home_line: float,
    home_px: float,
    away_px: float,
    draws: Any,
    last_update: datetime,
    as_of: datetime,
) -> dict[str, Any]:
    home = str(game["home_team"])
    away = str(game["away_team"])
    bet_on = "home" if side_team == home else "away"
    side_px = home_px if bet_on == "home" else away_px
    other_px = away_px if bet_on == "home" else home_px
    p_model = two_way_side_prob(
        spread_cover_probs(draws, float(home_line), game_index=0, side=bet_on)  # type: ignore[arg-type]
    )
    edge_res = compute_edge(p_model, [BookPrice(book, side_px, other_px)])
    model_line_home = -float(game["mu_margin"])
    residual = abs(model_line_home - float(home_line))
    market_line = float(home_line) if bet_on == "home" else -float(home_line)
    model_line = model_line_home if bet_on == "home" else -model_line_home
    age_h = (to_utc(as_of) - to_utc(last_update)).total_seconds() / 3600.0
    fail: list[str] = []
    if float(edge_res.edge) < PUBLIC_MIN_EDGE_DEFAULT:
        fail.append("edge")
    if residual > MIN_MODEL_MARKET_AGREEMENT:
        fail.append("resid")
    if age_h > MAX_AGE_H:
        fail.append("stale")
    return {
        "book": book,
        "side": side_team,
        "side_is": _side_is(game, side_team),
        "line": market_line,
        "price": float(edge_res.side_american),
        "last_update": to_utc(last_update).isoformat(),
        "edge": round(float(edge_res.edge), 4),
        "resid": round(float(residual), 2),
        "model_line": model_line,
        "age_hours": round(age_h, 3),
        "p_win": float(edge_res.p_model),
        "expected_value": round(float(edge_res.expected_value), 4),
        "gate_pass": not fail,
        "gate_fail": ",".join(fail),
    }


def _better_number_key(line: float) -> float:
    return float(line)


def _better_price_key(price: float) -> float:
    if price >= 100:
        return 1.0 + price / 100.0
    return 1.0 + 100.0 / abs(price)


def _pick_best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    elig = [r for r in rows if r.get("gate_pass") and r["book"] in REGULATED]
    if not elig:
        return None
    # Prefer higher edge, then better number, then better price, then stable
    # book order (matches W7-3 REGULATED preference for ties).
    book_rank = {b: i for i, b in enumerate(sorted(REGULATED))}
    elig.sort(
        key=lambda r: (
            float(r["edge"]),
            _better_number_key(float(r["line"])),
            _better_price_key(float(r["price"])),
            -book_rank.get(str(r["book"]), 99),
        ),
        reverse=True,
    )
    return elig[0]


def _resolve_all_event_ids(gids: set[int]) -> dict[str, str]:
    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        cw = store.read("odds_cfbd_game_crosswalk", filters={"season": 2026})
    sub = cw.loc[
        (cw["game_id"].astype("Int64").isin(list(gids)))
        & (cw["match_status"].astype(str) == "matched")
    ].copy()
    if "ingested_at" in sub.columns:
        sub["ingested_at"] = pd.to_datetime(sub["ingested_at"], utc=True)
        sub = sub.sort_values("ingested_at").groupby("game_id", sort=False).tail(1)
    return {str(int(r["game_id"])): str(r["odds_event_id"]) for _, r in sub.iterrows()}


def _pull_odds(
    event_ids: list[str],
    *,
    reuse_path: Path | None = None,
) -> tuple[list[Any], dict[str, Any], Path]:
    if reuse_path is not None and reuse_path.is_file():
        blob = json.loads(reuse_path.read_text(encoding="utf-8"))
        print(f"REUSING saved odds (0 credits): {reuse_path}")
        return blob["events"], blob["meta"], reuse_path
    if EST_CREDITS > CREDIT_HARD_STOP:
        raise RuntimeError(f"hard stop: est {EST_CREDITS} > {CREDIT_HARD_STOP}")
    secrets = load_secrets()
    key = secrets.odds_api_key.get_secret_value()
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
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = ART / f"odds_allbooks_{ts}.json"
    meta = {
        "pulled_at_utc": datetime.now(tz=UTC).isoformat(),
        "event_ids": event_ids,
        "params": {k: v for k, v in params.items() if k != "apiKey"},
        "headers": headers,
        "est_credits": EST_CREDITS,
        "n_events": len(payload) if isinstance(payload, list) else None,
    }
    out_path.write_text(
        json.dumps({"meta": meta, "events": payload}, indent=2) + "\n",
        encoding="utf-8",
    )
    print("credits_used", headers["x-requests-last"])
    print("credits_remaining", headers["x-requests-remaining"])
    print("saved", out_path)
    used = int(headers["x-requests-last"] or 0)
    if used > CREDIT_HARD_STOP:
        raise RuntimeError(f"hard stop after call: used {used} > {CREDIT_HARD_STOP}")
    return payload, meta, out_path


def _parse_quotes(
    events: list[Any],
    *,
    event_to_gid: dict[str, str],
    team_map: Any,
    as_of: datetime,
) -> dict[str, dict[str, dict[str, Any]]]:
    cutoff = to_utc(as_of) - timedelta(hours=MAX_AGE_H)
    by_gid: dict[str, dict[str, dict[str, Any]]] = {}
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
                "away_px": sides[away][1],
                "last_update": last_update,
                "home": home,
                "away": away,
            }
    return by_gid


def _qb_map(as_of: datetime) -> dict[tuple[str, str], dict[str, Any]]:
    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        qb = store.read("qb_status", filters={"season": 2026})
        teams = store.read("teams", filters={"season": 2026})
        games = store.read("games", filters={"season": 2026, "week": 7})
    tid_to_name = {int(r["team_id"]): str(r["school"]) for _, r in teams.iterrows()}
    qb = qb.copy()
    qb["event_time"] = pd.to_datetime(qb["event_time"], utc=True)
    as_ts = pd.Timestamp(to_utc(as_of))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for _, gr in games.iterrows():
        gid = str(int(gr["game_id"]))
        for tid in (int(gr["away_team_id"]), int(gr["home_team_id"])):
            school = tid_to_name.get(tid, str(tid))
            sub = qb.loc[
                (qb["game_id"].astype("Int64") == int(gid))
                & (qb["team_id"].astype(int) == tid)
                & (qb["event_time"] <= as_ts)
            ]
            if sub.empty:
                continue
            latest = sub.sort_values("event_time").iloc[-1]
            out[(gid, school)] = {
                "status": str(latest["status"]),
                "event_time": latest["event_time"].isoformat(),
            }
    return out


def _qb_pair(
    gid: str, game: dict[str, Any], qb: dict[tuple[str, str], dict[str, Any]]
) -> tuple[str | None, str | None, str]:
    away, home = str(game["away_team"]), str(game["home_team"])
    a = qb.get((gid, away))
    h = qb.get((gid, home))
    sa = a["status"] if a else None
    sh = h["status"] if h else None
    if sa is None or sh is None:
        return sa, sh, "qb_missing"
    if sa == "starter" and sh == "starter":
        return sa, sh, "ok"
    bits = []
    if sa != "starter":
        bits.append(f"{away}={sa}")
    if sh != "starter":
        bits.append(f"{home}={sh}")
    return sa, sh, "qb_" + ",".join(bits)


def _status_change(old: dict[str, Any], new: dict[str, Any] | None) -> str:
    if new is None:
        return f"DROPPED (no regulated book passes: check edge/resid/stale)"
    same_book = old["old_book"] == new["book"]
    same_line = abs(float(old["old_line"]) - float(new["line"])) < 1e-9
    same_price = abs(float(old["old_price"]) - float(new["price"])) < 1e-9
    if same_book and same_line and same_price:
        return "SAME"
    if same_book and not same_line:
        return "NEW LINE"
    if not same_book:
        return "NEW BOOK"
    if same_book and same_line and not same_price:
        return "NEW LINE"  # price-only still a line/quote change
    return "NEW LINE"


def _ridge_post_lines(side: str, model_line: float, market_line: float) -> tuple[str, str]:
    """Return (ridge_bullet, closing_sentence) per W7-7 rules."""
    ridge = fmt_line(side, round(float(model_line), 1))
    gap = round(abs(float(model_line) - float(market_line)), 1)
    # Market underdog (positive line) but Ridge has them favored (negative model line)
    if float(market_line) > 0 and float(model_line) < 0:
        closing = (
            f"Ridge has {side} as a slight favorite. That's {gap:g} points off the market."
        )
    else:
        closing = f"Model has this {gap:g} points off the market."
    return ridge, closing


def main() -> None:
    assert HFA_MODE == "NONE", HFA_MODE
    as_of = datetime.now(tz=UTC)
    print(f"as_of={as_of.isoformat()} HFA_MODE={HFA_MODE}")
    print(
        f"gates: edge>={PUBLIC_MIN_EDGE_DEFAULT} resid<={MIN_MODEL_MARKET_AGREEMENT} "
        f"age<={MAX_AGE_H}h stake={FIXED_STAKE}"
    )

    wp, src = _load_slate()
    games = list(wp["games"])
    for g in games:
        g.setdefault("season", wp.get("season", 2026))
        g.setdefault("week", wp.get("week", 7))
    games_by = {str(g["game_id"]): g for g in games}
    print(f"slate={src} n={len(games)}")

    gids = {int(g["game_id"]) for g in games}
    gid_to_event = _resolve_all_event_ids(gids)
    missing = sorted(gids - {int(k) for k in gid_to_event})
    print(f"crosswalk matched={len(gid_to_event)}/{len(gids)} missing={missing}")
    event_to_gid = {v: k for k, v in gid_to_event.items()}

    reuse: Path | None = None
    for p in sorted(ART.glob("odds_allbooks_*.json"), reverse=True):
        try:
            meta0 = json.loads(p.read_text(encoding="utf-8")).get("meta") or {}
        except json.JSONDecodeError:
            continue
        n_ev = len(meta0.get("event_ids") or [])
        # Prefer the W7-7 full-slate spreads pull (>=50 events).
        if n_ev >= 50 and "h2h" not in str((meta0.get("params") or {}).get("markets", "spreads")):
            reuse = p
            break
    events, meta, raw_path = _pull_odds(list(gid_to_event.values()), reuse_path=reuse)
    as_of = to_utc(datetime.fromisoformat(str(meta["pulled_at_utc"]).replace("Z", "+00:00")))
    print(f"snapshot_time={as_of.isoformat()}")

    team_map = load_team_name_map(ROOT / "configs" / "team_names.yaml")
    quotes = _parse_quotes(events, event_to_gid=event_to_gid, team_map=team_map, as_of=as_of)
    qb = _qb_map(as_of)

    # ---- §2 recheck old plays ----
    print("\n=== §2 RECHECK POSTED/APPROVED PLAYS ===")
    recheck: list[dict[str, Any]] = []
    for play in OLD_PLAYS:
        gid = play["game_id"]
        side = play["side"]
        game = games_by[gid]
        draws = _sample_draws(game)
        book_map = quotes.get(gid, {})
        rows: list[dict[str, Any]] = []
        print(f"\n======== {gid} {game['away_team']} @ {game['home_team']} OUR={side} ========")
        print(
            f"{'book':16} {'line':>7} {'price':>7} {'last_update':24} "
            f"{'edge':>7} {'resid':>6} {'gate':5} BEST?"
        )
        for book, q in sorted(book_map.items()):
            row = _price_side(
                game=game,
                side_team=side,
                book=book,
                home_line=q["home_line"],
                home_px=q["home_px"],
                away_px=q["away_px"],
                draws=draws,
                last_update=q["last_update"],
                as_of=as_of,
            )
            rows.append(row)
        best = _pick_best(rows)
        for row in sorted(rows, key=lambda r: float(r["edge"]), reverse=True):
            is_best = best is not None and row is best
            print(
                f"{row['book']:16} {row['line']:>+7g} {row['price']:>+7g} "
                f"{str(row['last_update'])[:24]:24} "
                f"{row['edge']:>7.4f} {row['resid']:>6.2f} "
                f"{'PASS' if row['gate_pass'] else 'FAIL':5} "
                f"{'BEST' if is_best else ''}"
            )
        if best is None:
            fails = sorted({r["gate_fail"] for r in rows if r["gate_fail"]}) or ["no_quotes"]
            status = f"DROPPED (gates: {','.join(fails)})"
        else:
            status = _status_change(play, best)
        print(
            f"SUMMARY: old {play['old_book']} {play['old_line']:+g}/{play['old_price']:+g} "
            f"e={play['old_edge']:.4f} -> "
            + (
                f"new {best['book']} {best['line']:+g}/{best['price']:+g} e={best['edge']:.4f}"
                if best
                else "new NONE"
            )
            + f" | {status}"
        )
        recheck.append({"play": play, "rows": rows, "best": best, "status": status})

    # ---- §3 full slate re-rank ----
    print("\n=== §3 FULL SLATE RE-RANK ===")
    for team in ("Tennessee", "Southern Miss", "Massachusetts", "Air Force"):
        hits = [(k, v) for k, v in qb.items() if k[1] == team]
        if not hits:
            print(f"qb_status {team}: (no row)")
        else:
            for (gid, _), v in hits:
                print(f"qb_status {team} game={gid}: {v['status']} @ {v['event_time']}")

    ranked: list[dict[str, Any]] = []
    for g in games:
        gid = str(g["game_id"])
        if not _kickoff_ok(str(g["kickoff_utc"])):
            continue
        if g.get("mu_margin") is None or g.get("sigma_margin") is None:
            continue
        book_map = quotes.get(gid, {})
        if not book_map:
            continue
        draws = _sample_draws(g)
        candidates: list[dict[str, Any]] = []
        for book, q in book_map.items():
            for side in (str(g["away_team"]), str(g["home_team"])):
                candidates.append(
                    _price_side(
                        game=g,
                        side_team=side,
                        book=book,
                        home_line=q["home_line"],
                        home_px=q["home_px"],
                        away_px=q["away_px"],
                        draws=draws,
                        last_update=q["last_update"],
                        as_of=as_of,
                    )
                )
        best = _pick_best(candidates)
        if best is None:
            continue
        sa, sh, qb_note = _qb_pair(gid, g, qb)
        # Air Force own-team backup: exclude from radar AND card
        own_backup = False
        if sa == "backup" and best["side"] == str(g["away_team"]):
            own_backup = True
        if sh == "backup" and best["side"] == str(g["home_team"]):
            own_backup = True
        # Also exclude if Air Force is backup on the game at all for radar (user: Air Force excluded)
        air_force_backup = (sa == "backup" and str(g["away_team"]) == "Air Force") or (
            sh == "backup" and str(g["home_team"]) == "Air Force"
        )
        row = {
            "game_id": gid,
            "matchup": f"{g['away_team']} @ {g['home_team']}",
            "kickoff_et": _kickoff_et(str(g["kickoff_utc"])),
            "kickoff_utc": g["kickoff_utc"],
            "side": best["side"],
            "side_is": best["side_is"],
            "book": best["book"],
            "line": best["line"],
            "price": best["price"],
            "edge": best["edge"],
            "resid": best["resid"],
            "model_line": best["model_line"],
            "snapshot_event_time": best["last_update"],
            "qb_away": sa,
            "qb_home": sh,
            "qb_note": qb_note,
            "qb_ok": qb_note == "ok",
            "own_backup": own_backup,
            "air_force_backup": air_force_backup,
            "stake": FIXED_STAKE,
            "units": round(FIXED_STAKE / UNIT_FRACTION, 1),
            "expected_value": best["expected_value"],
            "p_win": best["p_win"],
            "american_odds": best["price"],
        }
        ranked.append(row)

    ranked.sort(key=lambda r: float(r["edge"]), reverse=True)
    card_pool = [r for r in ranked if r["qb_ok"]]
    # Radar: QB-only fails, max 2, Air Force excluded (own-team backup)
    radar_pool = [
        r
        for r in ranked
        if (not r["qb_ok"])
        and (not r["air_force_backup"])
        and (not r["own_backup"])
        and r["qb_note"].startswith("qb_")
    ]
    marked = card_pool[:MAX_BETS]
    next4 = card_pool[MAX_BETS : MAX_BETS + 4]
    radar = radar_pool[:2]

    print("\n=== TOP 6 (QB starters both sides) ===")
    for i, r in enumerate(marked, 1):
        print(
            f"*{i}. {r['game_id']} | {r['matchup']} | side={r['side']} ({r['side_is']}) | "
            f"{r['book']} {r['line']:+g}/{r['price']:+g} | edge={r['edge']:.4f} resid={r['resid']:.2f}"
        )
    print("\n=== NEXT 4 ===")
    for i, r in enumerate(next4, 1):
        print(
            f"+{i}. {r['game_id']} | {r['matchup']} | side={r['side']} | "
            f"{r['book']} {r['line']:+g}/{r['price']:+g} | edge={r['edge']:.4f} resid={r['resid']:.2f}"
        )
    print("\n=== RADAR (max 2, Air Force excluded) ===")
    for r in radar:
        print(
            f"  {r['game_id']} | {r['matchup']} | side={r['side']} | "
            f"{r['book']} {r['line']:+g}/{r['price']:+g} | edge={r['edge']:.4f} | {r['qb_note']}"
        )

    old_card_ids = [p["game_id"] for p in OLD_PLAYS if p["role"] == "card"]
    new_card_ids = [r["game_id"] for r in marked]
    print("\n=== TOP6 vs PRIOR APPROVED ===")
    print("prior:", old_card_ids)
    print("new  :", new_card_ids)
    dropped = [g for g in old_card_ids if g not in new_card_ids]
    added = [g for g in new_card_ids if g not in old_card_ids]
    if dropped or added:
        print(f"CHANGED: dropped={dropped} added={added}")
        for gid in dropped:
            hit = next((r for r in ranked if r["game_id"] == gid), None)
            if hit is None:
                print(f"  {gid}: no longer gate-pass at any regulated book")
            elif not hit["qb_ok"]:
                print(f"  {gid}: QB fail ({hit['qb_note']}) — out of card pool")
            else:
                print(
                    f"  {gid}: still qualifies but edged out "
                    f"(edge={hit['edge']:.4f} rank below top 6)"
                )
        for gid in added:
            hit = next(r for r in marked if r["game_id"] == gid)
            print(f"  +{gid}: {hit['matchup']} side={hit['side']} edge={hit['edge']:.4f}")
    else:
        print("SAME top-6 game_ids (book/line may still differ — see §2)")

    # ---- §4 thread ----
    print("\n=== §4 THREAD ===")
    k = len(marked)
    posts: list[str] = []
    posts.append(
        "ridge's week 7 card \U0001f3c8\n"
        "\n"
        f"ran all 62 games, these are the {k} where it disagrees with the market the most. all 1u\n"
        "\n"
        "15-9-1 on the season. line and book on each play \U0001f447"
    )
    for i, r in enumerate(marked, 1):
        mkt = f"{fmt_line(r['side'], float(r['line']))} {fmt_price(float(r['price']))}"
        book = BOOK_LABEL.get(str(r["book"]), str(r["book"]))
        ridge, closing = _ridge_post_lines(r["side"], float(r["model_line"]), float(r["line"]))
        # kickoff day label
        ko = datetime.fromisoformat(str(r["kickoff_utc"]).replace("Z", "+00:00")).astimezone(
            __import__("zoneinfo").ZoneInfo("America/New_York")
        )
        day = ko.strftime("%a")
        posts.append(
            f"Best Bet #{i}\n"
            "\n"
            f"{r['matchup']} ({day})\n"
            f"\u25b8 Market: {mkt} ({book})\n"
            f"\u25b8 Ridge: {ridge}\n"
            f"\u25b8 1u\n"
            "\n"
            f"{closing}"
        )
    if radar:
        bits = ["On the radar (not on the card)\n"]
        for r in radar:
            mkt = f"{fmt_line(r['side'], float(r['line']))} {fmt_price(float(r['price']))}"
            book = BOOK_LABEL.get(str(r["book"]), str(r["book"]))
            # human qb note
            note = r["qb_note"]
            if "Massachusetts=unknown" in note or "Massachusetts=unknown" in note:
                why = "UMass starter unconfirmed"
            elif "backup" in note:
                why = note.replace("qb_", "").replace(",", "; ")
            else:
                why = note.replace("qb_", "")
            bits.append(f"\u25b8 {mkt} ({book}) - would qualify; {why}")
        bits.append("")
        bits.append("model doesn't know about the QB news; the market might.")
        posts.append("\n".join(bits))
    posts.append(
        "what ridge actually is:\n"
        "\n"
        "\u25b8 rates every offense + defense off play-by-play EPA, not final scores\n"
        "\u25b8 kalman filter updates them weekly, uncertainty included\n"
        "\u25b8 projects a margin + range, compares to the vig-free market\n"
        "\u25b8 only plays 4.5%+ edges with both QBs confirmed"
    )
    posts.append(
        "that's the card\n"
        "\n"
        "want the model's number on a game that's not here? reply with the matchup and i'll send it\n"
        "\n"
        "every forecast and every result is on the site: https://the-cfb-model.vercel.app"
    )

    thread_lines: list[str] = []
    n = len(posts)
    for i, post in enumerate(posts, 1):
        clen = twitter_len(post)
        flag = " **OVER 280**" if clen > 280 else ""
        thread_lines.append(f"--- POST {i}/{n} ({clen} chars){flag} ---")
        thread_lines.append(post)
        thread_lines.append("")
        print(f"--- POST {i}/{n} ({clen} chars){flag} ---")
        print(post)
        print()
    OUT_THREAD.parent.mkdir(parents=True, exist_ok=True)
    OUT_THREAD.write_text("\n".join(thread_lines), encoding="utf-8")
    TRACKED_THREAD.parent.mkdir(parents=True, exist_ok=True)
    TRACKED_THREAD.write_text("\n".join(thread_lines), encoding="utf-8")
    print("wrote", OUT_THREAD)
    print("wrote", TRACKED_THREAD)

    # ---- §5 ledger ----
    print("\n=== §5 LEDGER ===")
    now = datetime.now(tz=UTC).isoformat()
    # Load prior candidates to supersede
    prior_cands = []
    if CANDIDATES.is_file():
        prior_cands = json.loads(CANDIDATES.read_text(encoding="utf-8"))

    new_cands: list[dict[str, Any]] = []
    for r in marked:
        new_cands.append(
            {
                "game_id": r["game_id"],
                "market": "side",
                "side_team": r["side"],
                "market_line": r["line"],
                "model_line": r["model_line"],
                "edge": r["edge"],
                "expected_value": r["expected_value"],
                "american_odds": r["american_odds"],
                "p_win": r["p_win"],
                "stake_fraction": FIXED_STAKE,
                "model_market_residual_points": r["resid"],
                "book": r["book"],
                "snapshot_event_time": r["snapshot_event_time"],
            }
        )
    # Keep prior rows as superseded entries (do not delete)
    for old in prior_cands:
        if old.get("superseded_by"):
            new_cands.append(old)
            continue
        # If this game_id is still on the card, nest prior into history via superseded fields on new row
        match = next((c for c in new_cands if c["game_id"] == old["game_id"] and "superseded_by" not in c), None)
        if match is not None:
            match["superseded"] = {
                "book": old.get("book"),
                "market_line": old.get("market_line"),
                "american_odds": old.get("american_odds"),
                "edge": old.get("edge"),
                "model_line": old.get("model_line"),
                "snapshot_event_time": old.get("snapshot_event_time"),
                "superseded_by": "W7-7",
                "superseded_at": now,
            }
        else:
            new_cands.append(
                {
                    **old,
                    "superseded_by": "W7-7",
                    "superseded_at": now,
                    "superseded_reason": "W7-7 fresh-odds card rebuild; prior row retained.",
                }
            )
    CANDIDATES.write_text(json.dumps(new_cands, indent=2) + "\n", encoding="utf-8")
    print("wrote", CANDIDATES)

    def _update_ledger(path: Path) -> None:
        if path.is_file():
            blob = json.loads(path.read_text(encoding="utf-8"))
        else:
            blob = {"season": 2026, "week": 7, "label": "posted_calls", "posted_calls": [], "conditional": []}
        prior_posted = list(blob.get("posted_calls") or [])
        new_posted: list[dict[str, Any]] = []
        for r in marked:
            prior = next((p for p in prior_posted if str(p.get("game_id")) == r["game_id"]), None)
            row = {
                "game_id": r["game_id"],
                "matchup": r["matchup"],
                "side": r["side"],
                "side_is": r["side_is"],
                "book": r["book"],
                "line": r["line"],
                "price": r["price"],
                "snapshot_event_time": r["snapshot_event_time"],
                "posted_at_utc": None,
                "edge": r["edge"],
                "stake": FIXED_STAKE,
                "units": r["units"],
                "ladder_rung": "odds_api_live_sweep",
                "model_line": r["model_line"],
                "model_market_residual_points": r["resid"],
            }
            if prior is not None:
                # Keep prior as superseded (and any nested history)
                row["superseded"] = {
                    "book": prior.get("book"),
                    "line": prior.get("line"),
                    "price": prior.get("price"),
                    "edge": prior.get("edge"),
                    "snapshot_event_time": prior.get("snapshot_event_time"),
                    "ladder_rung": prior.get("ladder_rung"),
                    "prior_superseded": prior.get("superseded"),
                    "prior_w7_5_superseded": prior.get("w7_5_superseded"),
                    "by": "W7-7",
                    "at": now,
                }
            new_posted.append(row)
        # Prior posted games not on new card → mark superseded, keep in file under superseded_calls
        superseded_calls = list(blob.get("superseded_calls") or [])
        for p in prior_posted:
            if str(p.get("game_id")) in {r["game_id"] for r in marked}:
                continue
            if p.get("status", "").startswith("superseded") or p.get("w7_5_superseded"):
                superseded_calls.append(p)
            else:
                superseded_calls.append(
                    {
                        **p,
                        "status": "superseded_w7_7",
                        "w7_7_superseded": {
                            "by": "W7-7",
                            "at": now,
                            "reason": "Not in W7-7 top-6 after fresh odds recheck.",
                        },
                    }
                )

        # Buffalo stays in conditional
        conditional = []
        for r in radar:
            prior_c = next(
                (c for c in (blob.get("conditional") or []) if str(c.get("game_id")) == r["game_id"]),
                None,
            )
            entry = {
                "game_id": r["game_id"],
                "matchup": r["matchup"],
                "side": r["side"],
                "status": "not_graded_qb_unconfirmed",
                "qb_note": r["qb_note"],
                "book": r["book"],
                "line": r["line"],
                "price": r["price"],
                "edge": r["edge"],
                "snapshot_event_time": r["snapshot_event_time"],
            }
            if prior_c is not None:
                entry["superseded"] = {
                    "book": prior_c.get("book"),
                    "line": prior_c.get("line"),
                    "price": prior_c.get("price"),
                    "edge": prior_c.get("edge"),
                    "by": "W7-7",
                    "at": now,
                }
            conditional.append(entry)
        # Keep non-radar prior conditionals as superseded notes? User said Buffalo stays.
        # Drop Wisconsin if no longer radar — retain under superseded_conditionals
        prior_cond = blob.get("conditional") or []
        superseded_cond = list(blob.get("superseded_conditionals") or [])
        for c in prior_cond:
            if str(c.get("game_id")) in {r["game_id"] for r in radar}:
                continue
            superseded_cond.append({**c, "status": "superseded_w7_7", "superseded_at": now})

        blob.update(
            {
                "n": len(new_posted),
                "probe_book": "mixed",
                "pricing_note": (
                    "W7-7 fresh-odds ATS recheck: HFA=NONE; among regulated books with "
                    "edge>=0.045, |resid|<=7, snap<=6h, pick highest edge; tie-break better "
                    "number then better price. Stake fixed 0.015. QB starter both sides for "
                    "card; radar=QB-only fails max 2 (Air Force own-backup excluded)."
                ),
                "posted_calls": new_posted,
                "conditional": conditional,
                "superseded_calls": superseded_calls,
                "superseded_conditionals": superseded_cond,
                "sweep_meta": {
                    "raw_odds_path": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
                    "credits_last": meta["headers"].get("x-requests-last"),
                    "credits_remaining": meta["headers"].get("x-requests-remaining"),
                    "as_of": as_of.isoformat(),
                    "hfa_mode": HFA_MODE,
                },
                "w7_7_at": now,
            }
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")
        print("wrote", path)

    _update_ledger(SOCIAL_LEDGER)
    _update_ledger(TRACKED_LEDGER)

    report = {
        "as_of": as_of.isoformat(),
        "credits": meta["headers"],
        "raw_path": str(raw_path),
        "recheck": [
            {
                "game_id": x["play"]["game_id"],
                "side": x["play"]["side"],
                "status": x["status"],
                "best": x["best"],
            }
            for x in recheck
        ],
        "marked": marked,
        "next4": next4,
        "radar": radar,
        "top6_changed": {"dropped": dropped, "added": added},
    }
    report_path = ART / "w7_7_recheck_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print("wrote", report_path)


if __name__ == "__main__":
    main()
