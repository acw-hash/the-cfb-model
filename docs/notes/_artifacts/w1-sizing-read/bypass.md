# W1-SIZING-READ — why is stake resizing bypassed?

**Date:** 2026-09-02  
**Branch:** `social-s1-s2`  
**Task:** read-only. No writes to staged data, no config changes, no Odds API, no publish, no merge.  
**2025 lockbox:** untouched. **No fix proposed.**

Related: `docs/notes/_artifacts/w1-accept-loop/probe.md` (skip-and-continue accept behavior on 2026 W1 slate).

---

## 1 — `apply_bet_filters` verbatim (`predict.py` lines 580–620)

```python
    betting_config: BettingConfig | None = None,
) -> tuple[list[BetCandidate], list[tuple[BetCandidate, tuple[FilterReason, ...]]]]:
    """Run §12 filters in edge-descending order with threaded exposure state.

    Stake is computed via :func:`~ncaa_quant.betting.kelly.recommended_stake`
    before each filter call so weekly / team exposure caps can fire.
    """
    from ncaa_quant.betting.kelly import ExposureState, recommended_stake

    cfg = betting_config or load_config().betting
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
```

Source: `src/ncaa_quant/pipelines/predict.py` (lines 580–623 in current tree; loop continues through line 633).

---

## 2 — `recommended_stake` in full; live-exposure behavior

```python
def recommended_stake(
    p_win: float,
    american_odds: float,
    bankroll: float,
    config: BettingConfig,
    *,
    weekly_exposure_so_far: float = 0.0,
    team_exposure_so_far: float = 0.0,
) -> StakeResult:
    """Fractional Kelly stake with hard + config + exposure caps.

    Parameters
    ----------
    p_win:
        Calibrated win probability for the bet side.
    american_odds:
        Actual available American price.
    bankroll:
        Current bankroll (currency units); stake_amount = fraction * bankroll.
    config:
        Betting thresholds from ``configs/betting.yaml``.
    weekly_exposure_so_far:
        Sum of stake fractions already committed this week (0–1 of bankroll).
    team_exposure_so_far:
        Sum of stake fractions already on this team this week.
    """
    if bankroll <= 0.0:
        raise KellyError(f"bankroll must be positive, got {bankroll}")
    if weekly_exposure_so_far < 0.0 or team_exposure_so_far < 0.0:
        raise KellyError("exposures so far must be non-negative")

    f_full = full_kelly(p_win, american_odds)
    frac = _effective_kelly_fraction(config)
    before_cap = frac * f_full

    # Config stake ceiling is itself clamped to the hard 1.5% — config alone
    # can never authorize a larger stake than HARD_MAX_STAKE_PCT.
    hard_cap = HARD_MAX_STAKE_PCT
    config_cap = _effective_max_stake_pct(config)
    weekly_room = max(0.0, float(config.max_weekly_exposure) - weekly_exposure_so_far)
    team_room = max(0.0, float(config.max_exposure_per_team) - team_exposure_so_far)

    stake_frac = min(before_cap, config_cap, hard_cap, weekly_room, team_room)
    stake_frac = max(0.0, float(stake_frac))

    # Flag which constraints bind (a stake can hit several at once).
    capped_by_config = before_cap > config_cap + 1e-15 and abs(stake_frac - config_cap) < 1e-12
    capped_by_hard = before_cap > hard_cap + 1e-15 and stake_frac <= hard_cap + 1e-15
    capped_by_weekly = before_cap > weekly_room + 1e-15 and abs(stake_frac - weekly_room) < 1e-12
    capped_by_team = before_cap > team_room + 1e-15 and abs(stake_frac - team_room) < 1e-12

    return StakeResult(
        full_kelly_fraction=float(f_full),
        fractional_kelly_before_cap=float(before_cap),
        stake_fraction=stake_frac,
        stake_amount=float(stake_frac * bankroll),
        capped_by_hard_max=capped_by_hard,
        capped_by_config=capped_by_config,
        capped_by_weekly=capped_by_weekly,
        capped_by_team=capped_by_team,
    )
```

Source: `src/ncaa_quant/betting/kelly.py` lines 78–138.

### When remaining room is less than the computed stake (live-exposure path)

**Shrinks to fit** — `stake_frac = min(before_cap, config_cap, hard_cap, weekly_room, team_room)`. If `weekly_room` (or `team_room`) is the binding minimum, the returned stake equals that room, not the full Kelly stake.

