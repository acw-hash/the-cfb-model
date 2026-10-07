"""W6-3e — reconcile sec2/sec3 edges, fix probe path, finalize 4-book card.

No publish / R2 / config / production-code edits.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))

import ncaa_quant.betting.provider as provider_mod
from ncaa_quant.betting.edges import BookPrice, compute_edge
from ncaa_quant.betting.provider import (
    qb_status_known_for_game,
    resolve_asof_snapshot_window,
)
from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.distribution.bivariate import assemble_bivariate
from ncaa_quant.distribution.key_numbers import KeyNumberKernel
from ncaa_quant.distribution.simulate import sample_joint, spread_cover_probs, two_way_side_prob
from ncaa_quant.utils.timeutils import to_utc
from _s6_w6_card import (
    UNIT_FRACTION,
    _build_rows_for_book,
    _choose_book_rows,
    _game_has_backup_qb,
    _kickoff_et,
    _load_slate,
    _production_accept_loop,
    _survivor_public_row,
)

ART = Path("docs/notes/_artifacts/social-s6-w6-card")
TRACKED = Path("docs/notes/_artifacts/w6-posted-calls")
OUT_THREAD = Path("out/social/2026_w6/thread_w6.md")
POSTED = Path("data/social/posted_calls_2026_w6.json")
ET = ZoneInfo("America/New_York")
URL = "https://the-cfb-model.vercel.app"
N_PUBLISH = 58
PUBLIC_MIN_EDGE = 0.045
MAX_RESIDUAL = 7.0
MAX_AGE_H = 6.0
# Pin to the W6-3d reconcile snapshot (newest at report time was this event_time).
SNAPSHOT_ET = "2026-10-07T18:29:50.580691+00:00"
USABLE_BOOKS = ("betmgm", "draftkings", "fanduel", "williamhill_us")
BOOK_LABEL = {
    "betmgm": "BetMGM",
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "williamhill_us": "Caesars",
}


def _sample_draws_production(game: dict[str, Any], *, n_draws: int = 20_000) -> Any:
    """Match ``provider.build_candidates_from_odds`` draw seed/kernel/rho."""
    mu_m = float(game["mu_margin"])
    sig_m = float(game["sigma_margin"])
    mu_t = float(game.get("mu_total") or 50.0)
    sig_t = float(game.get("sigma_total") or 14.0)
    rho = float(game["rho"]) if game.get("rho") is not None else 0.0
    kernel = KeyNumberKernel(offset_weights={}, n=0)
    params = assemble_bivariate([mu_m], [sig_m], [mu_t], [sig_t], rho=rho)
    # Production: seed=42 + gid_int (NOT 42 + gid % 10000 — that was the W6-3d bug).
    seed = 42 + int(game["game_id"])
    return sample_joint(params, kernel=kernel, n_draws=n_draws, seed=seed)


def _price_side_at_book(
    *,
    game: dict[str, Any],
    side_team: str,
    book: str,
    as_of: datetime,
    snapshots: pd.DataFrame,
    cfg: Any,
    draws: Any,
) -> dict[str, Any] | None:
    """Single-book edge/EV/residual — same math as build_candidates_from_odds."""
    gid = str(game["game_id"])
    home = str(game["home_team"])
    away = str(game["away_team"])
    if side_team not in (home, away):
        return None
    bet_on = "home" if side_team == home else "away"
    as_of_utc = to_utc(as_of)
    kickoff = datetime.fromisoformat(str(game["kickoff_utc"]).replace("Z", "+00:00"))
    wf = provider_mod._walkforward_config(cfg)
    window, rung = resolve_asof_snapshot_window(
        snapshots,
        game_id=int(gid),
        bound=as_of_utc,
        kickoff=kickoff,
        config=wf,
    )
    if window.empty or rung == "null":
        return None
    window = window.loc[
        (window["book"].astype(str) == book) & (window["market"].astype(str) == "spread")
    ]
    if window.empty:
        return None
    books = provider_mod._book_two_way_spread(window, home_side=home)
    if not books:
        return None
    book_name, home_line, home_px, away_px = books[0]
    side_px = home_px if bet_on == "home" else away_px
    other_px = away_px if bet_on == "home" else home_px
    p_model = two_way_side_prob(
        spread_cover_probs(draws, float(home_line), game_index=0, side=bet_on)  # type: ignore[arg-type]
    )
    edge_res = compute_edge(p_model, [BookPrice(book_name, side_px, other_px)])
    model_line_home = -float(game["mu_margin"])
    residual = abs(model_line_home - float(home_line))
    market_line = float(home_line) if bet_on == "home" else -float(home_line)
    model_line = model_line_home if bet_on == "home" else -model_line_home
    latest_et = pd.Timestamp(window["event_time"].max()).to_pydatetime()
    snap = to_utc(latest_et)
    age_h = (as_of_utc - snap).total_seconds() / 3600.0
    edge = float(edge_res.edge)
    ev = float(edge_res.expected_value)
    fail: list[str] = []
    if edge < PUBLIC_MIN_EDGE:
        fail.append("edge_too_small")
    if residual > MAX_RESIDUAL:
        fail.append("model_market_disagree")
    if age_h > MAX_AGE_H:
        fail.append("stale_inputs")
    if ev <= 0:
        fail.append("non_positive_ev")
    return {
        "game_id": gid,
        "matchup": f"{away} @ {home}",
        "side": side_team,
        "book": book_name,
        "line": market_line,
        "price": float(edge_res.side_american),
        "other_price": float(edge_res.other_american),
        "snapshot_event_time": snap.isoformat(),
        "edge": round(edge, 4),
        "residual": round(residual, 2),
        "expected_value": round(ev, 4),
        "p_model": round(float(edge_res.p_model), 6),
        "p_market": round(float(edge_res.p_market), 6),
        "model_line": model_line,
        "age_hours": round(age_h, 3),
        "gate": "PASS" if not fail else "FAIL",
        "fail_reason": ",".join(fail) if fail else "",
        "ladder_rung": rung,
        "devig": "proportional_two_way_single_book",
    }


def _fmt_line(side: str, line: float) -> str:
    if abs(line) < 0.05:
        return f"{side} pick'em"
    if abs(line - round(line * 2) / 2) < 1e-9 and abs(line % 1 - 0.5) < 1e-9:
        num = f"{line:+.1f}" if line > 0 else f"{line:.1f}"
    elif abs(line - round(line)) < 1e-9:
        num = f"{int(round(line)):+d}" if line > 0 else f"{int(round(line))}"
    else:
        num = f"{line:+.1f}" if line > 0 else f"{line:.1f}"
    return f"{side} {num}"


def _fmt_model(side: str, model: float) -> str:
    if abs(model) <= 0.5:
        return f"{side} pick'em"
    return f"{side} {model:+.1f}" if model > 0 else f"{side} {model:.1f}"


def _twitter_len(text: str) -> int:
    return len(re.sub(r"https?://\S+", "x" * 23, text))


def _qb_status_detail(
    qb: pd.DataFrame,
    game: dict[str, Any],
    as_of: datetime,
) -> dict[str, Any]:
    known, src = qb_status_known_for_game(
        qb,
        game_id=int(game["game_id"]),
        home_team_id=int(game["home_team_id"]),
        away_team_id=int(game["away_team_id"]),
        as_of=as_of,
    )
    backup = _game_has_backup_qb(
        qb,
        game_id=str(game["game_id"]),
        team_ids=(str(game["home_team_id"]), str(game["away_team_id"])),
        as_of=as_of,
    )
    # per-team latest status
    work = qb.loc[qb["game_id"].astype("Int64") == int(game["game_id"])].copy()
    team_status: dict[str, str] = {}
    if not work.empty:
        work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
        work = work.loc[work["event_time"] <= pd.Timestamp(to_utc(as_of))]
        for tid, label in (
            (int(game["home_team_id"]), "home"),
            (int(game["away_team_id"]), "away"),
        ):
            sub = work.loc[work["team_id"].astype(int) == tid]
            if sub.empty:
                team_status[label] = "MISSING"
            else:
                team_status[label] = str(sub.sort_values("event_time").iloc[-1]["status"])
    else:
        team_status = {"home": "MISSING", "away": "MISSING"}
    return {
        "qb_status_known": known,
        "qb_status_source": src,
        "qb_backup": backup,
        "home_status": team_status.get("home"),
        "away_status": team_status.get("away"),
    }


def main() -> None:
    cfg = load_config()
    betting = cfg.betting
    # Bound just after the pinned snapshot so resolve_asof lands on 18:29:50Z.
    analysis_as_of = datetime.fromisoformat(SNAPSHOT_ET) + timedelta(milliseconds=1)

    print("=" * 72)
    print("W6-3e STEP 1 — RECONCILE (paths)")
    print("=" * 72)
    print(
        """
