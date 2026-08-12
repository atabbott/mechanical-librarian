# ADR-0048: compile closed rich-metadata policy previews

## Status

Accepted for F20 #566.

## Context

The immutable policy notebook could show category-level impact, but it could
not explain how the closed subtitle, series, genre, tag, description, ISBN,
ASIN, and explicit choices would project an exact current ebook case. Owner
preference must remain separate from both evidence admission and automatic
authority.

## Decision

Advance the canonical policy profile to `metadata-policy-profile-v2` and keep a
closed option vocabulary for the eight rich-field categories. A deep, pure
compiler consumes one resolved draft plus one exact `MetadataReport` and its
draft-bound `ProposalSet`. It may project only values already admitted by the
existing proposal evaluator. Missing, null, and empty targets share the empty
path; unusable target states and ambiguous or insufficient evidence produce a
typed abstention. The compiler never calls a provider, parses source markup,
converts identifiers, combines provider lists, mutates state, or schedules an
effect.

The application reads at most 20 exact-current report/proposal lineage pairs.
The stored pair proves current lineage; the immutable report is re-evaluated by
the existing pure proposal compiler under the draft's exact resolved identity.
The API, CLI, and owner policy page serialize the same result, including the
full qualified series tuple when Audiobookshelf can receive only its selected
first membership.

At this non-activating #566 increment every new Automatic destination remained
unqualified. The F20 activation seam pinned the public candidate digest and
every option-local terminal disposition with an empty passing set, rejecting
rich Automatic intent while exposing no qualification cases. #565 and #564
later replaced that candidate with the independently scored exact passing set;
the same seam now admits only those identities. Existing F19a authority and the
effect/consent boundary remain unchanged.
`metadata-policy-profile-v1` persisted documents are rejected under ADR-0013;
there is no reader, migration, conversion, or import path.

## Consequences

Owners can inspect exact projected values and stable abstention reasons before
activation without broadening authority. Preview output contains private
current and proposed metadata, so the HTTP response remains authenticated and
private/no-store and CLI output must be handled as private application data.
The bounded replay costs no network access and creates no database or target
write.
