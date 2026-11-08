# ADR-0013: Require clean schemas until the first production release

- Status: accepted
- Date: 2026-07-24
- Owners: project maintainers
- Supersedes: the pre-release schema-compatibility provisions of ADR-0003,
  ADR-0006, ADR-0009, and ADR-0012

## Context

The project is pre-release and has no supported historical installation base.
Schema evolution machinery would add authority and recovery promises without a
named present user need. The retained 2,022-work first-boot experiment later
created one such need: repeating hours of provider observations merely to add
reconstructible read indexes would discard valuable canonical development
state without improving its meaning.

## Decision

Until the first production release, every database, report, artifact,
configuration, and internal-document schema accepts only absent state or its
exact current state. Earlier schemas are rejected clearly and are never
silently deleted; they need not be read, migrated, imported, or converted. A
clean rebuild may be required. Current-schema restart, replay, and crash safety
remain mandatory.

A persisted-schema change may instead deliver one closed, code-defined adoption
from its exact immediate predecessor when every mutation is confined to
reconstructible derived objects. The plan belongs to that schema change's issue
and pull request, names the complete predecessor and result contracts, creates
and verifies a private backup, validates canonical state before and after, and
runs only through explicit offline owner action. Ordinary startup does not run
it. Runtime SQL, plugins, configuration-supplied operations, canonical row
conversion, general migration machinery, repair, import, and downgrade remain
out of scope.

Maintainer qualification continuity is a separate, narrower authority. A
code-defined bridge may read one named complete private test-state clone,
create and reverify an independent private backup, and reconstruct a fresh
exact-current qualification database through current domain derivation. The
plan must bind one exact source and target contract, preserve only evidence
whose meaning current code accepts, explicitly invalidate observations missing
current provenance, and discard every downstream decision or projection. It
never mutates the retained source, runs at startup, contacts a provider,
accepts configuration-supplied operations, or establishes product compatibility.

A code-defined provider-evidence qualifier may instead capture one separately
authorized bounded private cohort at the exact HTTP request/response boundary
before current provider parsing. It excludes credentials and configured contact
identity before sealing the canonical bundle. Replay is source-head-bound,
credential-free, and networkless; it verifies the bundle digest and current
request contract, selectively invalidates only changed provider lanes, then
re-runs current parsing and downstream derivation into absent exact-current
state. This authority applies only to captured works. It is not runtime caching,
database adoption, import, backup, migration, or proof of complete-library,
exact-installed, scale, browser, or semantic qualification.

The #800 index change predates this revision and is the sole retrospective
exception: #805 delivers and qualifies its adoption separately because #800 was
already merged when the retained experiment established the preservation need.
Later schema changes do not inherit that exception. #802 follows the ordinary
same-change rule: its `review-field-facts-v1` adoption accepts only the frozen
exact #805 schema, adds reconstructible derived field-fact objects and delayed
eligibility state, and atomically replaces that predecessor's historical
`updated_at` claim index with the current ordinal and due-promotion indexes. It
does not adopt #826's immediate predecessor, which already has the field-fact
objects and remains clean-rebuild required.

Compatibility requires a named present user need. Post-release migration and
restore work is deferred to #57; this decision grants no general migration,
repair, retention, downgrade, or destructive authority.

## Consequences

- Pre-release schema changes update the exact-current contract, prove clean
  creation plus current-schema safety, and declare either `clean rebuild
  required` or one same-change exact-predecessor derived-object adoption.
- Operators retain older state offline and create a clean current state when a
  schema changes unless that exact predecessor has a delivered adoption plan.
- The first production release must make a separate, explicit compatibility
  decision before users can rely on upgrade support.

## Supersession

The named ADR provisions remain historical context. This ADR governs every
pre-release schema, rather than only the 0.5 metadata-report increment.
Issue #830 retires the historical `review-field-facts-v1` plan and introduces
`review-owner-decision-actions-v1`, a literal exact-predecessor adoption that
rebuilds only the derived owner-actionability fact and trigger. Its literal
target is checked against the installed build before any filesystem access;
future schema changes therefore require a new reviewed plan or a clean rebuild.

Issue #872/#873 takes the clean-rebuild branch. Its reviewer-access and owner
state objects cannot update the canonical historical state targeted by the
three previous exact-predecessor plans, so those plans and the now-empty public
command are retired. This does not weaken ordinary absent-or-exact-current
startup or create a generic conversion seam.

