#!/usr/bin/env python3
"""S5 P0-5 — Is line shopping inert?

Reports n_books at bet time and whether best price differs from consensus.
Read-only. Uses staged odds_snapshots + S3 cache books. No Odds API.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts._s5_common import (  # noqa: E402
    SEASONS,
    assert_rows_match_s4,
    dump_json,
    ensure_out,
    load_graded_314,
    load_public_314_from_cache,
)
from scripts._s4_p0_clv_settle import _consensus_at, _two_way_at  # noqa: E402
from scripts._s4_phase0 import _games_lookup  # noqa: E402
from ncaa_quant.betting.clv import fair_prob_on_side  # noqa: E402
from ncaa_quant.config import load_config  # noqa: E402
from ncaa_quant.data.storage import ParquetStore  # noqa: E402
from ncaa_quant.evaluation.lockbox import assert_lockbox_excluded  # noqa: E402


def _n_books_at_tuesday(
    sn: pd.DataFrame,
    *,
    game_id: str,
    home_team: str,
) -> int:
    sub = sn.loc[
        (sn["game_id"].astype(str) == str(game_id))
        & (sn["market"].astype(str) == "spread")
        & (sn["decision_point"].astype(str) == "tuesday_0600_et")
        & (sn["side"].astype(str).str.casefold() == home_team.casefold())
    ]
    if sub.empty:
        return 0
    return int(sub["book"].nunique())


def _best_vs_consensus_price(
    sn: pd.DataFrame,
    *,
    game_id: str,
    book: str,
    home_team: str,
    away_team: str,
    bet_on: str,
    bet_line_home: float,
) -> dict[str, Any]:
    bet_q = _two_way_at(
        sn,
        game_id=game_id,
        book=book,
        decision_point="tuesday_0600_et",
        home_team=home_team,
        away_team=away_team,
        bet_on=bet_on,
        bet_line_home=bet_line_home,
    )
    if bet_q is None:
        return {"ok": False}
    cons = _consensus_at(
        sn,
        game_id=game_id,
        decision_point="tuesday_0600_et",
        home_team=home_team,
        away_team=away_team,
        bet_on=bet_on,
        event_time=bet_q["event_time"],
    )
    if cons is None:
        return {
            "ok": True,
            "bet_side_american": float(bet_q["side_american"]),
            "consensus_available": False,
        }
    p_best = fair_prob_on_side(float(bet_q["side_american"]), float(bet_q["other_american"]))
    p_cons = fair_prob_on_side(float(cons[0]), float(cons[1]))
    price_differs = abs(float(bet_q["side_american"]) - float(cons[0])) > 1e-9
    # Consensus-median book: approximate as whether bet book price equals median
    # (already computed). Book identity of "consensus median book" is not stored;
    # report price equality and shopping capture sign instead.
    return {
        "ok": True,
        "consensus_available": True,
        "bet_side_american": float(bet_q["side_american"]),
        "consensus_side_american": float(cons[0]),
        "price_differs_from_consensus": price_differs,
        "line_shopping_capture": float(p_best - p_cons),
        "n_books_available": _n_books_at_tuesday(sn, game_id=game_id, home_team=home_team),
    }


def main() -> int:
    assert_lockbox_excluded(list(SEASONS), context="S5 P0-5")
    out = ensure_out()
    graded = load_graded_314()
    cache, bets = load_public_314_from_cache()
    match = assert_rows_match_s4(graded, bets)

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
        g = g_by_id.get(str(r.game_id))
        if crow.empty or g is None:
            rows.append({"season": season, "game_id": str(r.game_id), "ok": False})
            continue
        book = str(crow.iloc[0]["book"])
        home, away = str(g.home_team), str(g.away_team)
        info = _best_vs_consensus_price(
            sn_by_season[season],
            game_id=str(r.game_id),
            book=book,
            home_team=home,
            away_team=away,
            bet_on=str(r.bet_on),
            bet_line_home=float(r.market_line_home),
        )
        info.update(
            {
                "season": season,
                "week": int(r.week),
                "game_id": str(r.game_id),
                "book": book,
            }
        )
        if "n_books_available" not in info:
            info["n_books_available"] = _n_books_at_tuesday(
                sn_by_season[season], game_id=str(r.game_id), home_team=home
            )
        rows.append(info)

    df = pd.DataFrame(rows)
    ok = df.loc[df.get("ok", False) == True].copy()  # noqa: E712

    by_season = {}
    for season in SEASONS:
        sub = df.loc[df["season"] == season]
        nb = sub["n_books_available"].dropna().astype(float)
        by_season[str(season)] = {
            "n": int(len(sub)),
            "min": int(nb.min()) if len(nb) else None,
            "median": float(nb.median()) if len(nb) else None,
            "max": int(nb.max()) if len(nb) else None,
            "share_lt_2": float((nb < 2).mean()) if len(nb) else None,
        }

    nb_all = df["n_books_available"].dropna().astype(float)
    cons = ok.loc[ok.get("consensus_available", False) == True]  # noqa: E712
    share_price_differs = (
        float(cons["price_differs_from_consensus"].mean()) if len(cons) else None
    )
    # "best-price book differs from consensus-median book": we only observe prices.
    # If price equals consensus median price, book choice is economically inert even
    # when the book name differs.
    share_price_differs_report = share_price_differs
    mean_capture = (
        float(cons["line_shopping_capture"].mean()) if len(cons) else None
    )

    payload = {
        "status": "MEASURED",
        "row_match": match,
        "should_hold": (
            "Under real shopping (ADR 0006): line_shopping_capture systematically "
            "negative (bought cheaper than consensus); best captured price often "
            "differs from consensus; n_books_available ≥ 2 on most tickets."
        ),
        "n_books_by_season": by_season,
        "n_books_overall": {
            "min": int(nb_all.min()) if len(nb_all) else None,
            "median": float(nb_all.median()) if len(nb_all) else None,
            "max": int(nb_all.max()) if len(nb_all) else None,
            "share_lt_2": float((nb_all < 2).mean()) if len(nb_all) else None,
        },
        "share_best_price_differs_from_consensus_price": share_price_differs_report,
        "n_with_consensus": int(len(cons)),
        "mean_line_shopping_capture": mean_capture,
        "s4_reference_capture": 0.00010787019194689681,
        "reading": (
            "If best price ≈ consensus price (share differing near 0) and capture ≈ 0, "
            "shopping is inert — same class as S4 MAX_TEAM_EXPOSURE silence."
        ),
        "observed": {
            "share_price_differs": share_price_differs_report,
            "mean_capture": mean_capture,
            "share_n_books_lt_2": float((nb_all < 2).mean()) if len(nb_all) else None,
        },
    }
    dump_json(out / "p0_5_shopping.json", payload)
    df.to_parquet(out / "p0_5_shopping_rows.parquet", index=False)
    print(
        f"[p0-5] share price differs={share_price_differs_report} "
        f"mean capture={mean_capture} share n_books<2="
        f"{payload['n_books_overall']['share_lt_2']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
