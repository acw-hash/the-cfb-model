# S6 — Public statement draft (NOT POSTED)

**Status:** DRAFT ONLY. No X API, no browser, no account access.  
**Date:** 2026-08-25  
**Branch:** `social-s1-s2`

Operator out from **Thursday 2026-08-27 noon**. First calendar Best Bets anchor
was scheduled **Tuesday 2026-09-01** per operator task brief; live pinned-post
verbatim is **NOT FOUND IN REPO** (see trace table).

---

## Draft (plain language)

We tested whether our Tuesday Best Bets process could pick winners against the
spread on historical tickets from 2021 through 2024. It could not.

On 314 bets that cleared our internal filters, the model claimed about a
**64%** chance of covering. Those tickets covered about **49%** of the time —
roughly a coin flip. When we sorted tickets by how confident the model was, the
win rate did not improve. The highest-confidence group still landed near **51%**,
below what you need to beat the standard **−110** price. A separate check on
moneyline wins showed our scoring pipeline works; the against-the-spread ticket
claims just do not separate winners from losers.

We already took the same line on the site: **not currently fit to bet** against
the closing line. This investigation is why we will not post a Best Bets card as
an edge product. Some weeks the honest answer is zero plays — and this backtest says that is not bad discipline, it is what the numbers show.

Forecasts and track record stay on the site. We are not promising picks we cannot
support.

---

## Trace table

| # | Draft sentence (or clause) | Source table / note | Label |
|---|---|---|---|
| 1 | "tested whether our Tuesday Best Bets process" | S6 operator brief (cadence); `RIDGE_X_PLAYBOOK.md` §2 Tue anchor — **not live pinned verbatim** | UNRESOLVED (commitment wording) |
| 2 | "2021 through 2024" | S3 method: seasons 2021–2024 | MEASURED |
| 3 | "314 bets" | S3 thr=0.05 weeks 2+; S4/S5 n=314 | MEASURED |
| 4 | "about **64%** chance" | S3/S4 mean claimed `p_win` **0.641** | MEASURED |
| 5 | "about **49%**" / "coin flip" | S3 ATS hit **0.494** | MEASURED |
| 6 | "win rate did not improve" / sorted by confidence | S5 P0-2: bins flat, Spearman **−0.012** | MEASURED |
| 7 | "highest-confidence group still landed near **51%**" | S5 top bin realized **0.508** | MEASURED |
| 8 | "below what you need … **−110**" | S5 top bin **0.508** vs break-even **0.524** (0.5238) | MEASURED |
| 9 | "moneyline wins … pipeline works" | S5 `p_ml_home` AUC **0.725** on same 314 | MEASURED |
| 10 | "against-the-spread ticket claims … do not separate" | S5 `p_win` AUC **0.493**, BSS **−0.090** | MEASURED |
| 11 | "**not currently fit to bet**" | `track_record.json` verdict.label | MEASURED |
| 12 | "will not post a Best Bets card as an edge product" | S5 finding: no discrimination; overlap **1.0** | MEASURED |
| 13 | "zero plays … honest answer" | S3/S5; playbook T6 No-Bet philosophy — philosophy only | MEASURED (backtest); internal playbook for tone |
| 14 | "Forecasts … stay on the site" | ADR 0015; site publishes forecasts not picks | MEASURED (product decision) |

**Cut from draft (not traceable to S3/S4/S5 numbers):** any claim that forecasts
beat the market; any pooled D6 b2 wording in public copy (different estimand +
2025 composition UNRESOLVED vs this replay); forward-looking performance.

**Pinned-thread verbatim:** NOT FOUND IN REPO. `@RidgeCFB` live profile fetch
returned 403. Insert exact pinned commitment quote here before any post goes live.

---

## Read-aloud check

- Avoided AUC, BSS, CLV in body copy.
- "Coin flip" / "64% vs 49%" carry the discrimination failure without jargon.
- Does not say picks are "delayed" or "paused" — states no edge product.
- Does not claim market-relative forecast superiority.
