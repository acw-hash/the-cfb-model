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

## W11-QA-2 — Remaining audit sections (READ-ONLY, post W11-2)

**Date:** 2026-11-12  
**HEAD at run:** `4358b5f`  
**Command:** `uv run python -u scripts/_qa_w11_qa2.py`  
**Artifacts:** `docs/notes/_artifacts/w11-qa/qa2_report.{json,md}`, `qa2_run.out`, `qa2_partial.json`  
**Ratings (C):** production `filter_history` SHA-256 `a66cf5f9c9eede4f0bc02d41ec6c32df80166e3f03f77f127392217b4400ac28` (W11_FCSFIX).  
**Week-10 (D/F/A2b):** Nov 4 publish_history / card lines (old model). Odds credits used: **0**.

### 1. Gate table

| Check | Verdict | Evidence |
|-------|---------|----------|
| C3 | **PASS** | Top off: WVU / Georgia / Miami / ND / Wake / Boise / S.Carolina / OkSt / Arizona / Houston. Top def (higher=better): **Tennessee, Alabama, Iowa, TCU, Oklahoma, Texas, UCLA, Wake, Old Dominion, Utah** — **SEC in def top10 = 4**, bot10 = 0. Bot def: CSU / NIU / EMU / Ball St / MTSU / Akron / MoSt / WKU / UTEP / Purdue. |
| C5 | **PASS*** | NaN/inf off/def = 0; teams unchanged since w1 = 0. Largest w9→w10 abs-sum: WKU 0.234, MTSU 0.206, LSU 0.179, Alabama 0.174, Tulsa 0.173. *Innovation z mean/var w5–w10: **UNVERIFIED** (`innovations.parquet` absent). |
| A1 | **PASS** | Paths: `data/registry/artifacts/v2/{production_ensemble,possessions_artifacts}.pkl`; `data/artifacts/state_space/filter_history.parquet`. SHA: ensemble `21c51341…`, possessions `26c729b1…`, filter `a66cf5f9…`. Champion `task23_fundamental_reduced_v3` v2. Mac `data/registry` for W10-3: **UNVERIFIED** (Mac commits absent here). |
| A2b | **PASS** | Edges at posted lines via Nov4 mu/sigma: Mizzou 18.64%, Ole Miss 14.86%, LSU 13.82%, UConn 13.31%, Auburn 9.05%, TXST 8.01%, **Kentucky 8.26%** (Mac 8.3%; delta=0.037 pp < 0.5). |
| B1 | **PASS** | Games/week 1–10: 99,86,75,71,59,58,62,56,56,63. Dup game_ids 0; completed-without-points 0; neutral_site 11. Card finals: TEX 34–20 MIZZ; UGA 42–7 OM; ALA 31–13 LSU; UNC 38–21 UConn; AUB 26–23 ARK; TXST 42–21 ORST; TENN 34–6 UK. Plays scoreboard cols absent (noted). |
| B2 | **PASS** | Mean snaps w8–10: 182.7 / 179.9 / 179.1. No PBP / <100 snaps: none. Null-EPA share ~0.25 all three. Garbage drop: w8 9.1%, w9 13.2%, w10 12.2% — no outlier flag. |
| B3 | **PASS** | No team_id↔school collisions; no w10 FBS missing from filter_history. |
| B4 | **FAIL** | CFBD home/away match Odds after normalize for all 7. Posted vs staged book line: Mizzou+2.5 OK; OM+3.5 OK; UK+6.5 OK. **Mismatch:** LSU posted -6 vs DK -5.5; UConn +7.5 vs FD +6.5; Auburn -10 vs MGM -10.5; TXST +5.5 vs MGM away +6.5 (home -6.5). |
| D1 | **PASS** | Ensemble non-additive; feature parts listed, do not sum to mu. Recompute at Nov4 as_of under current FCSFIX code within ~0.4 pts of published mu. Missouri: published home mu=+4.33 → American-style **Missouri -4.3** ("ridge has Missouri -4.5" rounded). |
| D2 | **PASS** | w8–w10: vintage `W9A_REVAL`, champion v2 / `production-v0_reduced_v3` / `task23_fundamental_reduced_v3`. w11: same identity + **vintage `W11_FCSFIX`**. Only change w10→w11 = vintage_label. |
| D3 | **PASS** | All 14 card-team rating inputs: latest event ≤ week 9 postgame / bye week 8; all before kickoff; none older than week 8. Publish as_of `2026-11-04T14:03:14Z`. |
| E1 | **PASS*** | Model MAE / cov80 w5–w10: 14.08/0.86; 11.60/0.95; 12.42/0.98; 12.08/0.96; 14.15/0.93; 13.91/0.84. *Closing-consensus MAE / difference: **null** (no attachable close in harness) → close half **UNVERIFIED**. |
| E2 | **UNVERIFIED** | n_slate_rows=0 (no closing-consensus attach for ATS buckets / P4 splits). |
| E3 | **UNVERIFIED** | Ledgers w2,w4–w9 present (7 files); w10 ledger absent. Full W-L-P/CLV tabulation deferred. From prior W11-1 closes (staged): card **1-6-0**; see addendum E3 table (same-book closes). |
| E4 | **UNVERIFIED** | Edge/gap sparse in ledgers; P4 share / mean gap / edge / \|Ridge−close\| not computed. |
| F1 | **FAIL** | Mizzou: mu=4.33 sigma=16.41 line+2.5 p_cover=0.668 edge~18.6%; interval width 53.10 vs N(0,sigma) 80% width 42.06 — **no match**. Kentucky: mu=3.06 sigma=19.42 line+6.5 p_cover=0.572; width 62.66 vs 49.79 — **no match**. sigma source: `publish_history.sigma_margin`. |
| F2 | **PASS** | Gates edge>=4.5% gap<=7 QB STARTER max7. Posted-7 edge/gap all clear. Local probe qb_status_unknown; Mac QB statuses **UNVERIFIED** here. |
| G2b | **FAIL** | R2 `latest/meta.json`: week **11**, published_at `2026-11-12T15:41:33Z`, vintage `W11_FCSFIX`. Homepage (`ridgecfb.com`) "Higher is better" mentions = **0**. Regex flagged `stale` in HTML/JS (likely bundle substring; no maintenance/fixture banner). |
| G3 | **FAIL** | Claims vs NEW w10 ratings (higher=better): see list below. |

