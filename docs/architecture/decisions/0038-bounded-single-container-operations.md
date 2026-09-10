# ADR-0038: Bound operational evidence inside the single container

Transport provisions are superseded by [ADR-0072](0072-explicit-local-transport.md);
all other provisions remain in force.


Status: accepted

## Context

Guarded field effects already persist their authoritative plan, claim,
verification, reconciliation and cleanup states. Continuous operation needs a
safe way to restart those states and explain degraded operation, but diagnostic
records must not become a second authority or leak owner library data.

## Decision

The supported runtime remains one non-root process in one container, with one
persistent `/data` volume, one HTTPS port and optional read-only media mounts.
Startup creates only absent exact-current stores or reopens the exact-current
schema; it adds no migration or older-state conversion.

Readiness starts only after exact-current state opens. Shutdown marks readiness
draining, stops admission of new pipeline claims, lets the current claim reach
its existing durable checkpoint, and then closes the repository. Restart uses
the existing persisted F13 states and actions, never process identity.

Operational status is a typed projection of closed cause, kind and narrow
scope codes. Shared causes aggregate; currentness conflicts retain their work
and field scope. Status and local timing are advisory. Timing is opt-in,
owner-private, capped at 256 records, and separate from SQLite audit truth.

Non-terminal cover pre-values use the existing atomic-file protocol in a
private recovery adapter, now with entry and byte bounds. Support preview and
export are authenticated, bounded to 64 KiB, use allow-listed producers, and
re-run redaction at export. They exclude credentials, values, paths, databases,
environment, media, cover bytes and the recovery store.

## Consequences

No notification service, monitoring platform, queue console, supervisor
product, extra process, second authoritative database, migration framework or
general rollback surface is introduced. Liveness remains process-only;
readiness and owner-authenticated status express recoverable degradation
without making routine work an alert.
