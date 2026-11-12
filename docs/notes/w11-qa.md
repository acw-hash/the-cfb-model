# W11-QA — Week-11 pre-publish QA

**Date:** 2026-11-12  
**HEAD / origin/main:** `f33b0d1` (Mac Nov 7 commits `26f8628` / `_s6_w10_sat_card.py` / W10-4b still **absent** after fetch-at-end).

Supporting scratch: `docs/notes/_artifacts/w11-qa/`.

---

## ADDENDUM — from W11-1 report (overrides where they conflict)

### 0. Git / card baseline

- `git fetch` at start and before this write: `26f8628` still unknown; `HEAD == origin/main == f33b0d1`.
- Until Mac commits land: week-10 card = W11-1 step-2 seven plays (no ledger).
- A2: no Mac outputs on this machine.

### A2 — reprice 7 plays vs Mac

**PASS**

- W10-3 snapshot: **not staged**.
- Snapshot used: `docs/notes/_artifacts/social-s6-w10-card/odds_allbooks_20261104T141200Z.json` (W10-2 all-books; last multi-book stage before Sat Nov 7 11:30 AM ET).
- Predictions: `data/webapp/publish_history/2026_w10.jsonl` record 1 (`as_of` `2026-11-04T14:03:14Z`).
- Method: production residual gap `|−μ − home_line|` at **posted** lines; Kentucky edge via seeded cover probs.

| Play | Side | Gap | Mac | Δ | Edge | Verdict |
|------|------|----:|----:|--:|-----:|---------|
| Texas @ Missouri | Missouri | 6.83 | 6.83 | 0.00 | 18.6% | PASS |
| Georgia @ Ole Miss | Ole Miss | 6.19 | 6.19 | 0.00 | 14.9% | PASS |
| Alabama @ LSU | LSU | 5.62 | 5.62 | 0.00 | 13.8% | PASS |
| North Carolina @ UConn | UConn | 4.77 | 4.77 | 0.00 | 13.3% | PASS |
| Arkansas @ Auburn | Auburn | 4.25 | 4.25 | 0.00 | 9.0% | PASS |
| Texas State @ Oregon State | Texas State (+5.5) | 3.71 | 3.71 | 0.00 | 8.0% | PASS |
| Kentucky @ Tennessee | Kentucky | 3.44 | — | — | **8.3%** (0.0826) | PASS |

Detail: `_artifacts/w11-qa/a2_reprice.md`.

### C4 — EPA gap check vs rating freshness (TOP PRIORITY)

**Verdict: gap check broken (labels only)** — week-10 forecasts were **not** built on stale ratings.

#### 1a. Classifier + W9→W10 change

W11-1 gap check (`docs/notes/_artifacts/w11-1/audit.py`):

```python
rating_weeks = {
    int(w["week"])
    for w in (teams_art.get(str(tid), {}) or {}).get("weeks", [])
}
if 10 in rating_weeks:
    klass = "HAS-WEEK10-RATING"
```

Same pattern in W9-1 / W10-1 with week integers **8 / 9** and labels `HAS-WEEK8-RATING` / `HAS-WEEK9-RATING`. Tracked diffs are mechanical week bumps (`61d9a90`, `f33b0d1`); classifier logic unchanged.

| Audit | Checks | Artifact COUNTS | Note prose |
|-------|--------|-----------------|------------|
| W9-1 | `8 in rating_weeks` | `HAS-WEEK8-RATING=112`, `PENDING=0` | mis-wrote “PENDING 112” |
| W10-1 | `9` | `HAS-WEEK9-RATING=112`, `PENDING=0` | matches artifact |
| W11-1 | `10` | `HAS-WEEK10-RATING=125`, `PENDING=1` | matches artifact |

**Label semantics:** `run_filter` postgame rows use **game week**; `_append_current_week_rating_points` adds a synthetic `kind=weekly` row labeled with the **publish week** at `as_of` for chart open circles; `build_team_ratings` keeps one point per `(team, week)`. Tuesday week-N publish therefore always has `week:N` **before** week-N games — matching W10-2 (“week-10 current on all 138… week-9 results folded”).