### G3 claim list (only)

| Claim | Verdict | Correct under higher=better |
|-------|---------|------------------------------|
| Tennessee's defense 137th of 138, allowing +0.26 EPA/play | **wrong** | Tennessee **1st of 138**, def_epa **+0.269** |
| Texas's D slipped +0.13 → +0.21 EPA/play allowed | **wrong** | w9→w10 def_epa **0.214 → 0.206** (claim's "allowed" framing ≠ def_epa) |
| Auburn's defense +0.20 → +0.09 allowed | **wrong** | w9→w10 def_epa **0.092 → 0.055** |
| UConn's defense negative EPA every week | **correct** | def_epa negative in **10/10** weeks |

### 2. Defects

**(i) Changes forecasts / edges**
- None newly found that rewrite Nov-4 mu/edges: A2b Kentucky parity holds; D1 recompute within ~0.4 pts under FCSFIX code (expected drift vs old pin).
- **B4** posted card lines ≠ staged book lines for LSU/UConn/Auburn/TXST — week-10 edges were computed at **posted** lines (A2b), not at the mismatched staged snapshot lines. Touches: LSU -6 vs -5.5; UConn +7.5 vs +6.5; Auburn -10 vs -10.5; TXST +5.5 vs +6.5.

**(ii) Display / copy only**
- **G3** week-10 tweet/rating-rank claims invert defense ranking language under higher=better (Tennessee claimed 137th; true rank **1**). File context: social blurbs (Mac artifacts absent); measured on production ratings.
- **G2b** homepage HTML lacks visible "Higher is better" defense label (chart component still has copy in `RatingTrajectoryChart.tsx` per earlier C2 — not SSR'd on home).
- **F1** published interval width ≠ 1.28*sigma*2 from `sigma_margin` — intervals from quantile/CQR fields (`export.py` ~735–776 / margin_interval_*), not sigma-derived; copy implying sigma↔interval width is misleading.

**(iii) Process / ops**
- **Broken EPA-gap check** (carry-forward): `docs/notes/_artifacts/w11-1/audit.py:397-402` — `if 10 in rating_weeks: HAS-WEEK10-RATING` treats publish-week synthetic point as folded week-10 game. Same pattern W9-1/W10-1. Labels only; week-10 forecasts were not on stale ratings (addendum C4).
- Closing-consensus attach missing → E1 close MAE / E2 ATS buckets **UNVERIFIED**.
- `innovations.parquet` absent → C5 innovation z **UNVERIFIED**.
- Mac W10-3 registry / sat-card commits still absent → A1 Mac registry **UNVERIFIED**; F2 QB STARTER on-machine **UNVERIFIED**.
- w10 `posted_calls` ledger absent locally (card graded from task lines + W11-1 closes).

### Numbers-only reminder (E)
No edge conclusions from card n=7 or incomplete slate ATS. E3 card closes previously: **1-6-0**.

## W11-QA-3 — Pricing calibration + week-11 card probe

**Date:** 2026-11-12  
**HEAD before:** `b277a8b`  
**Export gate:** `export False` (verified).  
**Commands:** `uv run python -u scripts/_qa_w11_qa3_calib.py`; then (PASS) odds stage + `uv run python -u scripts/_s6_w11_card.py`.  
**Artifacts:** `docs/notes/_artifacts/w11-qa/qa3_part_a.{json,md,out}`; `docs/notes/_artifacts/social-s6-w11-card/`.

### Reclassify — B4 (notes only)

B4 (posted w10 lines vs Nov-4 staged snapshot) is **(iii) process**, not a pricing defect. Mac Sat Nov 7 W10-4 fresh check matched LSU DK -6, UConn FD +7.5, Auburn MGM -10, TXST MGM +5.5 at the card books. Only the Nov-4 snapshot is on this machine; Mac commits not pushed.

---

### Part A — pricing sigma vs realized error (CARD GATE)

| Check | Verdict | Evidence |
|-------|---------|----------|
| A1 | **PASS** | Per-game `sigma_margin` on prediction/publish row (LightGBMSigmaHead). Card path: `build_candidates_from_odds` reads `sigma_margin` (`provider.py:575-576`), samples joint draws (`:665-677`), `P(cover)=two_way_side_prob(spread_cover_probs(...))` (`:692-698`). Card uses `n_draws=20_000` (`_s6_w10_card.py:778`). Week-11 FBS–FBS n=61: median **17.70**, IQR **1.67** (p25 16.91, p75 18.57). |
| A2 | **PASS** | Graded 2026 w2–w10 FBS–FBS with published pre-kickoff mu: **n=504**. SD(actual−mu)=**16.49**; robust SD=1.4826×MAD=**16.29**. By week SD ~14.5–18.5. By \|mu\|: 0–7 n=174 SD=16.54; 7–14 n=175 SD=15.51; 14+ n=155 SD=17.51. |
| A3 | **PASS** | sigma_price = median sigma on graded sample = **19.23**. R_plain=19.23/16.49=**1.166**; R_robust=**1.181**. Using w11 median: R=**1.074**. Rule: FAIL if R_plain < 0.90 → **PASS**. |
| A4 | **PASS** (info) | n=441 priced at latest pre-kickoff regulated-book line (MC 20k). Buckets: 50–55 n=115 pred=0.529 real=0.513; 55–60 n=94 pred=0.576 real=0.457; 60–65 n=98 pred=0.624 real=0.500; 65+ n=134 pred=0.739 real=0.493. |
| A5 | **UNVERIFIED** | Skipped: CFBD `/lines` is season_week grain; w5–w10 would be 6 calls (>5 credit cap). `lines_historical` has no 2026 rows here. |
| **CARD_GATE** | **PASS** | R_plain >= 0.90 → continue to Part B. |

---

### Part B — Week-11 card probe (NO PUBLISH, NO QB)

**Decisions (fixed):** Sat Nov 14 ET only; HFA NONE; stake 0.014; max 7; weekly cap 10%; gates edge>=4.5% and \|Ridge−market\|<=7; regulated books only (espnbet display "theScore Bet").

#### B1 — Odds pull

- `uv run python -u docs/notes/_artifacts/social-s6-w11-card/_w11_allbooks_stage.py`
- markets=spreads, regions=us,us2, american; Saturday week-11 events only.
- Credits used: **2** (remaining 342). Hard-stop 10 OK.
- Raw: `odds_allbooks_20261112T162342Z.json`. Staged **1708** rows with per-book `last_update` as `event_time`.
- Drops (stale>6h / one-sided): **0**. Books dropped: none.

#### B2 — Card script

- Copied `scripts/_s6_w10_card.py` → `scripts/_s6_w11_card.py`.
- Diff: artifact dir `social-s6-w11-card`; publish_history `2026_w11.jsonl`; HFA_MODE `"NONE"`; SITE_URL `https://www.ridgecfb.com`; Saturday Nov 14 ET filter; replies_w11.md.
- Production path: `build_candidates_from_odds` → `BetCandidate`, seed 42+game_id.

#### B3 — Top 25 non-QB-gate candidates (by edge)

| rank | game_id | kickoff ET | side | book | line | price | Ridge | gap | edge | noon |
|-----:|--------:|------------|------|------|-----:|------:|------:|----:|-----:|:----:|
| 1 | 401864537 | Sat 11:00 PM ET | Hawai'i | espnbet | 6.5 | +100 | -0.10 | 6.60 | 0.1726 | |
| 2 | 401871100 | Sat 7:00 PM ET | South Alabama | hardrockbet | -3.5 | +110 | -8.73 | 5.23 | 0.1536 | |
| 3 | 401869928 | Sat 6:00 PM ET | Marshall | hardrockbet | 1.5 | -105 | -4.71 | 6.21 | 0.1534 | |
| 4 | 401858520 | Sat 12:00 PM ET | Maryland | fanduel | 7.5 | +100 | 1.82 | 5.68 | 0.1435 | Y |
| 5 | 401856861 | Sat 5:30 PM ET | West Virginia | betrivers | -3.0 | +100 | -8.15 | 5.15 | 0.1391 | |
| 6 | 401856887 | Sat 4:15 PM ET | Oklahoma | betmgm | 1.0 | -112 | -5.51 | 6.51 | 0.1357 | |
| 7 | 401856862 | Sat 2:00 PM ET | TCU | betrivers | -10.5 | -109 | -16.24 | 5.74 | 0.1350 | |
| 8 | 401856863 | Sat 9:30 PM ET | Oklahoma State | draftkings | -3.5 | -105 | -9.24 | 5.74 | 0.1335 | |
| 9 | 401862826 | Sat 3:30 PM ET | Tulsa | betrivers | -5.5 | -110 | -12.47 | 6.97 | 0.1327 | |
| 10 | 401856858 | Sat 3:30 PM ET | Baylor | hardrockbet | 7.5 | -110 | 1.35 | 6.15 | 0.1300 | |
| 11 | 401862829 | Sat 1:00 PM ET | Temple | hardrockbet | -2.5 | -105 | -7.08 | 4.58 | 0.1276 | |
| 12 | 401871061 | Sat 3:30 PM ET | Liberty | fanduel | -21.5 | +100 | -26.40 | 4.90 | 0.1260 | |
| 13 | 401862827 | Sat 9:00 PM ET | North Texas | betrivers | -2.5 | -109 | -7.53 | 5.03 | 0.1217 | |
| 14 | 401856864 | Sat 7:30 PM ET | Arizona | espnbet | 2.5 | +100 | -1.49 | 3.99 | 0.1194 | |
| 15 | 401864539 | Sat 7:30 PM ET | New Mexico | espnbet | -14.5 | +100 | -18.50 | 4.00 | 0.1159 | |
| 16 | 401856859 | Sat 12:00 PM ET | Iowa State | fanduel | -3.5 | +110 | -6.00 | 2.50 | 0.1127 | Y |
| 17 | 401870752 | Sat 3:30 PM ET | Western Kentucky | draftkings | 7.5 | -112 | 2.27 | 5.23 | 0.1110 | |
| 18 | 401860915 | Sat 9:30 PM ET | Utah State | betrivers | -3.0 | -107 | -7.44 | 4.44 | 0.1094 | |
| 19 | 401856884 | Sat 6:15 PM ET | Kentucky | espnbet | -3.5 | +100 | -7.72 | 4.22 | 0.1080 | |
| 20 | 401871099 | Sat 5:00 PM ET | Louisiana Tech | espnbet | -17.5 | -105 | -21.86 | 4.36 | 0.1075 | |
| 21 | 401856886 | Sat 12:00 PM ET | Auburn | hardrockbet | -2.5 | -110 | -7.59 | 5.09 | 0.1071 | Y |
| 22 | 401858295 | Sat 3:30 PM ET | Virginia | fanduel | -10.5 | +100 | -14.34 | 3.84 | 0.1065 | |
| 23 | 401870796 | Sat 3:00 PM ET | Missouri State | espnbet | -1.5 | +100 | -4.62 | 3.12 | 0.1016 | |
| 24 | 401860914 | Sat 6:00 PM ET | Boise State | draftkings | -14.5 | -110 | -19.07 | 4.57 | 0.0949 | |
| 25 | 401871060 | Sat 3:00 PM ET | Delaware | ballybet | -4.0 | -113 | -8.60 | 4.60 | 0.0929 | |

Slate: 56 Saturday games; pre-QB survivors: 36. Noon-flagged in top 25: Maryland, Iowa State, Auburn.

#### B4 — QB-check list (top 20)

```
401864537 | North Dakota State @ Hawai'i (Sat 11:00 PM ET)
401871100 | Troy @ South Alabama (Sat 7:00 PM ET)
401869928 | App State @ Marshall (Sat 6:00 PM ET)
401858520 | Wisconsin @ Maryland (Sat 12:00 PM ET)
401856861 | Kansas @ West Virginia (Sat 5:30 PM ET)
401856887 | Ole Miss @ Oklahoma (Sat 4:15 PM ET)
401856862 | Kansas State @ TCU (Sat 2:00 PM ET)
401856863 | Texas Tech @ Oklahoma State (Sat 9:30 PM ET)
401862826 | Florida Atlantic @ Tulsa (Sat 3:30 PM ET)
401856858 | Baylor @ BYU (Sat 3:30 PM ET)
401862829 | UAB @ Temple (Sat 1:00 PM ET)
401871061 | Middle Tennessee @ Liberty (Sat 3:30 PM ET)
401862827 | North Texas @ UTSA (Sat 9:00 PM ET)
401856864 | Utah @ Arizona (Sat 7:30 PM ET)
401864539 | UNLV @ New Mexico (Sat 7:30 PM ET)
401856859 | Cincinnati @ Iowa State (Sat 12:00 PM ET)
401870752 | Jacksonville State @ Western Kentucky (Sat 3:30 PM ET)
401860915 | Utah State @ San Diego State (Sat 9:30 PM ET)
401856884 | Florida @ Kentucky (Sat 6:15 PM ET)
401871099 | Southern Miss @ Louisiana Tech (Sat 5:00 PM ET)
```

## W11-QA-4 — Card-range calibration (READ-ONLY)

**Date:** 2026-11-12  
**Command:** `uv run python -u scripts/_qa_w11_qa4_card_calib.py`  
**Artifacts:** `docs/notes/_artifacts/w11-qa/qa4_card_calib.json`, `qa4_ledger_rows.json`, `qa4_run.out`  
**Export:** False. No publish / R2 / config edits.

Source: W11-QA-3 A4 path (2026 w2–w10, FBS–FBS, staged pre-kickoff regulated lines, production MC `n_draws=20_000`, seed `42+game_id`).

### 1. Full A4 table (from W11-QA-3)

| bucket | n | mean predicted P(cover) | realized |
|--------|--:|------------------------:|---------:|
| 50–55 | 115 | 0.5286 | 0.5130 |
| 55–60 | 94 | 0.5757 | 0.4574 |
| 60–65 | 98 | 0.6243 | 0.5000 |
| 65+ | 134 | 0.7388 | 0.4925 |

n_used = 441 (qa3_part_a.json).

### 2. Same, card gates only (edge >= 4.5% and |Ridge − line| <= 7.0)

| bucket | n | mean predicted P(cover) | realized |
|--------|--:|------------------------:|---------:|
| 50–55 | 20 | 0.5459 | 0.6500 |
| 55–60 | 94 | 0.5757 | 0.4574 |
| 60–65 | 80 | 0.6205 | 0.4875 |
| 65+ | 6 | 0.6611 | 0.5000 |

| pooled | n | mean predicted | realized | 90% Wilson CI (realized) |
|--------|--:|---------------:|---------:|--------------------------|
| gated | 200 | 0.5932 | 0.4900 | [0.4324, 0.5479] |

### 3. Posted card plays w2–w10 (ledgers on this machine + w10 task-card 7)

Ledgers: `data/social/posted_calls_2026_w{2,4,5,6,7,8,9}.json`. Week 10: seven plays from W11-QA addendum (no local w10 ledger). Week 3 absent. P(cover) via same production MC at the posted side/line using that week’s published μ/σ.

| pooled | n | mean predicted | realized | 90% Wilson CI (realized) |
|--------|--:|---------------:|---------:|--------------------------|
| posted cards | 52 | 0.6237 | 0.4808 | [0.3706, 0.5928] |

(n_calls_loaded includes all ledger rows + 7 w10; n_used=52 after drops for push / missing publish join.)

### 4. Week-11 top-25 × FCS

Under staged 2026 `teams.classification`, **no** top-25 candidate involves a non-FBS school (`non_fbs_in_top25 = []`).

Rank-1 matchup `North Dakota State @ Hawai'i`: staged row for North Dakota State is `team_id=2449`, **classification=`fbs`**, conference=`Mountain West` (not FCS on this machine’s teams table).

Code path for the single shared pinned FCS state (used when `home_is_fcs` / `away_is_fcs` is true):

```350:363:src/ncaa_quant/ratings/league_state.py
        h_key = FCS_TEAM_KEY if home_is_fcs else str(home_id)
        a_key = FCS_TEAM_KEY if away_is_fcs else str(away_id)
        ...
        fcs = fcs_pinned_state(cfg)
```

```529:541:src/ncaa_quant/ratings/state_space.py
def fcs_pinned_state(config: StateSpaceConfig) -> GaussianState:
    """Pooled FCS prior with large variance (DESIGN §9.3)."""
    d = config.n_dims
    mean = np.full(d, config.fcs_prior_mean, dtype=float)
    ...
    if "def_epa" in config.state_dims:
        mean[config.dim_index("def_epa")] = config.fcs_prior_mean
    cov = np.eye(d, dtype=float) * config.fcs_prior_var
    return GaussianState(mean=mean, cov=cov)
```

```743:746:src/ncaa_quant/ratings/state_space.py
def _team_key(team_id: Any, *, is_fcs: bool) -> str:
    if is_fcs:
        return FCS_TEAM_KEY
    return str(team_id)
```

Filter wiring: `state_space.py:1210-1241` (`home_is_fcs` / `away_is_fcs` → `_team_key` / `league.update_game`).
