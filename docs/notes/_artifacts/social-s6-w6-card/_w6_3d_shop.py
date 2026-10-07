"""W6-3d — full line shop across books (report only; no card overwrites).

BOOKS I CAN USE was left as the template placeholder → treating as ``all``
books present in the newest snapshot.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from _s6_w6_card import (  # noqa: E402
    _build_rows_for_book,
    _choose_book_rows,
    _game_has_backup_qb,
    _kickoff_et,
    _load_slate,
    _production_accept_loop,
    _survivor_public_row,
)

ART = Path("docs/notes/_artifacts/social-s6-w6-card")
POSTED = Path("docs/notes/_artifacts/w6-posted-calls/posted_calls_2026_w6.json")
PUBLIC_MIN_EDGE = 0.045
MAX_RESIDUAL = 7.0
MAX_AGE_H = 6.0
ROWS_WRITTEN_CLI = 1132  # from `uv run ncaa-quant ingest odds --once` this task


def _newest_snapshot_books() -> tuple[datetime, list[tuple[str, datetime, int]]]:
    parts = sorted(Path("data/staged/odds_snapshots").glob("season=2026/**/part.parquet"))
    odds = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    odds["event_time"] = pd.to_datetime(odds["event_time"], utc=True)
    newest = odds["event_time"].max()
    sl = odds.loc[odds["event_time"] == newest]
    out: list[tuple[str, datetime, int]] = []
    for book, sub in sl.groupby(sl["book"].astype(str)):
        out.append((str(book), sub["event_time"].iloc[0].to_pydatetime(), int(len(sub))))
    out.sort(key=lambda x: x[0])
    return newest.to_pydatetime(), out


def _sample_draws(game: dict[str, Any], *, n_draws: int, seed: int | None = None) -> Any:
    """Match ``provider._sample_game_draws`` (seed default ``42 + game_id``)."""
    mu_m = float(game["mu_margin"])
    sig_m = float(game["sigma_margin"])
    mu_t = float(game.get("mu_total") or 50.0)
    sig_t = float(game.get("sigma_total") or 14.0)
    rho = float(game["rho"]) if game.get("rho") is not None else 0.0
    kernel = KeyNumberKernel(offset_weights={}, n=0)
    params = assemble_bivariate([mu_m], [sig_m], [mu_t], [sig_t], rho=rho)
    draw_seed = 42 + int(game["game_id"]) if seed is None else int(seed)
    return sample_joint(params, kernel=kernel, n_draws=n_draws, seed=draw_seed)


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
    """Edge/EV/residual for a fixed side at one book (same math as the card probe)."""
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
    is_stale = age_h > MAX_AGE_H
    edge = float(edge_res.edge)
    ev = float(edge_res.expected_value)
    fail: list[str] = []
    if edge < PUBLIC_MIN_EDGE:
        fail.append("edge_too_small")
    if residual > MAX_RESIDUAL:
        fail.append("model_market_disagree")
    if is_stale:
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
        "snapshot_event_time": snap.isoformat(),
        "edge": round(edge, 4),
        "residual": round(residual, 2),
        "expected_value": round(ev, 4),
        "model_line": model_line,
        "age_hours": round(age_h, 3),
        "gate": "PASS" if not fail else "FAIL",
        "fail_reason": ",".join(fail) if fail else "",
        "ladder_rung": rung,
    }


def _qb_clean(game: dict[str, Any], qb: pd.DataFrame, as_of: datetime) -> bool:
    known, _ = qb_status_known_for_game(
        qb,
        game_id=int(game["game_id"]),
        home_team_id=int(game["home_team_id"]),
        away_team_id=int(game["away_team_id"]),
        as_of=as_of,
    )
    if not known:
        return False
    if _game_has_backup_qb(
        qb,
        game_id=str(game["game_id"]),
        team_ids=(str(game["home_team_id"]), str(game["away_team_id"])),
        as_of=as_of,
    ):
        return False
    return True


def main() -> None:
    cfg = load_config()
    betting = cfg.betting
    analysis_as_of = datetime.now(tz=UTC)
    newest, book_meta = _newest_snapshot_books()
    all_books = tuple(b for b, _, _ in book_meta)
    usable = all_books  # BOOKS I CAN USE = all
    print("ASSUMPTION: BOOKS_I_CAN_USE = all (template placeholder was not filled)")
    print(f"analysis_as_of={analysis_as_of.isoformat()}")
    print(f"newest_snapshot_event_time={newest.isoformat()}")
    print("BOOKS_IN_NEWEST_SNAPSHOT")
    for book, et, n in book_meta:
        print(f"  {book}: event_time={et.isoformat()} n_rows={n}")

    posted = json.loads(POSTED.read_text(encoding="utf-8"))["posted_calls"]
    card_sides = {str(c["game_id"]): str(c["side"]) for c in posted}
    card_ids = [str(c["game_id"]) for c in posted]

    wp, _ = _load_slate()
    games = wp["games"]
    for g in games:
        g.setdefault("season", wp.get("season", 2026))
        g.setdefault("week", wp.get("week", 6))
    game_by_id = {str(g["game_id"]): g for g in games}

    with ParquetStore(cfg.paths.staged_dir) as store:
        qb = store.read("qb_status", filters={"season": 2026})
        # Hive week can lag CFBD week by ±1 (card games often stamped week=5).
        # Match provider._load_snapshots: season-wide, filter by game_id later.
        snapshots = store.read("odds_snapshots", filters={"season": 2026})

        by_book: dict[str, list[dict[str, Any]]] = {}
        for book in usable:
            by_book[book] = _build_rows_for_book(
                book=book,
                games=games,
                analysis_as_of=analysis_as_of,
                cfg=cfg,
                betting=betting,
                qb_frame=qb,
            )
        chosen, _ = _choose_book_rows(by_book)
        gate = _production_accept_loop(
            chosen,
            betting,
            public_min_edge=float(cfg.social.public_min_edge_sides),
            qb_frame=qb,
            as_of=analysis_as_of,
        )

        qb_clean_ranked: list[dict[str, Any]] = []
        for row in sorted(chosen, key=lambda r: float(r.get("edge") or -999), reverse=True):
            if not row.get("passes_pricing_gates"):
                continue
            if float(row.get("edge") or 0) < PUBLIC_MIN_EDGE:
                continue
            g = game_by_id[str(row["game_id"])]
            if not _qb_clean(g, qb, analysis_as_of):
                continue
            qb_clean_ranked.append(row)

        next3: list[str] = []
        for row in qb_clean_ranked:
            gid = str(row["game_id"])
            if gid in card_ids:
                continue
            next3.append(gid)
            if len(next3) >= 3:
                break
        # Marshall included (force into shop table if not already)
        if "401869943" not in card_ids and "401869943" not in next3:
            if len(next3) >= 3:
                next3[2] = "401869943"
            else:
                next3.append("401869943")

        shop_ids = card_ids + next3
        print("SHOP_IDS", shop_ids)
        print("NEXT3_QB_CLEAN", next3)

        sides = dict(card_sides)
        for row in chosen:
            gid = str(row["game_id"])
            if gid in next3:
                sides[gid] = str(row["side"])
        # Marshall side if forced and missing from chosen side map
        if "401869943" in shop_ids and "401869943" not in sides:
            for row in chosen:
                if str(row["game_id"]) == "401869943":
                    sides["401869943"] = str(row["side"])
                    break
            else:
                sides["401869943"] = "Marshall"  # W6-3b/c side

        draws_by = {
            gid: _sample_draws(game_by_id[gid], n_draws=20_000)
            for gid in shop_ids
            if gid in game_by_id
        }

        per_book_tables: dict[str, list[dict[str, Any]]] = {}
        print("\n======== PER_BOOK_TABLE (our side only) ========")
        for gid in shop_ids:
            g = game_by_id[gid]
            side = sides[gid]
            print(
                f"\n### {gid} | {g['away_team']} @ {g['home_team']} | "
                f"OUR_SIDE={side} | kick={_kickoff_et(str(g['kickoff_utc']))}"
            )
            rows_out: list[dict[str, Any]] = []
            for book in all_books:
                priced = _price_side_at_book(
                    game=g,
                    side_team=side,
                    book=book,
                    as_of=analysis_as_of,
                    snapshots=snapshots,
                    cfg=cfg,
                    draws=draws_by[gid],
                )
                if priced is None:
                    print(f"  {book:16} NO_QUOTE")
                    continue
                rows_out.append(priced)
            rows_out.sort(key=lambda r: float(r["edge"]), reverse=True)
            best_usable = next(
                (r for r in rows_out if r["gate"] == "PASS" and r["book"] in usable), None
            )
            best_all = next((r for r in rows_out if r["gate"] == "PASS"), None)
            for r in rows_out:
                marks = []
                if best_usable is r:
                    marks.append("BEST_USABLE")
                if best_all is r:
                    marks.append("BEST_ALL")
                r["marks"] = marks
                mark = f" [{','.join(marks)}]" if marks else ""
                print(
                    f"  {r['book']:16} line={r['line']:+g} px={r['price']:+g} "
                    f"edge={r['edge']:.4f} resid={r['residual']:.2f} "
                    f"EV={r['expected_value']:.4f} snap={r['snapshot_event_time']} "
                    f"{r['gate']}{(' ' + r['fail_reason']) if r['fail_reason'] else ''}"
                    f"{mark}"
                )
            per_book_tables[gid] = rows_out

        # Shadow across BOOKS I CAN USE (not PROBE_BOOKS-only qb_known_passing).
        accepted_ids = {str(g) for g in gate.get("accepted_game_ids") or []}
        shadow_rows_raw = [
            r
            for r in chosen
            if str(r["game_id"]) in accepted_ids
            and float(r.get("edge") or 0) >= PUBLIC_MIN_EDGE
            and str(r.get("book") or "") in usable
            and r.get("passes_pricing_gates")
        ]
        # Preserve accept-loop edge order
        shadow_rows_raw.sort(key=lambda r: float(r.get("edge") or -999), reverse=True)
        shadow = [_survivor_public_row(r) for r in shadow_rows_raw]
        print(
            "\n======== SHADOW_CARD "
            f"(usable={'+'.join(usable)}, qb_backup on, stake=0.015, weekly_cap=10%) ========"
        )
        print(
            f"accept_loop accepted_count={gate['accepted_count']} "
            f"weekly_exposure={gate['final_weekly_exposure']} "
            f"qb_known_passing_PROBE_only={gate['qb_known_pass_count']}"
        )
        for i, r in enumerate(shadow, 1):
            age_h = None
            stale2 = False
            if r.get("snapshot_event_time"):
                snap = datetime.fromisoformat(str(r["snapshot_event_time"]).replace("Z", "+00:00"))
                age_h = (analysis_as_of - snap).total_seconds() / 3600.0
                stale2 = age_h > 2.0
            print(
                f"{i:02d} {r['game_id']} | {r['matchup']} | {r['kickoff_et']} | "
                f"side={r['side']} | book={r['book']} line={r['line']} px={r['price']} "
                f"edge={r['edge']} resid={r['residual']} snap={r['snapshot_event_time']} "
                f"age_h={None if age_h is None else round(age_h, 3)} stale>2h={stale2}"
            )

        print("\n======== DIFF_VS_bdee747 ========")
        prev = {str(c["game_id"]): c for c in posted}
        new = {str(r["game_id"]): r for r in shadow}
        for gid, o in prev.items():
            if gid not in new:
                why = "?"
                for t in gate["loop_trace"]:
                    if str(t["game_id"]) == gid:
                        why = ",".join(t.get("reasons") or []) or "not_selected"
                        break
                # book on chosen row for context
                ch = next((r for r in chosen if str(r["game_id"]) == gid), None)
                book_note = f" chosen_book={ch.get('book')} edge={ch.get('edge')}" if ch else ""
                print(
                    f"DROPPED {gid} | {o['matchup']} | {o['side']} @ {o['book']} "
                    f"{o['line']} | why={why}{book_note}"
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
                    f"{n['book']} {n['line']} @{n['price']} edge={n['edge']} "
                    f"resid={n['residual']}"
                )
            else:
                print(f"SAME {gid} | {n['book']} {n['line']} @{n['price']} edge={n['edge']}")

        kos = [str(r["kickoff_et"]) for r in shadow if r.get("kickoff_et")]
        earliest = min(kos) if kos else None
        print("EARLIEST_SHADOW", earliest)
        stale_flags = []
        for r in shadow:
            if not r.get("snapshot_event_time"):
                continue
            snap = datetime.fromisoformat(str(r["snapshot_event_time"]).replace("Z", "+00:00"))
            age_h = (analysis_as_of - snap).total_seconds() / 3600.0
            if age_h > 2.0:
                stale_flags.append(
                    {
                        "game_id": r["game_id"],
                        "matchup": r.get("matchup"),
                        "book": r["book"],
                        "age_h": round(age_h, 3),
                        "snapshot_event_time": r["snapshot_event_time"],
                    }
                )
        print("STALE_GT_2H", stale_flags)

        out = {
            "assumption": "BOOKS_I_CAN_USE=all (template placeholder)",
            "rows_written_cli": ROWS_WRITTEN_CLI,
            "newest_event_time": newest.isoformat(),
            "books": [
                {"book": b, "event_time": et.isoformat(), "n_rows": n} for b, et, n in book_meta
            ],
            "usable": list(usable),
            "shop_ids": shop_ids,
            "next3": next3,
            "sides": sides,
            "per_book_tables": per_book_tables,
            "shadow": shadow,
            "accept_loop": {
                "accepted_count": gate["accepted_count"],
                "final_weekly_exposure": gate["final_weekly_exposure"],
                "accepted_game_ids": gate["accepted_game_ids"],
                "qb_known_pass_count_probe_books_only": gate["qb_known_pass_count"],
            },
            "as_of": analysis_as_of.isoformat(),
            "earliest_shadow": earliest,
            "stale_gt_2h": stale_flags,
        }
        path = ART / "w6_3d_shop_report.json"
        path.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
        print("wrote", path)
        print("STOP — waiting for your book picks. No card files overwritten.")


if __name__ == "__main__":
    main()