Texas example in Nov 4 artifact: week 9 = `2026-11-01T04:30Z` (vs Miss St); week 10 = `2026-11-04T14:03:14Z` (synthetic, same EPA).

#### 1b. Fourteen card teams — last game folded into Nov 4 rating state

Source: R2 `v1/2026/w10/tuesday_primary/team_ratings_2026.json` (as_of `2026-11-04T14:03:14Z`). **Card fail count: 0.**

| School | Last folded ET | Opp | W9 |
|--------|----------------|-----|----|
| Texas | 2026-11-01T04:30Z | Miss St | present |
| Missouri | 2026-11-01T04:45Z | Arkansas | present |
| Georgia | 2026-11-01T00:30Z | Florida | present |
| Ole Miss | 2026-10-31T21:45Z | Auburn | present |
| Alabama | 2026-10-25T00:30Z | Texas A&M | bye |
| LSU | 2026-10-24T21:00Z | Auburn | bye |
| North Carolina | 2026-11-01T04:30Z | Miami | present |
| UConn | 2026-11-01T00:00Z | Air Force | present |
| Arkansas | 2026-11-01T04:45Z | Missouri | present |
| Auburn | 2026-10-31T21:45Z | Ole Miss | present |
| Texas State | 2026-11-01T07:00Z | Boise St | present |
| Oregon State | 2026-11-01T08:00Z | Fresno St | present |
| Kentucky | 2026-10-25T04:30Z | Vanderbilt | bye |
| Tennessee | 2026-10-25T00:30Z | S. Carolina | bye |

Vs `backup_latest_20261104`: **108/112** week-9 players changed EPA (same as W10-2).

#### 1c. Twenty random FBS (`seed=20261112`)

**Fail count: 0** (mix of week-9 postgame matches and byes). Full table: `_artifacts/w11-qa/c4_report.md`.

#### 1d. Verdict

**Gap check broken (labels only).** A correct “did last weekend fold?” check must look for game-week **N−1** postgame (or EPA delta vs prior Tuesday), not publish-week **N** presence.

### H — test failures

Production card stake right now: **`FIXED_STAKE = 0.014`** in `scripts/_s6_w10_card.py` (also `BettingConfig.max_stake_pct`). **PASS** (gate: must be 0.014).

| # | Node id | Assertion | Expected → actual | First fail |
|---|---------|-----------|-------------------|------------|
| 1 | `tests/unit/test_betting.py::test_recommended_stake_quarter_kelly` | `stake_fraction == approx(HARD_MAX_STAKE_PCT)` | **0.015 → 0.014** | `db342c5` (*Tighten week-8 card stake to 1.4%…*; pin test not updated) |
| 2 | `tests/unit/test_betting_language_guard.py::test_ratchet_matches_exact_pin` | `result.matches == BASELINE_MATCHES` | **621 → 2547** | `665ab23` (*more scripts*; after re-pin `7be076f`) |
| 3 | `tests/unit/test_slot_close_capture.py::test_2026_credit_accounting_matches_v4_projection` | `report.total_slots == 263` | **263 → 335** | **data drift** (live staged schedule vs Sep-2 V4 pin; not a code commit) |
| 4 | `tests/unit/test_webapp_w9r.py::test_copy_cites_amended_numbers_and_reval_memo` | `"NOT CURRENTLY FIT TO BET" in about` | present → **absent** | `98124ba` (*Ship clarity pass…*) |

Detail: `_artifacts/w11-qa/h_test_failures.md`.

### G2 — revalidate

**PASS**

- `curl -I https://the-cfb-model.vercel.app` → **200 OK**; **no `Location`** header.
- w9 publish revalidate (`docs/notes/w9-2.md`): **HTTP 200**.
- w10 publish revalidate (`docs/notes/w10-2.md`): **HTTP 200**.
- Worker `/run` revalidate both weeks: **200**.

