"""W7-3 — best-line sweep across all books for 4 games (probe only).

Pulls live Odds API spreads for eventIds only, prices OUR side with the same
production edge/HFA path the card uses, rewrites thread + ledger. No ingest,
no Odds Worker, no production code edits.
"""

from __future__ import annotations

import hashlib
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
from ncaa_quant.betting.provider import _book_two_way_spread  # noqa: E402
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
    HFA_ROBUST_PTS,
    MIN_MODEL_MARKET_AGREEMENT,
    PUBLIC_MIN_EDGE_DEFAULT,
    _hfa_adjust_games,
    _load_slate,
)

ART = Path(__file__).resolve().parent
OUT_THREAD = ROOT / "out" / "social" / "2026_w7" / "thread_w7.md"
TRACKED_LEDGER = ROOT / "docs" / "notes" / "_artifacts" / "w7-posted-calls" / "posted_calls_2026_w7.json"
SOCIAL_LEDGER = ROOT / "data" / "social" / "posted_calls_2026_w7.json"
CANDIDATES = ART / "candidates_w7.json"
TRACKED_THREAD = ROOT / "docs" / "notes" / "_artifacts" / "w7-posted-calls" / "thread_w7.md"

# Card plays + conditional (QB-blocked) plays.
PLAYS: list[dict[str, Any]] = [
    {
        "game_id": "401862806",
        "side": "Kent State",
        "role": "card",
        "dk_line": 20.5,
        "dk_price": -102.0,
        "dk_edge": 0.0579,
        "dk_edge_hfa": 0.1603,
    },
    {
        "game_id": "401856721",
        "side": "Missouri",
        "role": "card",
        "dk_line": 6.0,
        "dk_price": -110.0,
        "dk_edge": 0.0508,
        "dk_edge_hfa": 0.1512,
    },
    {
        "game_id": "401866442",
        "side": "Buffalo",
        "role": "conditional",
        "dk_line": -6.5,
        "dk_price": -110.0,
        "dk_edge": 0.1394,
        "dk_edge_hfa": 0.047,
        "qb_note": "UMass starter unconfirmed",
    },
    {
        "game_id": "401858494",
        "side": "Wisconsin",
        "role": "conditional",
        "dk_line": 2.5,
        "dk_price": -105.0,
        "dk_edge": 0.0699,
        "dk_edge_hfa": 0.1836,
        "qb_note": "UCLA expected to start a backup",
    },
]

