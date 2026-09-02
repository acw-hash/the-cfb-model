# S5-W0 — real answer for the eight Aug 29–30 games

**Date:** 2026-08-25  
**Branch:** `social-s1-s2`  
**Export / R2 / push / publish:** left OFF  

**Authority:** DESIGN §12; S5 provider; Odds↔CFBD crosswalk.

---

## 1 — The eight games

Source: live publish-history record
`data/webapp/publish_history/2026_w1.jsonl` (as_of `2026-08-25T10:00:00Z`,
99 games). Local R2 `latest/week_predictions.json` is not on disk; this is the
same vintage the last live W1 publish wrote.

Kickoff before `2026-08-31` → **8 games**:

| game_id | away @ home | kickoff UTC | μ_margin | σ_margin | σ_cred | μ_total |
|--------:|-------------|-------------|---------:|---------:|:------:|--------:|
| 401856766 | North Carolina @ TCU | 2026-08-29T16:00:00Z | 17.620 | 19.822 | True | 56.005 |
| 401864494 | San José State @ USC | 2026-08-29T19:00:00Z | 19.854 | 20.257 | True | 56.843 |
| 401858202 | NC State @ Virginia | 2026-08-29T19:30:00Z | 2.981 | 23.641 | True | 52.098 |
| 401864577 | Jacksonville State @ North Dakota State | 2026-08-29T21:30:00Z | 14.513 | 17.807 | True | 53.830 |
| 401866408 | Sacramento State @ Eastern Michigan | 2026-08-29T22:30:00Z | 12.387 | 20.993 | True | 55.437 |
| 401858201 | Hawai'i @ Stanford | 2026-08-29T23:00:00Z | 11.151 | 21.297 | True | 55.370 |
| 401864570 | New Mexico State @ Florida State | 2026-08-29T23:00:00Z | 15.488 | 16.998 | True | 53.093 |
| 401862693 | Memphis @ UNLV | 2026-08-30T02:00:00Z | 10.896 | 20.078 | True | 57.352 |

Count confirmed: **8**.

---

## 2 — Odds chain for these eight

### a. Raw archives Aug 24–25

**Absent.** `data/raw/odds_api/` has day folders `2026-08-04` … `2026-08-12`
only (49 JSON files). Last file:
`2026-08-12/20260812T160010527345Z.json`. No capture on Aug 24 or 25.

### b. Staged `odds_snapshots` season 2026 (before repair)

| Metric | Value |
|--------|------:|
| Rows | 53 566 |
| `game_id` non-null | **0** |
| `event_time` min / max | 2025-12-02 … 2026-08-12 |
| Rows on the eight (by team/`game_key`) | present (~650–900/game) but all `game_id` null |

### c. `odds_cfbd_game_crosswalk` season 2026 (before repair)

Partition **exists**. 144 events, **0 matched**, all `match_status=unmatched`.
All eight early events present in the table (with two nickname-mangled names).

### d. Matcher on live ingest?

**Yes — live path invokes the matcher.** Not historical-only.

Call sites:

```1751:1760:src/ncaa_quant/ingestion/odds_api.py
            frame = _enrich_frame_via_crosswalk(
                store,
                body,
                names,
                captured_at=captured,
                ingested_at=ingested,
                snapshot_source="live",
                decision_point=None,
                event_time=captured,
            )
```

`_enrich_frame_via_crosswalk` → `match_odds_events_to_cfbd` →
`write_odds_cfbd_crosswalk` (same module ~1232–1272).

Prefect live flow:

```49:51:src/ncaa_quant/pipelines/odds.py
    def _run() -> dict[str, Any]:
        result: OddsIngestResult = run_odds_ingest()
        return {
```

Historical backfill / archive replay use the same enrich helper
(`_stage_historical_envelope` / `replay_historical_from_archives`).

### e. Null `game_id` — Odds vs CFBD names (the eight)

