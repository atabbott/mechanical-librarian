# ADR-0037: Origin-neutral guarded field effects

Status: Accepted

## Context

Mechanical Librarian already records immutable owner decisions and sealed,
qualified automatic-policy decisions. Neither fact is write authority by
itself. The first mutation boundary must prove safe planning, claim, effect,
readback, ambiguity handling, restart, and audit without constructing an
Audiobookshelf writer or treating advice and previews as authority.

## Decision

Use one field-level plan and persisted state machine for both decision origins.
The plan deterministically binds connection, library, work, closed destination,
captured pre-value, desired value, target version, evidence dependencies,
decision and policy/qualification identities, capability observation, and
idempotency identity. Series name and sequence are one compound destination.

The application layer owns target, backup, capability, currentness, dependency,
cover-recovery, and repository ports. The existing exact-current SQLite
repository implements plan/control/audit persistence. The supported
Audiobookshelf target adapter uses only API-key-authenticated item, media and
cover APIs. It reads a cover original only through the immutable,
digest-verifying cover-artifact store; old destination cover bytes remain in
the separate recovery-only store. The authenticated web edge may call the same
application service; no apply CLI is introduced.

Planning resolves the exact persisted decision and current review-case binding;
caller coordinates cannot change its destination, work, target fingerprint, or
target version. Connection and library names use the local configuration-name
vocabulary; work identifiers and target versions are separately bounded opaque
service tokens. A separate bounded consent fact binds the connection, library,
destination, and `apply_field` action. Backup readiness, capability, current authority, dependency freshness, target
pre-state, circuit state, then atomic pause/concurrency claim are checked in
that order and refreshed at the last feasible point before claim. Audiobookshelf
does not expose a supported value-and-version compare-and-set operation. The
adapter therefore performs exactly one destination-minimal request after the
authoritative pre-read and never blindly retries an ambiguous response; it then
performs authoritative read-back. This is not CAS: a same-field actor can write
between that final read and Audiobookshelf accepting our request, and our write
can overwrite that actor's value. Read-back can only detect later disagreement.
Writes to other fields remain outside that field-local lost-update window.
Repository-held authority, consent, pause, and
claim state share one `BEGIN IMMEDIATE` transaction; this is the supported
atomic boundary. For persisted decision authority, that transaction also
rechecks the complete current review case, metadata-report lineage, file-specific
report pointer, decision row, target fingerprint, and automatic safety posture.
Claimed work is never revoked mid-effect. Every transition into reconciliation,
including accepted-write readback mismatch and applying-state restart, atomically
schedules one durable targeted-read action: desired completes, the captured pre-state may requeue
after renewed gates, and a third value stales the plan for forward evaluation.
Verified siblings remain complete.

Capability results use closed application-owned categories. Authentication,
inactive-account, or basic library/item access failure records a durable
connection suspension. Insufficient permission, an incompatible destination
shape, or unexpected effect behavior records a durable destination suspension.
The claim transaction checks both scopes after acquiring its write lock. This
keeps a broken field from disabling unrelated writes while ensuring a raced
suspension wins admission. The append-only event ledger is exposed through a
bounded repository seam that validates canonical documents and gap-free
sequence before qualification hashes it; reconstructed operation summaries are
not audit proof.

One bounded restart runner enumerates only pending or interrupted targeted-read
and forward-reevaluation actions, claims each action, dispatches through those
two existing seams, and completes it only after success. The same restart entry
point separately enumerates pending cover-cleanup obligations.
Authenticated field-effect calls hold the same serialized repository lock as all
other web review services, so one cross-thread SQLite connection is never entered
concurrently through a raw application-service reference.

Cover recovery bytes use a separate owner-only bounded private store. Capture
uses an owner-mode temporary file, file sync, atomic rename, and directory sync.
Every terminal cover path records a retryable cleanup obligation. The bytes are
never placed in SQLite documents, logs, diagnostics, fixtures, APIs, exports,
or support material.

## Consequences

The database schema remains pre-release exact-current: only absent or this
exact schema opens, with no migration. The canary adapter is not composed into
ordinary continuous activation. Clearing, rollback/Undo, batching, embedded
media writes, and production-library use remain unavailable.
