# Pre-registration — 2026 paper-trade confirmatory instrument

**Instrument:** ADR 0007 primary confirmatory check on DESIGN §1.6  
**Registered:** 2026-09-02 (before first Labor Day kickoff `2026-09-03T22:00:00Z`)  
**Branch:** `social-s1-s2`  
**2025 lockbox:** untouched — not used for this instrument  

This document freezes the betting layer, accept-loop semantics, analysis plan, and
success criteria **before** 2026 forward outcomes exist. Any post-hoc change to
the frozen items voids the instrument (§5).

---

## STOP conditions at registration

| Check | Result |
|-------|--------|
| HEAD dirty at write time | **YES** — unrelated modified/untracked files present (`docs/notes/social-s6-w1-card.md` modified; several untracked artifacts). **This prereg commit contains only this file.** Code/config frozen at parent SHA below. |
| Champion model unresolved | **NO** — resolved (§1). |
| S5 314 re-stratifiable for tail-fill | **YES** — `stake_fraction` present on all 314 rows; tail-fill-excluded baseline equals full baseline (0 tail-fill tickets). |
| §1.6 secondary AUC numeric success bar | **NO NUMBER IN SPEC** — see §3. |

---

## 1. Frozen configuration

### Git SHA (code/config freeze anchor)

**Parent SHA at registration write:** `8f6506639a601b17e7f57c3cf652aa4d080b11a0`  
**Prereg commit:** the git commit that introduces this file (timestamp in commit message).

Working tree was dirty at write time (unrelated files only). The instrument binds to
the committed tree containing this document plus ancestor code at the prereg commit SHA.

### Champion model (live predictions / paper-trade source)

| Field | Value |
|-------|-------|
| Live config YAML | `configs/ablations/task23_fundamental_full_reduced_v3.yaml` |
| `CHAMPION_LIVE_CONFIG` | `task23_fundamental_full_reduced_v3` |
| `model_version` | `production-v0_reduced_v3` |
| `run_id` | `task23_fundamental_reduced_v3` |
| `champion_version` (registry / publish) | **2** |
| `ensemble_scope` | `REDUCED_PER_ADR_0013` |
| `vintage_label` | `W9A_REVAL` |
| Walk-forward `seed` | **42** |
| Champion backtest `config_hash` | `82b0cccdb0589fa4f53442489e1de00ca4bae3664cd831b9b213077e8683e761` |
| Feature-time label | `FEATURE_TIME=TUESDAY_DECISION` |

No separate `feature_hash` field exists on publish artifacts. **`config_hash`** from
the champion backtest manifest (`data/backtests/task23_fundamental_reduced_v3/full/manifest.json`)
is the reproducibility anchor for the mapping layer per DESIGN §1.4.

### `configs/betting.yaml` (verbatim)

```yaml
betting:
  # Named in DESIGN §12.
  min_edge_sides: 0.025
  min_edge_totals: 0.03
  kelly_fraction: 0.25
  max_stake_pct: 0.015
  # Bowl stricter threshold as an edge multiplier (TASK + §12).
  # PLACEHOLDER — spec says "stricter" but does not name the factor.
  bowl_edge_multiplier: 1.5
  # PLACEHOLDER — spec names the filter, not the integer.
  max_bets_per_week: 10
  # PLACEHOLDER — "max exposure/team" (§12); fraction of bankroll.
  max_exposure_per_team: 0.05
  # PLACEHOLDER — "min model-market agreement checks" (§12); abs residual in points.
  min_model_market_agreement: 7.0
  no_bet_on_stale: true
  no_bet_on_qb_unknown: true
  # Playbook: odds inputs fresh < 6h (snapshot age vs decision as_of).
  odds_max_age_hours: 6
  # PLACEHOLDER — weekly aggregate exposure as fraction of bankroll (§12).
  max_weekly_exposure: 0.10
  # S5: real odds→candidate provider; off until operator enables consciously.
  candidates_enabled: false
  # Totals implemented but disabled pending separate σ_total calibration.
  candidate_markets:
    - side
```

### PLACEHOLDER values — none derived

Every yaml value carrying a `PLACEHOLDER` comment is **frozen as written**. None
was derived from walk-forward optimization, the S5 314-ticket population, or the
2026 W1 slate:

