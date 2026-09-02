# W1-ACCEPT — exposure loop skip-and-continue probe

**Date:** 2026-09-02  
**Branch:** `social-s1-s2`  
**Task:** read-only. No Odds API, no writes to staged data, no config changes, no publish, no merge.  
**2025 lockbox:** untouched.

**Analysis `as_of`:** `2026-09-01T20:38:58Z` (post-crosswalk staged state)  
**Snapshot `event_time`:** `2026-09-01T20:34:56.940488Z`  
**Config:** `configs/betting.yaml` — `max_weekly_exposure=0.10`, `max_bets_per_week=10`, `max_exposure_per_team=0.05`, `max_stake_pct=0.015`, `kelly_fraction=0.25`

**Data source:** live re-run of `scripts/_s6_w1_card.run_analysis(as_of=2026-09-01T20:38:58Z)` → `betting_rows` (28 step-3 survivors). On-disk `docs/notes/_artifacts/social-s6-w1-card/analysis.json` still records step-3 count **21** (pre-crosswalk); the 28-row pool below matches current staged data at the same `as_of`.

---

## 1 — Accept loop verbatim; BREAK vs CONTINUE

### Production — `apply_bet_filters` (`src/ncaa_quant/pipelines/predict.py`)

```python
    ordered = sorted(candidates, key=lambda c: float(c.edge), reverse=True)
    accepted: list[BetCandidate] = []
    rejected: list[tuple[BetCandidate, tuple[FilterReason, ...]]] = []
    exposure = ExposureState()
    bets_this_week = 0

    for cand in ordered:
        if cand.p_win is not None and cand.american_odds is not None:
            # Stake *before* exposure clamps — exposure caps are applied by
            # evaluate_filters using the live ExposureState. Passing an
            # already-shrunk stake would make MAX_*_EXPOSURE unreachable.
            stake = recommended_stake(
                float(cand.p_win),
                float(cand.american_odds),
                bankroll=1.0,
                config=cfg,
                weekly_exposure_so_far=0.0,
                team_exposure_so_far=0.0,
            )
            proposed = float(stake.stake_fraction)
        else:
            proposed = 0.0

        result = evaluate_filters(
            cand,
            cfg,
            bets_this_week=bets_this_week,
            weekly_exposure_so_far=float(exposure.weekly_total),
            team_exposure_so_far=dict(exposure.per_team or {}),
            proposed_stake_fraction=proposed,
        )
        if result.accepted:
            accepted.append(cand)
            bets_this_week += 1
            if proposed > 0.0 and cand.team_ids:
                exposure = exposure.with_bet(cand.team_ids, proposed)
            elif proposed > 0.0:
                exposure = ExposureState(
                    weekly_total=float(exposure.weekly_total + proposed),
                    per_team=dict(exposure.per_team or {}),
                )
        else:
            rejected.append((cand, result.reasons))
    return accepted, rejected
```

**When a candidate's stake exceeds remaining weekly room:** **CONTINUES.** There is no `break`. Rejected candidates are appended to `rejected` and the `for cand in ordered` loop advances to the next edge rank.

### Probe — step-4 block (`scripts/_s6_w1_card.py`, `_ordered_gate_survivors`)

```python
            ordered = sorted(survivors, key=lambda r: float(r["edge"]), reverse=True)
            kept: list[dict[str, Any]] = []
            bets = 0
            weekly = 0.0
            team_exp: dict[str, float] = {}
            for row in ordered:
                cand = row["_candidate"]
                stake = float(row["stake_fraction"])
                fails = _exposure_fails(
                    cand,
                    betting,
                    stake,
                    bets_this_week=bets,
                    weekly_exposure=weekly,
                    team_exposure=team_exp,
                )
                if fails:
                    row = dict(row)
                    row["exposure_gate_fails"] = fails
                    continue
                kept.append(row)
                bets += 1
                weekly += stake
                for tid in cand.team_ids:
                    team_exp[tid] = team_exp.get(tid, 0.0) + stake
            survivors = kept
```

**When a candidate's stake exceeds remaining weekly room:** **CONTINUES** (`continue` on `if fails:`). No `break`.

**Production vs probe on exposure rejection:** **same behavior** — skip-and-continue. The probe isolates exposure filters (`_exposure_fails`); production runs all §12 filters in one `evaluate_filters` call (so QB/stale/etc. can reject before exposure is reached on the full publish path).

---

## 2 — Full 28-row step-3 pool (edge descending)

Step-3 survivors: passed snapshot/stale/kickoff/quarantine, edge/EV/σ, and model_market_disagree gates.

