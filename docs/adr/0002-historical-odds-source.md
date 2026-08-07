# ADR 0002: Historical odds source (The Odds API)

**Status:** Accepted (applied)
**Date:** 2026-08-06

This change set has been applied to `docs/DESIGN.md` and `docs/TASKS.md` as of 2026-08-06 (AUDIT-1). The body below is the original edit list preserved for audit trail; do not re-apply it.

---

# Change Set — Incorporating The Odds API Historical Endpoint

Apply these edits to `docs/DESIGN.md` and `TASKS.md` before running any further tasks.

**What changed:** we now have paid access to The Odds API historical odds endpoint
(snapshots back to 2020, 5-minute granularity, paid tier only). This closes the
bet-time-price gap that CFBD `/lines` cannot fill, but it introduces a source regime
change across the backtest window and a new way to accidentally invalidate CLV.

---

## Part A — Design document addenda

### A1. §3.2 — add a row to the market data table

| Source | Contents | Cost | Depth | Update | Value | Cx |
|---|---|---|---|---|---|---|
| **The Odds API — historical** | Point-in-time odds snapshots, all covered books, spreads/totals/h2h, wrapped in a snapshot envelope with `timestamp` / `previous_timestamp` / `next_timestamp` | Paid tier; historical requests billed at a higher credit rate than live — **verify the current multiplier on the plan page before sizing any pull** | 2020+ only; 5-min snapshot intervals; book coverage grows over time | Static (backfill) | **H — the only source of realistic bet-time prices for backtesting** | M |

### A2. §3.4 — add two availability warnings

- **Odds snapshot history begins 2020.** Season 2019 has CFBD open/close only. Since
  2020 is already excluded from headline metrics (§7.2 item 5), the effective
  snapshot-backed window is 2021–2025 — five seasons. Any market-movement feature
  fitted on this window has a small-sample problem and must be reported with that
  caveat. Market-aware backtests before 2021 are CFBD-only and must be labeled as a
  distinct regime, not pooled silently.
- **Bookmaker coverage is not constant over time.** The API added books progressively.
  A 2021 snapshot contains materially fewer books than a 2025 one. This directly
  biases two things: (a) "best available price across books" (§12) will appear to
  improve over time from coverage alone, and (b) cross-book dispersion (§4.5) is not
  comparable across seasons. Both must carry a `n_books_available` covariate and be
  reported per season, never pooled into a single headline number.

### A3. §2.7 — sharpen the closing-line definition

Replace the CLV label definition with:

> **Closing line** is defined as the last captured snapshot strictly before kickoff
> from the designated reference book set, sourced from The Odds API where available
> (2020+) and from CFBD's `close` field otherwise. The two definitions do **not**
> agree exactly. Where both exist, store both and record the divergence; systematic
> divergence beyond a documented tolerance is a data-quality finding, not something
> to average away. All reported CLV must state which close definition it used.

### A4. §7.2 — add item 8, mixed line-source regime

> **8. Line-source regime.** The walk-forward harness must record, per game, which
> source supplied the line at each decision timestamp. Metrics that depend on
> bet-time price (CLV, bet-layer ROI, edge distributions) are reported separately for
> snapshot-backed seasons and CFBD-only seasons, never pooled. A pooled number here
> would silently mix two different measurement instruments.

### A5. §4.5 — market features gain an availability contract

Market features follow the same null-with-indicator discipline as portal features
(§3.4): where no snapshot exists at the decision timestamp, the feature is null with
an `is_missing` indicator. **Never** substitute the CFBD open or close as a stand-in
for a missing intra-week snapshot — that is a forward-looking substitution and is
leakage.

---

## Part B — New task, paste-ready

Insert this between Task 5 and Task 6. It depends on Task 3 (storage/schemas) and
Task 4/5's shared team-name normalization. It is **not** urgent — by definition this
data is backfillable — so it must never be run in a way that competes with the live
capture for credits.

