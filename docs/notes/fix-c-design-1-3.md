# Fix C — DESIGN §1.3 stale docs

**Date:** 2026-09-08  
**Status:** Docs only (`docs/webapp/DESIGN.md` §1.3). No code, no publish.

## Corrections applied

1. Documented all four `grade_status` values matching `GradeStatus` /
   `results_season.schema.json`: `graded`, `game_not_final`,
   `no_pre_kickoff_publish`, `postgame_missing`.
2. Documented that the exporter ships the **full season schedule** as rows;
   ungraded statuses are included, not excluded.
3. Replaced the false claim that kind-precedence “ensures grades reflect what
   Ridge would have shown before kickoff” with kind-first / recency-second
   semantics and the daily_refresh-vs-tuesday_primary implication. Noted the
   paired re-grade AE evidence (weak n). **Did not change**
   `REFRESH_KIND_PRECEDENCE`.

## §1.7 version question (recommend, do not decide)

Documenting two enum values that already ship in the JSON schema and frontend
`GradeStatus` is a **patch** under §1.7 (“documentation/clarification only”).
It is not a semantic redefinition of an existing field, not a removal, and not
an additive consumer-facing field. The frontend major-mismatch maintenance
rule only fires on major mismatch — this docs sync should not bump major or
minor. Operator decides whether to bump `schema_version` patch on the next
artifact write; the DESIGN text itself does not require a version bump to
stay true.
