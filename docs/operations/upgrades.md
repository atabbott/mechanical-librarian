# Upgrades and compatibility

Until the first production release, ordinary startup recognizes only absent
state or exact current state. Earlier state is rejected fail-closed and never
silently changed. Each schema-changing issue and pull request declares a clean
rebuild or delivers one explicit exact-predecessor adoption limited to
reconstructible derived objects. This is not a general migration, import,
conversion, compatibility, downgrade, or repair facility.

The `fixed-conservative-v9` proposal contract is not an exact predecessor of
the installed v10 contract: its persisted decisions can disagree. It is rejected
at bootstrap and requires a clean rebuild; `review-field-facts-v1` cannot adopt
it.

The pre-release review-preparation claim index now orders each library's queued
cohort by its persisted discovery ordinal. The #826 immediate predecessor
already contains the field-fact objects and still carries the historical
`updated_at` claim index. That specific shape is rejected at bootstrap, requires
a clean rebuild, and is not adopted. The index remains reconstructible derived
state under ADR-0013, but this increment delivers no exact-predecessor adoption
plan for that immediate predecessor.

## Offline derived-schema adoption

Stop the container before adoption and retain the original volume as rollback.
Run a delivered plan only against a complete clone, with the database and new
backup in the same owner-only directory. The `review-field-facts-v1` command
accepts only its frozen v9 predecessor; it is not a v8 upgrade path:

```console
mechanical-librarian schema-adopt review-field-facts-v1 \
  --database /data/application.sqlite3 \
  --backup /data/application.before-review-field-facts-v1.sqlite3
```

The command accepts no SQL or configuration. It either verifies an already
current database without creating the requested backup, or validates the exact
predecessor, creates and verifies the non-overwriting `0600` backup, and adds
the two derived field-fact tables, their triggers, and measured index in one
transaction. From that frozen predecessor it also atomically replaces the
historical `updated_at` review-preparation claim index with the current ordinal
index. It reconstructs the facts from canonical ReviewCase and shadow-outcome
documents without changing a canonical row. The #826 immediate predecessor
already contains those field-fact objects and is rejected; it requires a clean
rebuild rather than this older plan. Any other schema,
sidecar, stale private binding, integrity failure, live database authority,
path replacement, backup collision, or backup-content change fails closed.
Keep both clone and backup until the new build has started and its measured
behavior is accepted.

`Schema adoption requires backup recovery.` means commit was attempted and the
source must not be trusted as predecessor or current; stop and restore the
named backup to a fresh clone before retrying. The ordinary safe-failure message
means no commit was attempted.

To adopt an increment, retain an older development database and its matching
artifact directory offline together as one historical state set. Supply an
absent database file path and a separately created, empty artifact-store
directory that is not the retained old directory, then let authenticated review
preparation regenerate supported current evidence. Never point a
current database at an older or unrelated artifact directory: exact-current
crash recovery removes unreferenced 64-hex artifact files, so that reuse is
destructive and unsupported. Pointing a current binary at an older or
intermediate database fails closed and does not rewrite it.

Retain older development state only as an offline historical artifact unless
its exact schema change delivered such a plan; otherwise use an absent path and
rerun the supported current operation. Post-release migration and restore policy
requires a separate decision and grants no pre-release repair, import,
downgrade, retention, pruning, purge, or general migration authority.
