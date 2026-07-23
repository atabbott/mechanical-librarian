# Upgrades and compatibility

0.4 is a deliberate pre-deployment clean-state reset. It recognizes neither
pre-0.4 development schemas nor their configuration or commands. The single
exception is the exact pre-release metadata-report-v1 two-table schema: it is
atomically widened to the exact v2 schema on the verified writable connection.
The accepted predecessor has only the report table, current-pointer table, and
the two append-only triggers; it must contain canonical v1 documents whose
envelopes agree with their rows. The widening preserves those historical bytes
and current pointers, then permits v2 successors. Extra tables or triggers,
altered schema SQL, malformed v1 content, and failed validation are rejected
without a partial state.

To adopt this checkpoint, retain any older development database only as an
offline historical artifact and supply an absent path for the new report
database. Do not point the 0.4 binary at an older database: it fails closed and
does not rewrite it.

Any future schema change must publish its own source/target versions, backup
requirements, validation steps, and rollback limits. That future obligation
does not create a migration promise for the retired development schemas.
It also grants no repair, import, downgrade, retention, pruning, purge, or
general migration authority.
