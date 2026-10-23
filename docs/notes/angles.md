# Situational-angle research harness

Research tooling only (DESIGN §13 item 1 / §16 item 1). No production
feature, model, or publish path was changed. `ncaa_quant.research` is
imported by the CLI verb lazily; production modules do not import it.

## Built

- `configs/angles.yaml` — pre-registered H1 / H3 / H14 then, in a second
  commit before any batch-2 outcome, H7 / H9 / H15 / H22 / H23 / H24.
  Variants named and excluded from the lock hash.
- `src/ncaa_quant/research/angles.py` — FBS-vs-FBS regular-season team-game
  frame 2014–2026, conditions as-of Tuesday 06:00 ET (or kickoff if earlier),
  rest/bye/rivalry reused from `build_situational_frame`, both-qualify games
  dropped and counted, outcomes attached in a second step.
- Analysis (`angles_analysis.py` / `angles_stats.py`): season-week cluster
  SE, block-bootstrap cover interval, `ats_residual ~ qualifies + spread +
  venue + conference_game + week bucket`, spread buckets, per-season sign
  table, H3 trend, H14 continuous luck regression, Tuesday→close line
  movement. CFBD-close and Odds-API-close never pooled (§7.2 item 8 /
  ADR 0002).
- BH at q=0.10 is **cumulative** across every primary ever run on discovery
  (`docs/notes/angles/discovery_primaries.json`).
- Holdout ledger (`angles_ledger.py`): one open per hypothesis; second open
  or definition-hash change refuses without `--force`.
- CLI: `ncaa-quant research angles --ids H1,H3,H14 --split discovery|holdout`.
  Step 1 prints qualifying counts per season before this-game residuals
  are attached (close spreads used as *conditions* may be present).
- Reports: `docs/notes/angles/{H1,H3,H7,H9,H14,H15,H22,H23,H24,summary}.md`.
- Research AP rankings ingest (`angles_rankings.py`) writes
  `data/research/rankings` (not production `ENDPOINT_SPECS`). Poll
  `event_time` is the release instant; a poll is never used at or before
  that instant.

## Discovery (this task)

```text
ncaa-quant research angles --ids H1,H3,H14 --split discovery
```

Holdout was **not** opened. `docs/notes/angles/holdout_ledger.jsonl` does
not exist.

| id | n (CFBD) | estimate | p | p_adj | BH reject | vs registered |
|---|---:|---:|---:|---:|:---:|:---:|
| H1 | 321 | +0.836 pts mean ATS residual | 0.2514 | 0.2854 | False | sign ok |
| H3 | 889 | +0.297 pts/week trend slope | 0.2854 | 0.2854 | False | sign ok |
| H14 | 808 | −0.937 pts mean ATS residual | 0.1258 | 0.2854 | False | sign disagrees |

2020 is a sensitivity row on each report, excluded from headline cells
(§7.2 item 5). Odds-API close and Tuesday snapshots are 2021–2022 only.
Ridge residuals exist for 2019–2022 (walk-forward artifact
`task23_fundamental_reduced_v4`); 2014–2018 are warmup and stay null.

## Decisions / spec gaps

1. **Rest unit.** Kickoff-to-kickoff seconds/86400, same as
   `features.builders.situational` (`BYE_DAYS = 13`). FCS opponents count as
   a week played for rest/streaks; they are not in the qualifying universe.
2. **Both-qualify drop.** A game where both sides meet the definition is
   dropped (both rows) and counted. Exploratory `H1v_both_rested` had n=0
   (172 games dropped) — both teams coming off ≥13 days rest both qualify,
   so the variant was empty **by construction**. Removed in batch 2 (does
   not re-lock the H1 primary hash; variants are excluded from it).
3. **H3 nested baseline.** Primary is a superset of the ≤3-week reference
   group. `flag_definition` keeps overlap rows as `qualifies` only, so the
   baseline column is empty. The pre-registered statistic is the linear
   trend; the ≤3 / 4 / 5 / 6 / 7+ residual table is in the trend buckets.
   Config was not rewritten after outcomes were computed.