| Key | Frozen value | PLACEHOLDER comment in yaml |
|-----|-------------:|-----------------------------|
| `bowl_edge_multiplier` | 1.5 | spec says "stricter" but does not name the factor |
| `max_bets_per_week` | 10 | spec names the filter, not the integer |
| `max_exposure_per_team` | 0.05 | §12 fraction of bankroll |
| `min_model_market_agreement` | 7.0 | §12 abs residual in points |
| `max_weekly_exposure` | 0.10 | §12 weekly aggregate exposure fraction |

Non-placeholder keys (`min_edge_sides`, `kelly_fraction`, `max_stake_pct`, gate
flags, `odds_max_age_hours`) are likewise frozen unchanged.

### Frozen operational selection convention (not in yaml)

S5 baseline comparability requires the **public ticket subset** used in S3/S4/S5:

- **Weeks:** CFBD **week ≥ 2** only (week 1 excluded from public card — S3 convention).
- **Public edge bar:** **`edge >= 0.05`** after `apply_bet_filters` (`scripts/_s5_common.py` `THRESHOLD = 0.05`).
- **Market:** side only (`candidate_markets: [side]`).
- **Provider:** `build_candidates_from_odds`, `n_draws=20_000`, `seed=42`.

This convention is frozen for baseline comparison; it is **not** written to
`betting.yaml` (`min_edge_sides` remains 0.025 for §12 filter).

---

## 2. Frozen gate ordering and sizing semantics

### Which ordering is under test

**Production** `apply_bet_filters` (`src/ncaa_quant/pipelines/predict.py`) — the
path wired to publish / paper-trade when `candidates_enabled` is on.

Probe-only step ordering in `scripts/_s6_w1_card.py` (QB gate last) is **not** the
confirmatory instrument. Per W1-ACCEPT, production and probe **share the same exposure
accept-loop semantics** (skip-and-continue); they differ only in whether non-exposure
filters are evaluated in one call (production) vs staged probe steps.

### Frozen accept-loop semantics

| Property | Frozen behavior |
|----------|-----------------|
| Sort order | Edge descending (`sorted(..., key=edge, reverse=True)`) |
| Stake sizing | **Unshrunk** quarter-Kelly: `recommended_stake(..., weekly_exposure_so_far=0.0, team_exposure_so_far=0.0)` |
| Exposure enforcement | `evaluate_filters` with live `ExposureState` on **full** proposed stake |
| On exposure reject | **Skip-and-continue** — no `break`; loop advances to next edge rank |
| Stake resize path | **Bypassed** — live-exposure sizing in `recommended_stake` is not used in the loop |

**Authority:** `docs/notes/_artifacts/w1-accept-loop/probe.md` (2026 W1: ranks 25 and 28
accepted after ranks 7–24 rejected; 8 survivors non-contiguous in edge rank).

**Bypass rationale (frozen, not reopened):** `docs/notes/_artifacts/w1-sizing-read/bypass.md`
— passing live exposure into stake sizing makes `MAX_WEEKLY_EXPOSURE` /
`MAX_TEAM_EXPOSURE` **unreachable** (commit `aee6178a`, 2026-08-25). The resizing
path stays bypassed for the life of this instrument.

**DESIGN / ADR status:** skip-and-continue + unshrunk-stake semantics are **NOT FOUND**
in DESIGN §12 or any ADR; they are side effects of commit `aee6178a` (documented in
W1-SIZING-READ). This prereg **freezes** them; it does not fix them.

---

## 3. Pre-registered success criteria (numbers)

### Primary — same-book, probability-valued mean CLV

| Quantity | Value | Source |
|----------|------:|--------|
| **Success threshold (mean CLV)** | **+0.0238** | S5 vig breakeven (`docs/notes/_artifacts/social-s5/p0_3_halves.json` → `mean_prob_clv_needed_vs_half_close`) |
| **S5 baseline (314 tickets, all strata)** | **+0.0044** | S5 P0-3 headline same-book probability CLV (`p0_3_halves.json`) |
| **Population** | Tail-fill **excluded** stratum only (§4) | — |
| **Settlement** | `clv_settlement=same_book`, probability-valued `clv_method` | DESIGN §1.6, §2.7; ADR 0006 |

