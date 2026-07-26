# Backups and recovery

The 0.10 runtime performs no target or media writes. Its durable application
state is the append-only report/ProposalSet/ReviewCase/shadow-review database
and current pointers.

For important history, stop invocations and copy the complete SQLite file using
a SQLite-supported consistent backup method. Verify the copy with SQLite
integrity and foreign-key checks before relying on it. Configuration,
qualification profiles, executable paths, and reviewer/target/provider auth
stores follow the host's encrypted secret-backup policy and must not be
embedded in the application database.

Backups expose bounded target metadata, cited evidence, sanitized shadow
packets, validated advice, and execution facts to anyone who can read them.
Use owner-scoped access and encrypted-at-rest storage, define an operator
retention period for complete backups, and destroy expired complete copies
through the storage system.

There is no 0.10 per-record purge, repair, import, selective restore, migration,
pruning, or purge command. If the database is corrupt or its schema is not the
exact clean 0.10 baseline, stop and restore a verified complete copy or start
with a new absent database path. Never edit rows or pointers directly to force
recovery.

Restoring the application database cannot change Audiobookshelf or media
because this runtime has no such write authority.