Issue #938 restores only the maintainer qualification boundary above. Its
`retained-library-v26-to-v32` plan reads the exact retained #744 checkpoint,
re-derives current reports, invalidates provider observations that predate
mandatory work-dispatch lineage, their dependent covers, and artifact-bearing
cover references whose complete private store was not verified, and creates
fresh report-only current state. The earlier `schema-adopt` plans remain
retired; #1232 below is the command's only plan.

Issue #939 adds the bounded provider-evidence authority above after inventory
proved that existing durable reports begin after provider parsing. Its sealed
`provider-replay-bundle-v1` is necessary to exercise current transport decoding
and parsers without reacquisition. The accepted 12-work proof does not contain a
complete corpus, so it replaces provider acquisition only for its captured
lanes and leaves #938's complete-library bridge unchanged.

Issue #869 also takes the clean-rebuild branch. Its exact-current application
schema adds the derived `review_preparation_presentation` object, including its
canonical contiguous first-terminal completion sequence and guarded persisted
sequence count, Settings-revision-bound deferred enrollments, and exact-cycle
recovery high-water markers used for authoritative recent ordering, and supplies no
migration, adoption, repair, or compatibility path. Absent state is created at
bootstrap; any earlier application schema is rejected before mutation.

Issue #1232 takes the adoption branch. Its `review-attention-facts-v1` plan
adds only ADR-0084's derived attention facts, their maintenance triggers and
three indexes to the exact 441e571f schema, derives every fact from existing
rows through the same code that creates them for absent state, and proves every
other table's rows unchanged. It runs only through the offline `schema-adopt`
command, never at startup.

Issue #1245 takes the clean-rebuild branch. Its `metadata-report-v50` report
CHECK cannot be reached from the exact 441e571f schema #1232 adopts, so that
plan and the now-empty public command are retired. This does not weaken
ordinary absent-or-exact-current startup or create a generic conversion seam;
every earlier application database is rejected before mutation and needs a
clean rebuild.

Issue #1255 also takes the clean-rebuild branch. `metadata-report-v51` records
an audiobook's discovery titles and binds its alternative work lane, so a v50
document cannot be read as current. Every earlier application database is
rejected before mutation and needs a clean rebuild; no adoption is supplied.

Issue #1256 also takes the clean-rebuild branch. `metadata-report-v52` adds no
field, but a v51 Audible overflow or empty rung meant the unnarrowed page, so a
v51 document cannot be read as current. Every earlier application database is
rejected before mutation and needs a clean rebuild; no adoption is supplied.

Issue #1257 also takes the clean-rebuild branch. `metadata-report-v53` adds
band-first recording-lookup hints to every provider lane, so a v52 document
cannot be read as current. Every earlier application database is rejected
before mutation and needs a clean rebuild; no adoption is supplied.

Issue #1251 also takes the clean-rebuild branch. `metadata-report-v54` adds no
field, but an incumbent description holding an attribute-free HTML tag slash or
U+2028 was `malformed` in v53 and is a `value` now, so a v53 document cannot be
read as current. Every earlier application database is rejected before mutation
and needs a clean rebuild; no adoption is supplied.

Issue #1249 also takes the clean-rebuild branch. Removing the audiobook clause
from ADR-0084's attention definition changes the text of the trigger that
maintains the facts, so the exact schema comparison rejects every earlier
application database before mutation. No version moves and no adoption is
supplied.

Issue #1250 also takes the clean-rebuild branch. `metadata-report-v55` adds no
field, but a v54 audio description over 512 characters was absent and is now an
`oversized` claim, a placeholder language code was a valid claim and is now
none, and a `prID` atom was a malformed ASIN claim and is now none, so a v54
document cannot be read as current. The decision policy moves to
`deterministic-shadow-decision-policy-v22` with the wider audiobook synopsis
and genre floor, so v21 decisions are refused too. Every earlier application
database is rejected before mutation and needs a clean rebuild; no adoption is
supplied.

Issue #1258 also takes the clean-rebuild branch. `metadata-report-v56` adds the
required `dispatch` record to every provider lane, `null` on an ebook lane, so
a v55 document has no field the current reader requires and cannot be read as
current. Every earlier application database is rejected before mutation and
needs a clean rebuild; no adoption is supplied.

