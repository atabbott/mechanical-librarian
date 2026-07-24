# Recoverability qualification

`scripts/recoverability_evaluator.py` is a versioned, aggregate-only audit
tool. Its supported view is exactly title, subtitle, creator, contributor,
publisher, language, publication date, identifier, collection, and collection
position; its exploratory view is exactly edition and rights. It classifies
each supported value-bearing row into exactly one of
`absent_in_source`, `inspection_failure`, `extracted_not_projected`,
`normalization_mismatch`, `semantic_mismatch`, `matched_partial`, or
`matched_complete`. These measure evidence recovery, not metadata decision
accuracy: accepted metadata may legitimately be absent from the EPUB.

The `synthetic-v1` profile has fixed synthetic field cardinalities. The
`gold-50-v1` profile requires 405 supported and 94 exploratory population
rows, of which 397 and 92 respectively were readable in the exact-current
qualification. Each field's population and readable cardinality is fixed, so
an input cannot silently move cells between an excluded state and the matrix.
The ten non-readable population rows remain explicitly excluded rather than
being represented as a successful or failed evidence observation.

Regression controls are derived from the matrices and bounded, value-free
candidate evidence; they are not caller-supplied totals. The frozen comparator
is kept separately from the current derived counts. Title, creator, language,
and overall matching are row units. Candidate precision is a candidate-atom
unit, and the explicit-negative rate is a separately bounded negative-evidence
unit; neither denominator is inferred from the other.

The public input is synthetic. Private gold labels, adjudication, reports, and
lab paths remain ignored. The output contains only counts by field/category,
separate unknown/empty/not-applicable/accepted-unsupported/unexplained-failure
counts, and ISBN relation counts. Edition and rights are exploratory only and
never enter supported regression denominators or gates.

ISBN relations are accepted, associated, unrelated, invalid, or unresolved.
Each observed identifier is bound to one sanitized target slot, and the target
list includes targets without an observation. Accepted recall therefore uses
the target-applicable denominator, while associated yield and the other rates
use the observed-identifier denominator. The five relations each require one
closed evidence tuple. In particular, an associated identifier requires the
Hardcover provider-scoped `provider_work_identity` same-work association; it is
not independent corroboration and does not turn a different identifier into a
metadata match. Missing provider work identity remains unresolved.

Exploratory recoverable-evidence recall is the matched-complete plus
matched-partial exploratory rows over rows with source evidence present; source
absence and whole-item unavailability do not dilute this recall denominator.
Recommendations are deterministic, count-only entries with a field, category,
and bounded scope for nonzero projection, normalization, or semantic gaps.

The private 50-book run must use product reports from an exact, clean,
non-editable installed wheel against the read-only loopback lab; the evaluator
itself runs from the exact checked-out candidate. It writes only ignored,
aggregate-only candidate output and a replay receipt.
It may recommend a follow-up only for an aggregate-demonstrated recoverable
category; it must not implement a product fix or freeze a new threshold.
