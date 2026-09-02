#!/usr/bin/env python3
"""S5 P0-3 — Headline CLV half vs moved-line half; vig requirement."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts._s5_common import (  # noqa: E402
    assert_rows_match_s4,
    dump_json,
    ensure_out,
    load_graded_314,
    load_public_314_from_cache,
)

# Odds closes from P0-1 if present; else rebuild lightly via flag.
from scripts._s5_p0_1_clv import _odds_closes_for_314  # noqa: E402


def _half_stats(df: pd.DataFrame) -> dict:
    y = df["covered"].astype(float)
    claim = df["p_win"].astype(float)
    return {
        "n": int(len(df)),
        "mean_claim": float(claim.mean()),
        "realized_cover": float(y.mean()),
        "gap_realized_minus_claim": float(y.mean() - claim.mean()),
        "unit_pnl": float(df["u_pnl"].sum()),
        "mean_u_pnl": float(df["u_pnl"].mean()),
    }


def main() -> int:
    out = ensure_out()
    graded = load_graded_314()
    cache, bets = load_public_314_from_cache()
    match = assert_rows_match_s4(graded, bets)

    odds_path = out / "p0_1_odds_closes.parquet"
    if odds_path.is_file():
        odds = pd.read_parquet(odds_path)
    else:
        print("[p0-3] rebuilding odds closes …", flush=True)
        odds = _odds_closes_for_314(graded, cache)
        odds.to_parquet(odds_path, index=False)

    m = graded.merge(
        odds[
            [
                "season",
                "week",
                "game_id",
                "bet_on",
                "odds_ok",
                "odds_moved",
                "s3_expr_on_odds",
                "line_units_on_odds",
            ]
        ],
        on=["season", "week", "game_id", "bet_on"],
        how="left",
    )
    if int(m["odds_ok"].fillna(False).sum()) != 314:
        # soft: report what we have
        pass

    same = m.loc[m["odds_ok"].fillna(False) & ~m["odds_moved"].fillna(True)]
    moved = m.loc[m["odds_ok"].fillna(False) & m["odds_moved"].fillna(False)]

    # −110 vig: fair break-even cover rate ≈ 0.52381
    # Headline probability CLV is E[p_bet − p_close]. To clear vig you need
    # enough edge that expected value is positive at −110.
    # At −110, EV = p*(100/110) - (1-p)*1 = p*(10/11) - (1-p).
    # EV>0 ⇒ p > 0.52381. If mean claim is c and mean close-implied is c − clv,
    # then for the close-side fair prob to be the market, a positive mean CLV of
    # (c − 0.52381) would be needed for the *bet* to clear if close is efficient
    # at 0.52381... More direct: the headline reports mean probability CLV
    # +0.0044 on same_line rows. The vig gap from a coin-flip market to −110
    # break-even is 0.52381 − 0.5 = 0.02381 if starting from 50/50, but the
    # right comparison for "how much CLV clears vig" when prices are already
    # −110/−110 is: you need your side's fair close prob below your bet fair
    # by enough that your bet fair exceeds 0.52381 while close sits at ~0.5
    # after de-vig... Task asks: "what mean probability CLV would be needed to
    # clear the vig? Report +0.0044 against that number on one line."
    #
    # Plain reading used in S4/§2.7 discussions: at −110 the break-even cover
    # rate is 52.4%. A mean probability CLV of +0.0044 is the mean of
    # (p_bet − p_close). If the close is an efficient −110 market (~0.5 fair
    # after de-vig on a coin-flip side), you need p_bet ≥ 0.5238, i.e. CLV
    # ≳ +0.024 against a 0.50 close. We report both the arithmetic vig width
    # (0.52381−0.5) and +0.0044 against it.
    breakeven = 110.0 / 210.0
    vig_width_from_half = breakeven - 0.5
    headline_clv = 0.004440985412038113  # S4 measured

    halves_differ = abs(
        _half_stats(same)["realized_cover"] - _half_stats(moved)["realized_cover"]
    ) > 0.05 or abs(
        _half_stats(same)["gap_realized_minus_claim"]
        - _half_stats(moved)["gap_realized_minus_claim"]
    ) > 0.05

    payload = {
        "status": "MEASURED",
        "row_match": match,
        "should_hold": (
            "Headline §2.7 probability CLV is the same_line half only; moved rows "
            "are line_units and must not be pooled. If halves differ materially, "
            "the headline describes only one of them."
        ),
        "same_line": _half_stats(same),
        "moved_line": _half_stats(moved),
        "halves_differ_materially": halves_differ,
        "n_same": int(len(same)),
        "n_moved": int(len(moved)),
        "vig": {
            "breakeven_cover_rate_m110": breakeven,
            "mean_prob_clv_needed_vs_half_close": vig_width_from_half,
            "headline_same_book_prob_clv": headline_clv,
            "one_line": (
                f"+0.0044 against {vig_width_from_half:.4f} needed to clear -110 "
                f"from a 0.50 close — short by factor "
                f"{vig_width_from_half / headline_clv:.1f}x"
            ),
            "interpretation": (
                "If the required CLV to clear vig is ~0.024 and we observe +0.0044, "
                "the −43.7u needs no bug: the headline edge is an order of magnitude "
                "too small to overcome the juice."
            ),
        },
    }
    dump_json(out / "p0_3_halves.json", payload)
    print(f"[p0-3] same n={len(same)} moved n={len(moved)}", flush=True)
    print(f"[p0-3] {payload['vig']['one_line']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