| rank | game_id | matchup | edge | before_cap | stake | acc | weekly_after | fail |
|-----:|---------|---------|-----:|-------------:|------:|:---:|-------------:|------|
| 1 | 401869129 | Northwestern State @ Louisiana Tech | 0.1483 | 0.064219 | 0.015000 | Y | 0.015000 | |
| 2 | 401856636 | Baylor @ Auburn | 0.1472 | 0.062115 | 0.015000 | Y | 0.030000 | |
| 3 | 401860879 | Portland State @ San Diego State | 0.1154 | 0.048085 | 0.015000 | Y | 0.045000 | |
| 4 | 401858209 | Tulane @ Duke | 0.1048 | 0.041925 | 0.015000 | Y | 0.060000 | |
| 5 | 401858434 | Marshall @ Penn State | 0.0992 | 0.038372 | 0.015000 | Y | 0.075000 | |
| 6 | 401866623 | North Carolina A&T @ Georgia State | 0.0978 | 0.037973 | 0.015000 | Y | 0.090000 | |
| 7 | 401862697 | Houston Christian @ Rice | 0.0952 | 0.037268 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 8 | 401864496 | Duquesne @ Air Force | 0.0921 | 0.036401 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 9 | 401864499 | Fordham @ North Dakota State | 0.0893 | 0.034634 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 10 | 401856780 | Coastal Carolina @ West Virginia | 0.0875 | 0.032460 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 11 | 401858435 | Indiana State @ Purdue | 0.0849 | 0.032072 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 12 | 401856775 | Utah Tech @ BYU | 0.0804 | 0.029710 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 13 | 401856666 | Furman @ Tennessee | 0.0797 | 0.029036 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 14 | 401867973 | Norfolk State @ Old Dominion | 0.0788 | 0.028870 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 15 | 401858204 | Akron @ Wake Forest | 0.0765 | 0.027396 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 16 | 401858212 | SMU @ Florida State | 0.0741 | 0.027371 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 17 | 401856661 | Louisville @ Ole Miss | 0.0734 | 0.025829 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 18 | 401866411 | Mississippi Valley State @ Sacramento State | 0.0645 | 0.021362 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 19 | 401858207 | Miami (OH) @ Pittsburgh | 0.0638 | 0.022124 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 20 | 401858427 | Hampton @ Maryland | 0.0579 | 0.016827 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 21 | 401864432 | Western Kentucky @ Nevada | 0.0566 | 0.017197 | 0.015000 | N | 0.090000 | max_weekly_exposure |
| 22 | 401856777 | Boston College @ Cincinnati | 0.0521 | 0.014853 | 0.014853 | N | 0.090000 | max_weekly_exposure |
| 23 | 401856658 | Tennessee State @ Georgia | 0.0487 | 0.013148 | 0.013148 | N | 0.090000 | max_weekly_exposure |
| 24 | 401860878 | Wyoming @ Colorado State | 0.0432 | 0.012061 | 0.012061 | N | 0.090000 | max_weekly_exposure |
| 25 | 401858422 | Eastern Illinois @ Minnesota | 0.0410 | 0.009084 | 0.009084 | Y | 0.099084 | |
| 26 | 401858426 | Northern Illinois @ Iowa | 0.0368 | 0.007992 | 0.007992 | N | 0.099084 | max_weekly_exposure |
| 27 | 401858208 | New Hampshire @ Syracuse | 0.0272 | 0.001721 | 0.001721 | N | 0.099084 | max_weekly_exposure |
| 28 | 401864498 | Central Michigan @ New Mexico | 0.0264 | 0.000814 | 0.000814 | Y | 0.099898 | |

- **before_cap** = `recommended_stake(...).fractional_kelly_before_cap` (quarter-Kelly before hard/config caps).
- **stake** = `betting_rows[].stake_fraction` (after hard 1.5% / config caps, **without** weekly-room shrink in the accept loop).
- **weekly_after** = running sum of accepted stakes only.

---

## 3 — Total exposure vs cap; room after each acceptance

| event | accepted game | stake | weekly committed | room remaining (`0.10 − committed`) |
|-------|---------------|------:|-----------------:|------------------------------------:|
| after rank 1 | Northwestern State @ LA Tech | 0.015000 | 0.015000 | 0.085000 |
| after rank 2 | Baylor @ Auburn | 0.015000 | 0.030000 | 0.070000 |
| after rank 3 | Portland State @ SDSU | 0.015000 | 0.045000 | 0.055000 |
| after rank 4 | Tulane @ Duke | 0.015000 | 0.060000 | 0.040000 |
| after rank 5 | Marshall @ Penn State | 0.015000 | 0.075000 | 0.025000 |
| after rank 6 | NC A&T @ Georgia State | 0.015000 | 0.090000 | **0.010000** |
| after rank 25 | Eastern Illinois @ Minnesota | 0.009084 | 0.099084 | 0.000916 |
| after rank 28 | Central Michigan @ New Mexico | 0.000814 | **0.099898** | 0.000102 |

