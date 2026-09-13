# Upgrades and compatibility

Mechanical Librarian remains pre-release. Ordinary startup accepts only absent
state or the exact current state and never silently rewrites an older schema.
The reviewer-access authority, `setup-state-v3`, `owner-settings-v4`,
`cover-consideration-v4`, `metadata-report-v43`, and the added application
tables change the current SQLite objects in this build. Every earlier
application database therefore requires a clean rebuild; no adoption,
migration, conversion, import, repair, or downgrade is supplied by #872, #873,
#749, #956, #965, #896, #888, #1000, #999, #957, #967, #892, or #144.

#967 requires current `metadata-report-v43`, `shadow-outcome-set-v5`,
`metadata-policy-profile-v3`, `shadow-review-packet-v8`, and
`cover-automatic-decision-v2`, with `fixed-conservative-v25` under the unchanged
`proposal-set-v7` document shape. These recording-support and qualification
contracts require a clean application rebuild. Historical records and their
measurements remain unchanged; no adoption or rescoring is supplied.

The Inventaire increment's exact predecessors were `config-v7`, `metadata-report-v32`,
`owner-settings-v2`, and `setup-state-v2`. Inventaire changes all four closed
vocabularies, so no predecessor is parsed or adopted: supply a `config-v9`
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

#1000 advances the report discriminator from `metadata-report-v37` to
`metadata-report-v38`: authority evaluation now derives whether each provider
candidate proves the target work, explicitly contradicts it, or lacks enough
identity evidence from the retained normalized claims. No classification state
is serialized, and the v37 and v38 payload field shapes are unchanged; the old
discriminator is not exact-current because that interpretation changed. Rebuild
an absent application database; no adoption is supplied.

#957 advances the report discriminator from `metadata-report-v38` to
`metadata-report-v39`: a provider lane and observation may now carry the
`identity` diagnostic at its `identity` stage, which the v38 reader rejects. No
field, payload shape, or vocabulary is added, but a v39 report is unreadable to
v38 and a v38 database cannot accept one, so it is not exact-current. Rebuild an
absent application database; no adoption is supplied.

#1004 advances `metadata-report-v41` to `metadata-report-v42` with the required
`recording_lookup_candidates` collection. Clean rebuild required: prior JSON
and application databases refuse unchanged; no adoption or migration is supplied.
The isolated audio protocol and owner detail API remain v4.

#1042 advances `metadata-report-v40` to `metadata-report-v41` and the isolated
audio protocol from v3 to v4. The required exact boolean `chapter_rows_omitted`
records examined chapter rows discarded by the parser; older reports cannot
establish this fact. Clean rebuild required. Startup still accepts only absent
or exact-current state; no adoption or migration is provided. Case and report
detail responses advance to v4 with required-nullable recording attribution.

#144 advances the report discriminator from `metadata-report-v39` to
`metadata-report-v40`: an audio observation now carries the provider candidate a
comparison was made against, four container and stream kinds joined its closed
vocabulary, and a provider candidate records whether qualification found this
recording short of the running time it stated. A v39 reader rejects all three.
Rebuild an absent application database; no adoption is supplied.

#896 advances the exact proposal policy from `fixed-conservative-v18` to
`fixed-conservative-v19` because decoration-equivalent titles collapse into one
class and the tidy tranche adds a new persisted automatic-decision path.
Existing pre-release proposal, review, decision, and downstream field-effect
state is not rewritten or adopted; rebuild the application database.

#1000 advances the exact proposal policy from `fixed-conservative-v19` to
`fixed-conservative-v20` because explicitly contradictory work evidence is no
longer allowed to vote for a publication date or an automatic genre. Historical
decisions remain readable, but cannot authorize a current effect. Existing
pre-release proposal, review, decision, and downstream field-effect state is not
rewritten or adopted; rebuild the application database.

#1001 advances the exact proposal policy from `fixed-conservative-v20` to
`fixed-conservative-v21` because an equivalent local description can change a
grouped alternative's exact value and content-derived identity. Historical
decisions remain readable but cannot authorize a current effect. Existing
pre-release proposal, review, decision, and downstream field-effect state is
not rewritten or adopted; rebuild the application database.

#999 advances `proposal-set-v6` to `proposal-set-v7`, the exact proposal policy
to `fixed-conservative-v22`, `cover-consideration-v3` to
`cover-consideration-v4`, and `cover-policy-report-v1` to
`cover-policy-report-v2`. Owner readiness is independent from automatic
recommendation, and automatic replacement binds the actual incumbent state and
strict rank. Existing pre-release state is not adopted; rebuild the application
database and artifact store.

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
original plan; its frozen target is
`metadata-report-v40` with `fixed-conservative-v23`. That target is incompatible
with the current v25 title policy; this change does not refresh the bridge. It leaves the source
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


#844 requires explicit replacement of `config-v8` and `review-bootstrap-v1`
with `config-v9` and `review-bootstrap-v2`: clean rebuild required for these
configuration documents only. No conversion or adoption is supplied. This
transport change does not advance database, owner-settings, setup, session,
report, or API schemas; earlier independent rebuild requirements still apply.

The ISBN-linked title policy requires a clean rebuild under ADR-0013. Retain
old evidence offline; v23 proposals and packet-v6 cannot be adopted into this
build. No migration, automatic repair or qualification-bridge refresh is supplied
(#1007).
