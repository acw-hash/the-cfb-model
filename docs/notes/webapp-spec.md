# W0 — WEBAPP-SPEC task notes

**Date:** 2026-08-13  
**Status:** Complete — documentation only; no application code.  
**Deliverables:** `docs/webapp/DESIGN.md`, `docs/webapp/TASKS.md`, this file.

---

## What was built

1. **Published artifact contract (DESIGN §1)** — frozen JSON schemas for `week_predictions.json`, `results_<season>.json`, `track_record.json`, `meta.json`, `team_ratings_<season>.json`; versioning rules; missing-value policy. Field names bridge Task 24 pipeline stubs (`mu_margin`, `sigma_margin`) and production walkforward columns (`pred_margin`, `sigma_m`).

2. **Conviction tier definition (DESIGN §2)** — reproducible from artifact fields: `p_favored` from `p_win_home` + `mu_margin` sign; enter/exit hysteresis bands; suppression when σ not credible or STALE age > 6 h; five ILLUSTRATIVE worked examples.

3. **Architecture (DESIGN §3)** — predict_publish → export → R2 → Next.js ISR; two staleness kinds; security boundary; $0/mo cost table with $20 ceiling turn-off order; zero CFBD/Odds credit confirmation.

4. **Design language (DESIGN §4)** — precedes page specs; Apple Sports benchmark; exact palette/type scale; component patterns; verbatim anti-pattern list.

5. **Page specs (DESIGN §5)** — This Week, Game Detail, Results/Track Record, About; every field mapped to artifact source; `grade_export` named as backend deliverable in TASKS.md W1.

6. **Disclaimers + legal flags (DESIGN §6)** — draft copy; L1–L6 table flagged not resolved.

7. **Build breakdown (TASKS.md)** — W1–W7 session tasks with dependencies and visual-review acceptance on UI tasks.

---

## Evidence — shapes from `pipelines/predict.py`

### `RefreshKind` (§9.8 schedule variants)

```python
class RefreshKind(StrEnum):
    TUESDAY_PRIMARY = "tuesday_primary"
    DAILY_REFRESH = "daily_refresh"
    T_MINUS_6H = "t_minus_6h"
    T_MINUS_1H = "t_minus_1h"
```

### `StaleSource` / `StaleContext`

```python
@dataclass(frozen=True, slots=True)
class StaleSource:
    source: str
    age_hours: float
    last_good_at: datetime

    def stamp(self) -> str:
        return f"STALE({self.source}, {self.age_hours:.1f}h)"

@dataclass(frozen=True, slots=True)
class StaleContext:
    sources: tuple[StaleSource, ...]
    use_last_good: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_stale": self.is_stale,
            "combined_stamp": self.combined_stamp,
            "sources": [
                {
                    "source": s.source,
                    "age_hours": s.age_hours,
                    "last_good_at": s.last_good_at.isoformat(),
                    "stamp": s.stamp(),
                }
                for s in self.sources
            ],
        }
```

### `StampedPrediction`

```python
@dataclass(frozen=True, slots=True)
class StampedPrediction:
    game_id: str
    mu_margin: float
    sigma_margin: float
    stale_stamp: str | None
    is_stale: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "mu_margin": self.mu_margin,
            "sigma_margin": self.sigma_margin,
            "stale_stamp": self.stale_stamp,
            "is_stale": self.is_stale,
        }
```

### `execute_predict_publish` return shape (excerpt)

```python
return {
    "season": season,
    "week": week,
    "refresh_kind": refresh_kind,
    "ingest_failed": ingest_failed,
    "stale": stale_ctx.to_dict(),
    "predictions": [p.to_dict() for p in stamped],
    ...
}
```

### Production distributional pass-through (export source — `walkforward.py`)

The W1 export layer maps these to public artifact fields:

`sigma_m`, `sigma_t`, `sigma_*_is_missing`, `pred_margin_q05`…`q95`, `cqr_lo/hi/nominal`, `p_ml_home`, `p_ats_home`, `p_ou_over`, `p_*_is_missing`, `null_reason`, member credibility flags.

