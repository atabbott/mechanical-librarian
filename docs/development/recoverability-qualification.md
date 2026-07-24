# Recoverability qualification

`scripts/recoverability_evaluator.py` is a versioned, aggregate-only audit
tool. It classifies each supported value-bearing row into exactly one of
`absent_in_source`, `inspection_failure`, `extracted_not_projected`,
`normalization_mismatch`, `semantic_mismatch`, `matched_partial`, or
`matched_complete`. These measure evidence recovery, not metadata decision
accuracy: accepted metadata may legitimately be absent from the EPUB.

The public input is synthetic. Private gold labels, adjudication, reports, and
lab paths remain ignored. The output contains only counts by field/category,
separate unknown/empty/not-applicable/accepted-unsupported/unexplained-failure
counts, and ISBN relation counts. Edition and rights are exploratory only and
never enter supported regression denominators or gates.

ISBN relations are accepted, associated, unrelated, invalid, or unresolved.
An associated ISBN requires provider-scoped work identity; that association is
not independent corroboration and does not turn a different identifier into a
metadata match. Missing provider work identity remains unresolved.

The private 50-book run must use an exact, clean, non-editable installed wheel
against the read-only loopback lab and write only ignored candidate output.
It may recommend a follow-up only for an aggregate-demonstrated recoverable
category; it must not implement a product fix or freeze a new threshold.
