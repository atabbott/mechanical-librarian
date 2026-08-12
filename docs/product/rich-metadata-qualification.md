# Rich-metadata qualification candidate

The F20 candidate manifest is the public, aggregate-safe record of the frozen
rich-metadata qualification contract and its exact option-level decisions. It
grants decision-only Automatic authority to the passing options; it does not
grant effect consent, construct a target write, or change provider selection.

Qualification is field-option-local. Prior use of the same work for unrelated
extraction, provider, cover, or metadata development does not disqualify it.
A case is excluded only when its expected result was used to implement or tune
the same field option, or when that option was changed after its current output
was viewed without versioning the evaluator or rule. An unseen or final
holdout is not required.

The candidate replayed retained provider responses for 49 accepted real-work
reports through the current provider adapters, proposal evaluator, and rich
preview compiler without network access. Public output contains only aggregate
metrics and contract identities; source material, expected values, and
case-level outcomes remain private.

The gate was frozen before scoring: at least three eligible cases, at least
three automatic predictions, 100% automatic precision, and no zero-tolerance
failure. Recall is reported but is not a pass gate. This favours safe
abstention while avoiding a novelty requirement unrelated to the option being
proved.

All three series policies passed after series correctness was separated from
preference: a prediction is correct when it is a valid membership for the
bound work, while the corpus value remains the preferred membership. Primary
narrative made 19 correct predictions; source order and all qualified each
made 20, all at 100% precision. The two ISBN policies also passed:
`isbn_form:prefer_isbn13` made 22 correct predictions from 41 eligible
opportunities and `isbn_form:preserve_exact` made 4, each at 100% precision.

Subtitle was adjudicated more narrowly at ebook-manifestation scope. Only 2 of
7 fill-empty predictions were valid: two other proposals duplicated text
already present in the title, and the remainder were series marketing,
proof-only, or later franchise descriptors. Corroborated split made no
predictions. Both subtitle policies therefore remain `not_qualified`.
Genres, tags, description, and ASIN remain `insufficient_coverage`. `tags:off` is policy-only,
`explicit:unsupported` remains human-only, and narrators and cover remain
excluded.

The machine-readable candidate is
[`f20-rich-metadata-qualification-candidate-v1.json`](../reference/f20-rich-metadata-qualification-candidate-v1.json).