---

## Evidence — ADR 0014 σ credibility (suppression gate)

From `docs/adr/0014-member-credibility-contract.md`:

> When σ would be block-constant, **refuse it** (null σ / missing probs) rather than flooring — and **do not erase finite μ** to paper over the gap.

Webapp contract: `sigma_margin_credible = !sigma_m_is_missing && null_reason == null`; tiers and probabilities suppressed when false.

---

## Evidence — 23-readout numbers (verbatim in `track_record.json` spec)

Source: `docs/notes/23-readout.md` (corrected v2, closed 2026-08-13).

- Verdict: **NOT CURRENTLY FIT TO BET** (full paragraph in DESIGN §1.4)
- Fundamental ATS snapshots: **50.7%** [48.7%, 52.7%] n=3496
- Fundamental ATS 2019: **51.3%** [48.3%, 54.3%] n=743
- OU snapshots: **52.3%** [49.7%, 54.8%] n=3136
- MAE margin fund vs A2: **14.85** vs **16.45** (Δ **+1.60**)
- ATS log-loss: **0.82–1.04** vs market **0.693**

No rounding applied in spec relative to memo.

---

## Decisions / ambiguities

1. **Column naming:** Public artifacts use `mu_margin`/`sigma_margin` (Task 24 stub names) with W1 rename from `pred_margin`/`sigma_m` — documented in DESIGN §1.2 source column.

2. **`p_cover_home` display:** Model-internal cover probability only; UI copy must not imply a sportsbook line (no Odds API in contract).

3. **Rating trajectories:** Not in current publish output; new `team_ratings_<season>.json` artifact specified; W1 backend deliverable.

4. **Site staleness threshold:** 36 h after expected publish slot (DESIGN §3.2) — chosen to survive single missed refresh without alarm fatigue; tunable in W7.

5. **Hysteresis state:** Stored on workstation between publishes; not in current `predict.py` — W1 adds tier state file.

6. **CFBD ToU (L1):** Flagged for legal review; spec does not resolve.

7. **Free-tier feasibility:** Vercel Hobby + R2 free tier sufficient for ISR + JSON artifact serving at forecast traffic; no conflict found (would STOP if otherwise).

---

## Grep evidence — betting-recommendation language

Command:

```bash
rg -i "best bet|yes bet|\\bplay\\b|edge vs market|\\bunits\\b" docs/webapp/DESIGN.md docs/webapp/TASKS.md
```

Expected: matches only inside sections explicitly stating what the site will **NOT** do (`NOT CURRENTLY FIT TO BET` verdict quote, "no picks/lines/edge" product constraints).

Post-write verification (2026-08-13):

```
rg -i "best bet|yes bet|\bplay\b|edge vs market|\bunits\b" docs/webapp/DESIGN.md docs/webapp/TASKS.md
→ 0 matches

rg -i "\b(bet|pick|play|edge|units)\b" docs/webapp/
→ matches only in:
  - product "will NOT do" constraints (no picks/lines/edge)
  - NOT CURRENTLY FIT TO BET verdict (23-readout verbatim)
  - "not displayed as a pick" (grading field note)
  - mermaid "Public edge" (CDN edge, not betting)
  - verdict plain_language quoting 23-readout ("no edge vs the close")
```

Gate passed.

---

## Acceptance checklist

- [x] `docs/webapp/DESIGN.md` §1–§6
- [x] Artifact schemas consistent with `predict.py` shapes (evidence above)
- [x] Tier definition reproducible from artifact fields; worked examples
- [x] Design language precedes page specs; anti-pattern list verbatim
- [x] Every §5 field maps to named artifact field
- [x] Track record carries CIs, labels, NOT CURRENTLY FIT TO BET unrounded
- [x] `docs/webapp/TASKS.md` session-sized; UI tasks include visual review
- [x] Grep gate (see below)
- [x] No application code modified

---

*End W0 notes.*