Issue #1260 also takes the clean-rebuild branch. `metadata-report-v57` adds no
field, but a band-corroborated production now admits an audiobook product a
v56 lane refused, and its lineage names productions and the `initials_fold`
family a v56 reader does not know, so a v56 document cannot be read as current.
Every earlier application database is rejected before mutation and needs a
clean rebuild; no adoption is supplied.

Issue #1283 also takes the clean-rebuild branch. The proposal policy moves to
`fixed-conservative-v36`, because an author or narrator that only drops a
current member's accents now preserves the current spelling where a v35
proposal replaced it, and the decision policy moves to
`deterministic-shadow-decision-policy-v23`, because credit-prefixed and
accent-stripping contributor candidates and announcement title candidates are
ineligible under new reason codes, and the synopsis rules refuse mostly quoted
praise. A v35 proposal or a v22 outcome cannot be read as current.
Every earlier application database is rejected before mutation and needs a
clean rebuild; no adoption is supplied.

Issue #1285 also takes the clean-rebuild branch. Owner decisions move to
`human-decision-v3`, whose new outcomes change the document, its request
digest and the `human_decisions` outcome check. Owner
decisions are canonical authority rather than reconstructible derived objects,
so the adoption branch does not apply. Every earlier application database is
rejected before mutation and needs a clean rebuild; no adoption is supplied.

Issue #1370 also takes the clean-rebuild branch. The field-effect consent state
moves to `field-effect-consent-state-v2`, whose closed `basis` records whether
the owner granted the consent or a policy activation carried it, and from which
consent. Consents are canonical owner authority rather than reconstructible
derived objects, so the adoption branch does not apply. Every earlier
application database holding a consent is rejected before mutation and needs a
clean rebuild; no adoption is supplied.

Issue #1424 also takes the clean-rebuild branch. `metadata-report-v58` adds no
field, but an Audiobookshelf incumbent description is now admitted as the text
it shows, its markup rendered and its character references decoded, where a v57
report kept the stored form, so a v57 document cannot be read as current. Every
earlier application database is rejected before mutation and needs a clean
rebuild; no adoption is supplied.

Issue #1308 also takes the clean-rebuild branch. `metadata-report-v59` adds no
field, but a v58 report could name `malformed_over_bound_response`, which no
longer exists, and its AudioSilo lookup misses as `http_non_success`, so a v58
document cannot be read as current. Every earlier application database is
rejected before mutation and needs a clean rebuild; no adoption is supplied.

Issue #1316 also takes the clean-rebuild branch. `metadata-report-v60` admits
the `prior_admission` claim basis and its field-result source, so every v60
report states a field result a v59 report cannot, and the proposal policy moves
to `fixed-conservative-v37` (ADR-0088). Every earlier application database is
rejected before mutation and needs a clean rebuild; no adoption is supplied.

Issue #1307 also takes the clean-rebuild branch. `metadata-report-v61` keeps
each product an audiobook ASIN lane refused with its own claims
(`refused_products`) and admits the `band_corroborated_title_relation`
production and the `leading_number_stripped` rung, so a v60 report cannot state
what a v61 report records, and the proposal policy moves to
`fixed-conservative-v38` (ADR-0067). Every earlier application database is
rejected before mutation and needs a clean rebuild; no adoption is supplied.

Issues #1315 and #1305 also take the clean-rebuild branch.
`metadata-report-v62` records the library languages an audiobook report was
prepared under (`library_languages`), which its admission reads, so a v61
report cannot state what a v62 report records, and the proposal policy moves to
`fixed-conservative-v39` (ADR-0064). Every earlier application database is
rejected before mutation and needs a clean rebuild; no adoption is supplied.

Issue #1317 also takes the clean-rebuild branch. The proposal policy moves to
`fixed-conservative-v40`, because a placeholder description incumbent now
counts as missing knowledge and a placeholder alternative is never elected, and
the decision policy moves to `deterministic-shadow-decision-policy-v24`,
because the audiobook synopsis, subtitle and genre rules change (placeholder and
cut-off fills, header lines, marketing copy and the language gate). The report
schema is unchanged; no adoption is supplied.

