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

The `synthetic-v1` profile has fixed synthetic denominators. The `gold-50-v1`
profile requires 405 supported and 94 exploratory population rows, of which
397 and 92 respectively were readable in the exact-current qualification. A
matrix mismatch fails rather than silently changing a gate. Regression controls are sanitized
integer counts: title, creator, language, and overall matched rows, plus
candidate-atom precision and explicit-negative false-positive numerators and
denominators. Floors permit improvement; 257 overall matched rows and
candidate-atom matches are intentionally different units.

The public input is synthetic. Private gold labels, adjudication, reports, and
lab paths remain ignored. The output contains only counts by field/category,
separate unknown/empty/not-applicable/accepted-unsupported/unexplained-failure
counts, and ISBN relation counts. Edition and rights are exploratory only and
never enter supported regression denominators or gates.

ISBN relations are accepted, associated, unrelated, invalid, or unresolved.
The aggregate emits each count and its common observation denominator as a
rational rate. Recommendations are deterministic count-only entries for
nonzero projection, normalization, or semantic recoverability gaps.
An associated ISBN requires provider-scoped work identity; that association is
not independent corroboration and does not turn a different identifier into a
metadata match. Missing provider work identity remains unresolved.

The private 50-book run must use product reports from an exact, clean,
non-editable installed wheel against the read-only loopback lab; the evaluator
itself runs from the exact checked-out candidate. It writes only ignored,
aggregate-only candidate output and a replay receipt.
It may recommend a follow-up only for an aggregate-demonstrated recoverable
category; it must not implement a product fix or freeze a new threshold.
