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
repository implements plan/control/audit persistence. A deterministic fake is
the only target implementation. The authenticated web edge may call the same
application service; no apply CLI is introduced.

Backup readiness, capability, current authority, dependency freshness, target
pre-state, circuit state, then atomic pause/concurrency claim are checked in
that order and refreshed at the last feasible point before claim. The target
adapter must independently compare both captured pre-value and target version
atomically with its write. These checks do not claim a transaction across the
repository and external systems; later drift is detected by target conflict or
readback and reconciled forward. Claimed work is never revoked mid-effect.
Ambiguous responses enter
durable reconciliation: desired completes, the captured pre-state may requeue
after renewed gates, and a third value stales the plan for forward evaluation.
Verified siblings remain complete.

Cover recovery bytes use a separate owner-only bounded private store. They are
never placed in SQLite documents, logs, diagnostics, fixtures, APIs, exports,
or support material, and are removed after verified completion or audited
abandonment.

## Consequences

The database schema remains pre-release exact-current: only absent or this
exact schema opens, with no migration. This increment can mutate only the fake
adapter. Audiobookshelf writes, media writes, clearing, rollback/Undo, batching,
and production use remain unavailable.