Issue #1319 also takes the clean-rebuild branch. `metadata-report-v63` adds
the `companion` section, the EPUB an audiobook item holds beside its tracks
with the claims read from it, so a v62 report cannot state what a v63 report
records. Every earlier application database is rejected before mutation and
needs a clean rebuild; no adoption is supplied.

Issue #1497 also takes the clean-rebuild branch. A Library of Congress
control number in opening matter is no longer an ISBN-10 (extractor
`epub-opening-v5`), Hardcover author names are tidied or left unoffered, and an
Open Library title or description that contradicts the work's language is no
longer an alternative, so report contents change and the proposal policy moves
to `fixed-conservative-v43`. The report schema is unchanged; no adoption is
supplied.

Issue #1496 also takes the clean-rebuild branch. Titles that differ only by a
leading article form one alternative, a bracketed ISBN suffix is the explicit
identifier decoration, a packaged generic subtitle is not observed, and a
language respelling is automatic (ADR-0065), so the proposal policy moves to
`fixed-conservative-v42`, the decision policy to
`deterministic-shadow-decision-policy-v26` and the F46 digest rotates. An
automatic decision minted under the earlier F46 digest no longer decodes. The
report schema is unchanged; no adoption is supplied.

Issue #1314 also takes the clean-rebuild branch. Every language is written as
its class's display name and the `preserve` language option is retired
(ADR-0065), so the proposal policy moves to `fixed-conservative-v41` and the
decision policy to `deterministic-shadow-decision-policy-v25`. The policy
profile document keeps its shape and `metadata-policy-profile-v3` label: a
profile, override or configuration naming `preserve` is refused as an
unavailable option, and every other v3 profile reads as before. The report
schema is unchanged; no adoption is supplied.

Issue #962 also takes the clean-rebuild branch. `metadata-report-v64` adds
each provider's `audio_editions`, the Hardcover audio editions that witness
which recording an audiobook is (ADR-0090), so a v63 report cannot state what
a v64 report records. Every earlier application database is rejected before
mutation and needs a clean rebuild; no adoption is supplied.

Issue #1451 also takes the clean-rebuild branch. `review_preparation_work`
gains the nullable `research_not_before` column, which marks when an audiobook
whose report admitted no recording is searched again (ADR-0089). A predecessor
table without it cannot hold that mark. Every earlier application database is
rejected before mutation and needs a clean rebuild; no adoption is supplied.

Issue #1396 also takes the clean-rebuild branch. Under ADR-0087 qualification
authority binds only the permitted operations, so the F19a, F20 and F46 digests
rotate once and the historical decoding tables are deleted: an automatic
decision minted under any earlier digest no longer decodes. Rich outcome action
identities embed the F20 digest, so outcomes move to `shadow-outcome-set-v11`
and a v10 outcome cannot be read as current. Every earlier application database
is rejected before mutation and needs a clean rebuild; no adoption is supplied.

Issue #1498 also takes the clean-rebuild branch. `metadata-report-v65` adds the
`adapter_fault` provider diagnostic, which names a lookup the adapter itself
failed instead of `unknown` (ADR-0017), so a v64 report cannot state what a v65
report records. The report browse facts gain an `adapter_fault` match state, so
such a work reads as its own Not matched reason, never as unavailable. Every earlier application database is rejected before mutation
and needs a clean rebuild; no adoption is supplied.

Issue #1499 also takes the clean-rebuild branch. An ebook titled with its
bracketed series name now admits the series-stripped work (ADR-0067), and an
ebook WORK lane that admits nothing records the rungs it sent as
`exhausted_dispatch`, so an earlier report's matches and lane records are not
what this code prepares. The report schema is unchanged; no adoption is
supplied.

Issue #1357 also takes the clean-rebuild branch. `metadata-report-v66` adds the
EPUB report's `local.cover_recovery`, the closed fallback and refusal codes of
a cover recovered from a malformed declaration as shadow evidence, so a v65
report cannot state what a v66 report records. Every earlier application
database is rejected before mutation and needs a clean rebuild; no adoption is
supplied.

Issue #1569 also takes the clean-rebuild branch. The EPUB cover child refuses
a declared cover that `encryption.xml` lists, before reading it, as
`unsupported_format`. The report schema is unchanged, but a report carries forward while
its file and configuration fingerprints are unchanged, so a stored report would
keep the earlier cover outcome. No adoption is supplied.