**DESIGN §1.6 formal gate (also binding):** mean same-book CLV **> 0** with **95% CI
excluding 0** over **≥ 300** settled headline tickets.

Both the vig threshold (+0.0238) and the DESIGN CI gate must be reported. Primary
**success** requires meeting the DESIGN §1.6 CI gate on the tail-fill-excluded stratum.
The +0.0238 line is the **economically meaningful** bar (clears −110 vig from a 0.50 close);
report mean CLV against both +0.0238 and +0.0044 baseline.

### Secondary — candidate `p_win` AUC (ATS discrimination)

| Quantity | Value | Source |
|----------|------:|--------|
| **S5 baseline AUC** | **0.493** [0.43, 0.56] | S5 P0-2 (`docs/notes/_artifacts/social-s5/p0_2_discrimination.json`) |
| **Success threshold** | **NO NUMBER IN SPEC** | DESIGN §1.6 secondary lists ATS accuracy ≥ 51.5%, not candidate AUC |

Report AUC with 95% CI on settled tail-fill-excluded tickets. **No success/fail bar**
is pre-registered for AUC beyond descriptive comparison to S5 baseline.

### Minimum n before any published read

**300** settled same-book, probability-valued, tail-fill-excluded headline tickets.

Source: DESIGN §1.6 primary criterion ("≥300 bets settled at `clv_settlement=same_book`").

No interim publish, social card, or operator-facing "confirmed edge" statement before
**n = 300** on the primary stratum.

### Pre-committed stopping week

| Quantity | Value |
|----------|-------|
| **Stopping calendar** | End of **CFBD 2026 regular season** (final scheduled week with FBS conference games, typically week **15**) |
| **Minimum duration (DESIGN §16 item 2)** | Full season **or at minimum half** — **NO NUMBER IN SPEC** for which CFBD week index counts as "half" |

Analysis runs once at stopping week (or when n ≥ 300, whichever comes first for the
primary read gate). No optional peeking before n = 300.

---

## 4. Pre-registered exclusion stratum — TAIL-FILL

### Mechanical definition (fixed before outcomes)

**Tail-fill ticket:** any **accepted** paper-trade candidate whose
`stake_fraction` is **strictly less than** the 1.5% `max_stake_pct` cap
(`0.015` at frozen config).

Equivalently: quarter-Kelly stake before exposure reject, after hard/config caps,
that did **not** bind at `max_stake_pct`.

### Analysis use

| Stratum | Primary CLV read | Reporting |
|---------|------------------|-----------|
| **Cap-bound** (stake = 0.015) | **Included** | Headline confirmatory stratum |
| **Tail-fill** (stake < 0.015) | **Excluded** | Separate stratum; reported but never pooled into primary |

### Rationale (pre-committed)

The accept loop allocates **residual weekly room** to weaker-edge candidates once
cap-bound stakes exhaust the weekly budget (skip-and-continue). On the frozen 2026
Week 1 slate (post-crosswalk, `as_of=2026-09-01T20:38:58Z`):

| Rank | Edge | Stake | Outcome |
|-----:|-----:|------:|---------|
| 25 | 0.0410 | 0.009084 | **Accepted** (tail-fill) |
| 28 | 0.0264 | 0.000814 | **Accepted** (tail-fill) |
| 7–24 | 0.0952–0.0121 | ≥ 0.012061 | Rejected (full stake exceeds room) |

Tail-fill exclusion is pre-registered so the primary read cannot drop weak-edge
residual-room tickets post hoc.

### S5 baseline re-stratification

| Question | Answer |
|----------|--------|
| Can the 314-ticket S5 population be re-stratified? | **YES** — `docs/notes/_artifacts/social-s4/p0_graded_314.parquet` includes `stake_fraction` on all 314 rows |
| Tail-fill count in S5 314 | **0** (all 314 have `stake_fraction = 0.015` exactly) |
| Tail-fill-excluded S5 baseline (mean prob CLV) | **Identical to full 314 baseline: +0.0044** |
| Comparison quality | **Exact** on historical baseline for primary CLV; no approximation required for S5 |

2026 forward paper-trade **may** contain tail-fill tickets (Week 1 already does in
simulation); S5 baseline does not. Disclose stratum counts on every read.

---

## 5. Analysis method (fixed)

