# Pre-release compatibility policy

#1307 advances the report to `metadata-report-v61` and the proposal policy to
`fixed-conservative-v38`: an audiobook ASIN lane keeps each product it refused
with its own claims, Audible's asserted lane may admit one runtime-, title- and
narrator- or series-corroborated product every exact path refused, provider
whole-minute runtimes are read as floors, and the comparison version moves to
`closed-title-creator-publication-work-identity-v6`. **Clean rebuild required** for every
predecessor application database; startup refuses v60 reports before mutation.
No migration, adoption, conversion, import or downgrade is supplied.

#1316 advances the report to `metadata-report-v60` and the proposal policy to
`fixed-conservative-v37`: an audiobook item carries the edition an earlier
preparation of the same track set admitted as a `prior_admission` claim under
its own field-result source, and one recording's sibling ASINs offer one value.
**Clean rebuild required** for every predecessor application database; startup
refuses v59 reports before mutation. No migration, adoption, conversion, import
or downgrade is supplied.

#1308 advances the report to `metadata-report-v59`: the provider diagnostic
category `malformed_over_bound_response` is replaced by `malformed_response`,
`oversized_response` and `candidate_limit_exceeded`, and an AudioSilo lookup
miss counts as `empty_no_match` rather than `http_non_success`. **Clean rebuild
required** for every predecessor application database; startup refuses v58
reports before mutation. No migration, adoption, conversion, import or
downgrade is supplied.

#1289 changes presentation only: the workspace paints its saved or system
theme before it loads, self-hosts Inter, and uses one Button and one
StatusPill. The web application serves the hashed font faces and its content
security policy admits `font-src 'self'`. No public schema, API version or
persisted document changes, so no clean rebuild is required. F19a/F20/F46
records are displaced because the web application module they bind changed,
with permission sets unchanged.

#1286 changes owner-facing copy only: evidence observations no longer repeat
their label or name internal scope, state and applicability codes; media
health states running times in hours and minutes; and an owner decision's
Activity row names the value its own plan replaced. No public schema, API
version or persisted document changes, so no clean rebuild is required.
F19a/F20/F46 records are displaced because the application read model, audio
health and Activity persistence adapter they bind changed, with permission
sets unchanged.

#1396 binds qualification authority to the permitted operations alone
(ADR-0087), so the F19a/F20/F46 digests rotate once and decisions minted under
any earlier digest no longer decode. Rich outcome action identities embed the F20
digest, so outcomes advance to `shadow-outcome-set-v11`. **Clean rebuild
required** for every predecessor application database, including an empty one;
startup refuses v10 outcomes before mutation. No migration, adoption,
conversion, import or downgrade is supplied.

#1424 advances the report to `metadata-report-v58`: an Audiobookshelf
incumbent description is admitted as the text it shows, and a description
write Audiobookshelf stored escaped or sanitised verifies when the stored form
shows the value sent. **Clean rebuild required** for every predecessor
application database; startup refuses v57 reports before mutation. No
migration, adoption, conversion, import or downgrade is supplied; F19a/F20/F46
records are displaced with their permission sets unchanged.

#1407 adds the derived `provider_match_state` column to
`metadata_report_browse_facts`, folded by its insert trigger and checked at
open beside `provider_limited`. **Clean rebuild required** for every
predecessor application database; startup refuses the predecessor schema before
mutation, with no adoption or migration. The Library response advances to
`review-library-v3` for the `not_matched` lens, count and closed row reasons;
`review-preparation-v2` keeps its version and gains a `not_matched` stage and
`now` count; the cold-start report advances to `cold-start-report-v5`.
Settings, sessions, setup state and the recovery store are unchanged.
F19a/F20/F46 records are displaced because the schema, application and web
interface they bind changed, with permission sets unchanged.

#1384 keys the session database's `review_login_failures` rows by a
verifier-bound client HMAC, so sign-in is throttled per client. **Clean rebuild
required** for `/data/private/review-sessions.sqlite` only: startup refuses the
predecessor table before mutation, with no adoption or migration. Remove the
file and restart; every browser signs in again. The application database,
settings, setup state and public API schemas are unchanged. F19a/F20/F46
records are displaced because the session service, web interface, validation
vocabulary and composition they bind changed, with permission sets unchanged.