### E3 — week-10 closes (from W11-1 step 4b; odds not re-pulled)

Source: `docs/notes/_artifacts/w11-1/audit.out`.

| Play | Book | Close line | Close price | Close ts (UTC) | Grade |
|------|------|------------|-------------|----------------|-------|
| Missouri +2.5 | fanduel | 2.5 | +100 | 2026-11-04T20:00:03Z | L |
| Ole Miss +3.5 | espnbet | 3.5 | −110 | 2026-11-04T14:11:05Z | L |
| LSU −6 | draftkings | −5.5 | −110 | 2026-11-04T20:00:03Z | L |
| UConn +7.5 | fanduel | 7.5 | −134 | 2026-11-04T20:00:03Z | L |
| Auburn −10 | betmgm | −10.5 | −108 | 2026-11-04T20:00:03Z | L |
| Texas State +5.5 | betmgm | 6.5 | −110 | 2026-11-04T20:00:03Z | W |
| Kentucky +6.5 | hardrockbet | 6.5 | −105 | 2026-11-04T14:11:55Z | L |

Card: **1-6-0**.

### G1 — missing 6-1 (report only; no ledger edits)

| Source | W-L-P |
|--------|-------|
| Operator public | **31-28-1** |
| Ledgers w2,w4–w9 + task-card w10 | **25-27-1** |
| Gap | **+6 W / +1 L** |

- Absent ledger weeks: **w1, w3** (w10 file also missing locally; already counted from task card).
- `posted_calls_2026_w1` / `_w3`: **nowhere** — repo, `out/`, `_artifacts/`, git history, R2 (`posted_calls` keys = 0).
- Contemporaneous docs: both weeks **0-0 / no Best Bets** (`social-s6-w1-card.md`; w4 `week3_note`; W6-1 report).
- `results_2026.json` / site `/results`: model track-record + graded games only — **no** operator Best Bets W-L; neither 31-28-1 nor 25-27-1 appears.
- Public threads track ledger math where present (e.g. w9 season line 21-17-1 = through w8), not 31-28-1.

**w1/w3 cannot explain the 6-1.** Operator 31-28-1 is not sourced inside this repo’s posted-calls trail.

---

## Gate summary (addendum)

| Check | Result |
|-------|--------|
| Mac commits on origin/main | **NO** (still absent) |
| A2 reprice vs Mac | **PASS** |
| C4 rating freshness | **PASS** (fold-in OK) |
| C4 gap-check semantics | **FAIL** (labels only / false positive) |
| H production stake == 0.014 | **PASS** |
| G2 revalidate 200 | **PASS** |
| G1 reconcile 31-28-1 | **FAIL** (unexplained 6-1) |

---

## MAIN BODY — pipeline audit (READ-ONLY)

**STOP after C1–C2.** Remaining sections (C3/C5, A, B, D, E, F, G) **not run** per stop rule.

Command evidence: `uv run python scripts/_qa_w11_c1_c2_def_epa.py` → `docs/notes/_artifacts/w11-qa/c1_c2_def_epa.{md,json}`.

### C1. def_epa direction — **PASS** (settled: higher = BETTER)

**Measurement** (`src/ncaa_quant/ratings/league_state.py` ~384–422):

```text
home_epa: h[home_off]=+1, h[away_def]=−1  ⇒  y ≈ off_home − def_away (+ HFA + env)
away_epa: h[away_off]=+1, h[home_def]=−1  ⇒  y ≈ off_away − def_home (+ env)
```

Holding offense fixed, higher `def_epa` lowers predicted opponent EPA/play ⇒ **higher def_epa = BETTER defense**.

Ratings source: Nov 4 publish `team_ratings_w10_publish.json` (local `filter_history.parquet` max season **2025**, stale). Opposing raw EPA / points from staged 2026 games+plays w1–w10.

| Spearman (n=138 FBS) | ρ | p |
|----------------------|--:|--:|
| def_epa vs opp raw off EPA/play | **−0.807** | 6.4e-33 |
| def_epa vs pts allowed/g | **−0.703** | 7.4e-22 |
| off_epa vs own raw EPA/play | **+0.787** | 2.7e-30 |
| off_epa vs pts scored/g | **+0.750** | 3.4e-26 |