If room is zero (exposure already at cap), `min(...)` yields **0.0** — the function returns a **zero stake**, not an error and not a reject at this layer.

### Minimum stake floor

**No minimum floor beyond zero.** Final line is `stake_frac = max(0.0, float(stake_frac))`. There is no unit minimum or “round up to 0.5u” rule; arbitrarily small positive stakes are valid return values (e.g. rank 28 on W1: **0.000814**).

Unit test coverage: `test_weekly_and_team_exposure_limits` exercises **team-room shrink** (`team_exposure_so_far=0.005` → stake **0.005**). No dedicated unit test passes non-zero `weekly_exposure_so_far` into `recommended_stake`; weekly shrink follows the same `min(..., weekly_room)` rule by construction.

---

## 3 — Origin of the bypass

### Git blame (bypass comment and `weekly_exposure_so_far=0.0` hardcode)

| Lines | Commit | Date | Author |
|------:|--------|------|--------|
| 598–607 | `aee6178a4f2300a4246f4108041420ea49b51af7` | 2026-08-25 | acw-hash |
| 582–586, 590–620, 623–629 | same | same | same |

**Commit message:**

> S5-W0-FIX/CARD: snapshot-age stale + QB truth; preview No-Bet card.
>
> Wire odds_max_age_hours and stop hardcoding qb_status_known when the QB check is disabled, then preview Week 1 on the 16:59Z snap (no QB rows staged → No-Bet thread).

The bypass comment and exposure-threaded accept loop landed in this commit as part of the `apply_bet_filters` rewrite (replacing a loop that called `evaluate_filters(cand, cfg)` with **no** exposure kwargs — see pre-change `56515326` / 2026-08-13).

`recommended_stake`'s live-exposure `min(..., weekly_room, team_room)` logic dates to initial commit `0d70347` (2026-08-06); only the **accept-loop call site** hardcodes `0.0`.

### ADR / DESIGN / S-series references

| Source | Reference | Content |
|--------|-----------|---------|
| `docs/DESIGN.md` §12 (lines 567–569) | Staking | Fractional Kelly 25%, 1.5% per-bet cap, weekly aggregate exposure limit — **no** accept-loop resize/reject/partial-fill semantics |
| `docs/notes/20.md` | Task 20 | Hard caps, exposure limits in `kelly.py`; bowl/exposure YAML values still **PLACEHOLDER** |
| `docs/notes/social-s4.md` (lines 292–301) | S4 Phase 0 exposure call path | **OBSERVED:** `recommended_stake` “deliberately pre-clamp with exposure 0.0”; caps fire in `evaluate_filters`. Weekly exposure **did** fire (1349 rejects). |
| `docs/notes/_artifacts/social-s4/phase0_summary.json` | S4 artifact | Same observed wording as social-s4.md |
| `docs/notes/_artifacts/social-s4/p0_5_selection.json` | S4 artifact | Same |
| ADRs (`docs/adr/*.md`) | — | **NOT FOUND** — no ADR mentions stake resizing, partial fills, or `weekly_exposure_so_far` bypass |
| `docs/notes/social-s5-w0-fix.md` | S5-W0-FIX | Documents stale/QB defects; **does not** name the exposure bypass |
| `docs/notes/_artifacts/w1-accept-loop/probe.md` | W1-ACCEPT | Documents skip-and-continue consequence; cites bypass comment |

**Rationale in version control:** only the inline comment at `predict.py:598–600`. No ADR or task memo records the decision separately — **partial: commit `aee6178a` + S4 “observed” call-path notes; no dedicated design rationale document.**

### Bug the bypass fixes

**Name (from the bypass comment):** passing an **already-shrunk stake** would make **`MAX_*_EXPOSURE` unreachable**.

Mechanism: if `recommended_stake` is called with live `weekly_exposure_so_far` / `team_exposure_so_far`, it shrinks `proposed` to remaining room. Then `evaluate_filters` checks `weekly_exposure_so_far + proposed_stake_fraction > max_weekly_exposure` — which is **always false** for a stake already clamped to room. The `FilterReason.MAX_WEEKLY_EXPOSURE` and `FilterReason.MAX_TEAM_EXPOSURE` enum values become **dead filters** (same “silent filter” class S4 flagged for `MAX_TEAM_EXPOSURE` before team_ids were populated).

The bypass separates **sizing** (full pre-exposure Kelly stake) from **gating** (live exposure state in `evaluate_filters`), so exposure caps can reject with explicit reason codes instead of silently auto-sizing.

---