#1366 adds an optional `now` count to the `review-preparation-v2`
presentation, whose schema version is unchanged: the run's works as they stand
now (`ready`, `recorded`, `failed`, `in_progress`) and the `scan_ids` of the
masthead rows it sums. Nothing persisted changes, so no clean rebuild is
required. F19a/F20/F46 records are displaced because the web interface,
application and persistence adapter (`review_scans.py`) they bind changed,
with permission sets unchanged.

#1383 moves incomplete first-run setup state to `setup-state-v4`, which keeps
the digest of the deployment-held setup claim token while setup is unclaimed
([ADR-0086](../architecture/decisions/0086-deployment-held-setup-claim-token.md)).
**Clean rebuild required** for setup state only: an incomplete `setup-state-v3`
store is refused at open, with no adoption or migration. Restart setup on an
empty `/data` volume. Completed owner settings, sessions, the application
schema and every other persisted document are unchanged, and no DDL changes.
F19a/F20/F46 records are displaced because composition and first-boot selection
changed, with permission sets unchanged.

#1282 adds an optional `provider_throttling` projection to
`review-preparation-v2`, whose schema version is unchanged, and the owner
command `POST /api/v1/provider-recovery/retry`, which raises the existing
`provider_recovery_generation` by one through the ordinary Settings write.
Nothing persisted changes: the settings document, `config-v9` and the recovery
store keep their schemas, so no clean rebuild is required. F19a/F20/F46 records
are displaced because the web interface, recovery store and composition they
bind changed, with permission sets unchanged.

#1281 adds an optional, pattern-bound `check_group` to each
`continuous-field-activity-v3` event, so the browser can gather one
preparation's automatic no-change checks; the schema version is unchanged.
Nothing persisted changes: the Activity fact store is rebuilt in memory at
startup, so no clean rebuild is required. F19a/F20/F46 records are displaced
because the web interface they bind changed, with permission sets unchanged.

#1283 advances the proposal policy to `fixed-conservative-v36` and the shadow
decision policy to `deterministic-shadow-decision-policy-v23`: an accent-only
contributor respelling is a preservation, and other accent-dropping or
credit-prefixed contributors, announcement title parts and mostly quoted praise descriptions are never
automatic. **Clean rebuild required** for every predecessor application
database; startup refuses v35 proposals and v22 outcomes before mutation. No
migration, adoption, conversion, import or downgrade is supplied; F19a/F20/F46
records are displaced with their permission sets unchanged.

#1260 advances the report to `metadata-report-v57`: an audiobook's asserted
lane may admit a product through a band-corroborated production. **Clean
rebuild required** for every predecessor application database; startup refuses
v56 reports before mutation. No migration, adoption, conversion, import or
downgrade is supplied; F19a/F20/F46 records are displaced with their permission
sets unchanged.

#1258 advances the report to `metadata-report-v56`: every audiobook provider
lane carries a required dispatch record and every ebook lane a `null` one.
**Clean rebuild required** for every predecessor application database; startup
refuses v55 reports before mutation. No migration, adoption, conversion, import
or downgrade is supplied; F19a/F20/F46 records are displaced with their
permission sets unchanged.

#1253 advances the proposal policy to `fixed-conservative-v35`: an unresolved
recording's book-level review also offers series and title, and immaterial
description and genre alternatives carry a new reason. **Clean rebuild
required** for every predecessor application database; startup refuses v34
proposals before mutation. No migration, adoption, conversion, import or
downgrade is supplied; F19a/F20/F46 records are displaced with their permission
sets unchanged.

#1250 advances the report to `metadata-report-v55` and the shadow decision
policy to `deterministic-shadow-decision-policy-v22`: audiobook description,
language and ASIN claims mean something different, and the audiobook synopsis
and genre floor is wider. **Clean rebuild required** for every predecessor
application database; startup refuses v54 reports and v21 outcomes before
mutation. No migration, adoption, conversion, import or downgrade is supplied;
F19a/F20/F46 records are displaced with their permission sets unchanged.

#1252 advances the proposal policy to `fixed-conservative-v34` and the shadow
decision policy to `deterministic-shadow-decision-policy-v21`: a recording's
reordered authors are a preservation, and series-label title strips and
narrator casts disjoint from the file's own credits ask the owner. **Clean
rebuild required** for every predecessor application database; startup refuses
v33 proposals and v20 outcomes before mutation. No migration, adoption,
conversion, import or downgrade is supplied; F19a/F20/F46 records are displaced
with their permission sets unchanged.