4. **Luck (H14).** Last-game luck is a *condition* (knowable once that game
   has ended before this game's Tuesday as-of), not this game's outcome.
   Trailing quantile uses an expanding pool of both-side luck values with
   `game_end < as_of` and `luck_quantile_min_n = 500`. Expected EPA margin
   uses Stage-1 postgame posteriors from `filter_history` plus the constant
   `hfa_epa_per_play: 0.025` (the history table does not persist HFA).
5. **Line sources.** CFBD `line_type=close` median across books; Odds close
   is `decision_point=slot_close`; Tuesday is `tuesday_0600_et`. Null
   snapshots stay null — CFBD is never substituted into the Odds instrument.
   Snapshot `game_id` nulls are dropped (staged rows exist).
6. **Coverage omit.** `angles_run.py` / `angles_report.py` are notes-runner
   I/O, omitted from the coverage gate the same way as `d3_eval` / `d4_eval`.
   Logic is covered via `tests/unit/test_angles.py` (PIT, planted +3 vs null,
   sign convention, both-qualify, ledger refuse, H3 trend / H14 regression).
7. **`adj_*_epa_l3d`.** Joined when the feature store is present; this
   workstation has no `data/features/adj_*_epa_l3d` partitions. Conditions
   for H1/H3/H14 do not read those columns.

## Batch 2

```text
ncaa-quant research angles --ids H7,H9,H15,H22,H23,H24 --split discovery
```

Yaml commit `6663a80` landed **before** these outcomes. Holdout was **not**
opened.

**STEP 0 H14 sign audit: passed.** Luck is ATS residual minus EPA surprise;
a 30-point win as a 7-point favorite with EPA = expectation is luck = +23.
All 872 H14-qualifying last-game luck values are positive. Five annotated
games are in `docs/notes/angles/H14.md`. H14 was not redefined and was not
re-run as a bug fix.

**STEP 1.** `H1v_both_rested` removed (n=0 by construction; see decision 2).

Cumulative BH (q=0.10, m=9 primaries). H1/H3/H14 **verdicts unchanged**
(still fail; p_adj rose because the family grew). H22 is the only BH
reject, but its estimate is **negative** (wrong sign vs the registered
bounce-back). Not a confirmed angle in the registered direction.

| id | n (CFBD) | estimate | p | p_adj | BH reject | vs registered |
|---|---:|---:|---:|:---:|:---:|:---:|
| H1 | 321 | +0.836 | 0.2514 | 0.5137 | False | sign ok |
| H3 | 889 | +0.297 /week | 0.2854 | 0.5137 | False | sign ok |
| H14 | 808 | −0.937 | 0.1258 | 0.4622 | False | sign disagrees |
| H7 | 98 | −0.982 | 0.4683 | 0.6378 | False | sign disagrees |
| H9 | 312 | +0.195 | 0.8126 | 0.8209 | False | sign ok |
| H15 | 630 | −0.135 | 0.8209 | 0.8209 | False | sign disagrees |
| H22 | 1939 | −1.072 | 0.0023 | 0.0211 | True | sign disagrees |
| H23 | 337 | +0.616 | 0.4961 | 0.6378 | False | sign ok |
| H24 | 3511 | +0.352 | 0.1541 | 0.4622 | False | sign ok |

H15 play_types counted as turnovers: `Pass Interception Return`,
`Interception Return Touchdown`, `Fumble Recovery (Opponent)`,
`Fumble Return Touchdown`. `Fumble Recovery (Own)` is not a turnover.

Ridge team rating for H7/H9 is filter_history net EPA (`off_epa − def_epa`)
with `event_time < as_of` (weekly rows that post-date Tuesday are not used).
AP polls live in research storage; `/rankings?seasonType=preseason` 400s
on CFBD so only `seasonType=regular` is fetched (week-1 ballots are in that
payload).

## Batch 3

Yaml commits `24c5320` / `2e4295b` landed **before** H25 outcomes.
Holdout was **not** opened. `docs/notes/angles/holdout_ledger.jsonl`
does not exist. H25 is **out of the cumulative BH family**.

### STEP 1 — H22 discovery sanity (appended to `H22.md`)

All-home CFBD close (2014–2022 excl. 2020): n=5662 mean ATS residual
**−0.473** (SE 0.184, p=0.0114). Homes are slightly worse than the close
in this window.

H22 baseline (home after a road win): n=1492 mean **+0.439**.

H22 raw mean **−1.072** (n=1939, p=0.0023) next to `qualifies` regressions
(CFBD close):

- vs H22 baseline (spread + venue + conference + week): coef=**−1.500**
  p=0.0133
- vs **all non-neutral home teams** (spread + conference + week;
  `venue_home` dropped as a constant): coef=**−0.860** p=0.0883

The road-loss effect does **not** survive the all-home control at 0.05.
Every discovery season is negative. Odds-API 2021–2022: H22 mean −0.815;
all-home mean −0.464. Mean Ridge residual on H22 qualifiers: −3.442.

### STEP 3 — H25 descriptive discovery (not evidence)

Registered slope of home ATS residual on (home last ATS − away last ATS)
per 10 points, CFBD close: **+0.101** p=0.2747 (n=5285; with controls
+0.095 p=0.2930). Sign matches the registration; it is not a test.
Binary `H25v_top_decile` n=475 mean −0.053 p=0.9366.

### STEP 4 — Holdout plan (NOT executed)

H22r and H25 each get **one** holdout opening on 2023–2025. Pass requires
all three:

1. Estimate has the registered sign.
2. One-sided p < 0.025 (Bonferroni across the two openings; two-sided
   p/2 when the sign matches, else 1 − p/2).
3. Registered sign in at least 2 of 3 holdout seasons.

2025 is the lockbox: including it requires `--confirm-lockbox-read` and a
`docs/lockbox_access.md` row. Expected qualifying n on **2023–2024 only**
(conditions, no this-game residuals, ledger not opened):

| id | expected n (2023–2024) |
|---|---:|
| H22r | 500 |
| H25 | 1364 |
| H25v_top_decile (exploratory) | 90 |

H22r's direction was chosen after seeing H22 discovery, so it is
confirmatory on holdout only. H25 was motivated by the H7/H14/H22
discovery pattern; its discovery slope is description, not evidence.

## Batch 4 — retire unopened; Ridge home-team diagnosis

**H22r and H25 status: `retired_unopened`.** Holdout was never opened;
the ledger is still empty.

- **H22r.** The H22 vs-all-home `qualifies` coefficient is −0.86
  (p=0.088). That is mostly the general home-team ATS residual (−0.473
  all-home), so a holdout pass could not separate “fade road-loss home
  teams” from “fade home teams.” Retired without opening.
- **H25.** Descriptive discovery slope p=0.27; binary top-decile mean
  ≈ 0 (n=475, −0.053, p=0.94). Failed descriptively; retired without
  opening.

Ridge home-team diagnosis (discovery 2014–2022 only; 2020 sensitivity;
**2023–2025 not touched**): `docs/notes/angles/ridge_home_diag.md`.

## Acceptance

- `tests/unit/test_angles.py` green (41): PIT, planted +3 vs null, sign
  convention, both-qualify drop, ledger refuse, H3 trend / H14 regression,
  H14 luck +23, each new hypothesis angle/baseline assignment, AP poll
  point-in-time, turnover play_types, last-game / Ridge / AP context attach,
  H22r / H25 assignment, H25 excluded from BH, H22 sanity fixture.
- Discovery reports written for H7/H9/H15/H22/H23/H24 plus H25
  (descriptive); H22 sanity appended; holdout not opened
  (`docs/notes/angles/holdout_ledger.jsonl` does not exist).
- Research modules ruff + mypy clean. Full-repo `make lint` / `make typecheck`
  still fail on pre-existing `social/` and `pipelines/cadence.py` (out of
  scope; not touched). Full-repo `make test` still fails on pre-existing
  HFA4 / betting-language-guard / slot-close-capture / webapp-w9r tests
  that this task did not touch.
