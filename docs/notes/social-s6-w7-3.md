# W7-3 — best-line sweep + thread rewrite

**Date:** 2026-10-13  
**Scope:** probe under `docs/notes/_artifacts/social-s6-w7-card/` only. No
Odds Worker, no publish, no `src/` / config edits.

## What ran

1. Live Odds API pull: `markets=spreads`, `regions=us,us2`,
   `eventIds` = crosswalk-mapped ids for Kent State, Missouri, Buffalo,
   Wisconsin. Cost **2** credits; remaining **477** (was 482).
2. Raw JSON:
   `docs/notes/_artifacts/social-s6-w7-card/odds_allbooks_20261013T161322Z.json`.
3. Per-book edge via production `compute_edge` + same draws seed
   (`42 + game_id`) and HFA ROBUST μ−4.56 dual gate as `_s6_w7_card.py`.
4. Dropped books: **none** (no `last_update` >6h; no one-sided quotes among
   returned bookmakers). `williamhill_us` / `fanatics` simply absent.
5. QB re-check: all 8 teams unchanged vs card states
   (UMass `unknown`, UCLA `backup`, others `starter`). Newest
   `event_time` = Ole Miss `2026-10-13T11:59:14Z` (already in card batch).

## Best regulated lines

| play | best book | line | price | edge | vs card DK edge |
|------|-----------|-----:|------:|-----:|----------------:|
| Kent State (card) | draftkings | +20.5 | −102 | 0.0579 | +0.0000 |
| Missouri (card) | ballybet | +6 | −108 | 0.0562 | +0.0054 |
| Buffalo (conditional) | espnbet | −6.5 | −105 | 0.1503 | +0.0109 |
| Wisconsin (conditional) | ballybet | +2.5 | −108 | 0.0644 | −0.0055 vs W7-2 DK −105 |

Both conditionals still clear dual gate at ≥1 regulated book; kept on radar,
not graded (`conditional` status `not_graded_qb_unconfirmed`).

## DraftKings default (unchanged code)

Card probe prices only `PROBE_BOOKS = (betmgm, draftkings, fanduel,
williamhill_us)`. `_choose_book_rows` keeps highest dual-gate edge; ties break
by `PROBE_BOOKS` index (betmgm first — docstring incorrectly says prefer DK).
Kent State: only DK cleared dual gate among probe books. Missouri: DK had
highest edge among dual-gate passers (0.0508 > betmgm 0.0486).

## Deliverables

- `out/social/2026_w7/thread_w7.md` (6 posts, all ≤280)
- `docs/notes/_artifacts/w7-posted-calls/posted_calls_2026_w7.json` (+
  `data/social/` copy) with `superseded` original DK rows + `conditional`
- `candidates_w7.json` updated for the 2 card plays
- Probe: `_w7_3_bestline_sweep.py`, report `w7_3_bestline_report.json`
