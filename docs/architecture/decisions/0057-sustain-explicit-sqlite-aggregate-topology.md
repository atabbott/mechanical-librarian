# ADR-0057: Sustain the explicit SQLite aggregate topology

- Status: accepted
- Date: 2026-08-20
- Owners: project maintainers

## Context

The pre-release exact schema has accumulated canonical documents, current
pointers, audit histories, recovery state, security records, and controls as
the review pipeline gained durable safety boundaries. The raw object count is
not a design measure: a smaller table count could erase an immutable authority
boundary, make a current read reconstruct a document, or turn separate state
machines into one type-switched table.

Issue #804 reviewed every exact schema object and its production caller against
a sanitized aggregate retained-library snapshot. The resulting responsibility
map is [`../sqlite-topology.md`](../sqlite-topology.md). It finds the largest
retained objects are immutable report and cover-consideration documents; the
current pointers and #802 derived browse facts are materially smaller and have
specific current consumers.

## Decision

Keep the present explicit aggregate topology. Canonical immutable documents,
append-only audit history, current pointers, transactional derived browse facts,
operational/recovery state, security/session state, and control state remain
separate where they have distinct authority, key scope, lifecycle, or restart
behaviour.

In particular:

- The two report-current pointers remain separate because file-exact currentness
  and target-lineage currentness use different keys.
- Transition history, lineage pointer, and browse package remain separate;
  history proves the change, the lineage guards currentness, and the package
  represents report-only as well as review states.
- #802 field facts remain a same-transaction, open-time-verified relational
  projection of immutable documents. They are not a cache or alternate source
  of authority.
- Provider recovery outbox, scan/work/absence/connection state, and guarded
  effect state remain separate state machines rather than a generic event or
  polymorphic row.

The exact object contract in `schema.py` remains the sole executable schema
authority. ADR-0013 continues to require absent or exact-current state during
ordinary startup; this decision adds no migration, adoption, rebuild worker,
cache, connection-pool, WAL, or schema change.

## Consequences

- No consolidation follow-up is created from this audit. The absence of a
  safe consolidation is an accepted result, not deferred churn.
- Future topology changes must demonstrate fewer persisted concepts and less
  total ownership/plumbing, not merely fewer tables. They must identify the
  current consumer removed or simplified and prove transaction rollback,
  restart, idempotency, optimistic currentness, audit retention, query-result
  equivalence, and exact-schema rejection.
- A future schema-changing proposal remains independently subject to ADR-0013
  and must declare clean rebuild or a same-change exact-predecessor derived
  adoption only where that rule permits it.