Issue #1309 also takes the clean-rebuild branch. `metadata-report-v67` adds
the `not_needed` provider status, which records an audiobook AudioSilo binding
that was not asked because Audible or Audnexus had already admitted a recording
by stated runtime. It also adds the `region_unavailable` provider diagnostic
for an Audnexus marketplace miss. A v66 report cannot state either, so every
earlier application database is rejected before mutation and needs a clean
rebuild; no adoption is supplied.

Issue #1616 also takes the clean-rebuild branch. An automatic subtitle that
only restates the case's series is withheld, and a title strip reads an
underscore segment and a series named only by a subtitle label (ADR-0073), so
the decision policy moves to `deterministic-shadow-decision-policy-v27`: an
outcome minted under v26 could state a write v27 refuses. The proposal policy
and report schema are unchanged; no adoption is supplied.

Issue #1639 also takes the clean-rebuild branch. The image moves to CPython
3.15, whose Unicode 17.0.0 data differs from 3.14's 16.0.0, so every earlier
application database's browse facts are refused before mutation. The report
schema is unchanged. No adoption is supplied.

Issue #1310 also takes the clean-rebuild branch. A populated description or
genre list that passes the audiobook synopsis and genre floor carries the
proposal reason `populated_value_meets_floor`, so the proposal policy moves to
`fixed-conservative-v44`, and the review-case field facts read the resolved
policy's options. The report schema is unchanged; no adoption is supplied.
Its single-source settlement changes the review-case field-fact trigger again:
a choice only one uncorroborated source proposes is no longer an unresolved
choice there. The proposal policy and report schema are unchanged; no adoption
is supplied.

Issue #1642 also takes the clean-rebuild branch. `metadata-report-v68` adds
Hardcover's work first-publication date claim on an audiobook WORK lane, which a
v67 report cannot state; a report carries forward while its file and
configuration fingerprints are unchanged, so a stored report would keep the
earlier claims. `fixed-conservative-v45` widens the audiobook work route to a
work's first publication date, narrators from audio editions Audible cannot
confirm, and a work-bound cover, so an earlier proposal set is not what this
policy prepares. Every earlier application database is rejected before
mutation and needs a clean rebuild; no adoption is supplied.

Issue #1468 also takes the clean-rebuild branch. `metadata-report-v69` plans
an audiobook's companion EPUB into its discovery: one ISBN lane at Hardcover,
the companion's title as the first alternative search title, and the
companion's title and creators as the search when the target names no complete
work. A v68 report replays none of these lanes, and a report carries forward
while its file and configuration fingerprints are unchanged, so a stored report
would keep the earlier plan. The proposal policy is unchanged. Every earlier
application database is rejected before mutation and needs a clean rebuild; no
adoption is supplied.

Issue #1723 also takes the clean-rebuild branch. `metadata-report-v70` records
on each lane candidate how that lane kept the corroborated recording, and the
report refuses a value that does not replay and a corroborated recording no
lane explains. A v69 report states none, and a report carries forward while
its file and configuration fingerprints are unchanged, so a stored report would
never explain its match. The proposal policy is unchanged. Every earlier
application database is rejected before mutation and needs a clean rebuild; no
adoption is supplied.

Issue #1752 also takes the clean-rebuild branch. `metadata-report-v71` adds
`final_host` to every cover source: the closed host family that served a
retrieved Open Library cover, now that Open Library covers follow the fixed
Internet Archive route. A v70 report states no such key, and a report carries
forward while its file and configuration fingerprints are unchanged, so a
stored report would keep its `unavailable` Open Library covers. The proposal
policy is unchanged. Every earlier application database is rejected before
mutation and needs a clean rebuild; no adoption is supplied.

Issue #1777 also takes the clean-rebuild branch. `metadata-report-v72` makes a
Hardcover WORK lane hold books rather than editions on both media, and adds a
WORK lane an ebook asks with its EPUB creators when the target names no author,
which carries that basis in its identity. A v71 report replays neither, and a
report carries forward while its file and configuration fingerprints are
unchanged, so a stored report would keep the earlier lanes. The proposal policy
is unchanged. Every earlier application database is rejected before mutation
and needs a clean rebuild; no adoption is supplied.

