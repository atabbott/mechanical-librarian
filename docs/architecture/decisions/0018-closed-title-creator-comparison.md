# ADR-0018: Close title and complete-creator comparison

- Status: accepted
- Date: 2026-07-29
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Metadata reports retained original local and provider claims but could explain
agreement only through one exact normalized equality. Common Unicode,
typographic, and comma-inverted name forms therefore appeared as disagreement.
Provider author identities and aliases were retained separately from claims, so
using them after parsing risked positional inference or provider-specific
comparison rules. Any broader fuzzy or partial comparison would hide ambiguity
and could increase downstream evidence authority.

## Decision

`closed-title-creator-v2` is the only current comparison policy. It records the
strongest deterministic method on every agreement link in this order:

1. exact source bytes;
2. Unicode NFC, case folding, and ASCII-whitespace normalization;
3. the same form after mapping non-breaking spaces, paired curly quotation
   marks, and en/em dashes; or
4. conservative `Family, Given` inversion for exactly one author-role value.

NFC does not strip or transliterate diacritics. Comma inversion requires exactly
one comma and two non-empty bounded sides and rejects suffix and organization
markers. Variant, translation, adaptation, and abridgement markers do not gain
non-exact agreement.

Creator agreement is candidate-wide and requires the complete ordered valid
same-scope, same-applicability, same-role set. An adapter may persist a neutral
name-to-identity binding only when it parsed that exact structural relationship.
The binding references the exact immutable provider claim. Multiple bound
claims for one identity may act as aliases, but every claim must have one
unambiguous binding and the resulting distinct identity sequence must still
match the complete local creator sequence. Bindings never replace a claim,
become a provider, or create corroboration.

Adapters continue to emit original claims and evidence. A domain finalization
seam adds comparison links after the complete candidate is assembled.
Construction and replay run the same finalizer and reject any non-canonical
link or binding. `ComparisonMethod.NONE` is valid only for disagreement,
incompatible, or not-comparable outcomes. The exact-current report and
database schema advances to `metadata-report-v19`; ADR-0013 rejects v18 and all
earlier state without migration or compatibility parsing.

## Consequences

### Positive

- Equivalent closed forms remain explainable without changing emitted values.
- Complete creator order and identity binding are validated once in the domain.
- Provider rank, response order, aliases, and repeated queries cannot create
  another evidence source or silently widen downstream authority.

### Negative

- Conservative rules intentionally leave many human-plausible names and titles
  unresolved.
- Earlier pre-release report databases must be rebuilt from a clean schema.

### Neutral or deferred

- No fuzzy matching, edit distance, token subsets, accent stripping, author
  reordering, subtitle composition, adaptive query, manifestation selection,
  reviewer decision, or production write is added.
