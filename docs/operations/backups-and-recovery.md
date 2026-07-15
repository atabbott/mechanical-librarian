# Backups and Recovery

## Principle

Unattended writes are disabled until the application can prove that required
backups exist and that the post-write result can be verified.

## Backup classes

### Item pre-state

Before each production apply, retain:

- current normalized item JSON;
- current cover bytes or an explicit no-cover fact;
- item fingerprint;
- service and adapter versions;
- intended field changes and decision provenance.

This is the primary source for targeted rollback.

### Service application backup

Before a write batch, create or verify a recent supported application backup.
For Audiobookshelf, prefer its supported backup surface when compatible. Never
rely on an inconsistent raw database file copy as the only recovery path.

### Curator data backup

Back up the `/data` volume, including SQLite, configuration, audit history,
reviewer authentication where policy permits, and non-reconstructable
artifacts. Reviewer credentials require encrypted storage and stricter access.

Migration 3 is forward-only. Before an F03 upgrade of important state, retain a
verified pre-F03 curator-data copy. If migration or qualification fails, stop
the newer process and restore that copy; do not start an older binary against
the newer schema. Transaction interruption during migration or inventory
publication leaves the previous committed schema and current observation
usable and is covered by deterministic fault-injection tests.

Before the forward-only F10a schema-7 migration of important state, retain and
verify a pre-schema-7 curator-data copy. Migration interruption rolls back to
the complete schema-6 prefix, and F10a publication failure preserves both the
already-committed F09a proposal input and the prior authoritative case state.
Downgrade is restore-only: stop the newer process, restore the verified copy,
and never delete the new tables or start an older binary against schema 7.
After restore, verify integrity with `state-check`, replay the applicable F09a
proposal scenario, and inspect it with `synthetic-title-review-case-report`
before resuming report-only operation.

### Host snapshots

Filesystem or ZFS snapshots are defense in depth. Document dataset coverage,
retention, integrity checks, and how to restore without starting the curator
against a half-restored service.

### Offsite backup

Encrypted offsite backup of service configuration, curator state, and metadata
artifacts is recommended. Full media backup is valuable but is not a
prerequisite for application-level metadata changes when item pre-state and
service metadata are recoverable.

## Suggested retention baseline

- Item pre-state: retain with apply history while the item exists, subject to
  privacy and storage policy.
- Batch application backups: retain enough to cover recent releases and
  detection delay.
- Host snapshots: frequent short-term, daily medium-term, and weekly long-term
  tiers.
- Audit events: durable; compact large reconstructable artifacts separately.

Exact defaults must be configuration with documented minimum safety bounds.

## Backup readiness

A backup is ready only when:

- creation completed successfully;
- expected files or service records exist;
- size and format are plausible;
- integrity or restore validation appropriate to the backup type passes;
- retention will not delete it before the guarded operation completes;
- the curator has recorded an opaque backup reference.

## Rollback

Automatic rollback is permitted only when:

- the failure is detected immediately;
- current state still matches the failed apply's observed post-state;
- restoring the item pre-state is supported and non-destructive;
- rollback itself can be verified.

Otherwise, halt affected writes and create a recovery case. Never overwrite a
newer user or service change to force rollback.

## Disaster recovery drill

A release is not operationally mature until documentation proves how to:

1. Stop or disable writes.
2. Restore the curator data volume.
3. Restore or reconnect the library service.
4. Re-establish reviewer authentication without exposing tokens.
5. Reconcile current items without replaying successful writes.
6. Validate audit continuity and backup references.
7. Re-enable report-only work before production writes.
