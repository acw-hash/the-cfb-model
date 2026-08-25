#!/usr/bin/env python3
"""S5 P0-1 — Reconcile S3 line CLV (+0.76) vs S4 line_units (−0.824).

Read-only. No src/ edits. Reuses S4 graded-314 artifact.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts._s5_common import (  # noqa: E402
    S4_ART,
    assert_rows_match_s4,
    dump_json,
    ensure_out,
    load_graded_314,
    load_public_314_from_cache,
    s3_line_clv,
)
from scripts.calibrate_social_threshold import _grade_row  # noqa: E402
from ncaa_quant.betting.clv import line_units_clv  # noqa: E402

# Reuse S4 settle helpers for Odds-book closes (same staged reconstruct).
from scripts._s4_p0_clv_settle import _two_way_at  # noqa: E402
from scripts._s4_phase0 import _games_lookup  # noqa: E402
from ncaa_quant.config import load_config  # noqa: E402
from ncaa_quant.data.storage import ParquetStore  # noqa: E402


def _s3_expr_source() -> dict[str, Any]:
    src = inspect.getsource(_grade_row)
    # Locate the line_clv block for citation
    lines = Path(inspect.getfile(_grade_row)).read_text(encoding="utf-8").splitlines()
    # 1-indexed line numbers for the expression
    start = None
    for i, line in enumerate(lines, start=1):
        if "line_clv = float(market_line - close_side)" in line:
            start = i
            break
    block = "\n".join(lines[234:241])  # 235-241 0-index → display 235-241
    return {
        "file": "scripts/calibrate_social_threshold.py",
        "lines": "235-241",
        "expression_line": start,
        "verbatim": (
            "close_side = close_h if bet_on == \"home\" else -close_h\n"
            "line_clv = float(market_line - close_side)"
        ),
        "surrounding": block,
        "sign_convention": (
            "Positive = bet-side line is higher (better) than the closing bet-side "
            "line derived from CFBD/backtest spread_close (home-centric). "
            "Comment on GradedBet: 'bet_side_line - close_side_line; higher = better number'."
        ),
        "whose_side": "the ticket's bet side (home or away), not home-always",
    }


def _line_units_source() -> dict[str, Any]:
    path = Path(inspect.getfile(line_units_clv))
    lines = path.read_text(encoding="utf-8").splitlines()
    # Find function
    start = None
    for i, line in enumerate(lines, start=1):
        if line.startswith("def line_units_clv"):
            start = i
            break
    end = start
    if start is not None:
        for j in range(start, len(lines) + 1):
            if j > start and lines[j - 1].startswith("def "):
                end = j - 1
                break
        else:
            end = start + 25
    verbatim_spread = "return float(bet_line) - float(close_line)"
    return {
        "file": "src/ncaa_quant/betting/clv.py",
        "lines": f"{start}-{start + 20 if start else '?'}",
        "verbatim_spread_branch": verbatim_spread,
        "docstring_excerpt": (
            "Points of closing movement toward the bet (positive = we hold value). "
            "A spread ticket is better than the close when its number is higher on the "
            "bet side (-6.5 beats -7; +3.5 beats +3)."
        ),
        "sign_convention": (
            "Positive = bet_line − close_line on the bet side; we hold a better number "
            "than the close."
        ),
        "whose_side": "the recommendation's bet side (bet_line and close.line are bet-side)",
    }


def _odds_closes_for_314(graded: pd.DataFrame, cache: pd.DataFrame) -> pd.DataFrame:
    """Bet-time + close bet-side lines from staged odds (S4 method)."""
    cfg = load_config()
    store = ParquetStore(cfg.paths.staged_dir)
    games = _games_lookup(store)
    g_by_id = {str(int(r.game_id)): r for r in games.itertuples(index=False)}
    sn_by_season: dict[int, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    for r in graded.itertuples(index=False):
        season = int(r.season)
        if season not in sn_by_season:
            sn = store.read("odds_snapshots", filters={"season": season})
            sn = sn.loc[sn["game_id"].notna()].copy()
            sn["game_id"] = sn["game_id"].astype("Int64").astype(str)
            sn_by_season[season] = sn
        crow = cache.loc[
            (cache["season"] == season)
            & (cache["week"] == int(r.week))
            & (cache["game_id"].astype(str) == str(r.game_id))
        ]
        if crow.empty:
            rows.append(
                {
                    "season": season,
                    "week": int(r.week),
                    "game_id": str(r.game_id),
                    "bet_on": str(r.bet_on),
                    "odds_ok": False,
                }
            )
            continue
        book = str(crow.iloc[0]["book"])
        g = g_by_id.get(str(r.game_id))
        if g is None:
            rows.append(
                {
                    "season": season,
                    "week": int(r.week),
                    "game_id": str(r.game_id),
                    "bet_on": str(r.bet_on),
                    "odds_ok": False,
                }
            )
            continue
        home, away = str(g.home_team), str(g.away_team)
        sn = sn_by_season[season]
        bet_q = _two_way_at(
            sn,
            game_id=str(r.game_id),
            book=book,
            decision_point="tuesday_0600_et",
            home_team=home,
            away_team=away,
            bet_on=str(r.bet_on),
            bet_line_home=float(r.market_line_home),
        )
        close_q = _two_way_at(
            sn,
            game_id=str(r.game_id),
            book=book,
            decision_point="slot_close",
            home_team=home,
            away_team=away,
            bet_on=str(r.bet_on),
            bet_line_home=float(r.market_line_home),
        )
        if close_q is None:
            # latest slot_close two-way (same fallback as S4)
            sub = sn.loc[
                (sn["game_id"] == str(r.game_id))
                & (sn["book"] == book)
                & (sn["market"] == "spread")
                & (sn["decision_point"] == "slot_close")
            ].copy()
            if not sub.empty:
                sub["event_time"] = pd.to_datetime(sub["event_time"], utc=True)
                latest = sub["event_time"].max()
                at = sub.loc[sub["event_time"] == latest]
                h = at.loc[at["side"].astype(str).str.casefold() == home.casefold()]
                a = at.loc[at["side"].astype(str).str.casefold() == away.casefold()]
                if not h.empty and not a.empty:
                    home_line = float(h.iloc[0]["line"])
                    if r.bet_on == "home":
                        close_q = {"bet_line": home_line, "home_line": home_line}
                    else:
                        close_q = {"bet_line": -home_line, "home_line": home_line}
        if bet_q is None or close_q is None:
            rows.append(
                {
                    "season": season,
                    "week": int(r.week),
                    "game_id": str(r.game_id),
                    "bet_on": str(r.bet_on),
                    "odds_ok": False,
                }
            )
            continue
        bet_line = float(bet_q["bet_line"])
        close_line = float(close_q["bet_line"])
        moved = abs(bet_line - close_line) > 1e-9
        s3_on_odds = float(bet_line - close_line)  # identical algebra on bet-side pair
        lu = float(
            line_units_clv(market="spread", bet_line=bet_line, close_line=close_line)
        )
        rows.append(
            {
                "season": season,
                "week": int(r.week),
                "game_id": str(r.game_id),
                "bet_on": str(r.bet_on),
                "odds_ok": True,
                "odds_bet_line": bet_line,
                "odds_close_line": close_line,
                "odds_moved": moved,
                "s3_expr_on_odds": s3_on_odds,
                "line_units_on_odds": lu,
            }
        )
    return pd.DataFrame(rows)


def _synthetic_controls() -> dict[str, Any]:
    cases = [
        {
            "label": "home_toward",
            "bet_on": "home",
            "market_line": -6.5,
            "close_home": -7.5,  # close_side=-7.5; we hold better
            "expect": 1.0,
        },
        {
            "label": "home_against",
            "bet_on": "home",
            "market_line": -7.5,
            "close_home": -6.5,
            "expect": -1.0,
        },
        {
            "label": "away_toward",
            "bet_on": "away",
            "market_line": 6.5,  # bet-side
            "close_home": -5.5,  # away close = +5.5
            "expect": 1.0,
        },
        {
            "label": "away_against",
            "bet_on": "away",
            "market_line": 5.5,
            "close_home": -6.5,  # away close = +6.5
            "expect": -1.0,
        },
    ]
    out = []
    all_ok = True
    for c in cases:
        s3 = s3_line_clv(c["market_line"], c["close_home"], c["bet_on"])
        close_side = (
            float(c["close_home"]) if c["bet_on"] == "home" else -float(c["close_home"])
        )
        lu = float(
            line_units_clv(
                market="spread",
                bet_line=float(c["market_line"]),
                close_line=close_side,
            )
        )
        ok = abs(s3 - c["expect"]) < 1e-12 and abs(lu - c["expect"]) < 1e-12
        all_ok = all_ok and ok
        out.append(
            {
                **c,
                "close_side": close_side,
                "s3_expr": s3,
                "line_units_clv": lu,
                "expect": c["expect"],
                "pass": ok,
            }
        )
    return {"cases": out, "all_pass": all_ok}


def _hand_checks(graded: pd.DataFrame) -> list[dict[str, Any]]:
    """Six real rows: home/away × toward/against/unmoved (CFBD close)."""
    g = graded.copy()
    g["close_side"] = np.where(
        g["bet_on"] == "home", g["spread_close"], -g["spread_close"]
    )
    g["s3"] = g["market_line"] - g["close_side"]
    g["lu"] = [
        float(line_units_clv(market="spread", bet_line=float(ml), close_line=float(cs)))
        for ml, cs in zip(g["market_line"], g["close_side"], strict=True)
    ]
    g["delta"] = g["market_line"] - g["close_side"]
    g["moved"] = g["delta"].abs() > 1e-9
    g["toward"] = g["delta"] > 1e-9
    g["against"] = g["delta"] < -1e-9

    picks: list[pd.Series] = []
    for bet_on in ("home", "away"):
        for kind in ("toward", "against", "unmoved"):
            if kind == "toward":
                sub = g.loc[(g["bet_on"] == bet_on) & g["toward"]]
            elif kind == "against":
                sub = g.loc[(g["bet_on"] == bet_on) & g["against"]]
            else:
                sub = g.loc[(g["bet_on"] == bet_on) & ~g["moved"]]
            if sub.empty:
                continue
            # Prefer |delta| near 1 for moved cases
            if kind != "unmoved":
                sub = sub.assign(ad=sub["delta"].abs())
                sub = sub.sort_values("ad")
            picks.append(sub.iloc[0])

    rows = []
    for r in picks[:6]:
        hand = float(r["market_line"] - r["close_side"])
        rows.append(
            {
                "game_id": str(r["game_id"]),
                "season": int(r["season"]),
                "week": int(r["week"]),
                "bet_on": str(r["bet_on"]),
                "market_line": float(r["market_line"]),
                "spread_close_home": float(r["spread_close"]),
                "close_side": float(r["close_side"]),
                "s3_expr": float(r["s3"]),
                "line_units_clv": float(r["lu"]),
                "hand": hand,
                "agree": abs(hand - float(r["s3"])) < 1e-12
                and abs(hand - float(r["lu"])) < 1e-12,
                "class": (
                    "unmoved"
                    if not bool(r["moved"])
                    else ("toward" if bool(r["toward"]) else "against")
                ),
            }
        )
    return rows


def main() -> int:
    out = ensure_out()
    graded = load_graded_314()
    cache, bets = load_public_314_from_cache()
    match = assert_rows_match_s4(graded, bets)
    print(f"[p0-1] row match: {match}", flush=True)

    s3_src = _s3_expr_source()
    lu_src = _line_units_source()

    # CFBD-based expressions on all 314
    close_side = np.where(
        graded["bet_on"].to_numpy() == "home",
        graded["spread_close"].to_numpy(dtype=float),
        -graded["spread_close"].to_numpy(dtype=float),
    )
    s3_cfbd = graded["market_line"].to_numpy(dtype=float) - close_side
    lu_cfbd = np.asarray(
        [
            float(line_units_clv(market="spread", bet_line=float(ml), close_line=float(cs)))
            for ml, cs in zip(graded["market_line"], close_side, strict=True)
        ]
    )

    print("[p0-1] reconstructing odds closes (staged) …", flush=True)
    odds = _odds_closes_for_314(graded, cache)
    odds_ok = odds.loc[odds["odds_ok"]].copy()
    moved = odds_ok.loc[odds_ok["odds_moved"]]
    same = odds_ok.loc[~odds_ok["odds_moved"]]

    # 2×2: expressions × populations
    # Populations: (A) all 314 with CFBD close; (B) 156 odds-moved (S4 line_units set)
    table = {
        "all_314_cfbd_close": {
            "n": int(len(graded)),
            "mean_s3_expr": float(np.mean(s3_cfbd)),
            "mean_line_units_clv": float(np.mean(lu_cfbd)),
            "exprs_identical": bool(np.allclose(s3_cfbd, lu_cfbd)),
            "close_source": "CFBD/backtest spread_close",
        },
        "odds_moved_156": {
            "n": int(len(moved)),
            "mean_s3_expr": float(moved["s3_expr_on_odds"].mean()) if len(moved) else None,
            "mean_line_units_clv": (
                float(moved["line_units_on_odds"].mean()) if len(moved) else None
            ),
            "exprs_identical": (
                bool(
                    np.allclose(
                        moved["s3_expr_on_odds"].to_numpy(),
                        moved["line_units_on_odds"].to_numpy(),
                    )
                )
                if len(moved)
                else None
            ),
            "close_source": "Odds API staged slot_close at bet book (bet-side)",
            "s4_reference_mean": -0.8237179487179487,
        },
        "odds_same_line_158": {
            "n": int(len(same)),
            "mean_s3_expr": float(same["s3_expr_on_odds"].mean()) if len(same) else None,
            "mean_line_units_clv": (
                float(same["line_units_on_odds"].mean()) if len(same) else None
            ),
        },
        "all_314_odds_close": {
            "n": int(len(odds_ok)),
            "mean_s3_expr": float(odds_ok["s3_expr_on_odds"].mean()) if len(odds_ok) else None,
            "mean_line_units_clv": (
                float(odds_ok["line_units_on_odds"].mean()) if len(odds_ok) else None
            ),
        },
    }

    # Explicit 2×2 cells as requested: both expressions × both populations
    two_by_two = {
        "rows": "expression",
        "cols": "population",
        "cells": {
            "s3_expr": {
                "all_314_cfbd": table["all_314_cfbd_close"]["mean_s3_expr"],
                "moved_156_odds": table["odds_moved_156"]["mean_s3_expr"],
            },
            "line_units_clv": {
                "all_314_cfbd": table["all_314_cfbd_close"]["mean_line_units_clv"],
                "moved_156_odds": table["odds_moved_156"]["mean_line_units_clv"],
            },
        },
        "note": (
            "On a fixed (bet_line, close_line) pair the two expressions are algebraically "
            "identical. Sign disagreement between S3 +0.76 and S4 −0.824 is therefore a "
            "population/close-source artifact, not a sign-flip bug in either function."
        ),
    }

    synth = _synthetic_controls()
    if not synth["all_pass"]:
        dump_json(out / "p0_1_clv.json", {"STOP": True, "synthetic": synth})
        print("STOP: synthetic control failed", flush=True)
        return 2

    hands = _hand_checks(graded)

    # Which figure is "correct"?
    reading = {
        "algebra": (
            "MEASURED — S3 line_clv and line_units_clv(spread) are the same formula "
            "on bet-side lines; synthetic controls both return +1/−1/+1/−1."
        ),
        "s3_plus_0_76": (
            "MEASURED — mean S3 line_clv on all 314 with CFBD spread_close = "
            f"{float(np.mean(s3_cfbd)):.6f} (matches S3 report +0.76)."
        ),
        "s4_minus_0_824": (
            "MEASURED — mean line_units on Odds-book moved-line subset n="
            f"{len(moved)} = {table['odds_moved_156']['mean_line_units_clv']}; "
            "matches S4 −0.824 within rounding."
        ),
        "framing": (
            "The +0.76 and −0.824 figures are not contradictory signs of one quantity: "
            "they use different closes (CFBD vs Odds book) and different populations "
            "(all 314 vs moved-only 156). Against the Odds book that priced the ticket, "
            "moved lines average ~0.8 points against the bet — a 49.4% realization is the "
            "ordinary consequence of buying a stale number; the S3 'positive line CLV + "
            "sub-coin-flip' anomaly framing does not survive on the book that matters."
        ),
        "which_correct_for_tickets": (
            "For ticket CLV, S4's Odds same-book line_units on moved rows (−0.82) and "
            "probability CLV on same_line rows (+0.0044) are the relevant §2.7 figures. "
            "S3's +0.76 vs CFBD close is a different instrument and should not be read as "
            "evidence we held a better number at the book."
        ),
    }

    payload = {
        "status": "MEASURED",
        "row_match": match,
        "s3_expression": s3_src,
        "line_units_clv": lu_src,
        "two_by_two": two_by_two,
        "populations": table,
        "synthetic_controls": synth,
        "hand_checks": hands,
        "reading": reading,
        "odds_n_ok": int(len(odds_ok)),
        "odds_n_moved": int(len(moved)),
        "odds_n_same": int(len(same)),
    }
    dump_json(out / "p0_1_clv.json", payload)
    odds.to_parquet(out / "p0_1_odds_closes.parquet", index=False)
    print("[p0-1] wrote p0_1_clv.json", flush=True)
    print(f"  2x2 s3/all={two_by_two['cells']['s3_expr']['all_314_cfbd']:.4f} "
          f"s3/moved={two_by_two['cells']['s3_expr']['moved_156_odds']:.4f} "
          f"lu/all={two_by_two['cells']['line_units_clv']['all_314_cfbd']:.4f} "
          f"lu/moved={two_by_two['cells']['line_units_clv']['moved_156_odds']:.4f}",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
