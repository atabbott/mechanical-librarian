# Upgrades and compatibility

0.4 is a deliberate pre-deployment clean-state reset. Every 0.5 increment keeps
that boundary: it recognizes only an absent database or its own exact current
schema. `metadata-report-v1`, prior 0.5 increment schemas, and all earlier
development schemas are rejected fail-closed before mutation. No migration,
import, preservation, compatibility parser, downgrade, or repair is supported.
Exact-ID and current-scope replay apply only to reports stored in the exact
current schema.

To adopt an increment, retain any older development database only as an offline
historical artifact, supply an absent path for the current report database, and
rerun the supported report command. Pointing a current binary at an older or
intermediate database fails closed and does not rewrite it.

Any future schema change must publish its own source/target versions, backup
requirements, validation steps, and rollback limits. That future obligation
does not create a migration promise for the retired development schemas.
It also grants no repair, import, downgrade, retention, pruning, purge, or
general migration authority.
