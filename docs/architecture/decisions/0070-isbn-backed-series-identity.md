# ADR-0070: ISBN-backed identity formatting for automatic series fill

- Status: accepted
- Date: 2026-09-07
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

Amends [ADR-0018](0018-closed-title-creator-comparison.md) and
[ADR-0067](0067-decoration-equivalence-and-compound-admission.md) only for
Automatic series applicability. Their comparison, title election and WORK
dispatch-admission contracts remain unchanged.

## Context

An ISBN response can name another work. Selecting a complete series superset
does not prove that its work describes the target. Refusing every selected
candidate without strict target-work admission closes that path, but also
withholds useful fills when the retained ISBN edition and complete author
identity explain a formatting difference. #895 requires both protections;
query-only title transformations cannot supply identity authority.

## Decision

Keep existing strict target-work admission first. A separate series-only
fallback may accept a selected contributing candidate only through a successful
target-backed ISBN lane. The lane retains its exact local input references and
computed identity, edition/work membership, and complete candidate set. Every
member must independently match the target, state the same nonempty resolved
provider WORK identity, and carry valid EDITION ISBN families all matching the
lane. Missing, malformed, ambiguous or variant identity remains refused. No
candidate is filtered out to obtain admission.

Use the existing complete ordered creator comparison without changing it:
duplicate or incomplete sets fail; conservative comma inversion remains limited
to one author, one comma and two bounded personal-name sides. There is no token
bag, initial expansion, alias matching or author reordering.

The title must satisfy one of three alternatives:

- Existing closed title equality with the candidate's valid WORK title.
- Exact byte equality with its single valid EDITION title, retaining the same
  candidate's complete WORK authors and every variant veto.
- Exactly one direct `parenthesized_series_name` decoration whose clean title
  byte-equals its WORK title and whose preserved component byte-equals a valid
  WORK SERIES claim on that same candidate. The clean title cannot require
  another decoration step.

No report-wide or sibling-candidate attestation, colon stripping, bare position,
year removal, stacked decoration, series alias, or arbitrary prefix is admitted.
This predicate creates no claim, value, identity binding or transformation
lineage. It changes neither WORK dispatch admission nor Ask, non-series policy,
proposal election, selected values/order/references, or populated-target safety.

## Consequences and alternatives

The product can recover only evidence-justified formatting cases; other useful
fills may remain withheld. Global work-identity normalization would broaden
unrelated consumers, while filtering and re-election could create new choices.
Both alternatives are rejected. A qualified successor must bind these source
changes; historical qualification records do not become evidence for the repair.

## Validation

Real-adapter synthetic ISBN reports exercise proposal-to-preview positives and
unrelated-book, complete-lane, source-attestation, variant and author-set
negatives. Ask, non-series, populated-target, decision and effect regressions
remain required. Owner-approved exposed regression uses the existing works and
unchanged truth/scorers, compares complete surviving previews, and retains old
holdout results as historical rather than claiming another fresh holdout pass.