## STOP — bypass comment cites correctness, not convenience

> ```python
>             # Stake *before* exposure clamps — exposure caps are applied by
>             # evaluate_filters using the live ExposureState. Passing an
>             # already-shrunk stake would make MAX_*_EXPOSURE unreachable.
> ```

This is a **filter-reachability / correctness** constraint: exposure caps must remain observable as reject reasons. It is not documented as a performance or implementation convenience.

**Do not change the accept loop in this task.**

---

## 4 — Reproducibility contract

**Yes.** Passing live exposure into `recommended_stake` makes a candidate's stake depend on edge-sort iteration order and on which other games have already consumed weekly/team room in that run. The same game re-run in a different slate context (different co-finalists in the step-3 pool, or different accept order) would receive a different shrunk stake or zero. That couples per-game stake to slate composition in ways the bypass path avoids for the **proposed** stake (which is a function of `p_win`, price, and static config caps only).

Note: §1.4 reproducibility in `DESIGN.md` anchors **inference and walk-forward replay given fixed model artifacts**; it does not explicitly define cross-slate stake invariance, but the bypass keeps `proposed_stake_fraction` independent of threaded exposure state.

---

## 5 — Which path produced settled / logged tickets?

| Path | Call sites | Used for tickets? |
|------|------------|-------------------|
| **Bypass** (`weekly_exposure_so_far=0.0`, `team_exposure_so_far=0.0` in accept loop) | `apply_bet_filters` (`predict.py:606–607`); `_s5_p0_4_selection._qualifies_ignoring_exposure`; `_s6_w1_card.py` row build (`recommended_stake` with defaults); `calibrate_social_threshold.py` post-accept stake recompute (defaults) | **Yes** — all **314** historical tickets (`docs/notes/_artifacts/social-s5/p0_4_selection.json`, `scripts/calibrate_social_threshold.py:140 → apply_bet_filters`) |
| **Live-exposure resize** (non-zero `weekly_exposure_so_far` or `team_exposure_so_far` passed into `recommended_stake`) | `tests/unit/test_betting.py::test_weekly_and_team_exposure_limits` only (`team_exposure_so_far=0.005`) | **No** — no production, probe, or logged ticket path passes live weekly/team exposure into `recommended_stake` |

**The two paths have not both been exercised on real tickets.** Bypass only in production/backtest; live-exposure shrink exists in `recommended_stake` and is unit-tested for team room only, never wired into the accept loop or ticket logger.

Exposure enforcement on tickets is entirely via **`evaluate_filters`** + skip-and-continue, not via stake resizing.

---

## 6 — DESIGN / ADR stake-sizing semantics

| Topic | Specified? | Where |
|-------|------------|-------|
| Quarter-Kelly fraction | **Yes** | DESIGN §12; `HARD_MAX_KELLY_FRACTION = 0.25` |
| Per-bet cap 1.5% | **Yes** | DESIGN §12; `HARD_MAX_STAKE_PCT = 0.015` |
| Weekly aggregate exposure limit | **Named, threshold PLACEHOLDER** | DESIGN §12 (“weekly aggregate exposure limit”); `configs/betting.yaml` `max_weekly_exposure: 0.10` marked PLACEHOLDER in Task 20 |
| Per-team exposure | **Named, PLACEHOLDER** | DESIGN §12; yaml `max_exposure_per_team: 0.05` |
| Accept-loop order (edge-descending) | **Implied, not normative** | Implemented in `apply_bet_filters`; S4/S5 measured as observed behavior |
| Shrink-to-fit vs reject vs partial fill | **NOT FOUND** | Not in DESIGN §12, any ADR, or yaml |
| Skip-and-continue on exposure reject | **NOT FOUND** | Observed behavior (W1-ACCEPT probe); V3 known defect |

**Summary:** DESIGN specifies Kelly fraction and caps at a high level; **accept-loop exposure semantics (bypass, no resize, skip-and-continue) are unspecified** — same epistemic status as PLACEHOLDER numeric thresholds, but with no yaml field at all for the behavioral choice.

---

## Additional STOP conditions (confirmed)

| Condition | Finding |
|-----------|---------|
| Live path has no minimum floor | **Confirmed** — floor is `max(0.0, stake_frac)` only; stakes can be arbitrarily small |
| Git history shows bypass added to fix a specific bug | **Confirmed** — commit `aee6178a` (2026-08-25); bug: **pre-shrunk stake makes `MAX_*_EXPOSURE` unreachable** (dead exposure filters) |
