# ADR 0015: Cover/over probabilities are market-referenced and unpublished

## Status

Accepted

## Context

DESIGN §1.2 described `p_cover_home` and `p_over` as **model-internal**
probabilities, “not vs a published line,” with UI copy “cover (model ref).”
The implemented predict path does the opposite.

`ProductionStack._lookup_closes` joins CFBD `lines_historical` rows with
`line_type == "close"` (median) by `game_id`. `spread_cover_probs` / `total_probs`
then score Monte Carlo draws against those closes. Export remaps
`p_ats_home` → `p_cover_home` and `p_ou_over` → `p_over` into
`week_predictions.json`. Those values reached R2 and the Game Detail
ProbabilityList.

A per-game cover probability against the close is the model’s disagreement
with the market. Product decision 1 forbids publishing Odds/market numbers.
Relabeling the existing figure to name the close would still publish it.

No well-defined model-internal cover reference exists in the current stack
(the computation is the close-conditional MC path). Changing that path and
recalibrating is not available before week 1.

The `ats_close` / `ou_close` calibrators, `p_ats_home`, and `p_ou_over` remain
the evaluation instruments for backtests, CLV settle, and the 23-readout ATS
analysis. This decision does not change them.

## Decision

1. **Code wins.** §1.2’s “model-internal / not vs a published line” wording is
   wrong. The spec is amended to describe the implemented computation.
   This ADR supersedes nothing in the model.
2. **Withdraw from publication.** `p_cover_home`, `p_over`, and their
   `_credible` companions are removed from exported artifacts, R2 objects
   produced by export, RSC payloads, and rendered UI. Upstream pipeline
   columns and calibrators are untouched.
3. **Do not relabel.** Honest market-reference copy would still publish a
   close-conditional probability, contradicting product decision 1 and L3.
4. **Schema 1.2.0 (minor).** DESIGN §1.7 is amended to distinguish
   **WITHDRAWAL** (field removed in the same change that removes every
   consumer of it — minor) from **REMOVAL** (field removed while a consumer
   still reads it — major). This withdrawal is the first application.
   `SUPPORTED_SCHEMA_MAJOR` stays 1. The W7-CLOSE-2 schema-gate test that
   uses `2.0.0` as the unsupported major is left in place; any real future
   move to major 2 must relocate that test to `3.0.0` first.
5. **`p_win_home` stays.** It references no line (moneyline vs 0). Out of
   scope here.

## Consequences

- Game Detail ProbabilityList shows **Home win** only. Cover/over rows and
  labels are gone. Empty-block substitution is not used; Home win remains.
- Aggregate ATS metrics and the NOT CURRENTLY FIT TO BET verdict on
  `/results` stay. They are 23-readout disclosures, not per-game figures.
- 1.1.0 artifacts (R2 `latest/` until the first live publish, plus the
  committed legacy fixture) may still carry the four keys. The site ignores
  unknown/withdrawn fields under the same major. Loaders must not reject
  them.
- A published-key **allowlist** (not a name denylist) guards artifact objects
  and This Week / Game Detail DTOs. A consumer-or-withdrawn test fails on
  any game key that is neither read by a named site consumer nor listed in
  `WITHDRAWN_FIELDS`.
- W8-R2-PUBLIC remains **BLOCKED** until this withdrawal is closed; public-read
  of raw `latest/week_predictions.json` would otherwise republish whatever
  `latest/` still holds.
- The first live `predict_publish` writes `schema_version` 1.2.0 without the
  four keys and overwrites `latest/`. Pre-existing `v1/*`, `v2/*`, and
  `sandbox/*` objects keep the keys until operator cleanup.
