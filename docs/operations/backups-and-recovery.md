# Backups and recovery

The 0.10 runtime performs no target or media writes. Its durable application
state is the append-only report/ProposalSet/ReviewCase/shadow-review database,
current pointers, and report-bound cover artifact store.

For important history, stop all invocations. Within that stopped consistency
boundary, make a SQLite-supported consistent backup of the complete application
database and copy its paired artifact-store directory. The database references
the artifact identities that remain current for its reports, so restore the
matching database and artifact-store pair together. This is not a transactional
cross-filesystem backup guarantee; stopped invocations are the consistency
boundary. Verify the database copy with SQLite integrity and foreign-key checks
before relying on it.

The separate catalogue database and immutable batch manifests are regenerable
from current configuration and supported catalogue/batch operations; preserve
them only when an operator needs their historical state. Configuration,
qualification profiles, executable paths, and reviewer/target/provider auth
stores follow the host's encrypted secret-backup policy and must not be
embedded in the application database.

Backups expose bounded target metadata, cited evidence, sanitized shadow
packets, validated advice, and execution facts to anyone who can read them.
Use owner-scoped access and encrypted-at-rest storage, define an operator
retention period for complete backups, and destroy expired complete copies
through the storage system.

There is no 0.10 per-record purge, repair, import, selective restore, migration,
pruning, or purge command. If the application database or paired artifact store
is corrupt, missing, or not exact-current, stop and restore a verified matching
pair or start with a new absent database path and empty artifact store, then
regenerate supported reports. Never edit rows, pointers, or artifact files
directly to force recovery.

Restoring the matching application database and artifact store cannot change
Audiobookshelf or media because this runtime has no such write authority.
