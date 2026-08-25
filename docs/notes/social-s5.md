# S5 — Bet candidate provider (`build_candidates_fn`)

**Date:** 2026-08-25  
**Status:** Complete (on `social-s1-s2`; not merged to `main`)  
**Authority:** DESIGN §12, §2.7; ADR 0002 / 0010 / 0017; Phase 0 + A1–A4 amendments.

---

## Phase 0 answers (accepted)

### 1. Odds snapshot schema

`OddsSnapshotsSchema`: `game_id`, `book`, `market` ∈ {spread,total,h2h}, `side`,
`line`, `price` (American), `event_time`, `snapshot_source`, `decision_point`,
`n_books_available`. Opposing two-way price is a **sibling row** keyed by
`(game_id, book, market, event_time)` with the other `side`.

### 2. As-of retrieval

ADR 0002 Part C ladder: decision-point snapshot → nearest earlier within
tolerance → null. Implemented in
`resolve_asof_snapshot_window` (inclusive `event_time <= as_of`, also
`event_time < kickoff`). **NOT FOUND** as a `ParquetStore` method — store only
`read()`s; PIT join is on the frame. Hive `week=` tags can lag CFBD week by ±1;
provider loads **season-wide** snapshots and filters by `game_id`.

### 3. Prediction fields / totals

2026 week 1 publish rows carry `mu_margin`, `sigma_margin`,
`sigma_margin_credible`, `null_reason`, `p_win_home` (export of `p_ml_home`),
**and** `mu_total` / `sigma_total` / `sigma_total_credible`. Totals exist.
Amendment A1: provider is generic over `side`/`total`;
`betting.candidate_markets` defaults to `["side"]`.

### 4. Cover probability

`betting.clv.spread_cover_prob` / `total_cover_prob` take a discrete PMF
(settlement). Amendment A3: S5 uses `distribution.simulate.spread_cover_probs`
/ `total_probs` (MC) at the **shopped** line — same path as production
`p_ats_home` / `p_ou_over`. Callable at an arbitrary line (stack only *happens*
to pass CFBD closes).

### 5. Home anchor

`filter_home_side_spreads`: CFBD designated home school name on `side`, not
Odds listing `home_team`.

### 6. Exposure filters

Pre-S5 `apply_bet_filters` called `evaluate_filters(cand, cfg)` with no
exposure kwargs → `MAX_BETS_PER_WEEK` / `MAX_WEEKLY_EXPOSURE` /
`MAX_TEAM_EXPOSURE` **could not fire**. Fixed in D4.

### 7. QB status

**FOUND:** staged `qb_status` table + `set_qb_status` CLI. Amendment A2: as-of
read; both teams need `event_time <= as_of` and `status != "unknown"`. Reuse
`no_bet_on_qb_unknown`. Stamp `qb_status_source` ∈
{`staged_asof`, `unchecked`, `check_disabled`}.

### 8. Config

`BettingConfig` / `SocialConfig` as quoted in Phase 0; plus S5 additions
`candidates_enabled: false`, `candidate_markets: [side]`.

---

## Built

| Module | Role |
|--------|------|
| `src/ncaa_quant/betting/provider.py` | `build_candidates_from_odds` |
| `src/ncaa_quant/betting/filters.py` | Construction `FilterReason`s + `block_reasons` / stake fields on `BetCandidate` |
| `src/ncaa_quant/pipelines/predict.py` | D4 exposure threading; D5 `candidates_enabled` wiring |
| `src/ncaa_quant/config.py` + `configs/betting.yaml` | `candidates_enabled`, `candidate_markets` |
| `tests/unit/test_social_s5_provider.py` | Hand fixture, as-of, QB, exposure caps, lockbox, no-network |
| `docs/notes/social-s5.md` | This note |

---

## Distributional assumption (cover)

**MC with key-number integer mass** via `sample_joint` +
`spread_cover_probs` / `total_probs`, then `two_way_side_prob` (push mass
removed). Default kernel is empty `KeyNumberKernel` (weight 1.0 → continuous
integer mass without fitted bumps). Do **not** use a Gaussian CDF shortcut
(smooths through 3 and 7). Do **not** use `betting.clv.spread_cover_prob`.

Justification: matches the production stack’s probability path; shopped line
is passed as the `spread` / `total_line` argument (verified callable at an
arbitrary line before wiring).

---

## Operator workflow (QB, two-pass)

1. Run with defaults (`no_bet_on_qb_unknown=true`). Missing staged rows report
   ``qb_status_known=false`` / ``qb_status_source=unchecked`` and reject with
   ``QB_STATUS_UNKNOWN``. Do **not** hardcode knownness.
