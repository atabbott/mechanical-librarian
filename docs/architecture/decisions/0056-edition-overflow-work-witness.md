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
