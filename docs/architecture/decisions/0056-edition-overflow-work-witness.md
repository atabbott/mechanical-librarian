# ADR-0056: Retain a work-only witness for bounded edition overflow

- Status: accepted
- Date: 2026-08-13

## Decision

When Hardcover returns exactly one row beyond the bounded edition envelope and
all six rows link uniquely to one complete work, retain a closed work-only
witness. It contains the provider work identity, validated title, ordered
author claims, and exact author identity bindings. It is a terminal overflow
state, not a provider candidate, lane candidate, proposal input, or cover
source. Edition identities and fields, raw provider values, and diagnostics are
discarded.

The metadata report advances to v24. Under ADR-0013, v23 data remains rejected
before mutation; there is no migration, import, or conversion.

## Amendment (#1380)

A WORK lane on either medium now asks Hardcover for books by title and author,
not for editions, so it no longer produces an edition-overflow witness. Only
an ISBN lane can still overflow, and qualification drops its witness, so no
witness reaches a report from either lane.

A book whose `canonical_id` names another book in the same answer is folded
into that book before the candidate limit is applied. The fold is one hop: the
target must carry no `canonical_id` of its own and must pass work-identity
admission on its own values. A book pointing at itself, at a book outside the
answer, or at a target that fails either test stands as its own candidate. The
fold only removes a book; it never lends one book's values to another.

A witness-id edition fetch, which would read editions for an overflow witness,
was to be built only if replaying the #744 library found at least ten ISBN-lane
witnesses. The replay found none, so neither that fetch nor a re-check of a
stored witness is built. No witness identifier is sent to Hardcover.

The report advances to `metadata-report-v72` under ADR-0013.

A book an ebook ISBN or WORK lane accepts now offers its editions' covers
(#1778). Hardcover reads the book's edition list by its integer book id, at
most 2 pages of 100 rows for at most 2 books an observation, and only when the
library lists languages. A compilation's editions are never read. An edition
row feeds a cover descriptor only, never a candidate, an identifier or a
field. Its cover is a `listed_edition` source, bound by the book's work
identity alone because an edition list says nothing that would bind it to the
item's edition. Cover identity keeps it `unknown` and records the work
identity level ADR-0096 would admit it at, until that record's activation. The
reviewer packet shows it as a related edition. Decision 3 of
[ADR-0090](0090-hardcover-audio-edition-witness.md), that an ebook sends no
edition request, is about the audio-edition document only. The edition-covers
document is a separate read with its own bounds.

That change is `metadata-report-v76` under ADR-0013.
