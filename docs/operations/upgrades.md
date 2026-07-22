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

Workflow-definition changes are classified independently from database schema
changes. Release notes identify the old and new workflow versions and graph
digests, newly required stages, compatible resumptions, and transitive
reuse and current-applicability boundary. An incompatible change never rewrites
a completed run's graph or outcomes; the application creates auditable linked
superseding or replanned work, marks transitively affected downstream
checkpoints there ineligible for reuse or current applicability, and preserves
immutable history.

## Preflight

Before an upgrade, the operator or deployment automation should:

1. Read the release notes and compatibility matrix.
2. Confirm the target service and host remain supported.
3. Wait for active mutations to finish or cancel them safely.
4. Verify the current database is healthy.
5. Create and validate an application-state backup.
6. Record the running image digest and schema version.
7. Confirm sufficient free disk space for the new image, migration, and rollback copy.
8. Inspect active workflow-definition versions and confirm how incompatible
   runs will be superseded, replanned, cancelled, or allowed to finish.

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

The F02 durable-job migration is additive and forward-only. It supports empty
state and the genuine F01 schema as starting points and is exercised by the
[synthetic job heartbeat](job-heartbeat.md). Downgrade requires restoring a
verified pre-F02 application-state copy; an F01 binary must not open F02 state.
The migration includes append-only workflow transition facts so recovery can
retain the validated old state, new state, actor, reason, run attribution, and
timestamp instead of inferring history from the current row alone.

The F03 inventory migration is likewise additive and forward-only from genuine
F02 state. It adds immutable canonical snapshot history, an exact scoped current
observation pointer, append-only history triggers, and a target-scoped index.
Fault injection after the first F03 object proves the whole migration rolls
back to usable F02 state while preserving instance, audit, workflow, stage,
job, attempt, checkpoint, and transition data. A clean retry applies migration
3 once. Downgrade requires restoring a verified pre-F03 copy; an F02 binary
must not open F03 state.

The F05a inventory-v2 migration is additive and forward-only from genuine F03
state. Migration 4 leaves every `snapshot-v1` history row and current pointer
unchanged, then adds immutable inventory-v2 history, current pointers, durable
scan state, and scan membership. Fault injection after its first object proves
the entire migration and version row roll back, the v1 reader remains usable,
and a clean retry applies migration 4 exactly once. Downgrade requires stopping
the service and restoring a verified pre-F05a backup; operators must not delete
v2 tables or edit `schema_migrations`. After upgrade or restore, run
`state-check`, a v1 synthetic inventory readback, and `inventory-inspect` before
resuming normal operation.

The F09a proposal-preview migration is additive and forward-only from genuine
schema 5. Migration 6 adds immutable `synthetic_preview` title-proposal history,
a separate synthetic current pointer, append-only history triggers, and a
lineage/revision index. It does not transform or update inventory, poll, job,
workflow, or audit rows. Migration and publication are independently atomic;
failure at history insertion, pointer advancement, or readback leaves no
partial proposal set. A clean retry applies migration 6 once and identical
scenario replay reuses history. Downgrade requires stopping the service and
restoring a verified pre-F09a schema-5 state copy; an older binary must not open
schema-6 state, and operators must not delete preview tables or edit
`schema_migrations`. After upgrade or restore, run `state-check` before
resuming normal operation.

The F10a review-case migration is additive and forward-only from genuine
schema 6. Migration 7 preserves all proposal, inventory, poll, job, workflow,
and audit rows and adds immutable synthetic review-case history, append-only
attributable lifecycle transitions, and one scoped current-or-retired pointer.
Migration and F10a admission are independently atomic. Injected interruption
after early, middle, and final schema-7 statements rolls every new object and
the version row back to usable schema 6; a clean retry applies migration 7
once. Case insert, transition insert, pointer advance, and readback failure
likewise roll the F10a transaction back without changing the already-committed
F09a input. Before upgrading important state, retain a verified pre-schema-7
copy. Downgrade requires stopping the service and restoring that copy; an older
binary must not open schema-7 state, and operators must not delete review tables
or edit `schema_migrations`. After upgrade or restore, run `state-check`
against non-production state.

The F07b evidence migration is additive and forward-only from every genuine
schema prefix through schema 7. Migration 8 adds immutable snapshot-file
bindings, durable EPUB work and attempts, immutable opening evidence, current
binding-set revisions and their current pointers, applicability, checkpoints,
and append-only events. It transforms no prior row;
legacy snapshots simply have no binding and emit no selector until a new
reconciliation. Migration interruption rolls all schema-8 objects and its
version row back together. Before upgrading important state, retain a verified
pre-schema-8 copy. Downgrade requires restoring that copy; never delete the new
tables or edit `schema_migrations`. After upgrade, run `state-check`,
`inventory-inspect`, and the retained report-only `live-title-proposal-report`
against isolated fixtures. EPUB opening extraction qualification remains an
internal fixture-only boundary, not an operator command.

The F09f durable-identity migration is additive and forward-only from every
genuine schema prefix through schema 8. Migration 9 adds bounded generations,
candidates, claims, attempts, retry schedules, append-only events, terminal
results, bounded observations, and the matched-current pointer. It transforms
no existing row. Interruption at early, middle, and final statements leaves the
complete schema-8 prefix and version ledger usable; a clean retry applies
migration 9 exactly once.

Before upgrading important state, stop relevant work and retain a verified
pre-schema-9 curator-data copy. Downgrade requires stopping the newer process
and restoring that whole copy. Never delete schema-9 objects or edit the
migration ledger. After upgrade, run `state-check`, inspect pending/claimed work
while stopped, and execute `durable-work-v3` using a separate initially absent
database beneath an empty owner-owned mode-`0700` disposable directory. The
scenario proves the schema-8 sentinel and canonical snapshot before migration,
then migration, restart, recovery,
publication rollback, stale-fence rejection, and currentness without live
provider or production-database authority.

The F09g proposal migration is additive and forward-only from every genuine
schema prefix through schema 11. Migration 12 adds an `execution_mode`
discriminator to the existing proposal-history table. Existing rows retain
`synthetic_preview`; no canonical proposal, pointer, inventory, EPUB evidence,
or provider-work row is rewritten. Live publication inserts immutable
`production_report_only` history and advances the shared current pointer in one
transaction after final evidence revalidation. Before upgrading important
state, retain a verified pre-schema-12 copy. Downgrade requires stopping the
newer process and restoring that copy. After upgrade, run `state-check`, replay
one synthetic proposal, and run one isolated
[`live-title-proposal-report`](live-title-proposal-report.md) against report-only
state.

## Verification

After upgrade:

- confirm readiness and liveness separately;
- confirm the expected application and schema versions;
- run database integrity checks;
- verify configured connections without mutating them;
- inspect failed, leased, and pending jobs;
- inspect superseded and replanned runs, graph-digest linkage, and downstream
  checkpoint reuse and current-applicability outcomes;
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

## Major versions

A major-version upgrade should support a documented rehearsal using a restored copy of `/data`. When a direct upgrade path is not provided, the project must document the required intermediate version or export/import path.
