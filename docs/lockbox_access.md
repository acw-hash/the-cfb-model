# Lockbox access log

Season **2025** is the lockbox (§7.2 item 9). It is excluded from all development,
HPO, ablation, and promotion evaluations. It may be read **at most once per
calendar year** for a confirmatory report only.

## Ingest hygiene vs evaluative metrics (amended 2026-08-09)

**Permitted** for the lockbox season without logging a confirmatory read:
partition existence, progress-marker / unit-complete counts, and ingest
quarantine row counts (book garbage sidecars). These are operational hygiene
for credit-spend and write integrity, not model or betting judgment.

**Prohibited** without an explicit confirmatory-read log row: coverage %,
`n_books_available` trajectories, CFBD↔Odds reconcile distributions, and any
model or betting output (predictions, edges, CLV, walk-forward metrics).

Task 5B post-backfill quarantine-by-season counts (including 2025 = 104 of 434)
are **PASS** under this amended rule. See `docs/notes/05b.md` § lockbox
resolution.

Every confirmatory *evaluative* read must append a row below. Enforcement is
code-level, not conventional: `src/ncaa_quant/evaluation/lockbox.py` raises
`LockboxViolation` from `WalkForwardConfig.validate_ablations`, so a run
touching 2025 fails before it spends compute unless
`lockbox_confirmatory_read=True` is set explicitly.

**2025 is not a virgin holdout.** The D7 diagnostic read it before the lockbox
designation existed. That read is logged below because the log has to reflect
what actually happened — the point of the register is to keep a truthful count of
how many times the season has been looked at, not to look clean. Any confirmatory
report on 2025 must state that its early weeks 1-4 were already used to refine
the week-interaction finding, so a confirmation there is weaker evidence than a
first read would have been.

| Date (UTC) | Reader | Purpose | Git SHA | Config hash | Summary |
|---|---|---|---|---|---|
| 2026-08-06 | D7 diagnostic (retro-logged 2026-08-07) | Pre-registered confirmatory holdout for the D7 early-week stack-weight finding; weeks 1-4 only. Predates the §7.2 item 9 lockbox designation, so it was not a violation at the time. | `b81cb536` (unresolvable — see ADR 0005) | canonical `ebb9ce08` | Early-week combination weight replicated on 2025 w1-4 (`b2 = 0.376`, the largest of any season). Did not amend the stop rule or authorize a betting layer on mu. |
