# ADR 0008: Saturday 09:00 ET historical decision point

## Status

Superseded by [ADR 0009](0009-saturday-0600-et-decision-point.md)

## Context

DESIGN §9.8 / ADR 0002 list Saturday **06:00** ET as a production daily-refresh
decision point. An earlier plan draft proposed `saturday_0900_et` as a gameday
morning board variant.

## Decision

~~Register `saturday_0900_et`.~~ **Rejected before any historical spend.** Align
with DESIGN/ADR 0002 at Saturday 06:00 ET instead (ADR 0009).

## Consequences

- No archive was written under `saturday_0900_et`; no comparability break.
- Do not reintroduce `saturday_0900_et` without a new ADR that accepts diverging
  from DESIGN §9.8.