Issue #1363 also takes the clean-rebuild branch. `metadata-report-v73` adds
`measures` to every decoded cover artifact: the perceptual, difference,
colour and layout measures the constrained decoder computes from the 320×480
preview, so later cover rules can tell the same art apart from other art. A
v72 report states none, and a report carries forward while its file and
configuration fingerprints are unchanged, so a stored report would never
measure its covers. `cover-consideration-v5` records the kept original of an
automatic switch, and three new tables bind each kept original to its plan,
record the order verified switches joined the bounded list, and hold each
removal obligation. The proposal policy is unchanged. Every earlier
application database is rejected before mutation and needs a clean rebuild; no
adoption is supplied.

Issue #1779 also takes the clean-rebuild branch. `metadata-report-v74` plans
one iTunes Search lane for an audiobook, whose answer supplies only its cover
(ADR-0061 clause 9). An earlier report replays no such lane, and a report carries
forward while its file and configuration fingerprints are unchanged, so a
stored report would never ask iTunes for the cover. The report browse facts
ignore a cover-only authority's lanes on an audio report, so an iTunes failure
never marks an audiobook provider-limited and an iTunes listing never takes it
out of Not matched. The proposal policy is unchanged. Every earlier
application database is rejected before mutation and needs a clean rebuild; no
adoption is supplied.

Issue #1481 also takes the clean-rebuild branch. `metadata-report-v75` gives
every audiobook sidecar manifest entry its kind, its binding, its closed
refusals, the claims it supplied, the fields it lost to disagreement or to
echoing the target, and Libation shadow values; `audio-inspection-v5` is the
matching child protocol. A v74 report states none of these and holds claims
from Audiobookshelf's own record, from shared folders and from other books'
sidecars, and a report carries forward while its file and configuration
fingerprints are unchanged, so a stored report would keep them. The proposal
policy is unchanged. Every earlier application database is rejected before
mutation and needs a clean rebuild; no adoption is supplied.

Issue #1796 also takes the clean-rebuild branch. `metadata-report-v76` records,
for each audiobook sidecar that starts with a byte-order mark, what a reading
without the mark would add and the refusals it would have had. A v75 report
states none of this, and a report carries forward while its file and
configuration fingerprints are unchanged, so a stored report would never
record it. The proposal policy is unchanged. Every earlier application
database is rejected before mutation and needs a clean rebuild; no adoption is
supplied.

Issue #1780 also takes the clean-rebuild branch. `metadata-report-v77` adds
the sidecar refusal `initials_spelling`, for a document whose creators agree
with the item's only once dotted initials fold. A stored report would name
such a document `other_book`, and a report carries forward while its file and
configuration fingerprints are unchanged. The audio child protocol carries no
refusal and stays `audio-inspection-v6`. The proposal policy is unchanged.
Every earlier application database is rejected before mutation and needs a
clean rebuild; no adoption is supplied.

Issue #1778 also takes the clean-rebuild branch, and with it the rest of
#1380. `metadata-report-v78` adds the `listed_edition` cover designation and
the `edition_limit`, `edition_fetch_incomplete` and `same_art` truncation
reasons to their closed sets. A report may now hold eight Hardcover cover
sources and twelve provider cover sources in all, where a v77 report held four
for each provider and eight in all. A v77 report has none of a found book's
edition covers, and a report carries forward while its file and configuration
fingerprints are unchanged, so a stored report would never offer them. The
proposal policy is unchanged. Every earlier application database is rejected
before mutation and needs a clean rebuild; no adoption is supplied.

Issue #1363's Put back also takes the clean-rebuild branch.
`metadata-report-v79` adds a `retained` cover source: the original an
automatic switch replaced, read from the retained store while its copy is
listed, and a report decoded under v78 states none. A report carries forward
while its file and configuration fingerprints are unchanged, so a stored report
would never offer the original back; a verified cover write therefore marks
its work for fresh preparation. Every decoded cover reports its oriented size.
`fixed-conservative-v46` makes the retained original an owner-only cover
alternative, `cover-consideration-v6` names each candidate's kind, and
`cover-selection-authority-v3` records the switch a Put back undoes, so a
verified Put back releases its copy. Every earlier application database is
rejected before mutation and needs a clean rebuild; no adoption is supplied.
