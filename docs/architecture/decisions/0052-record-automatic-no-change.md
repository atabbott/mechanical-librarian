# ADR-0052: Record automatic no-change outcomes

- Status: accepted
- Date: 2026-08-14
- Owners: project maintainers

## Context

An exact-current automatic candidate can equal its persisted target. It is a
useful terminal result, but creating a guarded field-effect plan would imply
write authority and would unnecessarily consult consent, capability, backup,
and target services.

## Decision

Persist one immutable, content-derived `field_effect_no_changes` fact before
the guarded-write path. Its `WITHOUT ROWID` ledger stores only identifiers,
currentness binding, origin, timestamp, and deterministic event identity. The
same transaction resolves the current persisted decision, case, report, and
target. The table is exact-current under ADR-0013; no migration is supplied.

## Consequences

Equal automatic candidates leave no plan, authority, consent, capability,
backup, target, or read-back record. They are excluded from pending work and
appear in Activity as `no_change_required`. Unequal candidates retain the
existing guarded path.