- **`max_weekly_exposure`:** 0.10  
- **Total committed by 8 accepted games:** **0.099898** (under cap; not over)  
- **Accepted count:** 8 (ranks 1–6, 25, 28)

After rank 6 the loop is **fully capped** for any candidate whose full stake exceeds **0.010000** of bankroll. Ranks 7–24 all propose stakes ≥ 0.012061 and are skipped. The loop keeps scanning.

---

## 4 — Ranks 25 and 28: confirmed

**Confirmed.** Ranks 25 and 28 were accepted because their quarter-Kelly stakes fit residual weekly room that ranks 7–24 could not.

| rank | edge | stake | room before decision | fits? |
|-----:|-----:|------:|---------------------:|:-----:|
| 7 | 0.0952 | 0.015000 | 0.010000 | no — needs 0.015 |
| 24 | 0.0432 | 0.012061 | 0.010000 | no — needs 0.012061 |
| **25** | **0.0410** | **0.009084** | **0.010000** | **yes** |
| 26 | 0.0368 | 0.007992 | 0.000916 (after rank 25) | no |
| 27 | 0.0272 | 0.001721 | 0.000916 | no |
| **28** | **0.0264** | **0.000814** | **0.000916** | **yes** |

Ranks 7–24 are not “missing” from the accept set because of sort instability — they are **rejected on `max_weekly_exposure`** while the loop **continues** to lower-edge candidates with smaller stakes.

---

## 5 — Stake shrink in the accept loop

**The accept loop does not shrink stakes to fit remaining room.** It only accepts candidates whose **full computed stake** fits.

Evidence — production computes stake with exposure inputs zeroed:

```python
            stake = recommended_stake(
                ...
                weekly_exposure_so_far=0.0,
                team_exposure_so_far=0.0,
            )
            proposed = float(stake.stake_fraction)
```

Exposure is enforced only in `evaluate_filters`:

```python
    if weekly_exposure_so_far + proposed_stake_fraction > float(config.max_weekly_exposure) + 1e-15:
        reasons.append(FilterReason.MAX_WEEKLY_EXPOSURE)
```

`recommended_stake` *can* shrink when called with live exposure (`stake_frac = min(before_cap, config_cap, hard_cap, weekly_room, team_room)`; floor `max(0.0, stake_frac)`), but **that path is deliberately not used inside the accept loop** (comment at lines 598–600 in `predict.py`). Rejection + continue is the mechanism, not partial fills.

Probe step-4 uses the precomputed `row["stake_fraction"]` from the same no-exposure `recommended_stake` call at row-build time — same rule.

---

## 6 — Which constraints bound on this slate?

| constraint | value | rejections in step-4 simulation | binds? |
|------------|------:|--------------------------------:|:------:|
| `max_weekly_exposure` | 0.10 | **20** (ranks 7–24, 26–27) | **yes — only active exposure constraint** |
| `max_bets_per_week` | 10 | 0 | no (8 accepted < 10) |
| `max_exposure_per_team` | 0.05 | 0 | no |

Per-team exposure never fires: each accepted bet is ≤ 0.015 on any single team, and no team accumulates more than 0.05 across the eight picks.

---

## INTERPRETATION (report only — no fix proposed)

The exposure accept loop is **skip-and-continue** on edge-descending order with a **hard weekly ceiling** and **no stake resizing**. After the top six 1.5%-cap picks consume 0.09 of the 0.10 weekly budget, **0.010000** of room remains. Every rank 7–24 candidate needs a full stake ≥ 0.012061 and is rejected; the loop keeps going. Rank **25** (stake **0.009084**) and rank **28** (stake **0.000814**) fit the leftover slots.

Under this rule, **residual weekly room is allocated to weaker-edge candidates** once higher-edge picks exhaust the cap with oversized (cap-bound) stakes. That is a **property of the sizing + skip-and-continue rule**, not a one-off artifact of this slate.

**Known-defects entry for V3** — no remediation proposed in this task.

---

## STOP-AND-REPORT checklist

| condition | result |
|-----------|--------|
| Loop shrinks stakes to fit | **No** — full stake or reject; shrink exists in `recommended_stake` but is bypassed in the loop |
| Committed exposure exceeds `max_weekly_exposure` | **No** — final 0.099898 ≤ 0.10 |
| Production and probe step-4 differ in break/continue | **No** — both **continue** on exposure rejection; probe uses explicit `continue`, production falls through to next iteration |

---

## Step-4 survivor set (matches gate output)

`401869129, 401856636, 401860879, 401858209, 401858434, 401866623, 401858422, 401864498`

(ranks 1–6, 25, 28 — not contiguous edge ranks 1–8)
