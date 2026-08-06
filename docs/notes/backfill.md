# Backfill status (Task 23-FIX-BACKFILL)

**Status:** CFBD season-grain backfill **substantially complete** on Tier 1
(77 calls used, 4923/5000 remaining). Open gaps below are findings, not
un-fetched seasons.

Full season lines: [`docs/notes/backfill-progress.md`](backfill-progress.md).

---

## Season × dataset coverage (staged, after Tier 1 run)

Legend: **Y** = present · **N** = missing · **0** = present empty (era) ·
**S** = schema-blocked (payload exists at CFBD, pandera rejected)

| Dataset | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| games | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| plays | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| advanced_box | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| lines_historical | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| teams | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| venues | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| rosters | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| returning_production | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| talent | 0 | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| recruiting | **S** | **S** | **S** | Y | **S** | Y | Y | Y | Y | Y | Y | Y |
| portal | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Y | Y | Y | Y | Y |
| coaches | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| weather | N | N | N | N | N | partial | **Y** | partial | partial | Y | partial | N |

---

## Walk-forward usability

| Use | Seasons | Note |
|---|---|---|
| Stage-1 / EPA-from-advanced | **2014–2025** | filter pass OK |
| Headline metrics (§7.2) | **2019, 2021–2025** | exclude 2020 |
| Roster / prior features | **2014–2025** except recruiting **S** years | 2014/15/16/18 recruiting blocked |
| Weather | **2020, 2023** full; others partial/blocked | venue TZ / lat-lon gaps |
| Portal priors | **2021+** only | pre-2021 correctly empty (0), not zero-filled |

---

## Wall clock (Tier 1 session)

| Phase | Time |
|---|---|
| Season-grain CFBD (value order) | ~3 min ingest + ~3 min quality/season |
| 2020 continuity + full weather | ~7 min |
| Filter 2014–2025 | **2.61 s** |
| CFBD calls consumed | **77** (5000 → 4923) |

---

## Findings (do not exclude seasons; do not edit schemas here)

1. **`RecruitingSchema.points >= 0` rejects CFBD `-0.04`** for seasons
   **2014, 2015, 2016, 2018**. Same class as the roster-negative schema bug.
   STOP — needs sanctioned `schemas.py` edit to allow small negatives (or
   null-with-indicator). Partition left unwritten; raw JSON archived under
   `data/raw/cfbd/`.
2. **Weather:** most seasons blocked or gapped by missing venue timezone /
   lat-lon (`venue_id=5455` Ford Center Frisco; `venue_id=4737`; others lacking
   TZ after raw venues write). Needs `configs/venues_overrides.yaml` fills —
   **not sanctioned** in this task. **2020** enriched cleanly (568/568).
3. **Quality gates** fail on most non-2020 seasons for known reasons
   (drives reconcile; play sequence; occasional advanced completeness) —
   reported, checks not loosened. **2020 quality: 65/65 pass.**
4. **Feature materialize** still blocked (`features` CLI NotImplemented;
   no builder_factory).

---

## Exclude from Task 23 re-run?

| Item | Exclude? | Why |
|---|---|---|
| Core week-grain 2014–2025 | **Yes — skip re-pull** | Already staged; checkpointed |
| Season-grain except recruiting S | **Yes — skip** | Present |
| Recruiting 2014/15/16/18 | Re-pull **after** schema fix | Schema-blocked, not missing at source |
| 2020 from headline metrics | Metrics only | §7.2 item 5 — data is staged |
| Weather for TZ-gap seasons | After venue overrides | Manual coords/TZ |

---

## Resume after schema / venue fixes

```bash
# After RecruitingSchema allows CFBD negative points:
uv run python scripts/backfill_23_cfbd.py --exit-on-quota --seasons 2014,2015,2016,2018 --skip-weather

# After venues_overrides.yaml fills:
uv run python scripts/backfill_23_cfbd.py --postprocess-core-only --skip-quality
```
