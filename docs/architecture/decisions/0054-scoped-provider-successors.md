# ADR-0054: Scoped provider retry successors

- Status: accepted
- Date: 2026-08-14
- Owners: project maintainers

## Context

F24 needs a safe way to turn one exact-current report's retry-eligible provider
lanes into immutable successor evidence without allowing a retry scheduler or
silently replacing the complete package currently shown to an owner.

## Decision

An internal application seam accepts an exact source report, the current
provider connection/adapter/generation identity, and an explicit non-empty set
of failed lane identities. It revalidates the source target and file, resolves
the current persisted source report, reconstructs the same discovery lanes,
and dispatches only `rate_limited`, `unavailable`, or `incomplete` lanes.

The replacement observation is merged with untouched sibling lane data. Exact
candidate identity duplicates coalesce; conflicting evidence is rejected by the
existing bounded observation constructor. Publication uses the existing
expected-current report transaction and optional review-package publication
callback, so dispatch failure, source drift, and a concurrent currentness race
leave the predecessor package current.

The seam has no composition, scheduler, CLI, endpoint, recovery claim, canary,
or production write caller in this constituent. F24 activation owns that
authority.

## Consequences

The successor operation is restart-safe through immutable report publication,
but it is not independently executable in normal deployment. No schema or
recovery persistence is added.

## Validation

Focused tests prove selected-lane-only dispatch, successful-sibling rejection,
and byte-for-byte sibling-lane preservation. Existing report publication tests
continue to prove expected-current atomicity and package cutover.