| Odds (stored / normalized) | CFBD schedule |
|----------------------------|---------------|
| North Carolina @ TCU | North Carolina @ TCU |
| San José State @ USC | San José State @ USC |
| NC State @ Virginia | NC State @ Virginia |
| Jacksonville State @ **North Dakota State Bison** | Jacksonville State @ North Dakota State |
| Sacramento State **Hornets** @ Eastern Michigan | Sacramento State @ Eastern Michigan |
| Hawai'i @ Stanford | Hawai'i @ Stanford |
| New Mexico State @ Florida State | New Mexico State @ Florida State |
| Memphis @ UNLV | Memphis @ UNLV |

Six of eight already string-equal. Rematching the stale crosswalk against the
**current** CFBD 2026 schedule (no YAML change) matched those six immediately.
The crosswalk was written while unmatched and never rebuilt after schedule
landed; live ingest also stopped on Aug 12 so nothing re-ran the matcher.

### Classification

**staged-not-matched** (primary), with two nickname gaps inside that bucket.

Not “no capture” for the eight (Aug 4–12 archives cover them).  
Not “matched-but-after-as_of” (rows are all `event_time <= as_of`).  
Aug 24–25 specifically: **no capture** (ingest gap), so the as-of ladder for
`2026-08-25T10:00Z` correctly uses **`odds_api_snapshot_fallback`** to the
latest earlier window (Aug 12).

---

## 3 — Fix (small)

### Mappings added to `configs/team_names.yaml`

```yaml
"North Dakota State Bison": "North Dakota State"
"Sacramento State Hornets": "Sacramento State"
```

(Only these two.)

### Replay

Zero-API wipe + restage of all 49 live archives through
`_enrich_frame_via_crosswalk` (`data/tmp/s5_w0_repair_2026_odds.py`).

| | Before | After |
|--|-------:|------:|
| Crosswalk events | 144 | 143 |
| Crosswalk matched | 0 | **109** |
| Eight matched | 0 | **8 / 8** |
| Odds rows with `game_id` | 0 | 50 220 / 58 432 |
| Odds rows on the eight | 0 (by id) | **6 546** |

---

## 4 — Candidate answer (the eight only)

Provider: `build_candidates_from_odds`, `candidates_enabled=true`,
`candidate_markets=[side]`, `no_bet_on_qb_unknown=false`, `n_draws=20_000`,
seed 42, as_of from publish history. Export untouched.

| Metric | Value |
|--------|------:|
| Ladder | 8× `odds_api_snapshot_fallback` |
| Constructed | 8 |
| `no_snapshot` | **0** |
| Accepted after §12 filters | **4** |
| Rejected | 4 — all `model_market_disagree` |

### Accepted card

| game_id | side | line | American | edge | EV |
|--------:|------|-----:|---------:|-----:|---:|
| 401858201 | Stanford | −5.5 | −108 | 0.113 | 0.172 |
| 401862693 | UNLV | −6.0 | −110 | 0.099 | 0.142 |
| 401866408 | Eastern Michigan | −8.5 | −105 | 0.086 | 0.122 |
| 401858202 | NC State | +5.5 | −106 | 0.052 | 0.056 |

### Rejected (constructed, disagreed with market)

San José State, North Dakota State, TCU, New Mexico State — each
`model_market_disagree` (large model–market residual vs `min_model_market_agreement`).

Artifact: `data/tmp/s5_w0_eight_result.json`.

**End state:** real candidate set for the eight (4 accepted / 4 refused on
§12), not `no_snapshot`. Market was compared. Fresher Tuesday-morning lines
are still missing because live capture stopped Aug 12 — that is an ops gap,
not a provider gap.

---

## Ambiguities / follow-ups

1. User prompt cut off at “If the cause is that live ingest never …”. Treated
   as: fix name map + rematch staged rows (done). Did **not** resume the
   Prefect `ingest_odds` cron or spend Odds API credits for an Aug 25 pull.
2. Fallback vs decision-point: all eight used Aug 12 snapshots at an Aug 25
   as_of. Restarting live capture would move the rung to
   `odds_api_snapshot` once a post-as_of-window (or nearer) pull exists.
3. 34 crosswalk events remain unmatched (mostly FCS nickname forms not in
   YAML / mascot strip). Out of scope for the eight; leave for a later map pass.
