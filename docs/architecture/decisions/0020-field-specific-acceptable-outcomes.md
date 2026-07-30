# ADR-0020: Model field-specific acceptable metadata outcomes

- Status: accepted
- Date: 2026-07-30
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Proposal comparison treated normalized string equality as the only agreement
shape. Canonical `en`, `eng`, and a compatible regional language such as
`en-US` therefore became competing choices. Publisher, imprint, brand, and
publishing-group statements for one selected manifestation likewise became a
false conflict even when each was a plausible field outcome.

Collapsing those raw statements into one canonical value would lose
provenance and would incorrectly assert language detail or corporate identity.
Selecting one arbitrary value would turn an acceptable set into an unsupported
preference.

## Decision

The proposal domain owns a closed field-policy seam. It changes decision
compatibility only; report claims and proposal alternatives retain their
original admitted value and evidence references.

Language policy accepts current ISO 639-1 and ISO 639-2 aliases as the same
base language. A canonical language-only tag is compatible with a more
specific canonical script or region tag. Two specified scripts or regions
must agree. Different base languages, incompatible specified scripts or
regions, unregistered primary codes, and malformed or noncanonical tags remain
conflicting.

Publisher policy admits only syntactically valid publisher or imprint claims
already bound to edition scope by selected local evidence or a provider
candidate whose exact usable ISBN family intersects the selected EPUB's and
whose closed edition evidence has no format or work-relation contradiction.
Distinct plausible publisher, imprint, brand, and publishing-group statements
are acceptable outcomes, not assertions that the organizations are legally
equal. Work-scoped, associated-manifestation, unrelated, unresolved-scope,
malformed, and target-only values cannot gain evidence authority.

`acceptable_choice` is a new proposal outcome. It retains at least two
field-policy-acceptable alternatives and selects none. It is actionable and is
projected into the existing reviewer packet. `keep` may retain multiple
language or publisher alternatives only when admitted evidence actually
supports the current value; target syntax alone is not evidence. A true
language contradiction remains `unresolved_choice`.

The exact-current proposal policy is `fixed-conservative-v5`. ADR-0013 rejects
earlier proposal-store shapes without migration, import, or conversion.

## Consequences

### Positive

- Compatible language forms no longer create false conflicts.
- Plausible manifestation-bound publishing-business statements remain useful
  without inventing a corporate hierarchy or fuzzy equality.
- Every distinct candidate and its provenance remains available to review.
- The policy can express an acceptable set without choosing an arbitrary raw
  value.

### Negative

- A reviewer or later decision layer still has to choose among acceptable
  alternatives when no current value is evidence-supported.
- The closed language registry and canonical tag subset require deliberate
  maintenance when standards change.
- Existing pre-release proposal databases must be rebuilt cleanly.

### Neutral or deferred

- There is no publisher registry, corporate graph, fuzzy matching, provider
  ranking, automatic manifestation selection, decision, apply, or write
  authority.
- Private acceptable-action adjudication and scoring remain separate
  qualification work.
