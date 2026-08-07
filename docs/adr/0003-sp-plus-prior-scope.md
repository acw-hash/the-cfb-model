# ADR 0003: SP+ in priors — market-aware stack only

## Status

Accepted

## Context

§3.1 described SP+ as a "prior anchor & benchmark" kept out of fundamental
model *features* but "IN market-aware/ensemble variant," while §9.6's prior
blend listed only roster/history components (no SP+). That left implementers
free to put SP+ in fundamental priors, market-aware features only, both, or
neither — and contradicted the independence goal of the fundamental stack
(external public rating must not contaminate the no-market forecaster).

## Decision

- **Fundamental stack:** SP+ appears **nowhere** in §9.6 priors and nowhere in
  Stage-2 features. It remains an **external benchmark** only (rank correlation,
  CRPS/log-loss comparisons).
- **Market-aware stack:** SP+ **may** enter the §9.6 prior blend as an additional
  weight `w7·SP+ preseason rating`. It may also appear as a market-aware Stage-2
  feature if a later task explicitly adds it; this ADR does not require that
  feature path.

## Consequences

- Two prior-fitting designs (or one design with an `include_sp_plus` flag gated
  by stack identity) are required; fundamental and market-aware Week-1 states
  can differ.
- Ablation A1 (priors off) and any SP+ ablation must state which stack's priors
  were altered.
- §3.1 Value cell and §9.6 / §5.2 wording are reconciled to this decision.
