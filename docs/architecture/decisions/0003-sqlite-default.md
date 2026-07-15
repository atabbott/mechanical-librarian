# ADR-0003: Use SQLite as the Default State Store

- Status: superseded
- Date: 2026-07-10
- Owners: project maintainers
- Supersedes: none
- Superseded by: ADR-0009

## Context

The normal installation has one application container, one owner, serialized
production writes, and modest job volume. Requiring an external database would
materially increase deployment complexity.

The product still needs durable jobs, migrations, audit history, concurrent
dashboard reads, crash recovery, and transactional state transitions.

## Decision

Use SQLite in WAL mode as the default state store. All schema changes use
forward migrations with explicit compatibility and rollback documentation.

Database access is behind repository and transaction interfaces owned by the
application. Domain code does not import SQLite or ORM types.

## Consequences

### Positive

- One-volume backup and restore.
- Transactional durable jobs without a queue service.
- Excellent fit for the expected workload.
- Simple local and integration testing.

### Negative

- Write concurrency must remain bounded.
- Network filesystem placement may be unsafe or poorly performing.
- Horizontal scaling needs a future storage implementation.

### Neutral or deferred

- PostgreSQL may become an optional adapter if real deployments require it.

## Validation

- Crash-recovery tests cover job leases and transactions.
- Migrations run against every supported prior schema in CI.
- The application refuses unsupported or unsafe database locations when they
  cannot provide required locking semantics.
