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

## Decision commit

`1e8b3e87b08fb0aa9e4c97261d5b1540e28ce153` (pushed to `main`).

## Rerun (STOP before card)

- `HFA_MODE = "NONE"` (only logic flip in `_s6_w7_card.py`).
- Fresh Odds API: `spreads` × `us,us2`, 59 W7 eventIds.
  Credits this call: **2**; remaining **475**.
  Raw: `docs/notes/_artifacts/social-s6-w7-card/odds_allbooks_20261013T183657Z.json`.
- Probe: `_w7_5_rebuild.py` → `w7_5_rebuild_report.json`.
- W7-3 Kent State / Missouri rows marked `superseded` / `w7_5_superseded`
  in `candidates_w7.json` and both `posted_calls_2026_w7.json` copies
  (never posted; nothing deleted).
- QB: reuse existing rows; research prompts printed for marked-10 teams
  with no row. **Stopped** — no card / thread / ledger finalize.
