# Recoverability qualification

`scripts/recoverability_evaluator.py` measures how much metadata evidence in an
EPUB the product recovers into its reports. It measures evidence recovery, not
metadata decision accuracy: accepted metadata may legitimately be absent from
the EPUB. Output is aggregate counts only.

```text
uv run python scripts/recoverability_evaluator.py --input INPUT.json --output OUTPUT.json
```

Input is `recoverability-evaluator-v4`; output is
`recoverability-aggregate-v4`. The public synthetic input is
`tests/support/recoverability_mini_corpus.json`, exercised by
`tests/unit/test_recoverability_evaluator.py`.

## Fields and categories

- **Supported fields:** title, subtitle, creator, contributor, publisher,
  language, publication date, identifier, collection and collection position.
- **Exploratory fields:** edition and rights. They are reported but never
  enter supported denominators or regression gates.

Each supported value-bearing row falls into exactly one category:
`absent_in_source`, `inspection_failure`, `extracted_not_projected`,
`normalization_mismatch`, `semantic_mismatch`, `matched_partial` or
`matched_complete`. Unknown, empty, not-applicable, accepted-unsupported and
unexplained-failure rows are counted separately.

## Profiles

A profile fixes each field's population and readable cardinality, so an input
cannot silently move rows between an excluded state and the matrix.
`synthetic-v1` is the public synthetic profile. `gold-50-v1` is the private
50-book profile.

Regression metrics are derived from the matrices and bounded candidate
evidence, never supplied by the caller. The frozen baseline is held separately
from the current counts. Title, creator, language and overall matching count
rows; candidate precision counts candidate atoms; the explicit-negative rate
has its own bounded denominator.

## ISBN relations

Each observed identifier binds to one sanitised target slot, and targets
without an observation are listed too. Relations are `accepted`, `associated`,
`unrelated`, `invalid` or `unresolved`, each needing one closed evidence
tuple. `associated` requires a Hardcover `provider_work_identity` same-work
association. It is not independent corroboration and does not make a
different identifier a metadata match. Missing provider work identity is
`unresolved`. Accepted recall uses targets as its denominator; the other rates
use observed identifiers.

Exploratory recall is matched-complete plus matched-partial rows over rows
with source evidence. Source absence and whole-item unavailability are not in
the denominator. Recommendations are deterministic count-only entries naming a
field, category and scope with a nonzero projection, normalisation or semantic
gap.

## Private run

The private 50-book run uses reports from an exact, clean, non-editable
installed wheel against a read-only loopback lab. The evaluator runs from the
same checked-out source. It writes only ignored aggregate output and a replay
receipt. Gold labels, adjudication, reports and lab paths stay private.

A run may recommend a follow-up only for a category the aggregates show is
recoverable. It must not implement a product fix or freeze a new threshold.
