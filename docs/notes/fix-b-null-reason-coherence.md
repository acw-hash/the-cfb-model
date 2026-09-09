# Fix B follow-on — populate null_reason on coherence-gate suppress

**Date:** 2026-09-08  
**Status:** Implemented. No publish.

## Cause (from prior stop)

`apply_margin_interval_coherence_gate` nulls `margin_interval_*` when
pre-CQR heads fail `q10 < mu < q90`. It previously left `null_reason` unset.

## Change

- Gate returns `(lo, hi, nominal, reason)`; reason is
  `incoherent_margin_interval` when suppressing, else `None`.
- `build_game_prediction` writes that onto the published row unless an
  upstream ADR 0014 `null_reason` is already set. Credibility still reads the
  **input** row, so this is not a σ-refusal.
- Frontend: `null_reason` remains `string | null` (not a closed enum). Added
  `NULL_REASON_LABELS` in `lib/results/copy.ts`; `nullReasonFootnote` looks
  them up. Game Detail margin interval absent line prefers the footnote.
- Betting-language ratchet pin re-measured to 618/465/103 (HEAD content was
  already one match above the old pin; our edits added zero union hits).

## Tests

Unit: incoherent export → null interval + `null_reason ==
"incoherent_margin_interval"`; coherent → interval present + `null_reason`
null. `make test`: 1052 passed, 1 deselected.
