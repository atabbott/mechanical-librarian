# Upgrades and compatibility

Mechanical Librarian remains pre-release. Ordinary startup accepts only absent
state or the exact current state and never silently rewrites an older schema.
The reviewer-access authority, `setup-state-v2`, `owner-settings-v2`,
`cover-consideration-v3`, `metadata-report-v32`, and the added application
tables change the current SQLite objects in this build. Every earlier
application database therefore requires a clean rebuild; no adoption,
migration, conversion, import, repair, or downgrade is supplied by #872, #873,
or #749.

`metadata-report-v31` in particular cannot carry an audiobook's provider
evidence: it refuses a provider observation on an audio report outright, and
has no ASIN query lane, marketplace provenance, chapter-structure evidence, or
recognized-sidecar manifest.

The three earlier exact-predecessor plans have been retired because each names
a schema older than this build. The installed runtime no longer exposes a
`schema-adopt` command. Retain an older database and its matching artifact
directory offline if it has historical value, then start this build with an
absent application database and artifact store and repeat first-run setup.

Maintainers have one separate qualification-only command:
`qualification-state-bridge retained-library-v26-to-v31`. It accepts only the
exact retained #744 test schema, an existing complete owner-only clone, an
independent backup path, and an absent output path under the same private
directory. It leaves the source untouched, verifies the backup, rebuilds a
fresh exact-current report database through current derivation, and
invalidates observations that lack current work-dispatch provenance plus all
downstream state. Artifact-bearing cover references are also invalidated
because this command accepts no artifact path. It makes no network, provider,
reviewer, credential, media, or target-service request and is not a supported
upgrade for an installation.

Current-schema restart, crash recovery, and replay remain supported. Never pair
a current database with an older or unrelated artifact directory: current
recovery may remove unreferenced digest-named artifacts. The independently
owned session database accepts only absent or exact-current state; deleting it
is its clean rebuild and signs the owner out.

Post-release migration and restore policy remains deferred to #57. Nothing in
this pre-release rebuild contract grants generic migration, retention, pruning,
purge, or repair authority.
