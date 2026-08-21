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

Genres, tags, and descriptions do not have one objectively correct semantic
answer. They are therefore not scored against a preferred taxonomy, tag set,
or prose description. Their objective gate is exact provenance and operation:
one complete ordered work-bound source list, no cross-source synthesis or
taxonomy translation, and the exact selected fill/add/replace behavior; or one
exact safe work-bound description, without rewriting or silent truncation.
Google visible-text remains a separate transformation and cannot qualify as
ordinary safe plaintext.

All three series policies passed after series correctness was separated from
preference: a prediction is correct when it is a valid membership for the
bound work, while the corpus value remains the preferred membership. Primary
narrative made 19 correct predictions; source order and all qualified each
made 20, all at 100% precision. The two ISBN policies also passed:
`isbn_form:prefer_isbn13` made 22 correct predictions from 41 eligible
opportunities and both ISBN-form options made 22, each at 100% precision.
The admitted proposal and decision retain every qualified membership; the
existing Audiobookshelf effect planner projects only the preferred first
membership because that target accepts one series value.

Subtitle was adjudicated more narrowly at ebook-manifestation scope. Only 2 of
7 fill-empty predictions were valid: two other proposals duplicated text
already present in the title, and the remainder were series marketing,
proof-only, or later franchise descriptors. Corroborated split made no
predictions. One valid proposal exactly matched a subtitle extracted from the
ebook package. The other was established by revised-edition evidence that the
current extractor does not yet expose as a structured subtitle claim. Two of
the invalid proposals were nevertheless repeated by multiple catalogues, so
provider count is not a safe substitute. A future subtitle candidate must bind
the selected ebook manifestation, remain distinct from the title, and exclude
marketing or status text. Both subtitle policies therefore remain
`not_qualified`.

The current policy separately treats the exact provider-edition label `A Novel`
as a generic marketing subtitle. It preserves a blank target without creating
a subtitle proposal. The vocabulary is provider-only and deliberately limited
to that exact normalized label; local package evidence and any longer subtitle
remain ordinary evidence. This is a proposal-policy rule, not F20 automatic
authority, so both subtitle options remain `not_qualified`.
Genre add and replace each made 3 exact predictions. Genre fill-empty was
evaluated with only the target genre field varied across the supported
`missing`, `null`, and `empty` states while preserving the observed provider
evidence; it made 4/4 correct predictions in each state, 12 total. Tag
fill-empty, add, and replace each made 3, and safe plaintext description made 9. All were
correct under the provenance-and-operation model, with zero safety failures,
so these options pass. Google visible-text made no predictions and remains
`not_qualified`. ASIN remains `insufficient_coverage`. `tags:off` is still the
default and policy-only, `explicit:unsupported` remains human-only, and
narrators and cover remain excluded. The ordinary populated-genre case remains
a no-op under fill-empty and is not rewritten.

The machine-readable candidate is
[`f20-rich-metadata-qualification-candidate-v1.json`](../reference/f20-rich-metadata-qualification-candidate-v1.json).
