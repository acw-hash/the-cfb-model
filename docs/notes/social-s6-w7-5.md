# W7-5 — rebuild W7 card under W6 rules (decision record)

**Date:** 2026-10-13T14:32:17-04:00  
**Authority:** W7-BT2 §4; W7-2 DECISIONS withdrawn; W6 card gates; W7-3 best-line.

## DECISIONS (committed before any card rerun)

- **HFA: NONE.** Reason: W7-BT2 §4. 4.56 is the model's full home-field
  term (Enet intercept × stack weight), not an excess; measured excess
  over market is +0.89 pts (95% CI 0.63–1.15). The ROBUST rule (W7-2) is
  withdrawn.
- **Card rule = W6 logic:** edge ≥ 0.045, raw |resid| ≤ 7.0, snapshot ≤ 6h,
  QB starter on both sides. Max 6 plays by edge, stake 0.015.
- **Best line:** all regulated books, as in W7-3.
- **Radar:** up to 2 plays that fail only on QB, same ranking.
- **Thread wording:** no edge %, no "calibrated" (see W7-BT2).

Runtime flip: set `HFA_MODE = "NONE"` in `scripts/_s6_w7_card.py` only after
this decision commit. No card / thread / ledger until QB statuses are entered.
