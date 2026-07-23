# Backups and recovery

The 0.4 command performs no product or media writes. Its only durable state is
the append-only report database and current pointers.

For important report history, stop report invocations and copy the complete
SQLite file using a SQLite-supported consistent backup method. Verify the copy
with SQLite integrity and foreign-key checks before relying on it. Configuration
and secret files follow the host's encrypted secret-backup policy and must not
be embedded in the report database.

There is no 0.4 repair, import, selective restore, migration, pruning, or purge
command. If the database is corrupt or its schema is not the exact clean 0.4
baseline, stop and restore a verified complete copy or start with a new absent
database path. Never edit rows or pointers directly to force recovery.

Restoring a report database cannot change Audiobookshelf or media because this
runtime has no such write authority.
