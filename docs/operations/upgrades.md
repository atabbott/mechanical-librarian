# Upgrades and compatibility

Mechanical Librarian remains pre-release. Ordinary startup accepts only absent
state or the exact current state and never silently rewrites an older schema.
The reviewer-access authority, `setup-state-v2`, `owner-settings-v2`,
`cover-consideration-v3`, `metadata-report-v31`, and the added application
tables change the current SQLite objects in this build. Every earlier
application database therefore requires a clean rebuild; no adoption,
migration, conversion, import, repair, or downgrade is supplied by #872, #873,
or #749.

`metadata-report-v30` in particular cannot carry an audiobook's local evidence:
it has no structured audio section for embedded chapters and cover presence, no
duration or performance-type claim kind, and no audio evidence sources.

The three earlier exact-predecessor plans have been retired because each names
a schema older than this build. The installed runtime no longer exposes a
`schema-adopt` command. Retain an older database and its matching artifact
directory offline if it has historical value, then start this build with an
absent application database and artifact store and repeat first-run setup.

Current-schema restart, crash recovery, and replay remain supported. Never pair
a current database with an older or unrelated artifact directory: current
recovery may remove unreferenced digest-named artifacts. The independently
owned session database accepts only absent or exact-current state; deleting it
is its clean rebuild and signs the owner out.

Post-release migration and restore policy remains deferred to #57. Nothing in
this pre-release rebuild contract grants generic migration, retention, pruning,
purge, or repair authority.
