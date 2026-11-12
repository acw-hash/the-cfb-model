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

## W11-FIX-1 — FCS defense prior sign (branch `w11-fcs-def-sign`, NO MERGE)

**Date:** 2026-11-12  
**Branch tip (fix commit):** `6c688af` — *Fix FCS def_epa prior sign under off-minus-def convention.*  
**Export gate (pre-edit verify):** `export False social False`  
(`uv run python -c "from ncaa_quant.config import load_config;c=load_config();print('export',c.webapp.export_enabled,'social',c.social.enabled)"`)

Sandbox only: `data/sandbox/w11_fcsfix/` (gitignored). No production `filter_history` / `tier_state` / `publish_history` / R2 writes. No weight or gate changes. No publish/merge.

Evidence: `docs/notes/_artifacts/w11-qa/w11_fix1_impact.json`, `fix1b_{fail_on_main,pass_on_branch}.*`, `make_test_w11_fix1.out`, `scripts/_qa_w11_fcs_impact{,_resume}.py`.

---

### 0. Scope check (report-only; higher def_epa = BETTER)

Only writers/assumptions beyond the predictor/site already covered in C2. Convention: under `y ≈ off − def`, weak defense ⇒ **negative** `def_epa`.

| Site | What it does | Verdict |
|------|--------------|---------|
| `state_space.py:149` `StateSpaceConfig.fcs_prior_mean=-0.05` | Scalar “slightly worse than average”; no dim-specific flip | **PASS** (scalar) |
| `state_space.py:529-539` `fcs_pinned_state` **(pre-fix)** | `mean[def]=-fcs_prior_mean` → `def=+0.05` (> FBS ~0) | **FAIL** — only FAIL found |
| `league_state.py:377-379` | Reads pinned FCS `mean[def]` / var into measurement; no extra negate | **PASS** |
| `priors.py:187-195` `regress_to_conference_mean` | Convex blend; same for off/def | **PASS** |
| `priors.py:198-212` `returning_pct_for_dim` | Picks offense vs defense continuity %; no sign flip | **PASS** |
| `priors.py:215-228` `talent_implied_rating` | `scale * talent_z`; same units for any dim | **PASS** |
| `priors.py:333-382` `build_predictors` (returning / talent / portal / coaching / QB) | Same-sign blend into prior mean per dim; portal/coach/QB not inverted for def | **PASS** |
| `priors.py:1028-1044` `gaussian_state_from_priors` | Copies fitted `prior_mean` into state dims | **PASS** |
| New-FBS / no-history | No separate def-sign path; diffuse / league-mean priors (`priors.py:494-503`, `1085+`) are dim-symmetric | **PASS** |
| `state_space.py:446-464` `inflate_q` (QB/coordinator/injury-style events) | Inflates **covariance** diagonals only; does not rewrite means | **PASS** |
| `efficiency.py:277-278` ridge docstring | “higher defense = more of y suppressed” ≡ better | **PASS** |
| Every other `fcs_prior_mean` use | Only config default + `fcs_pinned_state` assignment | **PASS** (no second writer) |

**STOP rule:** no other FAIL → proceed to step 1.

---

### 1. Fix + tests

**Diff (`6c688af`):**

```diff
-    # Offense slightly worse, defense slightly worse (higher allowed EPA).
+    # Offense slightly worse, defense slightly worse under off − def:
+    # higher def_epa = better defense (more EPA suppressed), so a weak FCS
+    # defense is a negative def_epa (same sign as fcs_prior_mean).
     if "off_epa" in config.state_dims:
         mean[config.dim_index("off_epa")] = config.fcs_prior_mean
     if "def_epa" in config.state_dims:
-        # Higher def_epa means worse defense under off − def measurement.
-        mean[config.dim_index("def_epa")] = -config.fcs_prior_mean
+        mean[config.dim_index("def_epa")] = config.fcs_prior_mean
```

