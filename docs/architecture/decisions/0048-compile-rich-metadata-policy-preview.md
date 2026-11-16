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

## Amendment: the other six fields and the cover (proposed, #1524)

Status: proposed. Only the owner's own answer accepts it.

The preview also compares title, authors, narrators, publication date,
publisher and language, and describes the cover. For those six text fields it
runs the existing in-memory pipeline for each sampled work, under the active
policy and under the draft, as far as the outcome set and stops there. A
result is compared by what the policy does (the destination, whether the value
is written or left to the owner, and the value), never by an identity that
changes with the policy. A work is compiled only when the resolved draft
differs from the resolved active policy for its library, at most twice.

The admission layer is not moved. What admission binds or refuses after the
outcome set is not previewed. The stated limit: a title and subtitle that
would only be accepted together are previewed field by field, so the pair's
effect is not shown.

A cover row is words built from the stored comparison for the sampled report:
its stage, and whether fetching had finished. It is read in the capture
transaction when that comparison is current, bound to the report, not stale
and at the current schema; otherwise the row says it was not previewed and
why. No source is asked, no image is read, and no reference, artifact key,
digest or token is carried. Current covers, holds, earlier choices and write
posture are live state the preview does not read; a replacement always reads
as a suggestion.

`metadata-policy-preview-v1` keeps its name: one member, the library field a
comparison is for, is added to each field comparison. Nothing is stored, and
the response stays authenticated and private, no-store.