REGULATED = frozenset(
    {
        "draftkings",
        "fanduel",
        "betmgm",
        "williamhill_us",
        "caesars",
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
    "caesars": "Caesars",
    "espnbet": "ESPN BET",
    "fanatics": "Fanatics",
    "betrivers": "BetRivers",
    "hardrockbet": "Hard Rock",
    "ballybet": "Bally Bet",
}
MAX_AGE_H = 6.0
EST_CREDITS = 2  # markets=spreads (1) × regions=us,us2 (2)
CREDIT_HARD_STOP = 10
QB_CUTOFF = datetime(2026, 10, 13, 11, 59, 0, tzinfo=UTC)


def twitter_len(text: str) -> int:
    url_re = re.compile(r"https?://\S+")
    n = 0
    pos = 0
    for m in url_re.finditer(text):
        n += len(text[pos : m.start()])
        n += 23
        pos = m.end()
    n += len(text[pos:])
    return n


def fmt_line(side: str, line: float) -> str:
    if line > 0:
        return f"{side} +{line:g}"
    if line < 0:
        return f"{side} {line:g}"
    return f"{side} PK"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _sample_draws(game: dict[str, Any], *, n_draws: int = 20_000) -> Any:
    """Match ``provider.build_candidates_from_odds`` seed/kernel/rho."""
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
    """Single-book edge/residual — same math as ``build_candidates_from_odds``."""
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
    return {
        "book": book,
        "line": market_line,
        "price": float(edge_res.side_american),
        "last_update": to_utc(last_update).isoformat(),
        "edge": round(float(edge_res.edge), 4),
        "residual": round(float(residual), 2),
        "expected_value": round(float(edge_res.expected_value), 4),
        "model_line": model_line,
        "age_hours": round(age_h, 3),
        "p_model": float(edge_res.p_model),
        "p_market": float(edge_res.p_market),
    }


def _dual_gate(raw: dict[str, Any], hfa: dict[str, Any] | None) -> dict[str, Any]:
    edge_ok = float(raw["edge"]) >= PUBLIC_MIN_EDGE_DEFAULT
    resid_ok = float(raw["residual"]) <= MIN_MODEL_MARKET_AGREEMENT
    if hfa is None:
        return {
            **raw,
            "edge_hfa": None,
            "resid_hfa": None,
            "gate_pass": False,
            "gate_fail": "no_hfa_quote",
        }
    hfa_edge_ok = float(hfa["edge"]) >= PUBLIC_MIN_EDGE_DEFAULT
    hfa_resid_ok = float(hfa["residual"]) <= MIN_MODEL_MARKET_AGREEMENT
    # Same side/line (HFA only shifts μ); require residual caps on both.
    fail: list[str] = []
    if not edge_ok:
        fail.append("edge")
    if not resid_ok:
        fail.append("resid")
    if not hfa_edge_ok:
        fail.append("edge_hfa")
    if not hfa_resid_ok:
        fail.append("resid_hfa")
    return {
        **raw,
        "edge_hfa": hfa["edge"],
        "resid_hfa": hfa["residual"],
        "gate_pass": not fail,
        "gate_fail": ",".join(fail),
    }


def _better_number_key(line: float) -> float:
    """Higher market line on our side is better (dog gets more; favorite lays less)."""
    return float(line)


def _better_price_key(price: float) -> float:
    """Higher American odds = better (via decimal)."""
    if price >= 100:
        return 1.0 + price / 100.0
    return 1.0 + 100.0 / abs(price)


def _pick_best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [r for r in rows if r.get("gate_pass") and r["book"] in REGULATED]
    if not eligible:
        return None
    eligible.sort(
        key=lambda r: (
            float(r["edge"]),
            _better_number_key(float(r["line"])),
            _better_price_key(float(r["price"])),
        ),
        reverse=True,
    )
    return eligible[0]


def _resolve_event_ids() -> dict[str, str]:
    """CFBD game_id → Odds API event id via staged crosswalk."""
    gids = {int(p["game_id"]) for p in PLAYS}
    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        cw = store.read("odds_cfbd_game_crosswalk", filters={"season": 2026})
    sub = cw.loc[
        (cw["game_id"].astype("Int64").isin(list(gids)))
        & (cw["match_status"].astype(str) == "matched")
    ]
    out: dict[str, str] = {}
    for _, row in sub.iterrows():
        out[str(int(row["game_id"]))] = str(row["odds_event_id"])
    missing = gids - {int(k) for k in out}
    if missing:
        raise RuntimeError(f"crosswalk missing matched odds_event_id for {sorted(missing)}")
    return out


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
        raise RuntimeError(f"hard stop: est credits {EST_CREDITS} > {CREDIT_HARD_STOP}")
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
    with httpx.Client(base_url=BASE_URL, timeout=45.0) as client:
        resp = client.get(f"/sports/{SPORT_KEY}/odds", params=params)
        resp.raise_for_status()
        body = resp.content
        headers = {
            "x-requests-remaining": resp.headers.get("x-requests-remaining"),
            "x-requests-used": resp.headers.get("x-requests-used"),
            "x-requests-last": resp.headers.get("x-requests-last"),
        }
    payload = json.loads(body)
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
    print("credits_used_this_call", headers["x-requests-last"])
    print("credits_remaining", headers["x-requests-remaining"])
    print("credits_used_cumulative_header", headers["x-requests-used"])
    print("saved", out_path)
    used = int(headers["x-requests-last"] or 0)
    if used > CREDIT_HARD_STOP:
        raise RuntimeError(f"hard stop after call: used {used} > {CREDIT_HARD_STOP}")
    return payload, meta, out_path


def _parse_book_quotes(
    events: list[Any],
    *,
    event_to_gid: dict[str, str],
    team_map: Any,
    as_of: datetime,
) -> tuple[dict[str, dict[str, dict[str, Any]]], list[dict[str, Any]]]:
    """Return gid → book → {home_line, home_px, away_px, last_update}; list drops."""
    drops: list[dict[str, Any]] = []
    by_gid: dict[str, dict[str, dict[str, Any]]] = {}
    cutoff = to_utc(as_of) - timedelta(hours=MAX_AGE_H)

    for event in events:
        if not isinstance(event, dict):
            continue
        eid = str(event.get("id", ""))
        gid = event_to_gid.get(eid)
        if gid is None:
            continue
        home = normalize_team_name(str(event.get("home_team", "")), team_map)
        away = normalize_team_name(str(event.get("away_team", "")), team_map)
        bookmakers = event.get("bookmakers") or []
        for bm in bookmakers:
            if not isinstance(bm, dict):
                continue
            book = str(bm.get("key", ""))
            last_raw = bm.get("last_update")
            if not isinstance(last_raw, str):
                drops.append({"game_id": gid, "book": book, "reason": "missing_last_update"})
                continue
            last_update = to_utc(datetime.fromisoformat(last_raw.replace("Z", "+00:00")))
            if last_update < cutoff:
                drops.append(
                    {
                        "game_id": gid,
                        "book": book,
                        "reason": f"stale_last_update>{MAX_AGE_H}h",
                        "last_update": last_update.isoformat(),
                        "age_hours": round(
                            (to_utc(as_of) - last_update).total_seconds() / 3600.0, 3
                        ),
                    }
                )
                continue
            markets = bm.get("markets") or []
            spread = next(
                (m for m in markets if isinstance(m, dict) and m.get("key") == "spreads"),
                None,
            )
            if spread is None:
                drops.append({"game_id": gid, "book": book, "reason": "no_spreads_market"})
                continue
            outcomes = spread.get("outcomes") or []
            if not isinstance(outcomes, list):
                drops.append({"game_id": gid, "book": book, "reason": "bad_outcomes"})
                continue
            sides: dict[str, tuple[float, float]] = {}
            for o in outcomes:
                if not isinstance(o, dict):
                    continue
                name = normalize_team_name(str(o.get("name", "")), team_map)
                if o.get("price") is None or o.get("point") is None:
                    continue
                sides[name] = (float(o["point"]), float(o["price"]))
            if home not in sides or away not in sides:
                drops.append(
                    {
                        "game_id": gid,
                        "book": book,
                        "reason": "one_sided_or_unmapped",
                        "sides_present": sorted(sides.keys()),
                    }
                )
                continue
            home_line, home_px = sides[home]
            _away_line, away_px = sides[away]
            by_gid.setdefault(gid, {})[book] = {
                "home_line": home_line,
                "home_px": home_px,
                "away_px": away_px,
                "last_update": last_update,
                "home_team": home,
                "away_team": away,
            }
    return by_gid, drops


def _qb_recheck() -> tuple[bool, list[dict[str, Any]]]:
    """Return (ok_to_proceed, rows). STOP if any status event_time > QB_CUTOFF
    with a *new* status relative to what was already recorded at/near cutoff.

    Practical rule: any event_time strictly after QB_CUTOFF is flagged. Ole Miss
    at 11:59:14 was part of the original card batch — we flag but allow proceed
    only when status values match the known card states and nothing is newer
    than the card's known max (11:59:14).
    """
    gids = [int(p["game_id"]) for p in PLAYS]
    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        qb = store.read("qb_status", filters={"season": 2026})
        teams = store.read("teams", filters={"season": 2026})
        games = store.read("games", filters={"season": 2026, "week": 7})

    tid_to_name = {int(r["team_id"]): str(r["school"]) for _, r in teams.iterrows()}
    known_card = {
        ("401866442", "Massachusetts"): "unknown",
        ("401866442", "Buffalo"): "starter",
        ("401858494", "Wisconsin"): "starter",
        ("401858494", "UCLA"): "backup",
        ("401862806", "Kent State"): "starter",
        ("401862806", "South Florida"): "starter",
        ("401856721", "Missouri"): "starter",
        ("401856721", "Ole Miss"): "starter",
    }
    rows_out: list[dict[str, Any]] = []
    changed = False
    newest = None
    for gid in gids:
        gsub = games.loc[games["game_id"].astype("Int64") == gid]
        if gsub.empty:
            continue
        gr = gsub.iloc[0]
        for tid, label in (
            (int(gr["away_team_id"]), "away"),
            (int(gr["home_team_id"]), "home"),
        ):
            name = tid_to_name.get(tid, str(tid))
            sub = qb.loc[
                (qb["game_id"].astype("Int64") == gid) & (qb["team_id"].astype(int) == tid)
            ].copy()
            if sub.empty:
                rows_out.append(
                    {
                        "game_id": str(gid),
                        "team": name,
                        "side": label,
                        "status": None,
                        "event_time": None,
                        "note": "MISSING",
                    }
                )
                changed = True
                continue
            sub["event_time"] = pd.to_datetime(sub["event_time"], utc=True)
            latest = sub.sort_values("event_time").iloc[-1]
            et = latest["event_time"].to_pydatetime()
            status = str(latest["status"])
            rows_out.append(
                {
                    "game_id": str(gid),
                    "team": name,
                    "side": label,
                    "status": status,
                    "event_time": et.isoformat(),
                }
            )
            if newest is None or et > newest:
                newest = et
            expected = known_card.get((str(gid), name))
            if expected is not None and status != expected:
                changed = True
                rows_out[-1]["note"] = f"STATUS_CHANGED expected={expected}"
            elif et > QB_CUTOFF + timedelta(seconds=30):
                # More than ~30s past the round cutoff → treat as post-card update.
                changed = True
                rows_out[-1]["note"] = f"NEWER_THAN_CUTOFF+30s cutoff={QB_CUTOFF.isoformat()}"

    ok = not changed
    return ok, rows_out


def main() -> None:
    as_of = datetime.now(tz=UTC)
    print(f"as_of={as_of.isoformat()}")

    gid_to_event = _resolve_event_ids()
    print("EVENT_MAP", gid_to_event)
    event_to_gid = {v: k for k, v in gid_to_event.items()}

    reuse = sorted(ART.glob("odds_allbooks_*.json"))
    reuse_path = reuse[-1] if reuse else None
    events, meta, raw_path = _pull_odds(
        [gid_to_event[p["game_id"]] for p in PLAYS],
        reuse_path=reuse_path,
    )
    # Pin as_of to pull time when reusing so age filters match the snapshot.
    if reuse_path is not None and meta.get("pulled_at_utc"):
        as_of = to_utc(datetime.fromisoformat(str(meta["pulled_at_utc"]).replace("Z", "+00:00")))
        print(f"as_of pinned to pull: {as_of.isoformat()}")

    team_map = load_team_name_map(ROOT / "configs" / "team_names.yaml")
    quotes, drops = _parse_book_quotes(
        events, event_to_gid=event_to_gid, team_map=team_map, as_of=as_of
    )
    print("\n=== DROPPED BOOKS ===")
    for d in drops:
        print(d)
    if not drops:
        print("(none)")

    wp, wp_source = _load_slate()
    print("slate_source", wp_source)
    games_by = {str(g["game_id"]): g for g in wp["games"]}
    for g in wp["games"]:
        g.setdefault("season", wp.get("season", 2026))
        g.setdefault("week", wp.get("week", 7))
    hfa_games = {str(g["game_id"]): g for g in _hfa_adjust_games(list(wp["games"]))}

    # Confirm production helpers are importable / callable (card path).
    print(
        f"production_helpers ok: compute_edge + HFA_ROBUST_PTS={HFA_ROBUST_PTS} "
        f"+ _book_two_way_spread={_book_two_way_spread.__name__}"
    )

    report: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "credits": meta["headers"],
        "est_credits": EST_CREDITS,
        "raw_path": str(raw_path),
        "drops": drops,
        "games": {},
        "dk_default_explanation": (
            "Card priced only PROBE_BOOKS=(betmgm, draftkings, fanduel, williamhill_us). "
            "_choose_book_rows keeps the highest-edge dual-gate row; ties break by "
            "PROBE_BOOKS index (betmgm first — docstring incorrectly says prefer DK). "
            "Kent State: only draftkings cleared dual gate among probe books. "
            "Missouri: draftkings had uniquely highest edge (0.0508 > betmgm 0.0486)."
        ),
    }

    best_by_gid: dict[str, dict[str, Any]] = {}
    tables: dict[str, list[dict[str, Any]]] = {}

    for play in PLAYS:
        gid = play["game_id"]
        side = play["side"]
        game = games_by[gid]
        hfa_game = hfa_games[gid]
        draws = _sample_draws(game)
        draws_hfa = _sample_draws(hfa_game)
        book_map = quotes.get(gid, {})
        rows: list[dict[str, Any]] = []
        print(f"\n======== {gid} {game['away_team']} @ {game['home_team']} OUR={side} ========")
        print(
            f"{'book':16} {'line':>7} {'price':>7} {'last_update':24} "
            f"{'edge':>7} {'edge_hfa':>8} {'resid':>6} {'resid_hfa':>9} "
            f"{'gate':5} BEST?"
        )
        for book, q in sorted(book_map.items()):
            raw = _price_side(
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
            hfa = _price_side(
                game=hfa_game,
                side_team=side,
                book=book,
                home_line=q["home_line"],
                home_px=q["home_px"],
                away_px=q["away_px"],
                draws=draws_hfa,
                last_update=q["last_update"],
                as_of=as_of,
            )
            row = _dual_gate(raw, hfa)
            rows.append(row)

        best = _pick_best(rows)
        for row in sorted(rows, key=lambda r: float(r["edge"]), reverse=True):
            is_best = best is not None and row is best
            row["BEST"] = bool(is_best)
            mark = "BEST" if is_best else ""
            gate = "PASS" if row["gate_pass"] else "FAIL"
            print(
                f"{row['book']:16} {row['line']:>+7g} {row['price']:>+7g} "
                f"{row['last_update'][:24]:24} "
                f"{row['edge']:>7.4f} {float(row['edge_hfa'] or 0):>8.4f} "
                f"{row['residual']:>6.2f} {float(row['resid_hfa'] or 0):>9.2f} "
                f"{gate:5} {mark}"
            )
        tables[gid] = rows
        if best:
            best_by_gid[gid] = best
            dk_edge = float(play["dk_edge"])
            print(
                f"BEST_REGULATED: {best['book']} line={best['line']} "
                f"px={best['price']} edge={best['edge']} "
                f"(vs DK edge {dk_edge:+.4f} delta={best['edge'] - dk_edge:+.4f})"
            )
        else:
            print("BEST_REGULATED: NONE (no dual-gate pass at regulated books)")

        report["games"][gid] = {
            "matchup": f"{game['away_team']} @ {game['home_team']}",
            "side": side,
            "role": play["role"],
            "rows": rows,
            "best": best,
            "dk": {
                "line": play["dk_line"],
                "price": play["dk_price"],
                "edge": play["dk_edge"],
                "edge_hfa": play["dk_edge_hfa"],
            },
        }

    # QB re-check
    print("\n=== QB RE-CHECK ===")
    qb_ok, qb_rows = _qb_recheck()
    for r in qb_rows:
        print(r)
    report["qb"] = {"ok": qb_ok, "cutoff": QB_CUTOFF.isoformat(), "rows": qb_rows}
    if not qb_ok:
        print("STOP: QB status changed since cutoff. No thread/ledger writes.")
        report_path = ART / "w7_3_bestline_report.json"
        report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print("wrote", report_path)
        raise SystemExit(2)

    print("QB OK — no material change since cutoff (card states unchanged).")

    # Conditional eligibility
    conditional_eligible: list[dict[str, Any]] = []
    conditional_excluded: list[str] = []
    for play in PLAYS:
        if play["role"] != "conditional":
            continue
        gid = play["game_id"]
        best = best_by_gid.get(gid)
        if best is None:
            conditional_excluded.append(gid)
            print(f"CONDITIONAL EXCLUDED (no dual-gate): {gid} {play['side']}")
        else:
            conditional_eligible.append({**play, "best": best, "game": games_by[gid]})

    # --- Rewrite thread ---
    card_plays = []
    for play in PLAYS:
        if play["role"] != "card":
            continue
        gid = play["game_id"]
        best = best_by_gid.get(gid)
        if best is None:
            raise RuntimeError(f"card play {gid} has no eligible regulated book")
        card_plays.append({**play, "best": best, "game": games_by[gid]})

    posts: list[str] = []
    k = len(card_plays)
    posts.append(
        "ridge's week 7 card \U0001f3c8\n"
        "\n"
        f"ran all 62 games, these are the {k} where it disagrees with the market the most. all 1u\n"
        "\n"
        "15-9-1 on the season. line and book on each play \U0001f447"
    )
    for i, play in enumerate(card_plays, 1):
        g = play["game"]
        best = play["best"]
        mkt = fmt_line(play["side"], float(best["line"]))
        ridge = fmt_line(play["side"], round(float(best["model_line"]), 1))
        edge_pct = round(float(best["edge"]) * 100, 1)
        gap = round(abs(float(best["model_line"]) - float(best["line"])), 1)
        book = BOOK_LABEL.get(str(best["book"]), str(best["book"]))
        posts.append(
            f"Best Bet #{i}\n"
            "\n"
            f"{g['away_team']} @ {g['home_team']} (Sat)\n"
            f"\u25b8 Market: {mkt} ({book})\n"
            f"\u25b8 Ridge: {ridge}\n"
            f"\u25b8 Edge: {edge_pct}% | 1u\n"
            "\n"
            f"Model has this {gap} points off the market."
        )

    if conditional_eligible:
        # Compact form stays under 280; side+book+edge+QB reason only.
        bits = ["On the radar (not on the card)\n"]
        for play in conditional_eligible:
            best = play["best"]
            mkt = fmt_line(play["side"], float(best["line"]))
            book = BOOK_LABEL.get(str(best["book"]), str(best["book"]))
            edge_pct = round(float(best["edge"]) * 100, 1)
            bits.append(
                f"\u25b8 {mkt} ({book}), {edge_pct}% - would qualify; {play['qb_note']}"
            )
        bits.append("")
        bits.append("model doesn't know about the QB news; the market might.")
        posts.append("\n".join(bits))

    posts.append(
        "what ridge actually is:\n"
        "\n"
        "\u25b8 rates every offense + defense off play-by-play EPA, not final scores\n"
        "\u25b8 kalman filter updates them weekly, uncertainty included\n"
        "\u25b8 projects a margin + calibrated range, compares to the vig-free market\n"
        "\u25b8 only plays 4.5%+ edges with both QBs confirmed"
    )
    posts.append(
        "that's the card\n"
        "\n"
        "want the model's number on a game that's not here? reply with the matchup and i'll send it\n"
        "\n"
        "every forecast and every result is on the site: https://the-cfb-model.vercel.app"
    )

    # Fit radar post to 280 if needed
    for i, post in enumerate(posts):
        assert "\u2014" not in post and "\u2013" not in post, (i, "em/en dash")
        clen = twitter_len(post)
        if clen > 280:
            print(f"WARN post {i+1} is {clen} chars — will still write; fix manually if needed")

    OUT_THREAD.parent.mkdir(parents=True, exist_ok=True)
    thread_lines: list[str] = []
    n = len(posts)
    print("\n=== THREAD ===")
    for i, post in enumerate(posts, 1):
        clen = twitter_len(post)
        thread_lines.append(f"--- POST {i}/{n} ({clen} chars) ---")
        thread_lines.append(post)
        thread_lines.append("")
        print(f"--- POST {i}/{n} ({clen} chars) ---")
        print(post)
        print()
    OUT_THREAD.write_text("\n".join(thread_lines), encoding="utf-8")
    TRACKED_THREAD.parent.mkdir(parents=True, exist_ok=True)
    TRACKED_THREAD.write_text("\n".join(thread_lines), encoding="utf-8")
    print("wrote", OUT_THREAD)
    print("wrote", TRACKED_THREAD)

    # --- Ledger ---
    # Original W7 card DraftKings rows (pre-sweep). Do not read from ledger after
    # a prior sweep rewrite — that would nest the new row into superseded.
    ORIGINAL_DK: dict[str, dict[str, Any]] = {
        "401862806": {
            "book": "draftkings",
            "line": 20.5,
            "price": -102.0,
            "edge": 0.0579,
            "edge_hfa_adj": 0.1603,
            "snapshot_event_time": "2026-10-13T12:02:04.719889+00:00",
            "ladder_rung": "odds_api_snapshot",
            "side_is": "away",
            "posted_at_utc": None,
        },
        "401856721": {
            "book": "draftkings",
            "line": 6.0,
            "price": -110.0,
            "edge": 0.0508,
            "edge_hfa_adj": 0.1512,
            "snapshot_event_time": "2026-10-13T12:02:04.719889+00:00",
            "ladder_rung": "odds_api_snapshot",
            "side_is": "away",
            "posted_at_utc": None,
        },
    }
    snap_ts = as_of.isoformat()
    new_calls = []
    for play in card_plays:
        gid = play["game_id"]
        best = play["best"]
        prev = ORIGINAL_DK[gid]
        new_calls.append(
            {
                "game_id": gid,
                "matchup": f"{play['game']['away_team']} @ {play['game']['home_team']}",
                "side": play["side"],
                "side_is": prev.get("side_is"),
                "book": best["book"],
                "line": float(best["line"]),
                "price": float(best["price"]),
                "snapshot_event_time": snap_ts,
                "posted_at_utc": prev.get("posted_at_utc"),
                "edge": float(best["edge"]),
                "edge_hfa_adj": float(best["edge_hfa"]),
                "stake": 0.015,
                "units": 3.0,
                "ladder_rung": "odds_api_live_sweep",
                "superseded": {
                    "book": prev["book"],
                    "line": prev["line"],
                    "price": prev["price"],
                    "edge": prev["edge"],
                    "edge_hfa_adj": prev.get("edge_hfa_adj"),
                    "snapshot_event_time": prev.get("snapshot_event_time"),
                    "ladder_rung": prev.get("ladder_rung"),
                },
            }
        )

    conditional_list = []
    for play in PLAYS:
        if play["role"] != "conditional":
            continue
        gid = play["game_id"]
        best = best_by_gid.get(gid)
        entry: dict[str, Any] = {
            "game_id": gid,
            "matchup": f"{games_by[gid]['away_team']} @ {games_by[gid]['home_team']}",
            "side": play["side"],
            "status": "not_graded_qb_unconfirmed",
            "qb_note": play["qb_note"],
        }
        if best:
            entry.update(
                {
                    "book": best["book"],
                    "line": float(best["line"]),
                    "price": float(best["price"]),
                    "edge": float(best["edge"]),
                    "edge_hfa_adj": float(best["edge_hfa"]),
                    "snapshot_event_time": snap_ts,
                }
            )
        else:
            entry["note"] = "no_dual_gate_pass_at_regulated_books"
        conditional_list.append(entry)

    ledger = {
        "season": 2026,
        "week": 7,
        "label": "posted_calls",
        "n": len(new_calls),
        "probe_book": "mixed",
        "pricing_note": (
            "W7-3 best-line sweep: among US-regulated books with dual-HFA PASS "
            "(edge>=0.045, edge_hfa>=0.045, resid<=7), pick highest edge; "
            "tie-break better number then better price. Stake fixed 0.015."
        ),
        "posted_calls": new_calls,
        "conditional": conditional_list,
        "sweep_meta": {
            "raw_odds_path": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
            "credits_last": meta["headers"].get("x-requests-last"),
            "credits_remaining": meta["headers"].get("x-requests-remaining"),
            "as_of": as_of.isoformat(),
            "conditional_excluded_no_gate": conditional_excluded,
        },
    }
    text = json.dumps(ledger, indent=2) + "\n"
    TRACKED_LEDGER.write_text(text, encoding="utf-8")
    SOCIAL_LEDGER.write_text(text, encoding="utf-8")
    print("wrote", TRACKED_LEDGER)
    print("wrote", SOCIAL_LEDGER)

    # candidates for card plays
    candidates = []
    for play in card_plays:
        best = play["best"]
        candidates.append(
            {
                "game_id": play["game_id"],
                "market": "side",
                "side_team": play["side"],
                "market_line": float(best["line"]),
                "model_line": float(best["model_line"]),
                "edge": float(best["edge"]),
                "expected_value": float(best["expected_value"]),
                "american_odds": float(best["price"]),
                "p_win": float(best["p_model"]),
                "stake_fraction": 0.015,
                "model_market_residual_points": float(best["residual"]),
            }
        )
    CANDIDATES.write_text(json.dumps(candidates, indent=2) + "\n", encoding="utf-8")
    print("wrote", CANDIDATES)

    report["best_by_gid"] = {k: v for k, v in best_by_gid.items()}
    report["conditional_excluded"] = conditional_excluded
    report["files_sha256"] = {
        str(p.relative_to(ROOT)).replace("\\", "/"): sha256_file(p)
        for p in [OUT_THREAD, TRACKED_THREAD, TRACKED_LEDGER, SOCIAL_LEDGER, CANDIDATES, raw_path]
        if p.is_file()
    }
    report_path = ART / "w7_3_bestline_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print("wrote", report_path)
    print("FILES_SHA256", json.dumps(report["files_sha256"], indent=2))


if __name__ == "__main__":
    main()