- **CLV settlement:** same-book per ADR 0006 / DESIGN §2.7 — probability-valued
  CLV when line unchanged at close; `line_units` and `fallback_consensus` reported
  separately, **never pooled** into headline.
- **`line_shopping_capture`:** reported alongside CLV; **never pooled into CLV** or
  primary criterion (DESIGN §1.6, §2.7).
- **Strata:** tail-fill vs cap-bound **never pooled** for primary read.
- **No mid-instrument changes:** no post-hoc filter edits, threshold edits, stratum
  redefinition, or accept-loop changes.

**Void rule (plain):** Any change to `configs/betting.yaml`, production gate ordering
in `apply_bet_filters`, or accept-loop stake/bypass semantics **VOIDS this instrument
and restarts at n = 0**. A new pre-registration is required.

---

## 6. What would falsify (no edge)

The confirmatory instrument is **falsified** (no demonstrated edge under live flow) if,
at **n ≥ 300** tail-fill-excluded same-book headline tickets:

1. **Mean same-book probability CLV ≤ 0**, **or**
2. **95% CI on mean CLV includes 0** (DESIGN §1.6 primary gate), **or**
3. **Mean same-book probability CLV < +0.0238** (does not clear vig breakeven — S5 showed
   +0.0044 vs +0.0238 needed).

Secondary AUC at or below the S5 baseline band (0.493, CI spanning 0.5) is **consistent
with falsification** but is not a standalone success criterion (no numeric bar in spec).

Honest inconclusive outcome (n < 300 at stopping week, or CI still spanning zero at
n ≥ 300 with point estimate > 0): **"not yet confirmed"** per ADR 0007 — not a promotion
of backtest results.

---

## 7. Known defects at freeze

Recorded honestly at registration. **Not fixed in this task.**

1. **Skip-and-continue accept loop** — edge-descending, unshrunk stakes, residual room
   to weak-edge tail-fill tickets (§2, §4). Semantics **NOT FOUND** in DESIGN or ADR;
   arrived via commit `aee6178a` (2026-08-25).

2. **Undefended PLACEHOLDER thresholds** (§1) — numeric caps and agreement window not
   derived from data.

