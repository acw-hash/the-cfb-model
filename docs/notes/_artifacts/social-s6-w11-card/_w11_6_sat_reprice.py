"""W11-6 — Saturday re-price Oklahoma; fill slot if it fails (NO PUBLISH).

One Odds API pull (spreads × us,us2) for the 7 ledger card games + 2
alternates. Hard stop if credits used > 10. Stage snapshots. Re-price
Oklahoma at best regulated; on FAIL try Arizona then Iowa State (kickoff
must be > now+3h). Other 6: report-only gate check (no swap). Update
posted_calls + tracked copy. No publish / no thread.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _w11_5_fri_linecheck import (  # noqa: E402
    ALTERNATES,
    ART,
    BOOK_DISPLAY,
    MAX_GAP,
    MIN_EDGE,
    REGULATED,
    SOCIAL,
    STAKE,
    TRACKED,
    UNITS,
    _drop_stale_and_onesided,
    _event_ids,
    _frame_with_per_book_event_time,
    _load_wp,
    _parse_quotes,
    _price_side,
    _pick_best,
    _reprice_play,
)
from ncaa_quant.config import load_config  # noqa: E402
from ncaa_quant.data.storage import ParquetStore  # noqa: E402
from ncaa_quant.ingestion.odds_api import write_odds_snapshots  # noqa: E402
from ncaa_quant.ingestion.teams import load_team_name_map  # noqa: E402
from ncaa_quant.utils.timeutils import to_utc  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
OKLAHOMA_GID = 401856887
ARIZONA_GID = 401856864
IOWA_STATE_GID = 401856859


def _pull_w11_6(event_ids: list[str]) -> tuple[list[Any], dict[str, Any], Path]:
    """Same pull as W11-5 but tag task W11-6 and new filename prefix."""
    from _w11_5_fri_linecheck import (  # local import keep helpers shared
        BASE_URL,
        CREDIT_HARD_STOP,
        EST_CREDITS,
        SPORT_KEY,
        load_secrets,
    )
    import httpx

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
    out_path = ART / f"odds_w11_6_{ts}.json"
    meta = {
        "pulled_at_utc": datetime.now(tz=UTC).isoformat(),
        "event_ids": event_ids,
        "params": {k: v for k, v in params.items() if k != "apiKey"},
        "headers": headers,
        "est_credits": EST_CREDITS,
        "n_events": len(payload) if isinstance(payload, list) else None,
        "task": "W11-6",
    }
    out_path.write_text(
        json.dumps({"meta": meta, "events": payload}, indent=2) + "\n",
        encoding="utf-8",
    )
    print("saved", out_path)
    return payload, meta, out_path


def _play_from_ledger(pc: dict[str, Any]) -> dict[str, Any]:
    return {
        "game_id": int(pc["game_id"]),
        "side": str(pc["side"]),
        "w11_4_book": str(pc.get("book") or "draftkings"),
        "w11_4_line": float(pc.get("w11_4_line") if pc.get("w11_4_line") is not None else pc["line"]),
        "w11_4_price": float(pc["price"]),
        "role": "card",
        "ledger_line": float(pc["line"]),
        "ledger_price": float(pc["price"]),
        "ledger_book": str(pc["book"]),
    }


def _row_from_selected(
    *,
    res: dict[str, Any],
    selected: dict[str, Any],
    source: str,
    prior_w11_4_line: float,
) -> dict[str, Any]:
    return {
        "game_id": str(int(res["play"]["game_id"])),
        "matchup": res["matchup"],
        "side": res["side"],
        "side_is": selected["side_is"],
        "book": selected["book"],
        "book_display": selected["book_display"],
        "line": float(selected["line"]),
        "price": float(selected["price"]),
        "snapshot_event_time": selected["snapshot_event_time"],
        "posted_at_utc": None,
        "edge": float(selected["edge"]),
        "model_line": float(selected["model_line"]),
        "model_market_residual_points": float(selected["gap"]),
        "stake": STAKE,
        "units": UNITS,
        "ladder_rung": "odds_api_snapshot+per_book_latest",
        "w11_4_line": float(prior_w11_4_line),
        "source": source,
        "kickoff_et": res["kickoff_et"],
        "kickoff_utc": res["kickoff_utc"],
        "gate_pass": bool(selected.get("gate_pass")),
        "gate_fail": selected.get("gate_fail") or "",
    }


def main() -> None:
    cfg = load_config()
    assert cfg.webapp.export_enabled is False, "export must stay False"
    print("export_enabled", cfg.webapp.export_enabled)

    ledger_prev = json.loads(SOCIAL.read_text(encoding="utf-8"))
    posted_prev = list(ledger_prev.get("posted_calls") or [])
    assert len(posted_prev) == 7, f"expected 7 posted_calls, got {len(posted_prev)}"
    by_gid = {str(c["game_id"]): c for c in posted_prev}
    assert str(OKLAHOMA_GID) in by_gid, "Oklahoma missing from ledger"

    card_plays = [_play_from_ledger(c) for c in posted_prev]
    # Keep W11-5 alternate definitions (sides Arizona / Iowa State).
    alt_plays = list(ALTERNATES)
    all_plays = card_plays + alt_plays
    gids = [int(p["game_id"]) for p in all_plays]
    assert len(set(gids)) == 9

    now = datetime.now(tz=UTC)
    print("\n=== 1. ODDS PULL ===")
    print("now_utc", now.isoformat())
    gid_to_eid = _event_ids(gids)
    event_ids = sorted(set(gid_to_eid.values()))
    reuse = "--reuse" in sys.argv
    if reuse:
        cands = sorted(ART.glob("odds_w11_6_*.json"), reverse=True)
        if not cands:
            raise SystemExit("no odds_w11_6_*.json to reuse")
        raw_path = cands[0]
        blob = json.loads(raw_path.read_text(encoding="utf-8"))
        events, meta = blob["events"], blob["meta"]
        print(f"REUSING {raw_path.name} (0 credits)")
        as_of = to_utc(
            datetime.fromisoformat(str(meta["pulled_at_utc"]).replace("Z", "+00:00"))
        )
        team_map = load_team_name_map(Path(cfg.data.team_names_path))
        cleaned, drops = _drop_stale_and_onesided(events, as_of=as_of, team_map=team_map)
        print("n_drop_records", len(drops))
        rc = Counter(
            d["reason"] if "stale" not in d["reason"] else "stale_>6h" for d in drops
        )
        print("drop_reason_counts", dict(rc))
        added, quarantined = 0, 0
        frame_rows = 0
    else:
        events, meta, raw_path = _pull_w11_6(event_ids)
        as_of = to_utc(
            datetime.fromisoformat(str(meta["pulled_at_utc"]).replace("Z", "+00:00"))
        )
        team_map = load_team_name_map(Path(cfg.data.team_names_path))
        cleaned, drops = _drop_stale_and_onesided(events, as_of=as_of, team_map=team_map)
        print("n_drop_records", len(drops))
        rc = Counter(
            d["reason"] if "stale" not in d["reason"] else "stale_>6h" for d in drops
        )
        print("drop_reason_counts", dict(rc))
        event_game_ids = {eid: gid for gid, eid in gid_to_eid.items()}
        frame = _frame_with_per_book_event_time(
            cleaned,
            event_game_ids=event_game_ids,
            team_map=team_map,
            captured_at=to_utc(as_of),
            ingested_at=datetime.now(tz=UTC),
        )
        print("frame_rows", len(frame))
        with ParquetStore(cfg.paths.staged_dir) as store:
            added, quarantined = write_odds_snapshots(
                store, frame, raw_archive_path=raw_path
            )
        print("rows_written", added, "quarantined", quarantined)
        frame_rows = len(frame)

    stage_report = {
        "raw_path": str(raw_path),
        "credits": meta.get("headers"),
        "n_games": len(gids),
        "n_event_ids": len(event_ids),
        "n_drop_records": len(drops),
        "drop_reason_counts": dict(rc),
        "rows_fetched": frame_rows,
        "rows_written": added,
        "quarantined": quarantined,
        "gids": gids,
        "reuse": reuse,
    }
    (ART / "w11_6_stage_report.json").write_text(
        json.dumps(stage_report, indent=2, default=str) + "\n", encoding="utf-8"
    )

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
        _reprice_play(p, games_by=games_by, quotes=quotes, as_of=as_of) for p in card_plays
    ]
    alt_results = [
        _reprice_play(p, games_by=games_by, quotes=quotes, as_of=as_of) for p in alt_plays
    ]
    by_res = {int(r["play"]["game_id"]): r for r in card_results + alt_results}

    # --- Oklahoma decision ---
    ok_res = by_res[OKLAHOMA_GID]
    ok_best = ok_res["best_regulated"]
    print("\n=== 2a. OKLAHOMA ===")
    print(
        f"  matchup={ok_res['matchup']} side={ok_res['side']} "
        f"Ridge={ok_best['model_line'] if ok_best else None}"
    )
    if ok_best is None:
        print("  best regulated: NONE -> FAIL")
        ok_pass = False
    else:
        print(
            f"  best={ok_best['book_display']} {ok_best['line']:+g}/{ok_best['price']:+g} "
            f"gap={ok_best['gap']:.2f} edge={ok_best['edge']:.4f} "
            f"gate={'PASS' if ok_best['gate_pass'] else 'FAIL:'+ok_best['gate_fail']}"
        )
        print(
            f"  gates: edge>={MIN_EDGE} gap<={MAX_GAP} -> "
            f"edge_ok={float(ok_best['edge']) >= MIN_EDGE} "
            f"gap_ok={float(ok_best['gap']) <= MAX_GAP}"
        )
        ok_pass = bool(ok_best.get("gate_pass"))

    superseded: list[dict[str, Any]] = list(ledger_prev.get("superseded_calls") or [])
    final_rows: list[dict[str, Any]] = []
    oklahoma_action: dict[str, Any]

    if ok_pass:
        assert ok_best is not None
        prior = by_gid[str(OKLAHOMA_GID)]
        final_rows.append(
            _row_from_selected(
                res=ok_res,
                selected=ok_best,
                source="card_oklahoma_kept",
                prior_w11_4_line=float(prior.get("w11_4_line") or prior["line"]),
            )
        )
        oklahoma_action = {
            "action": "KEEP",
            "best": ok_best,
            "ridge": ok_best["model_line"],
            "gap": ok_best["gap"],
            "edge": ok_best["edge"],
        }
        print("  -> KEEP Oklahoma at new line/price")
    else:
        # Drop Oklahoma into superseded
        prior = by_gid[str(OKLAHOMA_GID)]
        fail_reason = (
            f"W11-6 Oklahoma FAIL at best regulated "
            f"({(ok_best or {}).get('gate_fail') or 'no_quote'}); "
            f"best={None if ok_best is None else ok_best.get('book_display')} "
            f"line={None if ok_best is None else ok_best.get('line')} "
            f"price={None if ok_best is None else ok_best.get('price')} "
            f"edge={None if ok_best is None else ok_best.get('edge')} "
            f"gap={None if ok_best is None else ok_best.get('gap')}"
        )
        superseded.append(
            {
                **{k: prior[k] for k in prior},
                "superseded_at_utc": as_of.isoformat(),
                "supersede_reason": fail_reason,
            }
        )
        print("  -> DROP Oklahoma:", fail_reason)

        fill: dict[str, Any] | None = None
        fill_src = ""

        # Alternate 1: Arizona
        az = by_res[ARIZONA_GID]
        az_best = az["best_regulated"]
        print("\n=== 2b. ALT Arizona ===")
        if az_best is None:
            print("  NONE")
        else:
            print(
                f"  best={az_best['book_display']} {az_best['line']:+g}/{az_best['price']:+g} "
                f"gap={az_best['gap']:.2f} edge={az_best['edge']:.4f} "
                f"gate={'PASS' if az_best['gate_pass'] else 'FAIL:'+az_best['gate_fail']}"
            )
        if az_best is not None and az_best.get("gate_pass"):
            fill = _row_from_selected(
                res=az,
                selected=az_best,
                source="alternate_arizona",
                prior_w11_4_line=float(az["play"]["w11_4_line"]),
            )
            fill_src = "Arizona"
            print("  -> FILL with Arizona")
        else:
            # Alternate 2: Iowa State — only if kickoff > now + 3h
            isu = by_res[IOWA_STATE_GID]
            isu_ko = to_utc(
                datetime.fromisoformat(str(isu["kickoff_utc"]).replace("Z", "+00:00"))
            )
            cutoff = as_of + timedelta(hours=3)
            print("\n=== 2c. ALT Iowa State ===")
            print(
                f"  kickoff_utc={isu_ko.isoformat()} kickoff_et={isu['kickoff_et']} "
                f"cutoff(now+3h)={cutoff.isoformat()} eligible={isu_ko > cutoff}"
            )
            isu_best = isu["best_regulated"]
            if isu_best is None:
                print("  NONE")
            else:
                print(
                    f"  best={isu_best['book_display']} {isu_best['line']:+g}/{isu_best['price']:+g} "
                    f"gap={isu_best['gap']:.2f} edge={isu_best['edge']:.4f} "
                    f"gate={'PASS' if isu_best['gate_pass'] else 'FAIL:'+isu_best['gate_fail']}"
                )
            if isu_ko <= cutoff:
                print("  -> SKIP Iowa State (kickoff not after now+3h)")
            elif isu_best is not None and isu_best.get("gate_pass"):
                fill = _row_from_selected(
                    res=isu,
                    selected=isu_best,
                    source="alternate_iowa_state",
                    prior_w11_4_line=float(isu["play"]["w11_4_line"]),
                )
                fill_src = "Iowa State"
                print("  -> FILL with Iowa State")
            else:
                print("  -> Iowa State also FAIL / no fill")

        if fill is not None:
            final_rows.append(fill)
            oklahoma_action = {
                "action": "DROP_AND_FILL",
                "fill": fill_src,
                "oklahoma_best": ok_best,
                "fill_row": {
                    "matchup": fill["matchup"],
                    "side": fill["side"],
                    "book": fill["book"],
                    "line": fill["line"],
                    "price": fill["price"],
                    "edge": fill["edge"],
                    "gap": fill["model_market_residual_points"],
                },
            }
        else:
            oklahoma_action = {
                "action": "DROP_NO_FILL",
                "oklahoma_best": ok_best,
                "note": "card shrinks to 6",
            }
            print("  -> NO FILL; card will be 6 plays")

    # --- Other 6: report only ---
    print("\n=== 3. OTHER 6 (report only; no swap) ===")
    other_reports: list[dict[str, Any]] = []
    for res in card_results:
        gid = int(res["play"]["game_id"])
        if gid == OKLAHOMA_GID:
            continue
        best = res["best_regulated"]
        prior = by_gid[str(gid)]
        if best is None:
            status = "NO_QUOTE"
            flag_fail = True
            print(f"  FAIL {res['matchup']} {res['side']}: NO_QUOTE")
        else:
            flag_fail = not bool(best.get("gate_pass"))
            status = "PASS" if best.get("gate_pass") else f"FAIL:{best.get('gate_fail')}"
            print(
                f"  {status:12} {res['matchup'][:36]:36} {res['side'][:14]:14} "
                f"{best['book_display'][:14]:14} {best['line']:+g}/{best['price']:+g} "
                f"edge={best['edge']:.4f} gap={best['gap']:.2f}"
            )
            # Keep on card at new best-regulated prices even if gate fails
            # (report-only; do not swap). Per task: "flag any that now fail;
            # do not swap them" — still update to current best regulated price.
            final_rows.append(
                _row_from_selected(
                    res=res,
                    selected=best,
                    source="card_other",
                    prior_w11_4_line=float(prior.get("w11_4_line") or prior["line"]),
                )
            )
        other_reports.append(
            {
                "game_id": gid,
                "matchup": res["matchup"],
                "side": res["side"],
                "status": status,
                "gate_fail_flag": flag_fail,
                "best": best,
                "ledger_prior": {
                    "book": prior["book"],
                    "line": prior["line"],
                    "price": prior["price"],
                    "edge": prior["edge"],
                },
            }
        )
        if best is None:
            # No quote: keep prior ledger numbers so card isn't emptied.
            final_rows.append(
                {
                    "game_id": str(gid),
                    "matchup": prior["matchup"],
                    "side": prior["side"],
                    "side_is": prior["side_is"],
                    "book": prior["book"],
                    "book_display": prior.get("book_display")
                    or BOOK_DISPLAY.get(str(prior["book"]), prior["book"]),
                    "line": float(prior["line"]),
                    "price": float(prior["price"]),
                    "snapshot_event_time": prior.get("snapshot_event_time"),
                    "posted_at_utc": None,
                    "edge": float(prior["edge"]),
                    "model_line": float(prior["model_line"]),
                    "model_market_residual_points": float(
                        prior["model_market_residual_points"]
                    ),
                    "stake": STAKE,
                    "units": UNITS,
                    "ladder_rung": "odds_api_snapshot+per_book_latest",
                    "w11_4_line": float(prior.get("w11_4_line") or prior["line"]),
                    "source": "card_other_no_quote_kept_prior",
                    "kickoff_et": res["kickoff_et"],
                    "kickoff_utc": res["kickoff_utc"],
                    "gate_pass": False,
                    "gate_fail": "no_quote",
                }
            )

    # Rank final card by edge
    final_rows.sort(key=lambda r: float(r["edge"]), reverse=True)

    print("\n=== 4. FINAL CARD (by edge) ===")
    print(
        f"{'#':>2}  {'game':40}  {'kick':16}  {'side':16}  {'book':14}  "
        f"{'line':>6}  {'price':>6}  {'ridge':>7}  {'gap':>5}  {'edge':>7}"
    )
    card_table: list[dict[str, Any]] = []
    for i, row in enumerate(final_rows, 1):
        card_table.append({**row, "rank": i})
        print(
            f"{i:>2}  {row['matchup'][:40]:40}  {str(row.get('kickoff_et') or '')[:16]:16}  "
            f"{row['side'][:16]:16}  {row['book_display'][:14]:14}  "
            f"{float(row['line']):+6g}  {float(row['price']):+6g}  "
            f"{float(row['model_line']):+7.2f}  "
            f"{float(row['model_market_residual_points']):5.2f}  "
            f"{float(row['edge']):7.4f}"
        )

    flagged = [r for r in other_reports if r["gate_fail_flag"]]
    print(f"\nother6_flagged_fail={len(flagged)} oklahoma_action={oklahoma_action['action']}")

    # --- Ledger ---
    print("\n=== 5. LEDGER ===")
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
                "line": float(row["line"]),
                "price": float(row["price"]),
                "snapshot_event_time": row["snapshot_event_time"],
                "posted_at_utc": None,
                "edge": float(row["edge"]),
                "model_line": float(row["model_line"]),
                "model_market_residual_points": float(row["model_market_residual_points"]),
                "stake": STAKE,
                "units": UNITS,
                "ladder_rung": "odds_api_snapshot+per_book_latest",
                "w11_4_line": float(row["w11_4_line"]),
            }
        )
    ledger: dict[str, Any] = {
        "season": 2026,
        "week": 11,
        "label": "posted_calls",
        "n": len(calls),
        "probe_book": "regulated_mixed",
        "pricing_note": (
            "W11-6 Sat re-price: HFA=NONE; stake 0.014; max 7; regulated books "
            "(draftkings/fanduel/betmgm/williamhill_us/espnbet/fanatics/"
            "hardrockbet/ballybet/betrivers); best regulated line for ledger side; "
            "Oklahoma re-checked (fill Arizona then Iowa State if FAIL and "
            "kickoff>now+3h); other 6 re-priced report-only no swap; "
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
        "prior_snapshot_note": "W11-5 Fri ledger; W11-6 Sat Oklahoma re-price",
        "w11_6_at": as_of.isoformat(),
        "posted_calls": calls,
    }
    if superseded:
        ledger["superseded_calls"] = superseded

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
    print("n_posted", len(calls), "n_superseded", len(superseded))

    report = {
        "task": "W11-6",
        "export_enabled": False,
        "stage": stage_report,
        "week_predictions_source": wp_src,
        "publish_as_of": wp.get("as_of"),
        "pulled_at_utc": meta.get("pulled_at_utc"),
        "oklahoma_action": oklahoma_action,
        "other6": other_reports,
        "other6_flagged_fail": [
            {"game_id": r["game_id"], "matchup": r["matchup"], "status": r["status"]}
            for r in flagged
        ],
        "final_card": [
            {
                "rank": r["rank"],
                "game_id": r["game_id"],
                "matchup": r["matchup"],
                "kickoff_et": r.get("kickoff_et"),
                "side": r["side"],
                "book": r["book"],
                "book_display": r["book_display"],
                "line": r["line"],
                "price": r["price"],
                "ridge": r["model_line"],
                "gap": r["model_market_residual_points"],
                "edge": r["edge"],
            }
            for r in card_table
        ],
        "ledger_sha256": digest,
        "ledger_paths": [
            str(SOCIAL.relative_to(ROOT)).replace("\\", "/"),
            str(tracked_path.relative_to(ROOT)).replace("\\", "/"),
        ],
        "n_posted": len(calls),
        "n_superseded": len(superseded),
    }
    (ART / "w11_6_report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print("wrote", ART / "w11_6_report.json")
    assert cfg.webapp.export_enabled is False
    print("DONE export_enabled still", cfg.webapp.export_enabled)


if __name__ == "__main__":
    main()