SECTION-2 path (W6-3d _price_side_at_book / per-book table):
  - Snapshot: resolve_asof_snapshot_window on season-wide odds_snapshots,
    then filter to one book + market=spread.
  - p_model: Monte Carlo cover probs via spread_cover_probs + two_way_side_prob.
    BUG in W6-3d: seed = 42 + (game_id % 10000), kernel empty, rho from game/0.
  - p_market: compute_edge -> proportional two-way de-vig on THAT book's
    (side_american, other_american). Single book only; NOT consensus.
  - edge = p_model - p_market.

SECTION-3 / production path (_build_rows_for_book -> build_candidates_from_odds
  -> BetCandidate used by apply_bet_filters / evaluate_filters):
  - Snapshot: same resolve_asof ladder; book restricted via probe filter on
    _book_two_way_spread so only that book appears.
  - p_model: _sample_game_draws with seed = 42 + game_id (full int),
    KeyNumberKernel(offset_weights={}, n=0), rho from row or 0.0.
  - p_market: identical compute_edge / DEFAULT_DEVIG_METHOD=proportional,
    single-book two-way prices.
  - edge stored on BetCandidate; this is the production edge.

WHICH MATCHES PRODUCTION: section-3 / _build_rows_for_book.
WHY SECTION-2 DIFFERED: wrong Monte Carlo seed (42+gid%10000 vs 42+gid),
  so p_model (and therefore edge) drifted even when book/line/price matched.