3. **Week 1 partial read — QB worklist gap** — On the frozen W1 slate, Northwestern
   State @ Louisiana Tech (edge **0.1483**, step-3 rank **#1**) and Portland State @
   San Diego State (edge **0.1154**, rank **#3**) were step-4 exposure survivors but
   **excluded at step 5** with **no `qb_status` rows** (operational miss, not a market
   condition). Week 1 is therefore a **partial read** for any forward accounting that
   treats QB gate as binding.

4. **Mid-week crosswalk coverage change** — Spread coverage on the 2026 W1 slate moved
   **56/91 → 90/91** after the S7 crosswalk fix on the same raw pull; step-3 pool
   **21 → 28**. Instrument starts mid-season infrastructure drift; Week 1 step-4 set
   differs pre/post crosswalk (documented in `docs/notes/social-s6-w1-card.md` S7-DET).

5. **CLI cannot backdate `event_time`** — no backdated staged rows were written for this
   instrument; all timestamps reflect actual ingest/operator `as_of`.

6. **`candidates_enabled: false`** in frozen yaml — paper-trade wiring must not alter
   frozen thresholds; enabling the provider is an operational act, not a config change,
   provided `betting.yaml` values remain identical to §1.

---

## Epistemic ledger

| Claim | Status |
|-------|--------|
| Instrument designated by ADR 0007 | MEASURED (ADR text) |
| Config frozen verbatim | MEASURED (this commit) |
| Accept-loop semantics frozen | MEASURED (W1-ACCEPT, W1-SIZING-READ) |
| S5 baseline +0.0044 / vig +0.0238 | MEASURED (S5 P0-3 artifact) |
| S5 AUC 0.493 [0.43, 0.56] | MEASURED (S5 P0-2 artifact) |
| S5 tail-fill re-stratification | MEASURED (0/314 tail-fill) |
| 2026 forward edge | **NOT MEASURED** — instrument starts here |

---

*Registered before `2026-09-03T22:00:00Z` (first Labor Day kickoff). A
pre-registration written after outcomes exist is worth nothing.*

---

## Amendment 1 — baseline-convention eligibility for forward tickets

**Amends:** this document (parent commit `0026fb4`, 2026-09-02T14:01:51Z)  
**Written:** 2026-09-02 (before `2026-09-03T22:00:00Z`)  
**Scope:** analysis-plan amendment only — **not** a config change. Does **not**
edit `configs/betting.yaml` and does **not** trigger the §5 void rule.

### A1.0 — Problem statement

§1 freezes a **baseline selection convention** (CFBD week ≥ 2, public edge bar
`edge >= 0.05`, side market only) used to define the S5 +0.0044 comparand.
The frozen §12 config path (`min_edge_sides: 0.025`, all CFBD weeks) can accept
tickets outside that population. Week 1 forward survivors (Auburn, Duke) are in
that gap. Eligibility for the primary **n = 300** counter must be fixed before
outcomes.

**Population reference (current W1 step-4 set):** post-crosswalk exposure
survivors at `as_of=2026-09-01T20:38:58Z` (S7-DET; 8 games). Step-5 survivors
after QB gate: 2 games (Baylor @ Auburn, Tulane @ Duke). Source artifacts:
`docs/notes/_artifacts/w1-qb-refresh/report.json`,
`docs/notes/_artifacts/social-s7-xwalk-b/gate_rerun.json`.

### A1.1 — Mismatch table (W1 step-4 set vs baseline convention axes)

Baseline convention (§1): **week ≥ 2** AND **edge ≥ 0.05** AND **market == side**.
A ticket is baseline-convention-eligible only when **all three** pass.

| Matchup | Side | Edge | week ≥ 2 | edge ≥ 0.05 | market == side | All three (baseline-eligible) |
|---------|------|-----:|:--------:|:-----------:|:--------------:|:-----------------------------:|
| Northwestern State @ Louisiana Tech | Louisiana Tech | 0.1483 | **no** (W1) | yes | yes | **no** |
| Baylor @ Auburn | Auburn | 0.1472 | **no** (W1) | yes | yes | **no** |
| Portland State @ San Diego State | San Diego State | 0.1154 | **no** (W1) | yes | yes | **no** |
| Tulane @ Duke | Duke | 0.1048 | **no** (W1) | yes | yes | **no** |
| Marshall @ Penn State | Penn State | 0.0992 | **no** (W1) | yes | yes | **no** |
| North Carolina A&T @ Georgia State | Georgia State | 0.0978 | **no** (W1) | yes | yes | **no** |
| Eastern Illinois @ Minnesota | Minnesota | 0.0410 | **no** (W1) | **no** | yes | **no** |
| Central Michigan @ New Mexico | New Mexico | 0.0264 | **no** (W1) | **no** | yes | **no** |

**Per-axis counts (step-4, n = 8):**

| Axis | Inside baseline convention | Outside |
|------|---------------------------:|--------:|
| week ≥ 2 | 0 | 8 |
| edge ≥ 0.05 | 6 | 2 |
| market == side | 8 | 0 |
| **All three** | **0** | **8** |

**Step-5 survivors (n = 2):** Baylor @ Auburn (edge 0.1472, stake 0.015,
cap-bound) and Tulane @ Duke (edge 0.1048, stake 0.015, cap-bound). Both fail
**week ≥ 2** only; both pass edge and side axes. Neither is
baseline-convention-eligible.

### A1.2 — Eligibility rule (operator selection required)

Two pre-committed options. **Operator must select one** before the primary
read gate opens. Until selected, the instrument is incomplete on this axis.

#### Option (a) — BASELINE-MATCHED

Only tickets meeting **week ≥ 2 AND edge ≥ 0.05 AND market == side** count
toward **n = 300** on the tail-fill-excluded, same-book headline stratum.

**Consequence:** Week 1 tickets (including Auburn and Duke) are logged and
settled but **excluded from the primary read**. Comparison to the S5 +0.0044
baseline is **exact** (same population definition as §1).

#### Option (b) — ALL-CANDIDATES

Every accepted ticket under the frozen §12 config path counts toward **n = 300**
(tail-fill-excluded headline stratum unchanged).

**Consequence:** The forward population is **not** the S5 314-ticket population.
The +0.0044 comparison becomes **approximate** and must be **disclosed on every
read**.

**Operator selection:** `<SELECTED: a|b>`

### A1.3 — Per-ticket field at recommendation time

Every accepted forward ticket **must** record baseline-convention membership at
**recommendation time** (never recomputed at settlement):

| Field | Type | Semantics |
|-------|------|-----------|
| `baseline_convention_eligible` | `bool` | `true` iff week ≥ 2 AND edge ≥ 0.05 AND market == side at recommendation |
| `baseline_convention_exclusion_axes` | `list[str]` | Empty when eligible; otherwise one or more of: `week_lt_2`, `edge_lt_0.05`, `market_not_side` |

**Intended write location:** `RecommendationRecord`
(`src/ncaa_quant/betting/clv.py`) at the moment a forward paper-trade
recommendation is persisted — i.e. when `apply_bet_filters` accepts a candidate
and the instrument write path materializes a `RecommendationRecord` (or
equivalent staged row) with `recommended_at` set.

**Current state (pre-kickoff, 2026-09-02):** **No forward per-ticket write path
exists.** `candidates_enabled: false` in frozen §1 yaml; accepted W1 survivors
exist only in probe artifacts, not in a recommendation store. The social
sidecar (`export_social_candidates` → `CandidateRecord` in
`src/ncaa_quant/social/candidates.py`) lacks these fields and is not the
confirmatory instrument store. **`baseline_convention_eligible` must be captured
at recommendation time or it is unrecoverable** — edge and week at accept are
not guaranteed to be reconstructable from settlement inputs alone.

### A1.4 — Projected n (2026 regular season, stopping week 15)

Projection method: W1 observed step-5 survivors plus S5 P0-4 historical
per-week acceptance rates (`docs/notes/_artifacts/social-s5/p0_4_selection.json`,
55 weeks 2021–2024) applied to CFBD weeks **2–15** (14 weeks remaining after W1).
Headline stratum: same-book, probability-valued, **tail-fill-excluded**
(cap-bound only; S5 314 had 0 tail-fill; W1 step-5 both cap-bound at 0.015).

| Rule | W1 contribution | Weeks 2–15 (median / week) | Weeks 2–15 (mean / week) | **Projected 2026 total** |
|------|----------------:|---------------------------:|-------------------------:|-------------------------:|
| **(a) BASELINE-MATCHED** | 0 (W1 excluded) | 14 × 6 = 84 | 14 × 5.71 ≈ 80 | **≈ 80–84** |
| **(b) ALL-CANDIDATES** | 2 (Auburn, Duke) | 14 × 7 = 98 | 14 × 7.0 = 98 | **≈ 100** |

S5 reference: `n_accepted_public` median 6/week (314 over 55 weeks); frozen
§12-path `n_accepted_section12` median 7/week (385 over 55 weeks).

**n = 300 reachable in 2026?** **No.** Under either rule, projected settled
headline tickets at stopping week 15 are **well below 300** (≈ 80–84 under (a);
≈ 100 under (b)).

**Pre-committed outcome when n < 300 at stopping week:** per §6, the instrument
reads **"not yet confirmed"** — not a failed test and not a promotion of
backtest results.

### A1.5 — Multi-season clause (pre-committed; n < 300 in 2026)

Because **n = 300 is unreachable within the 2026 regular season** under either
eligibility rule, the confirmatory instrument **continues across seasons** under
the **same frozen config and accept-loop semantics** (§1–§2). The primary read
occurs at **n = 300** settled tail-fill-excluded headline tickets whenever that
count is first reached.

**Void on config change:** Any change to `configs/betting.yaml`, production gate
ordering, or accept-loop stake/bypass semantics in an intervening season
**voids the instrument and restarts at n = 0** (§5 void rule unchanged). A new
pre-registration is required after void.

**Multi-season eligibility:** The rule selected in A1.2 (`<SELECTED: a|b>`)
applies uniformly across all seasons in the instrument window.

### A1.6 — Amendment epistemic ledger

| Claim | Status |
|-------|--------|
| W1 step-4 mismatch measured (0/8 baseline-eligible) | MEASURED (S7-DET / w1-qb-refresh) |
| Eligibility rule selected | **NOT SET** — `<SELECTED: a|b>` unfilled |
| Per-ticket field write path exists | **NOT BUILT** — probe artifacts only |
| n = 300 reachable in 2026 | **NO** (projected ≈ 80–100) |
| This amendment triggers §5 void | **NO** — analysis plan only, not config |
