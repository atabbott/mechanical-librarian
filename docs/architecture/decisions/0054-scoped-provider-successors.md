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
callback. It also asserts the closed source package identity (report, proposal,
case, checkpoint, and outcome) inside that transaction,
while the final target/media/cover currentness check remains immediately before
it. This keeps provider and target work outside the SQLite writer lock, while
dispatch failure, source drift, and a concurrent package race leave the
predecessor package current.

Immediately before constructing the provider client, the seam repeats the full
source/package currentness and binding-identity checks. It binds a production
adapter's authority-less observation to the configured authority, gives the
single retry observation a 60-second monotonic aggregate deadline, and combines
selected and retained evidence without discarding bounded diagnostics, request
accounting, or a valid edition-overflow witness.
If a retained overflow witness would become invalid after a sibling retry, or a
replacement lane is malformed, it fails closed before publication.

F24 activation composes this seam behind the existing preparation supervisor
and its explicit owner CLI invocation. Neither path is an endpoint, bulk
operation, or target-metadata write caller.

## Consequences

The operation is intentionally not independently restartable or executable in
normal deployment: activation must supply durable claiming and scheduling. No
schema or recovery persistence is added here.

## Validation

Focused tests prove selected-lane-only dispatch, successful-sibling rejection,
and byte-for-byte sibling-lane preservation. Existing report publication tests
continue to prove expected-current atomicity and package cutover.