#1166 enables source-bound audiobook description/genre owner review without
recording admission. **Clean rebuild required** for `fixed-conservative-v33`,
including empty predecessor proposal tables. No migration or historical-document
resealing is supplied. Only case/report detail wire versions advance to v5 with
required-nullable review scope; stored document shapes stay unchanged. F19a/F20/F46
source bindings rotate with their existing permission sets and measurements.


#1140 rotates F19a, F20 and F46 exact-current bindings because their semantic
sources include browse orchestration. **Clean rebuild required** for predecessor
derived application stores. Schema shapes, shadow decision policy v20 and the
12/13/76 permission sets are unchanged. Prior automatic decisions remain
decodable history without current authority; no adoption or migration is supplied.

Audiobookshelf field-effect canaries support v2.26.0 and later without an
upper-version promise. Each qualification records the exact latest stable image
version and digest. Compatibility is field-local and demonstrated for metadata/
tags and multipart cover endpoints; a service version alone is not capability
proof.

Owner cover selection authority v2 (#1078) requires a **clean rebuild** under
ADR-0013. Exact DDL and document admission refuse both empty and populated
predecessor stores without mutation, migration or adoption. Consideration v4,
identity and policy evidence remain unchanged. The decision request now requires
a closed `cover_confirmation` for cover choices, with explicitly nullable
`expected_selection_id` and exact boolean `confirm_unverified_match`; other
fields reject this object. Historical automatic records retain their original
decode permissions and cannot mint current authority.

Report v46 preserves exact incumbent LF/CRLF description paragraphs and ordinary
boundary spaces/tabs for ebooks and audiobooks (#1080). **Clean rebuild required**
under ADR-0013: v44 reports and the prior SQL contract are refused without migration
or adoption. Review and shadow-packet envelope versions remain unchanged; their
embedded targets are validated through the current report contract. Comparison
normalization does not change stored text, fingerprints or effect preconditions.

The current installed contract is intentionally narrow:

- Python 3.13 or 3.14;
- `config-v9` only;
- an absent database or the installed exact current report database containing
  `metadata-report-v61` plus `proposal-set-v8` under
  `fixed-conservative-v38`, or the separate `catalogue-v2` schema;
- `review-case-v2`, `shadow-review-packet-v9`,
  `shadow-decision-set-v1` under
  `deterministic-shadow-decision-policy-v23`,
  `shadow-review-action-policy-v5`, `shadow-outcome-set-v11`,
  `metadata-policy-profile-v3` and `cover-automatic-decision-v3`;
  these successors require a clean rebuild and do not adopt prior state;
- `review-session-v1`, `review-library-v3`, `review-case-detail-v6`,
  `review-case-evidence-v4`,
  `review-report-detail-v5`, `review-case-history-v3`, `human-decision-v3`,
  `human-decision-event-v1`, `human-decision-ack-v1`, bounded
  evidence/current-case history projections, and the exact-current
  opaque-session, login-throttle, immutable human-decision and decision-event
  tables; older application databases without those exact tables are rejected;
- `cover-consideration-v4` with `cover-policy-report-v2`; owner readiness and
  automatic recommendation are independent, and the report binds the actual
  target state used for strict incumbent comparison;
- strict `batch-manifest-v5` for foreground evidence batches;
- Audiobookshelf v2.35.1 library, library-items, and current-item responses for
  explicitly configured book libraries;
- an explicitly selected EPUB on a mapped read-only mount; and
- fixed Hardcover, Inventaire, Open Library, and Google Books metadata operations for
  referenced enabled providers: at most eight exact ISBN-family lanes plus one
  title-plus-complete-author work lane with at most three deterministic widening
  variants and one provider-grammar narrowing retry. The final variant may use
  the canonical ampersand fold followed by the closed Latin combining-mark
  fold, with no partial egress on overflow;
  Hardcover selections use at most three relationship levels and reject
  provider-declared GraphQL errors before considering partial data; safely
  anchored records retain independently valid bounded optional fields and
  members while strict response shape, candidate identity, cardinality and
  required edition/work joins fail closed; all four ebook providers share
  one-second request-start pacing across foreground and recovery work, and
  exact Google Books and Hardcover authority distinguishes burst waits from
  daily reset waits;
- report-only bounded cover evidence from ABS, an EPUB-declared cover, and
  configured cover-enabled providers: at most eight provider sources overall
  and four per provider, with explicit provider/gallery truncation;
- serial foreground execution for explicit selections or at most 10,000
  all-eligible catalogue entries when Open Library is disabled. With Open
  Library enabled, only an explicit selection of at most five entries is
  supported.

Evidence v4 associates each retained observation with its bounded, nonempty
set of current field destinations. One observation can support several fields
without duplicating its transport row; the association is reconstructed only
from retained field provenance and is part of the opaque page-cursor identity.

Pre-0.4 and intermediate development databases,
`config-v1`/`config-v2`/`config-v3`,
earlier manifest shapes, retired inventory selectors, pre-0.9 proposal/review state,
preview fixtures, retry/work state, and all retired CLI commands are
unsupported. They are rejected rather than migrated, imported, or interpreted.

The installed runtime exposes `review-serve`, read-only `policy-preview`,
owner-explicit `cover-review`, and read-only `review-activity`. The cover and
Activity CLI views reuse the same application projections as HTTP/React and add
no write bypass. Its authenticated browser and API inspect exact-current Library, Attention, case, evidence, history, policy,
preparation, operation, and Activity projections; record individual immutable
ordinary-field human decisions and overrides; run persisted continuous review
preparation; admit qualified deterministic decision-only automatic outcomes;
and execute consent-gated exact field-effect plans through supported target APIs
with backup, currentness, verification, audit, pause, revocation, and recovery
controls. Publishing an automatic outcome itself grants neither an effect plan
nor target-write authority. Only a persisted qualified automatic decision may
later enter the same consent, backup, capability, field-currentness,
verification, audit, and reconciliation-guarded field-effect path as an
accepted human decision. The HTTP surface consumes one pre-composed application
boundary with bounded top-level view models.

Catalogue, foreground batch, report, cover-preview, ProposalSet, ReviewCase,
deterministic preview, and generic contained-reviewer operations are source-only typed
workbench scenarios against disposable or explicitly approved private test
state. They are not installed commands or container entrypoints. The installed
cover-consideration service is the sole narrow exception: it may send exact
report-bound PNG previews through the same isolated adapter and persist one
locally admitted closed observation or typed failure as advisory evidence. It
has no production-target bridge or write authority. Deterministic preview
performs no network or provider/model dispatch and persists nothing.

The runtime does not support generic model advice, model-selected writes,
reviewer queues, bulk or reversal effects, destructive operations, media writes,
arbitrary target requests, or a generic plugin/provider interface.
Audiobookshelf ebook detail responses may express `ebookFile.metadata.relPath`
relative to a directory item's top-level `relPath`. Flat `isFile` items instead
repeat their one service-relative file location. Optional item and file absolute
paths must agree with those coordinates whenever supplied; disagreement,
noncanonical paths, and traversal fail closed.

Audiobookshelf `publishedDate` target reads remain exact ISO calendar days.
Shared `YYYY-MM` proposal candidates are qualification-only and report-only;
partial-date service acceptance and exact read-back have not been qualified,
so month candidates are not write-eligible.

Language claims retain their admitted bytes and provenance in the immutable
report. Valid ISO aliases and regional variants group into one
base-and-exact-optional-script alternative; publisher alternatives remain
distinct. `acceptable_choice` exposes multiple closed-policy-acceptable
publisher outcomes without choosing one. No earlier proposal policy is
interpreted: pre-release databases using `fixed-conservative-v18` or earlier
are rejected before mutation and require a clean rebuild.

Packet v6 decision summaries and typed provider evidence are exact deterministic
projections of the current report, ProposalSet, ReviewCase, and bibliographic
combinations. The former F11
request, prompt, qualification-profile, provider-transport, and result contracts
are retired and have no current compatibility surface. Their stored lifecycle
state is not interpreted or migrated. The source-only contained-reviewer
workbench has its own bounded request and result contract and is excluded from
the installed wheel and production container.

The activated React workspace has one exact build surface:
`review-react-assets-v1`, its content-derived build identity, and the
independently packaged expected identity must agree. Missing, extra, stale,
corrupt, source-map-bearing, or earlier browser bundles are not interpreted or
migrated; direct entry receives only the stable owner-rebuild recovery state.
An open page must present the exact identity on every unsafe API request, and a
different or missing identity is rejected before mutation. Generated OpenAPI
DTOs and frontend source are build inputs, not installed compatibility surfaces.

Until the first production release, every database, report, artifact,
configuration, and internal-document schema accepts only absent state or its
exact current state. Earlier schemas are rejected fail-closed and never silently
deleted; there is no migration, import, conversion, preservation, or compatibility
parser. A clean rebuild may be required and old state may be retained offline.
Exact-ID and current-scope replay apply only within the exact current schema;
current-schema restart, replay, and crash safety remain mandatory.

The #1072 browse-read increment requires a **clean rebuild** of the application
database. `cover_considerations.currentness_document` is a required immutable
derivation of the complete canonical consideration. Root-open admission
recomputes it and rejects disagreement; the exact predecessor without this
column is refused without mutation. There is no adoption or migration command.
The dedicated session database and public API shapes are unchanged.

The F45 audiobook-decision increment declares a clean rebuild. It admits only
`proposal-set-v6`, `human-decision-v2`, and a `review_preparation_work` table
whose completion is recorded in an `outcome` column: a prepared item completes
on `case`, `no_case`, or `evidence_only`, and `evidence_only` is no longer a
member of the failure-code vocabulary. Earlier proposal-set, human-decision, or
preparation-work state is rejected without migration or conversion.

The F47 guarded-audiobook-application increment changes no persisted shape.
`human-decision-v2` is unchanged, no schema version moves, and a `recorded_only`
document retained from F45 stays readable on an audiobook case, is never
migrated, and never applies. The guarded consent envelope identity does move,
because it digests the whole destination set and narrators joins it: every
library consent granted before this build lapses and is re-offered in Activity,
without any schema or data change
([ADR-0071](../architecture/decisions/0071-book-media-guarded-application.md)).

The F31 reviewer-access increment declares a clean rebuild. It admits only
`setup-state-v3`, `owner-settings-v3`, `reviewer-access-v1`, and the exact
current application objects; the former offline exact-predecessor plans and
their public command are retired. Reviewer engine/model provenance remains
readable only inside `cover-consideration-v3` and its matching current report
schema.

The guarded-effect plan, authority, consent, recovery-action, cleanup-obligation
tables, control row, and append-only audit events are part
of the exact current application schema. A database without them, with altered
SQL, extra columns/objects, or an earlier experimental effect schema is
rejected without migration or conversion. Operation, plan, nested
authority/consent, action, cleanup, and event documents and their matching
columns are validated canonically before an existing database opens. Scalar
members retain exact JSON types: booleans are never accepted as integer
attempts, sequences, or timestamps. Restart compatibility applies only inside
that exact schema.

Private qualification attempt, retry-pair, scoring, cohort, analysis, and
manifest records are evidence formats, not installed compatibility surfaces.
They are frozen and validated by their owning private programme and cannot be
loaded as application state or used to widen the supported reviewer tuple.

This is a pre-release reset and not a deprecation cycle. Compatibility requires
a named present user need. A future public release must document its own
compatibility, migration, and restore promises before users depend on them.

The historical #1002 title-component association increment required a clean
rebuild from `fixed-conservative-v22` to v23. The historical #1007 increment
required v24, report v40, proposal document v7 and relationship v1.
The current versions are listed above. No alias is persisted and no adoption
or migration is provided.

The #1007 ISBN-linked title election used `shadow-review-action-policy-v2`. Its packet-v7
candidate fact identifies only the elected title; it does not expand reviewer
authority or require companion changes. **Clean rebuild required**: v23
proposals, packet-v6 and their application stores are rejected before mutation.
See [ADR-0075](../architecture/decisions/0075-independent-evidence-led-titles.md).

Report `metadata-report-v46` requires a **clean rebuild** (#1083): v45 may contain
false author provenance that cannot be distinguished from corrected evidence.
`fixed-conservative-v28`, `deterministic-shadow-decision-policy-v15` and
`epub-opening-v4` are current. Opening report v2, citation v1, proposal envelope
v8, packet v9, outcome v6 and package-parser identities are unchanged.


## Provider title report successor

Report `metadata-report-v47` and its exact SQLite discriminator require a **clean rebuild** (#1084). Prior v46 reports may contain falsely VALID title text that cannot reach comparison. Both old persisted schemas and forged current documents remain refused; no adoption or compatibility parser is supplied.

## Title preservation and recovery (#1073)

The #1073 shadow outcome document and SQL contract was `shadow-outcome-set-v7`.
F19a v32, F20 v46 and F46 v15 retain the previous option sets and historical
measurement evidence. Report v47, proposal policy v28, action policy v4 and
decision policy v15 are unchanged. Prior application stores require a clean
rebuild; historical decision documents remain history only.

## Language evidence equivalence (#1071)

Current report `metadata-report-v48`, proposal policy `fixed-conservative-v29`,
action policy `shadow-review-action-policy-v5`, decision policy
`deterministic-shadow-decision-policy-v16` and outcome `shadow-outcome-set-v8`
require a **clean rebuild** under ADR-0013. Proposal shape v8, packet v9 and
automatic document v1 are unchanged. Earlier reports, proposals and outcomes
are rejected before mutation; retained automatic documents are history only.
No migration, adoption or relabelling grants current authority.

English names and supported equivalent codes group before election. Undetermined
`UND`/`und` facts remain readable without positive authority. Equivalent current
values stay unchanged; filling selects only an actually admitted form according
to the policy preference. Genuine base, script and unsupported-value conflicts
remain manual through compiler and recording admission. Local audio facts count
as one source, including creator roles; this grants no recording identity.


Genre applicability (#1074) advances reports to v49 and proposal policy to v30.
Inventaire's early negative filtering can change retained candidates, overflow
status and request accounting, even though final positive WORK admission remains
unchanged. Prior v48 reports cannot recover pruned evidence from replay.
**Clean rebuild required** under ADR-0013; no migration or predecessor adoption.
Historical automatic decisions remain readable history, not current authority.


## Same-name series membership (#1075)

A supported series may have the same name as the book whose title is preserved.
The bounded exception applies only to the series name when a KEEP title is
preserved and a candidate fills missing, null or empty series. Equality compares
whole normalized tokens; the original title and series spelling are retained.
Sequence, appended-component and subtitle duplication checks still apply. Work
association, competing positions and independent title evidence retain their
existing authority. Ask, Ignore and owner-final decisions remain authoritative.
Proposal policy v31 requires a clean rebuild; report v49 remains readable and
can be re-evaluated. Old proposal/store state is rejected before mutation.

## Audiobook synopsis preservation (#1070)

Shadow decision policy v17 and F46 v19/F19a v36/F20 v50 require a **clean rebuild**
of prior derived stores. Report v49, proposal policy v31, action policy v5,
proposal shape v8, packet v9, outcome shape v8 and Automatic schema v1 are unchanged.
The existing outcome SQL CHECK binds the exact shared policy version and digest;
exact-schema validation refuses old stores before mutation. Prior reports/proposals
remain readable; old Automatic documents are historical,
not current scheduling authority. The closed whole-value placeholder comparison
preserves useful plain incumbents and exact original source text. It does not
score general writing quality or detect semantic contradictions.

## Ebook synopsis fills (#1076)

Shared shadow policy v18 and F19a v37/F20 v51/F46 v20 require a **clean rebuild**.
Report v49, proposal policy v31, action policy v5, outcome v8, proposal shape v8,
packet v9 and automatic document v1 remain unchanged. Prior reports/proposals
still parse; old shadow documents and derived stores refuse current use, and
historical automatic records confer no effect authority. No adoption is supplied.

## Compact qualification transition

Outcome v9 requires a **clean rebuild** of prior derived stores, including empty
v8 stores. Exact predecessor DDL is refused without mutation; no migration or
adoption is provided. Canonical authority-only digests replace cumulative record
hashes without changing current grants. Historical decoding remains available but
cannot authorize current decisions or effects. The historical qualification-state
bridge target stays fixed and does not adopt this transition. See the
[qualification contract](../product/rich-metadata-qualification.md#current-compact-authority).

Automatic cover authority v3 binds the actual policy option into its immutable
decision and idempotency digests. **Clean rebuild required** under ADR-0013: old
v2 documents and predecessor application databases, even empty ones, are refused
before mutation. No migration or adoption is supplied; the selection wrapper
remains `cover-selection-authority-v1`.

## Material ISBN/title conflict precedence (#1132)

Shadow decision policy v19 preserves explicit material title conflicts for owner
review before candidate or combination selection. Outcome shape v10 is unchanged,
but its exact policy-bound DDL requires a **clean rebuild** of prior derived
stores under ADR-0013. No adoption or historical rewriting is provided.


## Exact incumbent title preservation (#1138)

Shadow decision policy v20 elects the existing compiled keep action when the
sole eligible scalar title equals the incumbent and its citations match exactly,
with no combination on either action. Material-conflict precedence is unchanged.
Outcome v10 retains its shape; exact policy-bound DDL requires a **clean rebuild**,
including empty v19 stores. No migration, adoption or historical rewrite is supplied.

## Audiobook work-ladder refusals (#1245)

Report `metadata-report-v50` adds each audiobook work lane's exhausted ladder
and the closed `work_identity_refused` diagnostic, so its exact SQLite
discriminator requires a **clean rebuild** under ADR-0013. Prior v49 reports and
stores are refused before mutation; ebook lanes record no ladder. #1232's
`schema-adopt` plan is retired. No migration, adoption or relabelling is supplied.

## Audiobook discovery titles (#1255)

Report `metadata-report-v51` adds each audiobook's recorded discovery titles and
binds the alternative work lane they may plan, so its exact SQLite
discriminator requires a **clean rebuild** under ADR-0013. Prior v50 reports and
stores are refused before mutation; ebook plans and ladders are unchanged. No
migration, adoption or relabelling is supplied.

## Runtime-narrowed Audible overflow (#1256)

Report `metadata-report-v52` adds no field, but an Audible
`candidate_limit_exceeded` now means more than five products inside the loose
runtime band and an `empty_no_match` rung may be one the band emptied. A v51
lane meant the unnarrowed page, so the exact SQLite discriminator requires a
**clean rebuild** under ADR-0013. Prior v51 reports and stores are refused
before mutation. No migration, adoption or relabelling is supplied.

## Band-first recording-lookup hints (#1257)

Report `metadata-report-v53` adds `recording_lookup_hints` to every provider
lane: up to two Audible products refused on title whose runtime is inside the
tight band, which the ASIN feed looks up last. A fed ASIN answer naming another
work now keeps only its ASIN. The exact SQLite discriminator requires a **clean
rebuild** under ADR-0013. Prior v52 reports and stores are refused before
mutation. No migration, adoption or relabelling is supplied.

## Incumbent description markup and line separators (#1251)

Report `metadata-report-v54` adds no field. An incumbent description holding an
attribute-free HTML tag slash (`</p>`, `<br />`) or U+2028 is now its exact
`value`; text whose only defect is C1 control characters is refused with its
own validator reason and still reads `malformed` in the report and UI. A v53
field meant the narrower grammar, so the exact SQLite discriminator requires a
**clean rebuild** under ADR-0013. Prior v53 reports and stores are refused
before mutation. No migration, adoption or relabelling is supplied.
`review-case-v2` and shadow-review packet envelopes are unchanged.

## Recording attention parity (#1249)

The persisted attention facts drop the audiobook-only reason, so a recording's
field asks for the owner only for a reason an ebook's would. EPUB facts are
unchanged. No report, proposal, outcome or policy version moves, but the
trigger maintaining the facts changes text, so the exact schema comparison
requires a **clean rebuild** under ADR-0013. Predecessor stores are refused
before mutation. No migration, adoption or relabelling is supplied. The
case-detail field `classification` gains the value `owner_choosable` for such a
settled fill; `review-case-detail-v5` and stored documents are unchanged.

## Audiobook local claims and quality floor (#1250)

Report `metadata-report-v55` adds no field. An audio description over 512
characters is now an `oversized` claim holding its first 512 characters with no
canonical value; `und`, `mul`, `mis`, `zxx` and `xxx` are no language claim; and
the MP4 `prID` atom is no ASIN claim. A v54 report meant otherwise, so the exact
SQLite discriminator requires a **clean rebuild** under ADR-0013, and decision
policy v22 refuses v21 outcomes. Prior stores are refused before mutation. No
migration, adoption or relabelling is supplied. `audio-inspection-v4`,
`review-case-v2` and shadow-review packet envelopes are unchanged.

## Dispatch classification (#1258)

Report `metadata-report-v56` adds one required field to every provider lane:
`dispatch`, an object of `outcome`, `rungs` and `near_miss_seconds` on an
audiobook lane and `null` on an ebook lane. A v55 document has no such key, so
the exact SQLite discriminator requires a **clean rebuild** under ADR-0013.
Prior stores are refused before mutation. No migration, adoption or relabelling
is supplied. Proposal, shadow-decision and review envelopes are unchanged, and
the record changes no request, admission or decision. The cold-start report
moves to `cold-start-report-v3`, and the closed stage-timing vocabulary gains
`cover`, `proposal` and `persistence` with `stage-timings-v1` unchanged.

## Band-corroborated admission (#1260)

Report `metadata-report-v57` adds no field. A lane's `transformation_lineage`
may now name `band_parenthesized_series_name`,
`band_order_inverted_series_prefix` or `band_initials_fold`, and an application
may name the `initials_fold` family; the lineage shape is unchanged. A v56
lane refused products v57 admits, so the exact SQLite discriminator requires a
**clean rebuild** under ADR-0013. Prior stores are refused before mutation. No
migration, adoption or relabelling is supplied. The comparison version, the
proposal and decision policies and the proposal, packet, outcome and review
envelopes are unchanged.

## Corroborated admission and refused products (#1307)

Report `metadata-report-v61` adds `refused_products` to every provider
observation: the full product behind each ASIN-only stub on an audiobook ASIN
lane, with no evidence. A lane's `transformation_lineage` may now name
`band_corroborated_title_relation` with one `corroborated_title_relation`
application, and an Audible asserted lane's dispatch lineage may name the
`leading_number_stripped` rung. Provider whole-minute runtimes are read as
floors against the tight band. A v60 report cannot state what a v61 report
records, so the exact SQLite discriminator requires a **clean rebuild** under
ADR-0013, and the proposal policy moves to `fixed-conservative-v38`. Prior
stores are refused before mutation. No migration, adoption or relabelling is
supplied. The comparison version moves to
`closed-title-creator-publication-work-identity-v6` because a lone apostrophe
between letters now folds (ADR-0018). The packet, outcome and review envelopes
are unchanged.

## Provider log events (#1299)

No persisted schema, report, proposal, decision or envelope version changes,
so upgrading needs no clean rebuild. Preparation and provider recovery add
value-free `provider_*` JSON lines on stderr. The workbench cold-start report
moves to `cold-start-report-v4`, because the rows of `band`, `audible_rungs`
and `dispatches.providers` change shape and new keys are added. A reader of v3
must be updated.

## ASIN-only audiobook plans (#1306)

No persisted schema, report, proposal, decision or envelope version changes,
so upgrading needs no clean rebuild. A report already published with a false
Hardcover or Open Library `unavailable` keeps it; the product has no release
install, so no adoption or relabelling is supplied.

## Owner-entered values successor (#1285)

#1285 moves owner decisions to `human-decision-v3`, whose outcomes add
`owner_entered` and `withdraw`; the `human_decisions` outcome check widens with
it. Case
detail moves to `review-case-detail-v6` (each field's `owner_control`) and
history to `review-case-history-v3`. Owner decisions are canonical authority, so
upgrading requires a clean rebuild: a predecessor database is refused before
any mutation and nothing is adopted or converted. Admission now carries a work's
active manual hold to its successor case, without a schema change. F19a/F20/F46
records are displaced because the decision, persistence and web modules they
bind changed, with permission sets unchanged.

## Visible incumbent descriptions (#1424)

Report `metadata-report-v58` adds one persisted field: every description target
field carries a required boolean `restorable`, and no other destination may
carry it. Audiobookshelf stores a description as sanitised HTML, so
`Fish & chips` is stored as `Fish &amp; chips`. An incumbent description is now
the text it shows: markup is rendered through the providers' visible-text
renderer, paragraphs joined by ` • `, and other text has its character
references decoded. `restorable` records whether that text, written back, is
stored as it was: the stored form equals the text, or the text with only `&`,
`<` and `>` escaped, and the text carries no `<`, `>` or character reference.
The raw stored form is never persisted. Because the field
is in the target, it changes target fingerprints; the review API omits it. A v57
field held the stored form, so the exact SQLite discriminator requires a
**clean rebuild** under ADR-0013. Prior stores are refused before mutation. No
migration, adoption or relabelling is supplied. The comparison version, the
proposal and decision policies and the proposal, packet, outcome and review
envelopes are unchanged.

