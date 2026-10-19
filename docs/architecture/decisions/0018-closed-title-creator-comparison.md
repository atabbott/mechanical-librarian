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

`closed-title-creator-publication-work-identity-v5` is the only current
comparison policy. It records a
truthful deterministic method on every agreement link:

1. exact source bytes;
2. closed field-specific normalization for retained byte-different non-title
   and non-creator values;
3. Unicode NFC, case folding, and ASCII-whitespace normalization;
4. the same form after mapping non-breaking spaces, paired curly quotation
   marks, and en/em dashes; or
5. conservative `Family, Given` inversion for exactly one author-role value.

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
match the complete local creator sequence. An incomplete optional binding set
is non-authoritative and leaves direct complete-name comparison unchanged.
Bindings never replace a claim,
become a provider, or create corroboration. Each lexically matching alias
retains its own closed lexical method; a nonmatching sibling admitted only by
the complete identity binding records `structural_identity_binding`.

Adapters continue to emit original claims and evidence. A domain finalization
seam adds comparison links after the complete candidate is assembled.
Construction and replay run the same finalizer and reject any non-canonical
link or binding. `ComparisonMethod.NONE` is valid only for disagreement,
incompatible, or not-comparable outcomes. The exact-current report and
database schema originally advanced to `metadata-report-v20`.

The #870 amendment adds one separate, domain-owned work-identity admission
decision without broadening ordinary title agreement. Its closed transformation
table distinguishes query-enabled from admission-enabled families. The reviewed
0.18.0 admission subset is empty: no isolated family cleared both independently
corroborated positive and same-author boundary evidence. Every table row,
including `filename_author_title_prefix`, is therefore query-only. Enabling a
future family requires new per-family evidence and a new ADR and persisted-schema
change; changing the table flag alone cannot create a canonical lineage.

Admission retains both sides' original claims, requires the unchanged closed
title comparison, exact complete ordered authors, and one provider-local work
identity, and preserves every translation, adaptation, abridgement, and
variant-marker veto. Dispatch lineage is retained and never creates
equivalence. The nullable v28 transformation-lineage field remains a replay
guard.
Initial qualification, overflow witnesses, persisted readback, retry successors,
and ProposalSet replay invoke the same decision. ADR-0013 rejects v27 and every
other predecessor during ordinary startup.

The #896 amendment ([ADR-0067](0067-decoration-equivalence-and-compound-admission.md))
grants admission through a closed compound production rather than a table row.
Every table row, `filename_author_title_prefix` included, stays query-only; one
production, `author_prefix_then_underscores`, composes the complete first-author
filename prefix with filename-underscore normalization and admits only when the
remainder after the closed separator carries at least one underscore. The
transformation lineage becomes the ordered families, the derived production
name, and the local side; a lineage naming a single family, an unenabled
composition, a mismatched production name, or the provider side is invalid. The
schema advances to `metadata-report-v36`. The identity floor, the veto
vocabulary, and the rule that query shaping never creates admission equivalence
are unchanged.

The admission baseline retains the pre-amendment target-normalization floor:
NFC, case folding, and collapse of all Unicode whitespace while preserving
punctuation. Query transformations never create admission equivalence.

ADR-0068 adds one closed Latin canonical-combining-mark family to final-rung
query shaping and admission. Exact changed local/provider title and author
ordinals are persisted separately from actual dispatch lineage; non-Latin
letters, orphan marks, compatibility decomposition, transliteration, and
substitutions remain excluded. The schema advances to `metadata-report-v37`;
ordinary startup rejects v36 under ADR-0013.

The #889 amendment permits reordered complete author multisets only while
replaying a retained WORK-lane candidate through an exact permitted dispatch.
Each author still matches through the existing closed lexical relation exactly
once, preserving cardinality and duplicate values. Direct identity admission,
identity bindings, Latin and filename compound productions remain ordered; this
adds no transformation lineage or schema change.

The #1255 amendment adds two rows, after `order_inverted_series_prefix` and
before `subtitle_segment`, and an audiobook-only column to the table:
`leading_sequence_token` and `right_hand_segment`. Both are query-enabled,
admission-disabled and shape the normalized rung, and only
`work_query_ladder(..., audiobook=True)` applies them, so every ebook ladder and
lineage is byte-identical. `leading_sequence_token` removes one anchored
position token -- an optional book, vol(ume), part or no prefix with an
optional "." or "#", one to three digits with an optional one- or two-digit
decimal, then a separator -- when the rest names a letter.
`right_hand_segment` keeps the right side of exactly one spaced dash when both
sides name a letter and the right side is neither a series marker nor only
words from the closed stop list both audiobook title grammars share. On
#1246's no-match tail the two reached 32 items (15 and 17), and they rewrite
rung 1 for 5 of 91 case-bearing items and 47 of 238. Admission is unchanged: a rewritten query
admits only what exact admission against the lane's own title admits. The
schema advances to `metadata-report-v51`.

## Consequences

### Positive

- Equivalent closed forms remain explainable without changing emitted values.
- Complete creator order and identity binding are validated once in the domain.
- Provider rank, response order, aliases, and repeated queries cannot create
  another evidence source or silently widen downstream authority.
- Provider-specific query grammar cannot define work-title equivalence.

### Negative

- Conservative rules intentionally leave many human-plausible names and titles
  unresolved.
- Earlier pre-release report databases must be rebuilt from a clean schema.

### Neutral or deferred

- No fuzzy matching, edit distance, token subsets, accent stripping, subtitle
  admission, manifestation selection,
  reviewer decision, or production write is added.
