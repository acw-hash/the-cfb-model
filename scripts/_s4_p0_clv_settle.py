#!/usr/bin/env python3
"""S4 P0-3 follow-up — attempt §2.7 probability CLV from staged snapshots.

Rebuilds RecommendationRecord + ClosingQuote for the thr=0.05 weeks-2+ set
from staged ``odds_snapshots`` (tuesday_0600_et bet-time, slot_close close).
No Odds API. No src/ edits.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.calibrate_social_threshold import CACHE_PATH, SEASONS, _select_public  # noqa: E402

from ncaa_quant.betting.clv import (  # noqa: E402
    PROBABILITY_VALUED_METHODS,
    ClosingQuote,
    build_recommendation_record,
    settle,
    summarize_settlements,
)
from ncaa_quant.config import load_config  # noqa: E402
from ncaa_quant.data.storage import ParquetStore  # noqa: E402
from ncaa_quant.evaluation.lockbox import assert_lockbox_excluded  # noqa: E402

OUT = REPO / "docs" / "notes" / "_artifacts" / "social-s4"
THRESHOLD = 0.05


def _two_way_at(
    sn: pd.DataFrame,
    *,
    game_id: str,
    book: str,
    decision_point: str,
    home_team: str,
    away_team: str,
    bet_on: str,
    bet_line_home: float,
) -> dict[str, Any] | None:
    sub = sn.loc[
        (sn["game_id"].astype(str) == str(game_id))
        & (sn["book"].astype(str) == book)
        & (sn["market"].astype(str) == "spread")
        & (sn["decision_point"].astype(str) == decision_point)
    ].copy()
    if sub.empty:
        return None
    sub["event_time"] = pd.to_datetime(sub["event_time"], utc=True)
    # Prefer rows whose home line matches bet_line_home (within 0.51)
    home_rows = sub.loc[sub["side"].astype(str).str.casefold() == str(home_team).casefold()]
    if home_rows.empty:
        return None
    home_rows = home_rows.assign(line_f=pd.to_numeric(home_rows["line"], errors="coerce"))
    match = home_rows.loc[(home_rows["line_f"] - float(bet_line_home)).abs() <= 0.51]
    use = match if not match.empty else home_rows
    latest = use["event_time"].max()
    at = sub.loc[sub["event_time"] == latest]
    # Home / away prices at this timestamp
    h = at.loc[at["side"].astype(str).str.casefold() == str(home_team).casefold()]
    a = at.loc[at["side"].astype(str).str.casefold() == str(away_team).casefold()]
    if h.empty or a.empty:
        return None
    hrow, arow = h.iloc[0], a.iloc[0]
    home_line = float(hrow["line"])
    home_px = float(hrow["price"])
    away_px = float(arow["price"])
    if bet_on == "home":
        side_px, other_px, bet_line, side_label = home_px, away_px, home_line, home_team
        side_id, other_id = str(hrow["snapshot_id"]), str(arow["snapshot_id"])
    else:
        side_px, other_px, bet_line, side_label = away_px, home_px, -home_line, away_team
        side_id, other_id = str(arow["snapshot_id"]), str(hrow["snapshot_id"])
    return {
        "side_american": side_px,
        "other_american": other_px,
        "bet_line": bet_line,
        "home_line": home_line,
        "side_label": side_label,
        "source_row_id": side_id,
        "other_source_row_id": other_id,
        "event_time": latest.to_pydatetime(),
    }


def _consensus_at(
    sn: pd.DataFrame,
    *,
    game_id: str,
    decision_point: str,
    home_team: str,
    away_team: str,
    bet_on: str,
    event_time: datetime,
) -> tuple[float, float] | None:
    sub = sn.loc[
        (sn["game_id"].astype(str) == str(game_id))
        & (sn["market"].astype(str) == "spread")
        & (sn["decision_point"].astype(str) == decision_point)
    ].copy()
    if sub.empty:
        return None
    sub["event_time"] = pd.to_datetime(sub["event_time"], utc=True)
    # nearest timestamp at or before event_time
    eligible = sub.loc[sub["event_time"] <= pd.Timestamp(event_time)]
    if eligible.empty:
        return None
    ts = eligible["event_time"].max()
    at = eligible.loc[eligible["event_time"] == ts]
    # median price per side across books at that stamp (approx consensus)
    h = at.loc[at["side"].astype(str).str.casefold() == str(home_team).casefold(), "price"]
    a = at.loc[at["side"].astype(str).str.casefold() == str(away_team).casefold(), "price"]
    if h.empty or a.empty:
        return None
    home_px, away_px = float(h.median()), float(a.median())
    if bet_on == "home":
        return home_px, away_px
    return away_px, home_px


def main() -> int:
    assert_lockbox_excluded(list(SEASONS), context="S4 P0-3 CLV settle")
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    store = ParquetStore(cfg.paths.staged_dir)

    cache = pd.read_parquet(CACHE_PATH)
    bets = [b for b in _select_public(cache, THRESHOLD) if b.week >= 2]

    # games → team names
    from scripts._s4_phase0 import _games_lookup

    games = _games_lookup(store)
    g_by_id = {str(int(r.game_id)): r for r in games.itertuples(index=False)}

    # Batch snapshots by season
    sn_by_season: dict[int, pd.DataFrame] = {}
    for season in SEASONS:
        sn = store.read("odds_snapshots", filters={"season": int(season)})
        sn = sn.loc[sn["game_id"].notna()].copy()
        sn["game_id"] = sn["game_id"].astype("Int64").astype(str)
        sn_by_season[int(season)] = sn

    settled = []
    failures: list[dict[str, Any]] = []
    for b in bets:
        crow = cache.loc[
            (cache["season"] == b.season)
            & (cache["week"] == b.week)
            & (cache["game_id"].astype(str) == str(b.game_id))
        ]
        if crow.empty:
            failures.append({"game_id": b.game_id, "reason": "cache_row_missing"})
            continue
        book = str(crow.iloc[0]["book"])
        g = g_by_id.get(str(b.game_id))
        if g is None:
            failures.append({"game_id": b.game_id, "reason": "game_missing"})
            continue
        home, away = str(g.home_team), str(g.away_team)
        sn = sn_by_season[int(b.season)]

        bet_quote = _two_way_at(
            sn,
            game_id=str(b.game_id),
            book=book,
            decision_point="tuesday_0600_et",
            home_team=home,
            away_team=away,
            bet_on=str(b.bet_on),
            bet_line_home=float(b.market_line_home),
        )
        if bet_quote is None:
            failures.append({"game_id": b.game_id, "reason": "no_tuesday_two_way"})
            continue

        close_quote = _two_way_at(
            sn,
            game_id=str(b.game_id),
            book=book,
            decision_point="slot_close",
            home_team=home,
            away_team=away,
            bet_on=str(b.bet_on),
            bet_line_home=float(b.market_line_home),  # prefer same line; else latest close
        )
        # If close line moved, still take latest slot_close two-way at that book
        if close_quote is None:
            # retry without line match by passing extreme tolerance via re-call on latest close
            sub = sn.loc[
                (sn["game_id"] == str(b.game_id))
                & (sn["book"] == book)
                & (sn["market"] == "spread")
                & (sn["decision_point"] == "slot_close")
            ].copy()
            if sub.empty:
                failures.append({"game_id": b.game_id, "reason": "no_slot_close"})
                continue
            sub["event_time"] = pd.to_datetime(sub["event_time"], utc=True)
            latest = sub["event_time"].max()
            at = sub.loc[sub["event_time"] == latest]
            h = at.loc[at["side"].astype(str).str.casefold() == home.casefold()]
            a = at.loc[at["side"].astype(str).str.casefold() == away.casefold()]
            if h.empty or a.empty:
                failures.append({"game_id": b.game_id, "reason": "close_sides_incomplete"})
                continue
            hrow, arow = h.iloc[0], a.iloc[0]
            home_line = float(hrow["line"])
            if b.bet_on == "home":
                close_quote = {
                    "side_american": float(hrow["price"]),
                    "other_american": float(arow["price"]),
                    "bet_line": home_line,
                    "home_line": home_line,
                    "source_row_id": str(hrow["snapshot_id"]),
                }
            else:
                close_quote = {
                    "side_american": float(arow["price"]),
                    "other_american": float(hrow["price"]),
                    "bet_line": -home_line,
                    "home_line": home_line,
                    "source_row_id": str(arow["snapshot_id"]),
                }

        cons = _consensus_at(
            sn,
            game_id=str(b.game_id),
            decision_point="tuesday_0600_et",
            home_team=home,
            away_team=away,
            bet_on=str(b.bet_on),
            event_time=bet_quote["event_time"],
        )

        rec = build_recommendation_record(
            recommendation_id=f"{b.season}-{b.week}-{b.game_id}-{b.bet_on}",
            game_id=str(b.game_id),
            season=int(b.season),
            week=int(b.week),
            side=str(bet_quote["side_label"]),
            edge=float(b.edge),
            bet_side_american=float(bet_quote["side_american"]),
            bet_other_american=float(bet_quote["other_american"]),
            recommended_at=bet_quote["event_time"],
            close_definition="odds_api_consensus",
            book=book,
            market="spread",
            bet_line=float(bet_quote["bet_line"]),
            bet_line_source_row_id=str(bet_quote["source_row_id"]),
            consensus_side_american=None if cons is None else float(cons[0]),
            consensus_other_american=None if cons is None else float(cons[1]),
        )
        close = ClosingQuote(
            side_american=float(close_quote["side_american"]),
            other_american=float(close_quote["other_american"]),
            book=book,
            line=float(close_quote["bet_line"]),
            source_row_id=str(close_quote["source_row_id"]),
        )
        try:
            settled.append(settle(rec, same_book_close=close))
        except Exception as exc:  # noqa: BLE001
            failures.append({"game_id": b.game_id, "reason": f"settle_error:{exc}"})

    # Summarize unpooled
    if settled:
        # fake week aggregate across all — still stratify by settlement/method
        report = summarize_settlements(
            settled, season=0, week=0, close_definition="odds_api_consensus"
        )
        headline = [
            s
            for s in settled
            if s.clv_settlement == "same_book" and s.clv_method in PROBABILITY_VALUED_METHODS
        ]
        fallback = [
            s
            for s in settled
            if s.clv_settlement == "fallback_consensus"
            and s.clv_method in PROBABILITY_VALUED_METHODS
        ]
        units = [s for s in settled if s.clv_method == "line_units"]

        def _block(label: str, rows: list) -> dict[str, Any]:
            vals = [float(s.clv) for s in rows if np.isfinite(float(s.clv))]
            if label == "line_units":
                vals = [float(s.clv_line_units) for s in rows if np.isfinite(float(s.clv_line_units))]
            arr = np.asarray(vals, dtype=float)
            if arr.size == 0:
                return {"label": label, "n": 0}
            mean = float(arr.mean())
            se = float(arr.std(ddof=1) / np.sqrt(arr.size)) if arr.size >= 2 else float("nan")
            return {
                "label": label,
                "n": int(arr.size),
                "mean": mean,
                "pct_positive": float(np.mean(arr > 0)),
                "ci_95": [mean - 1.96 * se, mean + 1.96 * se]
                if np.isfinite(se)
                else [None, None],
            }

        shopping = np.asarray(
            [float(s.line_shopping_capture) for s in settled], dtype=float
        )
        shopping = shopping[np.isfinite(shopping)]

        method_counts = {}
        for s in settled:
            method_counts[s.clv_method] = method_counts.get(s.clv_method, 0) + 1

        out = {
            "status": "MEASURED",
            "n_attempted": len(bets),
            "n_settled": len(settled),
            "n_failures": len(failures),
            "failure_reasons": pd.Series([f["reason"] for f in failures]).value_counts().to_dict()
            if failures
            else {},
            "method_counts": method_counts,
            "headline_same_book_probability_valued": _block("same_book", headline),
            "fallback_consensus": _block("fallback_consensus", fallback),
            "line_units": _block("line_units", units),
            "mean_line_shopping_capture": {
                "value": float(np.mean(shopping)) if shopping.size else None,
                "n": int(shopping.size),
                "adr_0006_sign": (
                    "implied(best@bet) − implied(consensus@bet); "
                    "negative ⇒ bought cheaper than consensus (not a skill credit)"
                ),
            },
            "summarize_settlements_headline_n": report.n_bets,
            "summarize_settlements_mean_clv": report.mean_clv,
            "note": (
                "Bet-time quote taken from decision_point=tuesday_0600_et at the "
                "cached book; close from slot_close at the same book. "
                "S3 as_of is week_decision_as_of (may differ slightly from the "
                "named tuesday slot); treat as staged-only approximation."
            ),
        }
    else:
        out = {
            "status": "NOT MEASURED",
            "n_attempted": len(bets),
            "n_settled": 0,
            "n_failures": len(failures),
            "failure_reasons": pd.Series([f["reason"] for f in failures]).value_counts().to_dict()
            if failures
            else {},
        }

    (OUT / "p0_3_clv_settle.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
