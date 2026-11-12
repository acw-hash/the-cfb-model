# W11-QA addendum — G2 / E3 / G1

**Date:** 2026-11-12  
**Scope:** Report only. No ledgers created or edited.

---

## G2 — revalidate

### Verdict: **PASS**

### 1. `curl -I https://the-cfb-model.vercel.app`

| Field | Value |
|-------|--------|
| Status | **200 OK** |
| Location | **absent** (no redirect header) |

Captured via `curl.exe -sI` (2026-11-12). Response is direct 200 on `the-cfb-model.vercel.app`; no `Location` to ridgecfb.com or elsewhere.

### 2. Publish-path revalidate POST status (w9 / w10)

| Week | Source | Publish revalidate | Worker `/run` revalidate |
|------|--------|--------------------|---------------------------|
| w9 | `docs/notes/w9-2.md` §A | **HTTP 200** → `the-cfb-model.vercel.app` | **200** (`_artifacts/w9-2/worker_run.out` `revalidate_status`) |
| w10 | `docs/notes/w10-2.md` §A | **HTTP 200** → `the-cfb-model.vercel.app` | **200** (`_artifacts/w10-2/worker_run.out` / `worker_run_console.out`) |

No publish-console log under `_artifacts/w9-2` / `w10-2` reprints a raw `webapp_revalidate_ok` line; both weekly notes explicitly record publish revalidate **HTTP 200**. No non-200 publish revalidate found for these weeks.

**FAIL criterion:** anything but 200 for those publish revalidates → **not triggered**.

---

## E3 — week-10 closes at posted book

### Verdict: **DOCUMENTED** (source: W11-1 step 4b / `docs/notes/_artifacts/w11-1/audit.out`; odds **not** re-pulled)

Closes are the last same-book quote at the posted book from the audit’s close lookup (not a fresh Odds API pull).

| # | Matchup | Side (posted) | Book | Close line | Close price | Close timestamp (UTC) | Grade |
|---|---------|---------------|------|------------|-------------|------------------------|-------|
| 1 | Texas @ Missouri | Missouri +2.5 | fanduel | 2.5 | +100.0 | 2026-11-04T20:00:03.244387+00:00 | L |
| 2 | Georgia @ Ole Miss | Ole Miss +3.5 | espnbet | 3.5 | −110.0 | 2026-11-04T14:11:05+00:00 | L |
| 3 | Alabama @ LSU | LSU −6 | draftkings | −5.5 | −110.0 | 2026-11-04T20:00:03.244387+00:00 | L |
| 4 | North Carolina @ UConn | UConn +7.5 | fanduel | 7.5 | −134.0 | 2026-11-04T20:00:03.244387+00:00 | L |
| 5 | Arkansas @ Auburn | Auburn −10 | betmgm | −10.5 | −108.0 | 2026-11-04T20:00:03.244387+00:00 | L |
| 6 | Texas State @ Oregon State | Texas State +5.5 | betmgm | 6.5 | −110.0 | 2026-11-04T20:00:03.244387+00:00 | W |
| 7 | Kentucky @ Tennessee | Kentucky +6.5 | hardrockbet | 6.5 | −105.0 | 2026-11-04T14:11:55+00:00 | L |

**Week-10 card:** 1-6-0 (audit `public_box_expected 1-6-0`; disagreement False).

Posted lines/prices from the same audit block (for CLV context only):  
Missouri 2.5/−104; Ole Miss 3.5/−105; LSU −6/−108; UConn 7.5/−102; Auburn −10/−108; Texas State 5.5/−108; Kentucky 6.5/−105.

---

## G1 — missing 6-1 in season record

### Verdict: **FAIL to reconcile** (gap remains; w1/w3 cannot explain it)

### Arithmetic

| Source | W-L-P |
|--------|-------|
| Operator public (given) | **31-28-1** |
| Ledgers / audit season (w2, w4–w9 + task-card w10) | **25-27-1** |
| **Gap** | **+6 W / +1 L (= 6-1)** |

Weekly ledger grades (audit / prior notes):

| Week | W-L-P | Ledger file |
|------|-------|-------------|
| w1 | — | **MISSING** |
| w2 | 4-2-0 | present |
| w3 | — | **MISSING** |
| w4 | 4-1-1 | present |
| w5 | 4-2-0 | present |
| w6 | 3-4-0 | present |
| w7 | 4-3-0 | present |
| w8 | 2-5-0 | present |
| w9 | 3-4-0 | present |
| w10 | 1-6-0 | absent on machine; graded from task card |

Sum w2+w4–w10 = 25-27-1. Missing ledger **weeks** are w1 and w3 (plus w10 file absent but already counted).

### Do w1 / w3 supply the 6-1?

**No — contemporaneous evidence says both were empty Best Bets weeks.**

| Week | Ledger anywhere? | Card / notes |
|------|------------------|--------------|
| **w1** | **No** `posted_calls_2026_w1.json` in repo, `data/social/`, `out/`, `_artifacts/`, or git add/delete history of that path | `docs/notes/social-s6-w1-card.md`: no Best Bets thread; 0 accepted. `out/social/thread_w2.md`: “first card of the season.” W6-1 report: `W1 record 0-0-0` |
| **w3** | **No** `posted_calls_2026_w3.json` anywhere same search | `data/social/posted_calls_2026_w4.json` `week3_note`: “No posted_calls_2026_w3.json exists; week-3 social export accepted=[]. No week-3 Best Bets were recorded.” W6-1 report: `W3 record 0-0-0` |

### R2 / backups / git / out/

- R2 bucket `ridge-artifacts`: **0** keys containing `posted_calls` (full object scan, n=210).
- Backups (`backup_latest_20260925|1027|1104|1112/`) hold only publish artifacts (`meta`, `results_*`, `team_ratings_*`, `track_record`, `week_predictions`, `odds_snapshot`) — **no** social ledgers.
- Git: no historical blob for `posted_calls_2026_w1` / `_w3`; ledger commits start at w2/w4+.
- `out/social`: threads for w2, w5–w9; no w1/w3 Best Bets thread artifacts with a 6-1 card.

### `results_2026.json` and site `/results`

- `results_2026.json` (latest / R2): game-level model grades only (`games`, `grading_rule=last_pre_kickoff_publish`). **No** Best Bets season W-L, no posted-calls fields, no `31-28` / `25-27` strings.
- `track_record.json`: walk-forward ATS/OU/MAE metrics only — not operator card record.
- Live site:
  - `/` → **Week 10** slate (published Nov 4); no Best Bets W-L strip in fetched content.
  - `/results` → model **Track record** metrics + per-game graded rows; footer states live graded record starts empty until Week 1 finals — **does not publish operator 31-28-1 or 25-27-1**.

Public **thread** season lines match ledger math where present (e.g. w7 `15-9-1`, w8 `19-12-1`, w9 `21-17-1`), not 31-28-1.

### G1 conclusion

- The only ledger gaps in w1–w10 are **w1** and **w3**.
- Both are documented **0-0 / no Best Bets**, so they **do not** account for the operator’s extra **6-1**.
- Operator **31-28-1** is **not** reproduced by repo ledgers, R2, results artifacts, or the public `/results` page.
- **FAIL** to source the missing 6-1 inside this repo’s posted-calls trail.