Negative defense correlations confirm **BETTER**. Repo contradiction: `state_space.py` comment (“higher = worse”) is **wrong**; `_state_space_acceptance_14.py` (“higher = better”) matches data.

### C2. Consumers under C1=BETTER — **FAIL** (FCS prior) → **STOP**

| Consumer | Direction | Result |
|----------|-----------|--------|
| **FCS prior** `fcs_pinned_state` `state_space.py:529-538` | `mean[def]=-fcs_prior_mean` → with `fcs_prior_mean=-0.05` pins **def_epa=+0.05**; FBS mean def≈**+0.003** ⇒ pinned FCS defense **BETTER** than avg FBS | **FAIL** `state_space.py:537-538` |
| **Predictor** features `production_stack.py:438-445` | `rating_diff_def_epa = home − away`; linear identity under off−def is `(off_h−off_a)+(def_h−def_a)` — **correct** for higher=better; no sign flip | **PASS** |
| `build_team_ratings` `export.py:1295-1304` | passthrough of stored `def_epa`; no rank/sort | **PASS** |
| Site `RatingTrajectoryChart.tsx:186-188` | “Higher is better on defense… EPA suppressed” | **PASS** (aligned) |
| Acceptance script `_state_space_acceptance_14.py:252-253` | sorts `def_epa` descending for top defense | **PASS** (aligned) |
| W10-5 blurb data | not present on this machine (Mac-only / absent from repo) | **UNVERIFIED** |

**Comment bug (same lines):** `fcs_pinned_state` says “Higher def_epa means worse defense” — false under C1; that wrong belief drove `mean[def]=-prior` and the inverted FCS defense pin.

#### STOP GATE

> **STOP AND REPORT** — C2 finds the **FCS prior** using the wrong direction (`state_space.py:537-538`). Predictor feature construction is OK. Per task rule: no further sections until this is addressed (fixes are FORBIDDEN in this QA pass).

**What this can touch (report only):** any game involving an FCS opponent — filter updates treat FCS defense as *stronger* than average FBS (`def=+0.05` vs ~0), which biases the FBS side’s offense/defense innovations when the measurement moves `y + fcs_def`. Week-10 card slate is all FBS–FBS, so card μ/edges are not directly FCS-pinned; season-long rating levels for teams that played FCS can still carry the bias.

### Main-body gate table (partial)

| Check | Result | Evidence |
|-------|--------|----------|
| C1 def_epa direction | **PASS** | Spearman def vs pts allowed ρ=−0.70; measurement off−def |
| C2 FCS prior direction | **FAIL** | `fcs_def=+0.05` > FBS mean; `state_space.py:537-538` |
| C2 predictor ratings→margin | **PASS** | `production_stack.py:438-445` h−a |
| C2 site defense labels | **PASS** | `RatingTrajectoryChart.tsx:186-188` |
| C2 W10-5 blurb | **UNVERIFIED** | artifact absent here |
| C3+ / A / B / D / E / F / G | **not run** | stopped after C2 FAIL |

### Defects so far (grouped)

**(i) Changes forecasts / edges**
- `src/ncaa_quant/ratings/state_space.py:537-538` — FCS `def_epa` pinned to **+0.05** under a “higher=worse” comment; under C1 that makes FCS defense **better** than average FBS. Affects filter updates vs FCS (indirect season ratings for any team that played FCS).

**(ii) Public display / copy only**
- (none new from C2; site chart copy matches C1)

**(iii) Process / machines / ops**
- Addendum C4 EPA-gap check label false positive (`HAS-WEEK{N}-RATING` = publish-week synthetic point).
- Local `data/artifacts/state_space/filter_history.parquet` stale (max season 2025); C1 used R2/Nov-4 `team_ratings` instead.
- W10-5 / Mac Nov 7 card commits still absent on `origin/main`.
