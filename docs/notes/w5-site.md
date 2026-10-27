# W5-SITE — empty market column diagnose + odds manual refresh

**Date:** 2026-10-02  
**Export:** False throughout. No publish / no prediction artifact edits.

## Diagnose

1. **Rows:** R2 `latest/week_predictions.json` = **57** games (week 5). Production `/`
   SSR renders **57** `game-row`s — **0 missing**. No row cap; `projectThisWeekGames`
   maps all `week.games`; client filters only via TeamSearch.
2. **Odds (pre-fix):** `latest/odds_snapshot.json` was **week 4**,
   `snapshot_at=2026-10-01T13:01:21Z`, `games=[]`, intersection with week-5 = **0**.
   Worker keys off `latest/week_predictions.json` (+ meta). Cron `0 13 * * *`
   (next was `2026-10-02T13:00:00Z`). No run since 12:22Z publish before the fix.
3. **Layout:** `GameRow` sets `showMarket = Boolean(odds)` on the **per-game**
   odds view. Missing match → market cells omitted (empty `<span>` only inside
   model stacks when values null; market columns not rendered). Header still
   shows Market when page-level `OddsPageContext` is non-null → column shift on
   unmatched rows. **No webapp code change in this task.**

## Fix

Documented manual trigger (`docs/runbooks/odds_refresh.md`, Worker `POST /run`):

```
npx tsx scripts/manual-run.ts
→ http 200, ok, matched=56, unmatched=2, revalidate_status=200
```

Post: `snapshot_at=2026-10-02T12:30:28Z`, week **5**, intersection **56**.
Unmatched: McNeese naming (`McNeese` vs `McNeese State` for `401856706`).
Production `/`: 57 rows, **56** market-cells, odds as of Fri Oct 2 12:30 PM UTC.
