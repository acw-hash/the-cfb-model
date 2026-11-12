# W11-QA addendum §H — make-test failures

Investigated at `HEAD=f33b0d1e37eca77f4b1ce0e18238f6b39b064e97` (2026-11-12).
Same four node IDs W11-1 re-ran as “pre-existing.” Bisects used a disposable
worktree (`the-cfb-model-w11qa-bisect`); main left on `f33b0d1` / clean of
checkout dirt.

## Production card stake (gate)

| Source | Value |
|--------|------:|
| `scripts/_s6_w10_card.py` `FIXED_STAKE` | **0.014** |
| `scripts/_s6_w9_card.py` / `_s6_w8_card.py` `FIXED_STAKE` | 0.014 |
| `configs/betting.yaml` / `BettingConfig.max_stake_pct` | 0.014 |
| `recommended_stake(...)` under repo config (p=0.6, +100) | 0.014 |

**Verdict: PASS** — production card path resolves **0.014** (not 0.015).

---

## 1. Stake 1.4% pin

| Field | Value |
|-------|-------|
| **Node id** | `tests/unit/test_betting.py::test_recommended_stake_quarter_kelly` |
| **Failing assert** | `assert result.stake_fraction == pytest.approx(HARD_MAX_STAKE_PCT)` (line 177) |
| **Expected** | `HARD_MAX_STAKE_PCT` = **0.015** (±1.5e-08); comment still says “capped at 1.5%”; also `stake_amount ≈ 150.0` |
| **Actual** | `stake_fraction=0.014`, `stake_amount=140.0` (`BettingConfig().max_stake_pct=0.014` caps below hard max) |
| **Started failing** | **`db342c5910659afcbf50916de4a9b48a4df53f8d`** (`db342c5` — *Tighten week-8 card stake to 1.4% with a 7-play weekly cap.*, 2026-10-20) |
| **Bisect proof** | `db342c5^` → PASS; `db342c5` → FAIL (same assertion). Commit only touched `configs/betting.yaml`, `src/ncaa_quant/config.py`, `tests/unit/test_config.py` — **did not** update `test_recommended_stake_quarter_kelly` / `HARD_MAX_STAKE_PCT`. |

---

## 2. Language-guard baseline

| Field | Value |
|-------|-------|
| **Node id** | `tests/unit/test_betting_language_guard.py::test_ratchet_matches_exact_pin` |
| **Failing assert** | `assert result.matches == guard.BASELINE_MATCHES` (line 57; first of three exact pins) |
| **Expected** | `BASELINE_MATCHES=621`, `BASELINE_LINES=468`, `BASELINE_FILES=104` |
| **Actual (HEAD)** | **matches=2547**, lines=2300, files=162 |
| **Started failing** | **`665ab239a8c4755c16640fefaa94d683ffab6e28`** (`665ab23` — *more scripts*, 2026-09-23) |
| **Bisect proof** | Last clean re-pin `7be076f` (*chore(ratchet): re-pin betting-language to 621/468/104*) → PASS (621/468/104). Binary search over `7be076f..f33b0d1` (38 commits): first fail = `665ab23` with **813/627/109** vs pin 621/468/104. Parent `7be076f` PASS. Drift continued afterward (HEAD 2547/2300/162); pin never re-bumped. |

---

## 3. Credit-slot projection

| Field | Value |
|-------|-------|
| **Node id** | `tests/unit/test_slot_close_capture.py::test_2026_credit_accounting_matches_v4_projection` |
| **Failing assert** | `assert report.total_slots == 263` (line 588); next would be `total_credits == 789` |
| **Expected** | `total_slots=263`, `total_credits=789` (V4 projection frozen in test; measured 2026-09-02 in `docs/notes/_artifacts/v4b1-slot-close/build.md`) |
| **Actual (HEAD staged)** | **`total_slots=335`**, `total_credits=1005`; week-1 still 37 |
| **Started failing** | **Not a git commit** — reads live `data/staged/games/season=2026`. No code changes to `credit_accounting_report` after pin intro `0de3fc3`. Failure is staged-schedule growth vs a hardcoded snapshot. |
| **Data boundary** | Original week mix (Sep 2) summed to 263. Current cumuls: through w10 = **262** (already ≠ 263), through w11 = **291** (clear FAIL), HEAD total **335**. Pin intro commit (for context only): `0de3fc387a8e6e7512939f84fb8a5d4b3f7ae626`. |

---

## 4. About-copy string

| Field | Value |
|-------|-------|
| **Node id** | `tests/unit/test_webapp_w9r.py::test_copy_cites_amended_numbers_and_reval_memo` |
| **Failing assert** | `assert "NOT CURRENTLY FIT TO BET" in about` (line 263; `about` = `webapp/site/src/lib/about/copy.ts`) |
| **Expected** | substring `NOT CURRENTLY FIT TO BET` present in About copy |
| **Actual** | absent — About rewritten to plain-English clarity copy (no fit-to-bet verdict banner) |
| **Started failing** | **`98124ba54cc09d6d26c54f29c3501ab45d8f1db7`** (`98124ba` — *Ship clarity pass: plain-English forecasts, About rewrite, no verdict banner.*, 2026-09-24) |
| **Bisect proof** | `98124ba^` / `feecbc9` still contain the phrase in `about/copy.ts`; `98124ba` removes it (`git log -S "NOT CURRENTLY FIT TO BET" -- webapp/site/src/lib/about/copy.ts`). Earlier asserts in the same test (results `copy.ts` amended numbers) still pass at HEAD. |

---

## Summary table

| # | Nickname | Node id | Expected → actual | First fail |
|---|----------|---------|-------------------|------------|
| 1 | stake 1.4% pin | `tests/unit/test_betting.py::test_recommended_stake_quarter_kelly` | 0.015 → **0.014** | `db342c5` |
| 2 | language-guard baseline | `tests/unit/test_betting_language_guard.py::test_ratchet_matches_exact_pin` | 621 → **2547** matches | `665ab23` |
| 3 | credit-slot projection | `tests/unit/test_slot_close_capture.py::test_2026_credit_accounting_matches_v4_projection` | 263 → **335** slots | **data drift** (not a commit) |
| 4 | about-copy string | `tests/unit/test_webapp_w9r.py::test_copy_cites_amended_numbers_and_reval_memo` | phrase present → **absent** | `98124ba` |

| Gate | Result |
|------|--------|
| Production card stake == 0.014 | **PASS** |
