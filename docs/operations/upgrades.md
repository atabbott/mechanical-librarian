# Upgrades and compatibility

Mechanical Librarian remains pre-release. Ordinary startup accepts only absent
state or the exact current state and never silently rewrites an older schema.
The reviewer-access authority, `setup-state-v3`, `owner-settings-v3`,
`cover-consideration-v3`, `metadata-report-v37`, and the added application
tables change the current SQLite objects in this build. Every earlier
application database therefore requires a clean rebuild; no adoption,
migration, conversion, import, repair, or downgrade is supplied by #872, #873,
#749, #956, #965, #896, or #888.

The exact predecessors for this change are `config-v7`, `metadata-report-v32`,
`owner-settings-v2`, and `setup-state-v2`. Inventaire changes all four closed
vocabularies, so no predecessor is parsed or adopted: supply a `config-v8`
document and rebuild an absent application database through first-run setup.
No migration is supplied.

#965 advances the report discriminator from `metadata-report-v34` to
`metadata-report-v35`: an audio report now carries its chapter-composition
kind, the ordered per-file track manifest each claim is attributed to, and the
closed cross-file observations, so a v34 report is not exact-current. Rebuild an
absent application database; no adoption is supplied. The predecessor #956
advanced v33 to v34 when the retained local-claim grammar admitted the
Audiobookshelf-asserted ASIN.

#896 advances the report discriminator from `metadata-report-v35` to
`metadata-report-v36`: the work-title transformation lineage becomes an ordered
compound production and carries its first non-null value, so a v35 report is not
exact-current. Rebuild an absent application database; no adoption is supplied.

#888 advances the report discriminator from `metadata-report-v36` to
`metadata-report-v37`: the closed Latin combining-mark recovery records exact
dispatch and field-application lineage, so a v36 report is not exact-current.
Rebuild an absent application database; no adoption is supplied.

#896 advances the exact proposal policy from `fixed-conservative-v18` to
`fixed-conservative-v19` because decoration-equivalent titles collapse into one
class and the tidy tranche adds a new persisted automatic-decision path.
Existing pre-release proposal, review, decision, and downstream field-effect
state is not rewritten or adopted; rebuild the application database.

F35 advances the exact proposal policy from `fixed-conservative-v13` to
`fixed-conservative-v14` because provider-consensus title election adds a new
persisted automatic-decision path. Existing proposal, review, decision, and
downstream field-effect state is not rewritten or adopted; rebuild the
pre-release application database.

F38 advances the exact proposal policy from `fixed-conservative-v14` to
`fixed-conservative-v15` because canonical contributor display can replace an
equivalent local form through provider consensus. Existing pre-release proposal,
review, decision, and downstream field-effect state is not rewritten or adopted;
rebuild the application database.

#897 advances the exact proposal policy from `fixed-conservative-v17` to
`fixed-conservative-v18` because an empty provider ISBN needs deterministic
identity evidence with a bound work identity before election. Existing pre-release
proposal, review, decision, and downstream field-effect state is not rewritten
or adopted; rebuild the application database.

`metadata-report-v31` in particular cannot carry an audiobook's provider
evidence: it refuses a provider observation on an audio report outright, and
has no ASIN query lane, marketplace provenance, chapter-structure evidence, or
recognized-sidecar manifest.

The three earlier exact-predecessor plans, including #830's owner-decision
action predecessor, have been retired because each names a schema older than
this build. The v13 title-policy change supplies no adoption route for them.
The installed runtime no longer exposes a
`schema-adopt` command. Retain an older database and its matching artifact
directory offline if it has historical value, then start this build with an
absent application database and artifact store and repeat first-run setup.

Maintainers have one separate qualification-only command:
`qualification-state-bridge retained-library-v26-to-v32`. It accepts only the
exact retained #744 test schema, an existing complete owner-only clone, an
independent backup path, and an absent output path under the same private
directory. The command name remains the frozen authority identifier from its
original plan; its fresh output is always the exact current schema,
`metadata-report-v37` with `fixed-conservative-v19`. It leaves the source
untouched, verifies the backup, rebuilds a fresh exact-current report database
through current derivation, and
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
