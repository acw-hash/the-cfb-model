# AUDIT-2 — CLV definition repair

**Date:** 2026-08-06
**Scope:** Documentation only (no code changes).

## Problem

Two mechanical inflations / mismeasures in the prior CLV definition:

1. **Book mismatch.** Edge is priced at the best captured book, but CLV was settled
   against consensus close. Shopping a soft book vs a softer consensus close can
   produce positive CLV with zero model skill.
2. **Line mismatch.** Probability-space CLV that de-vigs the closing *price* at the
   closing *line* does not price the bettor's ticket when the spread/total has moved
   (e.g. −6.5 → −7 at unchanged −110/−110 → naive price-only CLV ≡ 0).

## Changes

### `docs/DESIGN.md`

| Section | Action |
|---|---|
| **§1.6 Primary** | Restated to same-book, line-translated CLV; fallback-consensus excluded; `line_shopping_capture` excluded from skill criterion |
| **§2.7** | Rewrote CLV: same-book settlement + `clv_settlement` flag; same-line probability definition; line-translation priority (`alt_line_price` → `model_dist` → `line_units`) with `clv_method`; added `line_shopping_capture` |
| **§3.4** | Soft-book close warning updated so consensus is flagged fallback only, not the primary settlement instrument |
| **§7.2 items 7–8** | Backtest / line-source regime now require same-book stratification and companion `line_shopping_capture` |
| **§7.3 Tier 1** | Headline CLV = same-book + probability-valued methods; shopping capture alongside, not in skill/promotion |
| **§12** | CLV settlement stores book identity + same-book close; shopping capture separate from edge/CLV |

### `docs/TASKS.md` — Task 20

- Deliverable 2: emit `line_shopping_capture`; Task 21 must not fold it into CLV.
- Deliverable 6: same-book settlement, fallback flag, line translation, `clv_method`.
- Tests: −6.5→−7 / −110/−110 fixture with fixed margin distribution; hand-compute
  `model_dist` CLV; assert naive price-only differs; assert same-book vs fallback
  rows are not pooled.

## Grep: "consensus close" / settling CLV against consensus

| Location | Verdict |
|---|---|
| §2.7 `clv_settlement=fallback_consensus` | **Keep** — explicit fallback path; never pooled |
| §1.6 / §7.2 / §7.3 / §12 / Task 20 | **Keep** — all say fallback only or diagnostic storage |
| §3.2 Sportsbook APIs row: "else consensus close" | **Justify / leave** — data-source preference for a sharp closing *benchmark* when Pinnacle-style feed is unavailable; not the CLV settlement formula. Primary settlement remains same-book (§2.7) |
| §2.7 ATS label: "closing consensus spread" | **Justify / leave** — ATS evaluation label, not CLV settlement |
| `docs/adr/0002-historical-odds-source.md` | **Leave** — historical ADR recording AUDIT-1 / odds-source change; superseded living text is DESIGN §2.7 |
| `docs/notes/20.md` | **Leave** — Task 20 completion notes under the *old* definition; living spec is DESIGN + Task 20 prompt. Code repair is a future implementation pass, not this audit |
| `src/ncaa_quant/betting/clv.py` docstring ("Attach consensus close…") | **Out of scope** — documentation-only audit; implementation still matches pre-AUDIT-2 behavior and must be brought into line when betting-layer CLV is next edited |

No remaining living-spec instance treats consensus close as the *primary* CLV settlement instrument outside the flagged fallback path.

## Ambiguities left by the spec (smallest choice recorded)

- **`same_line` vs probability methods:** when bet line equals close line, `clv_method=same_line` and CLV is still probability-space (de-vigged prices). `line_units` is only the no-translation-possible fallback.
- **Sign of line-unit CLV:** "points of close movement toward the bet" — positive when the close moved in favor of the ticket (favorite got shorter / dog got longer relative to the side bet). Exact arithmetic left to the implementer with a fixture in Task 20.
- **Which model distribution for `model_dist`:** the calibrated predictive distribution attached to the recommendation at bet time (not a post-close refit).

## Verification checklist

- [x] §2.7 rewritten (shown in audit response)
- [x] Task 20 test description includes −6.5→−7 line-translation fixture
- [x] Grep reviewed; leftover consensus-close uses justified or fallback-only
- [x] No code edited
