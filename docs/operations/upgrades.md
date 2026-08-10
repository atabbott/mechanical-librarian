# Upgrades and compatibility

Until the first production release, every database, report, artifact,
configuration, and internal-document schema recognizes only absent state or its
exact current state. Earlier state is rejected fail-closed and never silently
deleted. No migration, import, conversion, preservation, compatibility parser,
downgrade, or repair is supported. Exact-ID and current-scope replay apply only
to reports stored in the exact current schema; restart, replay, and crash safety
remain mandatory.

To adopt an increment, retain an older development database and its matching
artifact directory offline together as one historical state set. Supply an
absent database file path and a separately created, empty artifact-store
directory that is not the retained old directory, then let authenticated review
preparation regenerate supported current evidence. Never point a
current database at an older or unrelated artifact directory: exact-current
crash recovery removes unreferenced 64-hex artifact files, so that reuse is
destructive and unsupported. Pointing a current binary at an older or
intermediate database fails closed and does not rewrite it.

Retain older development state only as an offline historical artifact, then use
an absent path and rerun the supported current operation. Post-release migration
and restore policy requires a named present user need and a separate decision;
it grants no pre-release repair, import, downgrade, retention, pruning, purge,
or general migration authority.