```
TASK 5B of 25: Historical odds backfill. Read @docs/DESIGN.md §3.2, §7.2, and the
historical-odds addenda. Depends on Tasks 3, 4, 5.

Implement historical snapshot ingestion in src/ncaa_quant/ingestion/odds_api.py
(extending the Task 4 module — this is a sanctioned edit to that file, nothing else).

CONTEXT: this is a paid, credit-metered backfill of unrepeatable spend. A bug that
pulls the wrong timestamps costs real money and cannot be undone by re-running. Treat
the cost estimator and the dry-run mode as first-class deliverables, not conveniences.

Deliverables:

1. Snapshot schedule config in configs/data.yaml, PRE-REGISTERED before any spend.
   The schedule must mirror the production decision times in §9.8 exactly:
     - Tuesday 06:00 ET (the primary as-of, matching §7.2 item 1)
     - Thursday 06:00 ET, Saturday 06:00 ET (the daily-refresh decision points)
     - kickoff minus 6h, kickoff minus 1h
     - last snapshot strictly before kickoff (the close)
   Each entry is a named decision point; the name is stored on every row. Adding or
   removing a decision point later is a config change that invalidates prior backtest
   comparability — document that in the module docstring.

2. Historical client method, distinct from the live one. The endpoint is
   /v4/historical/sports/americanfootball_ncaaf/odds with a `date` parameter, and the
   response is the live schema wrapped in an envelope carrying `timestamp`,
   `previous_timestamp`, `next_timestamp`.

   CRITICAL: event_time is the envelope's RETURNED `timestamp`, never the requested
   `date`. The API returns the closest snapshot at or before the requested time, so
   these differ by up to 5 minutes. Storing the requested date claims the information
   was knowable later than it was and will corrupt as-of joins. Write a test that
   asserts the stored event_time equals the returned timestamp and not the request
   parameter.

3. Cost estimator and dry-run mode FIRST, before any live call:
   `ncaa-quant ingest odds-historical --estimate --seasons 2021-2025` prints the exact
   request count broken down by season and decision point, the credit cost using the
   historical multiplier from config, and the remaining balance after. Refuse to
   proceed if the estimate exceeds a configured budget ceiling without --force.
   Verify the multiplier against the current plan page and put it in config — do not
   hardcode a guess.

4. Separate credit budget buckets for live and historical. The historical backfill
   must be structurally incapable of consuming the reserve the Task 4 live capture
   depends on. The live snapshot job is the unbackfillable one and takes absolute
   priority. Write a test proving the historical path trips its guard while leaving
   the live reserve intact.

5. Resumable backfill keyed by (season, week, decision_point), completed units
   skipped unless --force, progress and running credit spend logged per unit. A crash
   mid-backfill must never re-spend credits on units already stored.

6. Raw archival before parse, same discipline as Task 4:
   data/raw/odds_api_historical/{date}/{requested_ts}_{returned_ts}.json

7. Schema: extend odds_snapshots from Task 3 with `snapshot_source`
   {live, historical}, `decision_point` (the config name), and `n_books_available`.
   Backfill n_books_available for existing live rows too. Rows from the two sources
   are otherwise identical in shape and share one normalizer.

8. Reconciliation report: for every game where both a CFBD close and a historical
   last-pre-kickoff snapshot exist, compute the difference in spread and total.
   Report the distribution. Systematic bias beyond a documented tolerance is a
   finding to write up in the notes, NOT something to correct away with an offset.

Tests: returned-vs-requested timestamp discipline (above), budget bucket isolation,
resumability across a simulated crash, cost estimator arithmetic against hand-computed
fixtures, envelope parsing, dedupe against live rows covering the same moment.

Acceptance:
- `--estimate` for 2021-2025 runs and prints a credit figure BEFORE any spend — show
  me this number and wait for my go-ahead before running the real backfill
- After backfill: report snapshot coverage % per season per decision point, and the
  n_books_available trajectory by season (it will rise — quantify it)
- Report the CFBD-close vs snapshot-close reconciliation distribution
- make lint typecheck test pass

docs/notes/05b.md — including the coverage table, the book-count trajectory, the
reconciliation findings, and total credits spent.
```

---

## Part C — Edits to existing tasks

### Task 4 (odds ingester) — one addition
Add `snapshot_source='live'`, `decision_point=null`, and `n_books_available` to the
normalized output so live and historical rows share one schema. If Task 4 is already
built and running, this is a schema migration — do it as part of Task 5B, and do not
interrupt the live capture to do it.

### Task 7 (data quality) — add two validators
- **Snapshot monotonicity:** within a (game_key, book, market), snapshots ordered by
  event_time must not contain duplicate timestamps, and the last pre-kickoff snapshot
  must actually precede kickoff.
- **Source reconciliation:** flag (not fail) games where the CFBD close and the
  snapshot close differ beyond tolerance. Genuine late movement exists; systematic
  divergence is a bug.

### Task 16 (walk-forward harness) — this is the load-bearing edit
The harness's line lookup must resolve to the snapshot at the configured decision
point via as-of join, with an explicit fallback ladder that is **logged per game**:
snapshot at decision point → nearest earlier snapshot within a configured tolerance →
null with indicator. The CFBD open/close must never enter this ladder for
snapshot-backed seasons. Add to the information-set audit: assert no line used at a
Tuesday decision point has an event_time later than that Tuesday timestamp.

Also record `line_source` and `n_books_available` on every prediction row so Task 21
can slice by them.

### Task 20 (betting layer) — best-price caveat
`edges.py` computes edge against the best captured price across books. Add
`n_books_available` to the output and require Task 21 to report edge and ROI
stratified by it. Otherwise the line-shopping alpha in §16 item 5 will be
overestimated in later seasons purely from coverage growth.

### Task 21 (metrics) — one new slice
Add `line_source` and book-count bucket to the §7.2 item 3 slice analysis. Diagnostic,
not a model-selection input.

### Task 23 (backtest and ablations) — add ablation A6
```
- A6: market features from CFBD open/close only, vs full snapshot history.
  This is the direct measurement of whether the historical odds purchase paid for
  itself. Run it on 2021-2025 only, where both are available. Report the delta in
  CRPS, log-loss, and mean CLV with CIs. If A6 shows no material difference, say so
  plainly — that is a legitimate and useful result, and it tells you whether to keep
  renewing the subscription.
```
Also: split all headline bet-layer metrics by line-source regime per §7.2 item 8, and
state explicitly that 2019 market-aware results are not comparable to 2021+.

---

## Part D — Sequencing

Run 5B after Task 5, before Task 6. Nothing downstream of Task 16 can be trusted
without it, but nothing before Task 16 needs it, so there is no rush — and the
`--estimate` gate means you see the bill before you commit to it.

Do not let this task touch the live capture schedule. That job stays running
untouched throughout.