New unit test: `tests/unit/test_fcs_def_prior_sign.py` — pinned FCS `off`/`def` both `< 0` and `def <` FBS mean-zero reference; equals `fcs_prior_mean`.

| Evidence | Result |
|----------|--------|
| 1b on main/pre-fix (`fix1b_fail_on_main.txt`) | **FAIL** `assert 0.05 < 0.0` |
| 1b on branch (`fix1b_pass_on_branch.out`) | **PASS** |
| 1c `make test` (`make_test_w11_fix1.out`) | **4 failed, 1054 passed** — exactly the known four (stake pin, language-guard ratchet, credit-slot projection, about-copy) |

---

### 2. Ratings impact (sandbox `data/sandbox/w11_fcsfix/`)

Δ = fixed − current. n=138 FBS. No NaN/inf.

#### 2a. Week-10 end

| Metric | Value |
|--------|------:|
| median \|Δoff\| | 0.000326 |
| median \|Δdef\| | 0.000314 |
| max \|Δoff\| | 0.003171 |
| max \|Δdef\| | 0.001770 |
| Spearman(Δoff, # FCS games) | ρ=**−0.301**, p=3.3e-4 |

Top 15 by \|Δoff\|+\|Δdef\|:

| School | Δoff | Δdef | \|Δ\| | # FCS |
|--------|-----:|-----:|------:|------:|
| Wake Forest | −0.00317 | +0.00035 | 0.00352 | 1 |
| Purdue | +0.00030 | +0.00177 | 0.00207 | 1 |
| Florida | +0.00092 | +0.00109 | 0.00201 | 1 |
| Ole Miss | +0.00088 | +0.00088 | 0.00176 | 0 |
| Minnesota | +0.00133 | +0.00042 | 0.00175 | 1 |
| Tennessee | +0.00081 | +0.00091 | 0.00172 | 1 |
| Delaware | −0.00055 | −0.00113 | 0.00168 | 1 |
| Kent State | −0.00063 | −0.00099 | 0.00162 | 1 |
| Sam Houston | −0.00084 | −0.00075 | 0.00158 | 1 |
| Stanford | −0.00130 | +0.00025 | 0.00155 | 1 |
| Washington | +0.00035 | +0.00120 | 0.00154 | 1 |
| Mississippi State | +0.00102 | +0.00051 | 0.00153 | 0 |
| Georgia | +0.00042 | +0.00102 | 0.00143 | 1 |
| Missouri | +0.00091 | +0.00050 | 0.00141 | 1 |
| Ohio | −0.00059 | −0.00079 | 0.00139 | 1 |

#### 2b. Nov 4 state (through week 9)

| Metric | Value |
|--------|------:|
| median \|Δoff\| | 0.000345 |
| median \|Δdef\| | 0.000365 |
| max \|Δoff\| | 0.001741 |
| max \|Δdef\| | 0.002028 |
| Spearman(Δoff, # FCS games) | ρ=**−0.351**, p=2.4e-5 |

Top 15: Purdue, Texas, Kentucky, Eastern Michigan, Michigan State, Oklahoma, South Carolina, Florida Atlantic, Ohio, Florida, UCLA, Georgia, Massachusetts (2 FCS), Ole Miss, Iowa — max \|Δ\| = 0.00229 (Purdue).

---

### 3. Forecast impact

#### 3a. Week 10, Nov 4 as-of (n=61 finals)

| | Current | Fixed |
|--|--------:|------:|
| MAE | 13.890 | **13.864** |
| 80% coverage | 0.705 | 0.705 |

\|Δmargin\|: median **0.022**, p90 **0.063**, max **1.080**. No NaN/inf.

#### 3b. Seven week-10 card plays (posted line/price; fixed ratings)

Gates unchanged (edge ≥ 4.5%, gap ≤ 7). All seven still **pass**.

| Play | Ridge μ (side) | Gap | Edge | Gates |
|------|---------------:|----:|-----:|-------|
| Missouri +2.5/−104 | 3.956 | 6.46 | 15.8% | PASS |
| Ole Miss +3.5/−105 | 2.538 | 6.04 | 14.1% | PASS |
| LSU −6/−108 | 11.664 | 5.66 | 13.1% | PASS |
| UConn +7.5/−102 | −2.705 | 4.80 | 11.8% | PASS |
| Auburn −10/−108 | 14.116 | 4.12 | 8.6% | PASS |
| Texas State +5.5/−108 | −1.703 | 3.80 | 8.2% | PASS |
| Kentucky +6.5/−105 | −3.002 | 3.50 | 7.8% | PASS |

\|Δμ\| vs current on these plays ≤ 0.063.

#### 3c. Week 11, as-of now

Production path returned **n=61** games (not 67 — slate available locally). \|Δmargin\|: median **0.021**, p90 **0.040**, max **0.116**.

Largest 15 \|Δ\| (signed Δ = fixed − current):

| Matchup | Δmargin |
|---------|--------:|
| Wake Forest @ SMU | +0.116 |
| Tennessee @ Texas A&M | −0.072 |
| Georgia Southern @ Georgia State | −0.067 |
| James Madison @ UConn | +0.055 |
| Purdue @ Iowa | −0.055 |
| Texas Tech @ Oklahoma State | −0.053 |
| Middle Tennessee @ Liberty | −0.040 |
| Troy @ South Alabama | −0.040 |
| Minnesota @ Penn State | −0.039 |
| North Dakota State @ Hawai'i | −0.038 |
| New Mexico State @ Missouri State | −0.037 |
| San José State @ Air Force | −0.034 |
| Arkansas State @ Coastal Carolina | −0.033 |
| Nevada @ Northern Illinois | −0.033 |
| Florida @ Kentucky | −0.032 |

---

### 4. Validation (criteria fixed before results)

Walk-forward 2026 weeks 2–10; same champion weights. 2023–2025: **skipped** (time; 2026-only completed). ATS vs closing consensus: **unavailable** in this harness run (pred frames had no attachable `spread_close` / consensus close → `ats_cur`/`ats_fix` = null).

| Week | MAE cur | MAE fix | cov80 cur | cov80 fix |
|-----:|--------:|--------:|----------:|----------:|
| 2 | 15.067 | 15.052 | 0.709 | 0.721 |
| 3 | 13.408 | 13.281 | 0.853 | 0.867 |
| 4 | 15.695 | 15.678 | 0.648 | 0.634 |
| 5 | 13.996 | 13.992 | 0.746 | 0.746 |
| 6 | 11.618 | 11.597 | 0.897 | 0.897 |
| 7 | 12.314 | 12.343 | 0.790 | 0.790 |
| 8 | 12.075 | 12.092 | 0.839 | 0.821 |
| 9 | 14.256 | 14.262 | 0.714 | 0.714 |
| 10 | 13.890 | 13.864 | 0.705 | 0.705 |
| **mean** | **13.591** | **13.573** | **0.7668** | **0.7661** |

Promotion API: present; verdict **`NOT_RUN_NO_CHALLENGER`** (same champion weights for both pins — not worse than current).

#### Criteria

| Criterion | Result |
|-----------|--------|
| 1b–1c tests | **PASS** |
| MAE fixed ≤ current + 0.10 (13.573 ≤ 13.691) | **PASS** |
| cov80 fixed in 0.75–0.92 **or** no further from 0.80 than current (both ~0.766; \|fix−0.80\|=0.034 ≈ \|cur−0.80\|) | **PASS** |
| promotion not worse | **PASS** |
| no NaN/inf in sandbox ratings/forecasts | **PASS** |
| **Overall** | **PASS** |

No tuning performed to pass.

---

### Verdict

**W11-FIX-1 overall: PASS.** Ship-ready on branch only — do **not** merge/publish until explicitly approved. Rating/margin deltas are small (max \|Δμ\| week-10 ≈ 1.08 pts on one game; card edges all still clear gates). FCS pin now matches C1 (higher def = better; weak FCS def = −0.05).

