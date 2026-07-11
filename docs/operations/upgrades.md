# Upgrades and Migrations

Upgrades must be predictable, reversible where data permits, and safe for unattended deployments. The container image is replaceable; `/data` is the durable product state.

## Version selection

Production examples must pin a released version or immutable image digest. A floating `latest` tag may exist for convenience, but it is not a recommended production configuration.

Automatic update checks may report that a release exists. The application must not replace its own container or silently perform a major-version upgrade.

## Upgrade contract

Every release that changes durable state must publish:

- the source and target schema versions;
- preconditions and estimated resource needs;
- whether the migration is reversible;
- expected downtime or degraded behavior;
- backup requirements;
- validation steps; and
- rollback limits.

Breaking configuration, API, or adapter changes follow the compatibility and deprecation policy.

## Preflight

Before an upgrade, the operator or deployment automation should:

1. Read the release notes and compatibility matrix.
2. Confirm the target service and host remain supported.
3. Wait for active mutations to finish or cancel them safely.
4. Verify the current database is healthy.
5. Create and validate an application-state backup.
6. Record the running image digest and schema version.
7. Confirm sufficient free disk space for the new image, migration, and rollback copy.

The application must refuse a migration when its preconditions are not met.

## Migration behavior

- Schema migrations run under an application-wide migration lock.
- Normal workers and mutation endpoints remain unavailable until migration completes.
- Each migration is versioned, deterministic, and tested from every supported starting version.
- Destructive transformations require a protected pre-migration backup.
- A failed migration leaves an explicit recovery state; it must not repeatedly retry on every restart without a bounded policy.
- Migration logs contain versions and step identifiers but no secrets or private content.

Before applying any migration, startup verifies that the recorded migration
versions, checksums, and managed schema objects form one known contiguous
prefix. It refuses newer, tampered, partial, or unrecognized state without
deleting or rewriting it. Operators must inspect that state and restore a
verified backup when the documented recovery procedure requires it; ordinary
startup does not attempt an unbounded repair loop.

## Verification

After upgrade:

- confirm readiness and liveness separately;
- confirm the expected application and schema versions;
- run database integrity checks;
- verify configured connections without mutating them;
- inspect failed, leased, and pending jobs;
- run a read-only discovery smoke test; and
- perform a guarded mutation canary only when the release affects apply behavior.

## Rollback

Container rollback and data rollback are distinct:

- If no forward-only migration ran, restore the previous image and verify readiness.
- If a reversible migration ran, use its tested down path only when the release instructions allow it.
- If a forward-only migration ran, stop the new version, restore the pre-upgrade application-state backup, and start the previous image.

Never start an older binary against a newer schema unless that exact pairing is documented as supported.

Restoring Mechanical Librarian state does not undo changes already applied to Audiobookshelf. Those require the affected apply plan's target rollback workflow.

## Interrupted upgrades

Power loss or forced termination during migration must be covered by automated fault-injection tests. Recovery behavior should be one of:

- transaction rollback to the previous schema;
- safe resumption from a durable checkpoint; or
- explicit stop with instructions to restore the pre-migration backup.

Ambiguous partial completion is unacceptable.

## Schema 1 to 2: audit timestamp index

Schema 2 is a forward-only application-state migration from schema 1. It
rebuilds `audit_events`, normalizes every supported legacy timestamp to
fixed-width UTC microseconds, and creates the managed chronological index.

- Preconditions: schema 1 must validate exactly, no application work may be
  active, and every stored audit timestamp must use a supported UTC or numeric
  offset spelling. An invalid timestamp stops the upgrade without changing the
  schema-1 database.
- Resources and downtime: migration holds the exclusive database migration
  lock, scans and copies the audit table once, and requires temporary disk space
  proportional to that table. Workers and mutation endpoints remain unavailable
  until the transaction commits.
- Backup: create and validate a curator application-state backup before
  starting. The backup is required even though a failed migration rolls its own
  transaction back atomically.
- Validation: confirm schema versions 1 and 2 are recorded, verify
  `audit_events_by_created_at` exists, run the database integrity check, and
  exercise a bounded read-only recent-audit query before enabling workers.
- Rollback: there is no down migration. An older binary rejects schema 2. To
  roll back after a successful upgrade, stop the new container, restore the
  pre-upgrade application-state backup, then start the previous image and verify
  schema 1. Restoring curator state does not reverse changes already applied to
  an external library service.

## Major versions

A major-version upgrade should support a documented rehearsal using a restored copy of `/data`. When a direct upgrade path is not provided, the project must document the required intermediate version or export/import path.
