# AUDIT-1 — Spec consolidation

**Date:** 2026-08-06
**Scope:** Documentation only (no code changes).

## Summary

Applied Parts A–C of the historical-odds change set into the living specs,
archived the change set as ADR 0002, confirmed the obsolete design-doc duplicate
is gone, and normalized Cursor rules / line endings.

## Sections touched

### `docs/DESIGN.md` (Part A — already present; verified, not rewritten)

Part A content was already incorporated in refined form before this audit. Left
as-is rather than regressing to the looser change-set wording. Verified present:

| Change-set item | Location in DESIGN.md |
|---|---|
| A1 — Odds API historical table row | §3.2 market data table |
| A2 — snapshot-history / book-coverage warnings | §3.4 |
| A3 — sharpened closing-line definition | §2.7 |
| A4 — Line-source regime item 8 | §7.2 |
| A5 — market-feature availability contract | §4.5 |

Also already present (related, not in Parts A–C as written): §9.8 note on
mirroring production decision points; §15 item 5b historical backfill prompt.

### `docs/TASKS.md`

| Section | Action |
|---|---|
| **TASK 5B** (between Tasks 5 and 6) | Already present (budget-aware refinement of Part B); left as-is |
| **TASK 4** | Added `snapshot_source='live'`, `decision_point=null`, `n_books_available` schema note (Part C) |
| **TASK 7** | Added Snapshot monotonicity and Source reconciliation validators (Part C) |
| **TASK 16** | Added line-lookup fallback ladder, `line_source` / `n_books_available` on prediction rows, Tuesday event_time audit assert (Part C) |
| **TASK 20** | Added `n_books_available` output + Task 21 stratification requirement (Part C) |
| **TASK 21** | Added `line_source` and book-count bucket to §7.2 item 3 slice analysis (Part C) |
| **TASK 23** | Added ablation A6; split bet-layer metrics by line-source regime; acceptance bullets for A6 and regime-split CLV (Part C) |

### File moves / deletes

| Path | Action |
|---|---|
| `docs/ncaa_prediction_system_design.md` | **Absent** (renamed to `docs/DESIGN.md` in Task 1 per `docs/notes/01.md`). No file to diff or delete. Confirmed neither root nor `docs/` copy exists. |
| `docs/historical_odds_change_set.md` | Moved → `docs/adr/0002-historical-odds-source.md` with applied-on-2026-08-06 preamble |
| `docs/adr/0002-historical-odds-source.md` | Created (ADR archive of the change set) |

### Cursor tooling / line endings

| Path | Action |
|---|---|
| `.cursorrules` | Already named `.cursorrules` (not `_cursorrules`) — no rename needed. Normalized CRLF → LF. Blank line before `## Environment` already present; confirmed retained. |
| `.gitattributes` | Created — enforces LF for `*.md` and common text files |

## Verification (run at audit close)

- `DESIGN.md`: matches for `n_books_available`, `previous_timestamp`, `Line-source regime`
- `TASKS.md`: matches for `TASK 5B`, `A6`
- `ncaa_prediction_system_design.md`: does not exist
- `.cursorrules`: LF endings confirmed