2. Optionally set ``no_bet_on_qb_unknown=false`` only to *inspect* a shortlist;
   ``qb_status_source`` becomes ``check_disabled`` but ``qb_status_known`` still
   reflects staged truth (S5-W0-FIX).
3. For shortlisted games, ``ncaa-quant roster set-qb --game … --team … --status …``
   for **both** teams (`starter` / `backup`; never leave `unknown` if clearing
   the filter).
4. Re-run with the flag on. Missing or ``unknown`` rows → reject.

---

## Measurements

### As-of ladder — 2024 week 5 (`tuesday_0600_et`)

| Rung | Count |
|------|------:|
| `odds_api_snapshot` | 53 |
| `null` (no snapshot) | 3 |
| `odds_api_snapshot_fallback` | 0 |

`n_pred=56`. Season-wide snapshot load required (week-5 CFBD games live under
hive `week=4` for Odds rows).

### Candidates / rejections — 2024 week 5

With `no_bet_on_qb_unknown=false`, defaults otherwise, `n_draws=20_000`:

| Bucket | Count |
|--------|------:|
| Constructed | 53 |
| `no_snapshot` | 3 |
| Accepted after §12 | 7 |
| Rejected | 49 |

Reject reason counts: `max_weekly_exposure` 30, `model_market_disagree` 11,
`edge_too_small` 5, `non_positive_ev` 4, `no_snapshot` 3.

Constructed edge min / median / max: **0.009 / 0.086 / 0.432**.

Accepted edge min / median / max: **0.038 / 0.146 / 0.177**.

**Note on median ~8.6%:** Hand-checked Texas A&M @ Arkansas (`401628373`):
μ≈8.94, σ≈16.7, shopped home −3.5 at BetMGM +100/−120 → raw MC
`p_home≈0.629`, edge≈0.151. De-vig arithmetic matches; the median is
**model–market residual**, not a broken de-vig. `min_model_market_agreement=7`
removes the worst residuals before the public card.

### 2026 week 1 dry run

`EXPORT_ENABLED` left false. Provider against publish-history prediction rows
+ staged 2026 odds:

| Metric | Value |
|--------|------:|
| Games | 99 |
| Constructed | 0 |
| `no_snapshot` | 99 |
| Accepted | 0 |

**Cause (MEASURED):** all 53 566 staged 2026 `odds_snapshots` rows have
`game_id` null (crosswalk unresolved). Ladder correctly returns `null`.
No-Bet `thread_w1.md` written under `data/tmp/s5_2026_w1_dry/`.

Isolation (provider-only dry run): tier_state / publish_history / idempotency
ledger untouched (**identical** SHA-256 before/after).

---

## measured vs NOT MEASURED vs UNRESOLVED

| Item | Status |
|------|--------|
| Hand-computed −110/−110 → edge 0.05 / EV 0.05 through provider | **measured** |
| As-of never returns `event_time > as_of` | **measured** (unit + 2024w5) |
| Exposure caps fire (`max_bets_per_week`, `MAX_TEAM_EXPOSURE`) | **measured** |
| Lockbox 2025 still raises | **measured** |
| Provider makes no network calls | **measured** (urlopen monkeypatch) |
| `candidates_enabled=false` empty default | **measured** |
| 2024w5 candidate/edge/refusal counts | **measured** |
| 2026w1 all `no_snapshot` (null `game_id`) | **measured** |
| Forward profitability / CLV of accepted card | **NOT MEASURED** |
| σ_total calibration for enabling totals | **NOT MEASURED** (gated off) |
| Champion key-number kernel vs empty default kernel delta | **NOT MEASURED** |
| Live `execute_predict_publish` with `candidates_enabled=true` end-to-end social sidecar on 2026w1 | **UNRESOLVED** until Odds↔CFBD crosswalk fills `game_id` |
| Byte-identical webapp artifacts flag on vs off with real odds card | **NOT MEASURED** this session (export stayed off; empty accepted both ways covered in unit test) |

Nothing in this task is a claim about forward profitability.

---

## Acceptance checklist

1. Hand-computed fixture — `test_hand_computed_provider_fixture`
2. 2024 week 5 replay — counts above
3. 2026 week 1 dry run — No-Bet thread; 99× `no_snapshot`
4. Isolation — SHA unchanged on tier/history/idempotency for provider dry run
5. Flag-off empty card — unit test; live export left disabled
6. Zero credits / no network — unit test
7. Lockbox — unit test
8. `make lint typecheck test` — see session closeout
