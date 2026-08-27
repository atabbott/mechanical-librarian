# Upgrades and compatibility

Until the first production release, ordinary startup recognizes only absent
state or exact current state. Earlier state is rejected fail-closed and never
silently changed. Each schema-changing issue and pull request declares a clean
rebuild or delivers one explicit exact-predecessor adoption limited to
reconstructible derived objects. This is not a general migration, import,
conversion, compatibility, downgrade, or repair facility.

`metadata-report-v26` cannot represent lane-bound provider diagnostics. It is
rejected at bootstrap and requires a clean rebuild as `metadata-report-v27`;
this increment provides no adoption or legacy fallback.

The terminal preparation-failure codes in this increment change the
exact-current review schema. The supplied compatibility paths are the closed
owner-actionability plan and the exact owner-settings predecessor below.

The `fixed-conservative-v9` proposal contract is not an exact predecessor of
the installed v10 contract: its persisted decisions can disagree. It is rejected
at bootstrap and requires a clean rebuild.

The `fixed-conservative-v11` proposal contract is likewise not an exact
predecessor of the installed v12 contract: v12 elects locally attested
subtitles that v11 left unresolved, so persisted proposals can disagree. The
shadow decision policy moved to `deterministic-shadow-decision-policy-v9` in
the same increment: the subtitle destination's shadow outcome is now owned by
the qualified subtitle category whenever it routes to Automatic and no
title-combination candidate acts. A v11
database is rejected at bootstrap and requires a clean rebuild. The
`review-owner-decision-actions-v1` adoption plan below now validates only a
predecessor already carrying the v12 proposals contract; a pre-#830 database
still on v11 cannot adopt into this build and also requires a clean rebuild.

The pre-release review-preparation claim index now orders each library's queued
cohort by its persisted discovery ordinal. The #826 immediate predecessor
already contains the field-fact objects and still carries the historical
`updated_at` claim index. That specific shape is rejected at bootstrap, requires
a clean rebuild, and is not adopted. The index remains reconstructible derived
state under ADR-0013, but this increment delivers no exact-predecessor adoption
plan for that immediate predecessor.

## Offline review-session decoupling adoption

#912 (ADR-0059) moved `review_sessions` and `review_login_failures` into the
dedicated session database `private/review-sessions.sqlite`, which ordinary
startup creates on its own. `review-session-decoupling-v1` accepts only the
exact immediately preceding schema and drops those two application-database
tables, discarding at most a few live sessions (one owner re-login) and five
minutes of throttle history; every other table is preserved and
digest-verified. The two older plans below now also drop the session tables,
so their predecessors still adopt straight to the current target. Run it
offline with an owner-only backup in the same directory:

```console
mechanical-librarian schema-adopt review-session-decoupling-v1 \
  --database /data/application.sqlite3 \
  --backup /data/application.before-session-decoupling.sqlite3
```

## Offline owner-actionability adoption

`review-owner-decision-actions-v1` accepts only the exact pre-#830 schema and
rebuilds only `review_case_field_facts` and its insert trigger from immutable
ReviewCase documents. It preserves canonical and all other operational and
derived rows. Run it only offline against a complete clone with an owner-only
backup in the same directory:

```console
mechanical-librarian schema-adopt review-owner-decision-actions-v1 \
  --database /data/application.sqlite3 \
  --backup /data/application.before-owner-decision-actions.sqlite3
```

Keep both clone and backup until the new build has started and its measured
behavior is accepted.

## Offline owner-settings adoption

`owner-settings-v1` accepts the exact 0.17.0 predecessor lacking only the
durable first-preparation acknowledgement, preserves its seven control values,
and initializes that acknowledgement to false. It also retains the existing
exact predecessor that lacks the owner-settings tables. Run it offline with an
owner-only backup in the same directory:

```console
mechanical-librarian schema-adopt owner-settings-v1 \
  --database /data/application.sqlite3 \
  --backup /data/application.before-owner-settings.sqlite3
```

The command reports `Upgrade Passed` only after exact-schema validation,
canonical-row equivalence, backup read-back, and source identity checks pass.

`Schema adoption requires backup recovery.` means the source may have committed;
restore the named verified backup to a fresh clone before retrying. The ordinary
safe-failure message means no commit was attempted.

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
