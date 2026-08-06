# ADR 0001: Record architecture decisions

## Status

Accepted

## Context

Architectural choices in this project (data contracts, validation strategy, model
promotion gates, betting filters) have long-lived consequences and must not be
reconstructed from chat history or commit archaeology.

## Decision

All material architecture changes require an Architecture Decision Record under
`docs/adr/`, numbered sequentially (`NNNN-short-title.md`). Each ADR states
context, decision, and consequences. ADRs are immutable once accepted; superseding
decisions get a new ADR that references the old one.

## Consequences

- Reviewers can reject PRs that change architecture without an ADR.
- Future agents and humans have a durable decision log alongside the design spec.
