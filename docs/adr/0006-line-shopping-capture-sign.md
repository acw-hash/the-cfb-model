# ADR 0006: `line_shopping_capture` sign convention

## Status

Accepted

## Context

DESIGN §2.7 defines the metric verbatim as

```
line_shopping_capture = implied_prob(best captured price at bet time)
                      − implied_prob(consensus price at bet time)
```

de-vigged proportionally on the bet side. Implementing it exposed that the sign
runs opposite to what the name suggests.

Worked example. Our side is available at -105 (with -115 on the other side) at
one book while consensus is -110/-110. Proportionally de-vigged:

| quantity | value |
|---|---|
| `implied_prob(best)` | 0.489166 |
| `implied_prob(consensus)` | 0.500000 |
| `line_shopping_capture` | **-0.010834** |

We bought the *better* price, and the metric is *negative*. That is not a bug in
the arithmetic: a book offering our side more cheaply is a book whose fair view
of our side sits below consensus, and that disagreement is exactly why its price
was worth taking. The metric measures the book's disagreement with consensus, not
a saving relative to it.

The magnitude is the number that matters. For a bet whose own book never moves —
true closing-line value of exactly zero — settling against a consensus close
reports `+0.010834` instead. The spurious credit equals
`−line_shopping_capture`. This is precisely the bias audit finding A-1(a)
describes, and it is now pinned by
`test_consensus_settlement_credits_a_skill_free_bet_and_same_book_does_not`.

## Decision

1. Implement the formula **exactly as §2.7 specifies**. The spec is the
   authority, the arithmetic is right, and silently negating it would make the
   code disagree with the document that governs it.
2. Document the sign at every point of use: the function docstring, the field
   docstring on `SettledBet`, and this ADR. A reader who sees a negative
   "capture" must be able to find out immediately why.
3. Do **not** rename the field. It is named in §1.6, §2.7, §7.3, §12 and Task 20
   acceptance; renaming in code only would fork the vocabulary.
4. When Task 21 renders this metric, it must print the interpretation alongside
   the number rather than the bare value, in the same spirit as §7.3's
   anti-metric rule for bare win rates.

## Consequences

- Any future aggregation must not sum `line_shopping_capture` with CLV, and must
  not flip its sign to make dashboards read nicer. The relationship to
  consensus-settlement bias is the useful reading, and it is a magnitude.
- If the spec is ever revised to make the name and sign agree, it should be
  revised in DESIGN.md first and this ADR superseded — not patched in code.
- A reader comparing the two metrics should expect that in a healthy shopping
  regime `line_shopping_capture` is systematically negative while same-book CLV
  is the only thing allowed to carry a skill claim.