""".strip()
    )

    wp, _ = _load_slate()
    games = wp["games"]
    for g in games:
        g.setdefault("season", wp.get("season", 2026))
        g.setdefault("week", wp.get("week", 6))
    game_by_id = {str(g["game_id"]): g for g in games}

    prev_posted = json.loads(
        (TRACKED / "posted_calls_2026_w6.json").read_text(encoding="utf-8")
    )["posted_calls"]
    # Shared rows from W6-3d: intersection of shop table sides and shadow card.
    w63d = json.loads((ART / "w6_3d_shop_report.json").read_text(encoding="utf-8"))
    shadow_old = w63d["shadow"]
    per_old = w63d.get("per_book_tables") or {}
    sides_old = w63d.get("sides") or {}

    print("\nSHARED ROWS ON 18:29:50Z (W6-3d recorded edges, before fix):")
    print(f"{'game':12} {'book':14} {'line':7} {'px':6} {'sec2':8} {'sec3':8} {'delta'}")
    shared_keys: list[tuple[str, str, str]] = []
    for r3 in shadow_old:
        gid = str(r3["game_id"])
        book = str(r3["book"])
        side = str(r3["side"])
        rows2 = per_old.get(gid) or []
        r2 = next((x for x in rows2 if x.get("book") == book), None)
        if r2 is None:
            continue
        shared_keys.append((gid, side, book))
        e2 = float(r2["edge"])
        e3 = float(r3["edge"])
        print(
            f"{gid:12} {book:14} {float(r2['line']):+7g} {float(r2['price']):+6g} "
            f"{e2:8.4f} {e3:8.4f} {e2 - e3:+.4f}"
        )

    print("\n" + "=" * 72)
    print("W6-3e STEP 2 — FIX seed; reprice sec2 vs sec3 on same as_of/snapshot")
    print("=" * 72)

    with ParquetStore(cfg.paths.staged_dir) as store:
        qb = store.read("qb_status", filters={"season": 2026})
        snapshots = store.read("odds_snapshots", filters={"season": 2026})

        by_book: dict[str, list[dict[str, Any]]] = {}
        for book in USABLE_BOOKS:
            by_book[book] = _build_rows_for_book(
                book=book,
                games=games,
                analysis_as_of=analysis_as_of,
                cfg=cfg,
                betting=betting,
                qb_frame=qb,
            )
        chosen, _ = _choose_book_rows(by_book)
        chosen_by = {str(r["game_id"]): r for r in chosen}

        mismatches: list[dict[str, Any]] = []
        print(f"{'game':12} {'book':14} {'line':7} {'px':6} {'sec2':8} {'sec3':8} {'ok'}")
        for gid, side, book in shared_keys:
            g = game_by_id[gid]
            draws = _sample_draws_production(g)
            priced = _price_side_at_book(
                game=g,
                side_team=side,
                book=book,
                as_of=analysis_as_of,
                snapshots=snapshots,
                cfg=cfg,
                draws=draws,
            )
            row3 = chosen_by.get(gid)
            if priced is None or row3 is None:
                print(f"{gid:12} {book:14} MISSING priced={priced is not None} chosen={row3 is not None}")
                mismatches.append({"game_id": gid, "book": book, "reason": "missing"})
                continue
            # Section-3 edge for this exact book row (not necessarily chosen if book differs)
            book_row = next(
                (r for r in by_book[book] if str(r["game_id"]) == gid),
                None,
            )
            e3 = float(book_row["edge"]) if book_row else float("nan")
            e2 = float(priced["edge"])
            ok = abs(e2 - e3) < 5e-5  # 4 decimal places
            print(
                f"{gid:12} {book:14} {float(priced['line']):+7g} {float(priced['price']):+6g} "
                f"{e2:8.4f} {e3:8.4f} {'MATCH' if ok else 'DIFF'}"
            )
            if not ok:
                mismatches.append(
                    {
                        "game_id": gid,
                        "book": book,
                        "sec2": e2,
                        "sec3": e3,
                        "p_model_2": priced.get("p_model"),
                        "p_market_2": priced.get("p_market"),
                        "p_model_3": book_row.get("p_model") if book_row else None,
                        "p_market_3": book_row.get("p_market") if book_row else None,
                    }
                )

        if mismatches:
            print("STOP — edges still differ after fix:")
            print(json.dumps(mismatches, indent=2, default=str))
            sys.exit(2)
        print("All shared rows match to 4 decimals.")

        # Also verify full card selection rows match sec2 reprice
        gate = _production_accept_loop(
            chosen,
            betting,
            public_min_edge=float(cfg.social.public_min_edge_sides),
            qb_frame=qb,
            as_of=analysis_as_of,
        )
        accepted_ids = {str(g) for g in gate["accepted_game_ids"]}
        card_raw = [
            r
            for r in chosen
            if str(r["game_id"]) in accepted_ids
            and float(r.get("edge") or 0) >= PUBLIC_MIN_EDGE
            and str(r.get("book") or "") in USABLE_BOOKS
            and r.get("passes_pricing_gates")
        ]
        card_raw.sort(key=lambda r: float(r.get("edge") or -999), reverse=True)
        card = [_survivor_public_row(r) for r in card_raw]

        print("\n" + "=" * 72)
        print("W6-3e STEP 3 — FINAL CARD (4 books, qb_backup on, 0.015 / 10%)")
        print("=" * 72)
        print(
            f"accepted_count={gate['accepted_count']} "
            f"weekly_exposure={gate['final_weekly_exposure']} "
            f"n_card={len(card)}"
        )

        qb_bad: list[dict[str, Any]] = []
        for r in card:
            g = game_by_id[str(r["game_id"])]
            detail = _qb_status_detail(qb, g, analysis_as_of)
            print(
                f"  {r['game_id']} QB home={detail['home_status']} "
                f"away={detail['away_status']} known={detail['qb_status_known']} "
                f"backup={detail['qb_backup']}"
            )
            if (
                not detail["qb_status_known"]
                or detail["qb_backup"]
                or detail["home_status"] in (None, "MISSING", "unknown")
                or detail["away_status"] in (None, "MISSING", "unknown")
                or str(detail["home_status"]).casefold() == "backup"
                or str(detail["away_status"]).casefold() == "backup"
            ):
                qb_bad.append({"game_id": r["game_id"], **detail})

        if qb_bad:
            print("STOP — card game has unknown/backup/missing QB:")
            print(json.dumps(qb_bad, indent=2, default=str))
            sys.exit(3)

        # Attach units / full fields from chosen
        for i, r in enumerate(card):
            full = chosen_by[str(r["game_id"])]
            r["units"] = full.get("units")
            r["stake_fraction"] = full.get("stake_fraction")
            r["expected_value"] = full.get("expected_value")
            r["p_model"] = full.get("p_model")
            r["model_line"] = full.get("model_line")
            r["model_market_residual_points"] = full.get("model_market_residual_points")
            # W6-3 step-3 style line
            print(
                f"{i + 1:02d} {r['game_id']} | {r['matchup']} | {r['kickoff_et']} | "
                f"side={r['side']} | book={r['book']} line={r['line']} px={r['price']} "
                f"edge={r['edge']} resid={r['residual']} model={r['model_line']} "
                f"snap={r['snapshot_event_time']} units={r['units']}"
            )

        print("\n======== DIFF_VS_bdee747 ========")
        prev = {str(c["game_id"]): c for c in prev_posted}
        new = {str(r["game_id"]): r for r in card}
        for gid, o in prev.items():
            if gid not in new:
                why = "?"
                for t in gate["loop_trace"]:
                    if str(t["game_id"]) == gid:
                        why = ",".join(t.get("reasons") or []) or "not_selected"
                        break
                print(
                    f"DROPPED {gid} | {o['matchup']} | {o['side']} @ {o['book']} "
                    f"{o['line']} | why={why}"
                )
        for gid, n in new.items():
            if gid not in prev:
                print(
                    f"ADDED {gid} | {n['matchup']} | {n['side']} @ {n['book']} "
                    f"{n['line']} px={n['price']} edge={n['edge']}"
                )
        for gid, n in new.items():
            if gid not in prev:
                continue
            o = prev[gid]
            if (o["book"], float(o["line"]), float(o["price"])) != (
                n["book"],
                float(n["line"]),
                float(n["price"]),
            ):
                print(
                    f"MOVED {gid} | {n['matchup']} | "
                    f"{o['book']} {o['line']} @{o['price']} edge={o['edge']} -> "
                    f"{n['book']} {n['line']} @{n['price']} edge={n['edge']}"
                )
            else:
                print(f"SAME {gid} | {n['book']} {n['line']} @{n['price']} edge={n['edge']}")

        print("\n" + "=" * 72)
        print("W6-3e STEP 4 — rewrite artifacts + hashes")
        print("=" * 72)

        candidates = []
        for r in card:
            candidates.append(
                {
                    "game_id": str(r["game_id"]),
                    "market": "side",
                    "side_team": r["side"],
                    "market_line": float(r["line"]),
                    "model_line": float(r["model_line"]),
                    "edge": float(r["edge"]),
                    "expected_value": float(r["expected_value"]),
                    "american_odds": float(r["price"]),
                    "p_win": float(r["p_model"]),
                    "stake_fraction": float(r["stake_fraction"] or 0.015),
                    "model_market_residual_points": float(r["model_market_residual_points"]),
                }
            )
        cand_path = ART / "candidates_w6.json"
        cand_path.write_text(json.dumps(candidates, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {cand_path}")

        posted_calls = []
        for r in card:
            posted_calls.append(
                {
                    "game_id": str(r["game_id"]),
                    "matchup": r["matchup"],
                    "side": r["side"],
                    "book": r["book"],
                    "line": float(r["line"]),
                    "price": float(r["price"]),
                    "snapshot_event_time": r["snapshot_event_time"],
                    "posted_at_utc": None,
                    "edge": float(r["edge"]),
                    "units": float(r["units"] or (0.015 / UNIT_FRACTION)),
                    "ladder_rung": r.get("ladder_rung") or "odds_api_snapshot",
                }
            )
        payload = {
            "season": 2026,
            "week": 6,
            "label": "posted_calls",
            "n": len(posted_calls),
            "probe_book": "mixed",
            "pricing_note": (
                "Lines/prices/edges from betmgm+draftkings+fanduel+williamhill_us "
                "highest-edge PASS row per game; probe-only qb_backup reject applied "
                "before exposure. Edges match production BetCandidate path "
                "(seed=42+game_id)."
            ),
            "posted_calls": posted_calls,
        }
        text = json.dumps(payload, indent=2) + "\n"
        POSTED.parent.mkdir(parents=True, exist_ok=True)
        POSTED.write_text(text, encoding="utf-8")
        TRACKED.mkdir(parents=True, exist_ok=True)
        tracked = TRACKED / "posted_calls_2026_w6.json"
        tracked.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest().upper()
        print(f"wrote {POSTED}")
        print(f"wrote {tracked}")
        print(f"POSTED_SHA256={digest}")

        posts: list[str] = []
        hook = (
            "ridge's week 6 card 🏈\n"
            "\n"
            f"ran all {N_PUBLISH} games, these are the {len(card)} where it disagrees "
            "with the market the most. all 1u\n"
            "\n"
            "<RECORD> on the season. line and book on each play 👇"
        )
        posts.append(hook)
        for i, r in enumerate(card, 1):
            away, _, home = str(r["matchup"]).partition(" @ ")
            kick = datetime.fromisoformat(str(r["kickoff_utc"]).replace("Z", "+00:00")).astimezone(
                ET
            )
            day = kick.strftime("%a")
            book = BOOK_LABEL[str(r["book"])]
            mkt = _fmt_line(str(r["side"]), float(r["line"]))
            ridge = _fmt_model(str(r["side"]), float(r["model_line"]))
            edge_pct = round(float(r["edge"]) * 100, 1)
            gap = round(abs(float(r["model_line"]) - float(r["line"])), 1)
            body = (
                f"Best Bet #{i}\n"
                "\n"
                f"{away} @ {home} ({day})\n"
                f"▸ Market: {mkt} ({book})\n"
                f"▸ Ridge: {ridge}\n"
                f"▸ Edge: {edge_pct}% | 1u\n"
                "\n"
                f"Model has this {gap} points off the market."
            )
            posts.append(body)
        posts.append(
            "what ridge actually is:\n"
            "\n"
            "▸ rates every offense + defense off play-by-play EPA, not final scores\n"
            "▸ kalman filter updates them weekly, uncertainty included\n"
            "▸ projects a margin + calibrated range, compares to the vig-free market\n"
            "▸ only plays 4.5%+ edges with both QBs confirmed"
        )
        posts.append(
            "that's the card\n"
            "\n"
            "want the model's number on a game that's not here? reply with the matchup "
            "and i'll send it\n"
            "\n"
            f"every forecast and every result is on the site: {URL}"
        )
        n = len(posts)
        blocks = []
        for i, body in enumerate(posts, 1):
            n_chars = _twitter_len(body)
            flag = " OK" if n_chars <= 280 else " OVER"
            print(f"POST {i}/{n}: {n_chars} chars{flag}")
            blocks.append(f"--- POST {i}/{n} ({n_chars} chars) ---\n{body}\n")
        thread_text = "\n".join(blocks).rstrip() + "\n"
        OUT_THREAD.parent.mkdir(parents=True, exist_ok=True)
        OUT_THREAD.write_text(thread_text, encoding="utf-8")
        (TRACKED / "thread_w6.md").write_text(thread_text, encoding="utf-8")
        # Also keep a copy next to candidates for convenience (W6-3c pattern used tracked).
        print(f"wrote {OUT_THREAD}")
        print(f"wrote {TRACKED / 'thread_w6.md'}")

        # Patch _w6_3d_shop.py seed for future runs
        shop = ART / "_w6_3d_shop.py"
        src = shop.read_text(encoding="utf-8")
        old = "seed=42 + int(gid) % 10_000"
        new = "seed=42 + int(gid)  # production: provider._sample_game_draws"
        if old in src:
            shop.write_text(src.replace(old, new), encoding="utf-8")
            print(f"patched {shop} draw seed to match production")
        else:
            print(f"NOTE: seed pattern not found in {shop} (may already be patched)")

        report = {
            "snapshot_et": SNAPSHOT_ET,
            "as_of": analysis_as_of.isoformat(),
            "shared_pre_fix": [
                {
                    "game_id": gid,
                    "book": book,
                    "side": side,
                    "sec2": next(
                        (x["edge"] for x in (per_old.get(gid) or []) if x.get("book") == book),
                        None,
                    ),
                    "sec3": next(
                        (x["edge"] for x in shadow_old if str(x["game_id"]) == gid),
                        None,
                    ),
                }
                for gid, side, book in shared_keys
            ],
            "match_ok": True,
            "card": card,
            "posted_sha256": digest,
            "diff_note": "see stdout",
        }
        (ART / "w6_3e_report.json").write_text(
            json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
        )
        print("wrote", ART / "w6_3e_report.json")
        print("==== THREAD VERBATIM ====")
        print(thread_text)


if __name__ == "__main__":
    main()
