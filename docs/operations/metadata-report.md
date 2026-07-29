# Metadata report

The current runtime retains one synchronous single-item report operation
alongside the 0.9 catalogue, foreground batch, and ProposalSet surfaces:

```text
mechanical-librarian metadata-report \
  --config /run/secrets/mechanical-librarian.json \
  --database /data/reports.sqlite \
  --artifact-store /data/cover-artifacts \
  --connection primary \
  --library ebooks \
  --item ITEM_ID \
  --file-index 0
```

Every option is required. There are no default paths, current-directory lookup,
implicit filenames, environment fallbacks, presentation flags, or alternate
output formats.

An exact persisted report can be reopened without operational configuration,
credentials, target access, provider construction, or network access:

```text
mechanical-librarian metadata-report-get \
  --database /data/reports.sqlite \
  --report-id report_<64-lowercase-hex-characters>
```

The current report for a known scope can also be reopened offline:

```text
mechanical-librarian metadata-report-get --database /data/reports.sqlite --current \
  --connection primary --library ebooks --item ITEM_ID --file-identity <64-lowercase-hex>
```

On success stdout is exactly the persisted canonical JSON report followed by
one newline. On failure stdout is empty, stderr contains one fixed bounded
diagnostic, and the exit status is nonzero. Diagnostics never include paths,
credentials, target values, provider bodies, or EPUB text.

Cover evidence is report-only. Where valid cover bytes are available, the report
contains a source outcome and a private artifact reference. The only supported
offline read is a report-bound preview:

```text
mechanical-librarian metadata-report-cover-preview \
  --database /data/reports.sqlite --artifact-store /data/cover-artifacts \
  --report-id report_<64-lowercase-hex-characters> \
  --artifact-id cover_<64-lowercase-hex-characters> \
  --preview-variant preview-320x480-rgba-png-v1
```

It writes raw preview bytes only to non-interactive stdout. It never performs
target, provider, or remote-cover transport and it cannot retrieve originals.

An exact current report can be evaluated by the separate fixed-policy
[`proposal-set-create`](proposal-set.md) operation. That operation references
this evidence without changing or duplicating the report and performs no
provider call.

## Preconditions

- The database path is absent for the first invocation or already contains the
  exact current schema required by the installed increment.
- `metadata-report-v1` through `metadata-report-v21`, and all earlier development
  schemas are rejected before mutation. There is no migration, import,
  preservation, or compatibility parser. Keep an old database offline if
  required, initialize a clean database, and rerun the supported report command.
  This database procedure is the narrower current-runtime application of
  ADR-0013's repository-wide pre-release clean-schema rule.
- On supported Linux deployments, the database parent directory is owned by
  the process user and grants no group or other permissions. The configured
  database basename has no `-journal`, `-wal`, or `-shm` recovery sidecar.
- The artifact-store path already exists as a directory owned by the process
  user and grants no group or other permissions. The command does not create
  this directory.
- Every absolute parent-path component is a real directory rather than a
  symbolic link. Its directory entry is protected by root/process ownership
  and non-writable permissions, or by sticky-directory ownership.
- The container runs under a dedicated Unix identity with no untrusted
  same-identity processes. Privileged and hostile same-identity filesystem
  mutation is outside the supported unprivileged exclusion boundary.
- The configuration uses `config-v5` and names the requested connection and
  library explicitly.
- The target secret and each enabled referenced provider secret are owner-only
  regular files.
- Ebook libraries map to a read-only media mount.
- The selected item is an ebook and the selected file is EPUB.

Audiobook and non-EPUB targets fail before EPUB inspection, provider
construction, provider credential access, egress, or report publication.

Linux production retains the no-follow root-to-parent chain and opens the
database through a private verified hard-link alias while holding the
parent-directory lock for the repository lifetime. It rewalks the current
absolute chain before publication writes and immediately before commit. The
alias and its clean private journal artifacts are removed on close. The lock
coordinates repository instances; ancestor permissions and the dedicated Unix
identity provide exclusion from other unprivileged actors. Non-Linux runs
retain the database no-follow and inode checks for development qualification,
but do not claim the Linux production exclusion guarantee.

## Evidence and replay

The command reads the current target item directly; it does not persist an
inventory. The target projection contains exactly title, subtitle, authors,
narrators, series, genres, publishedYear, publishedDate, publisher,
description, isbn, asin, language, explicit, tags, and cover. Each destination
preserves one exact `value`, `missing`, `null`, `empty`, `unloaded`,
`malformed`, or `oversized` state, including source list order. Every
destination participates in the target fingerprint and final currentness
check. A structurally safe target scalar, contributor/list member, series
component, description, or collection cardinality that exceeds its fixed field
bound is `oversized`; invalid typed syntax is never hidden by size. Unsafe,
private-path, credential-shaped, or otherwise invalid non-title input remains
`malformed`. For the title specifically, a private-path, credential-shaped, or
equivalently unsafe string rejects the target response with a redacted error
before a report is created. A safe oversized title or a missing, null, empty,
unloaded, or non-string title instead preserves its field-local `oversized`,
`missing`, `null`, `empty`, `unloaded`, or `malformed` state and projects no
usable snapshot title; report processing may continue. The selected file index
resolves once to an opaque identity derived by the Audiobookshelf adapter.
Final currentness locates that identity again, so a harmless file-list reorder
does not change the target.

The isolated child receives only the already-open read-only EPUB descriptor,
source fingerprint, and fixed limits. It first captures one bounded immutable
byte snapshot, verifies its digest, and uses that same snapshot for all EPUB
 parsing. It returns only bounded, source-attributed local metadata facts:
 explicit package fields, bounded opening matter, and an independent rear-spine
 indicia pass. The rear pass considers only the final eight accepted spine
 slots in reverse order, opens at most four XHTML/HTML resources, and never
 substitutes an excluded, missing, encrypted, image, unsupported, or duplicate
 slot. It admits only explicit title, target-work subtitle, publisher, imprint,
 creator, controlled contributor role, selected-manifestation publication
 date, distinct contextual date/production credit, or checksum-valid ISBN
 evidence when the same resource has a copyright marker; it is not whole-book
 mining. Subtitle/publisher/imprint require explicit EPUB semantics or an exact
 field label. Copyright holders, printers, distributors, retailers,
 conversion credits, advertisements, previews, and other-work material do not
 become those bibliographic fields. Extracted label values are checked against
 the same conversion, scanning, production, distribution, retail, workflow,
 and promotion vocabulary. Each complete nested or sibling label owns its
 exact occurrence span without a synthetic combined value, while unmatched
 split labels in the same ancestor remain assembled. Repeated equal spans
 remain separate, including complete and split siblings with the same value.
 Production/tool credits never become contributors.
 If that optional rear pass reaches its aggregate resource or fact limit, it
 records a bounded warning while retaining facts admitted before exhaustion as
 well as already validated package and opening-matter evidence; package and opening resource limits
 remain terminal. The retained report exposes that condition as the static
 `rear_resource_limit` local reason.
 Across both pools there are at most nine content opens, 9 MiB of expanded
 content, 786432 projected characters, 150000 nodes, and 80 claims.
Each ISBN fact keeps its exact observed substring, a directly supporting
excerpt, checksum result, canonical value only when valid, and an explicit
ebook, print, unknown, or ambiguous classification with its nonnumeric label
basis. ISBN-10 remains ISBN-10; print and invalid identifiers are retained as
evidence but are never sent merely as ebook inputs. An explicitly non-ISBN
scheme or URI cannot contribute an ISBN-shaped substring.
Every report contains every supported local field in fixed order with one closed
state: `valid`, `absent`, `malformed`, `oversized`, `unsupported`, `untyped`, or
`inapplicable`. These field results are independent evidence summaries, not a
rank or decision. Each field also carries source states: Audiobookshelf target
metadata supports only title in this slice, while EPUB supports the complete
local field vocabulary. Source and DOM occurrence order is retained from the
isolated inspector through persisted replay; content-derived references never
reorder evidence. Unsafe and oversized sentinels remain non-canonical evidence.
Explicit front or rear opening-matter publisher and imprint observations remain
distinct edition claims through inspection, immutable report serialization,
database reopen, and exact replay. Target-work subtitles remain work-scoped.
Each rear occurrence retains its exact bounded excerpt and closed
resource/spine/tail/marker provenance; repeated and conflicting values remain in
source order. Front and rear subtitle/publisher/imprint canonical values share
NFC and whitespace normalization. Rear position does not increase authority.
Provider comparisons link only claims with the same role; no publisher/imprint
hierarchy is inferred. The exact-current persisted report and database
discriminator is `metadata-report-v22`. Every earlier report discriminator,
including `metadata-report-v21`, remains offline and requires a clean current
database under ADR-0013.

Every valid local publication or typed date-event claim retains its exact
observed lexical and binds it to the canonical value and precision produced by
parsing that same lexical. A mismatched canonical date, mismatched precision,
or malformed observed lexical is rejected at the isolated-inspector fact and
IPC replay boundary as well as during claim construction and report replay,
before proposal or reviewer-packet evaluation.
Non-valid local date facts retain the exact observed lexical and typed event,
but their canonical value and date precision are both absent at the isolated
inspector IPC boundary and in the immutable report.

Publication dates use the closed `date_precision_compatibility` comparison
method. Each link records one exact, month-compatible, year-compatible,
finer-conflict, event-incompatible, or manifestation-incompatible relation.
The independently recorded shared precision distinguishes, for example, two
different days in one month from two different months in one year. Original
`YYYY`, `YYYY-MM`, and `YYYY-MM-DD` values are never filled to an invented
precision.

A provider manifestation date is eligible only when a checksum-valid local
ISBN family and provider ISBN family match exactly, both are ebook or unknown
applicability, and no closed provider format or edition/work fact contradicts
that manifestation. Title, creator, work identity, provider rank, and query
order never substitute for this binding. Work-first, copyright, modification,
and unsafe-manifestation dates remain visible with explicit incompatible links
but cannot vote for manifestation fields.
When a provider edition declares a valid but conflicting, duplicate, or
multiple work relation, the report retains exactly one canonical candidate
relation plus one closed `edition_work_relation_contradiction` fact. It keeps
edition-scoped facts, including the lexical publication date and precision,
but suppresses work-scoped claims, evidence, identity bindings, and work cover
authority. The date remains explicitly manifestation-incompatible.
WORK lookup qualification applies the same least-authority rule: it strips
edition claims and evidence except for bounded unresolved publication text
that report finalization needs to express manifestation incompatibility.

Timestamp comparison uses the calendar date stated in each retained lexical
value. Different times or offsets on the same stated date agree at day
precision; different stated dates conflict even when they denote the same UTC
instant. A timestamp and matching day agree exactly at day precision; matching
month or year prefixes are month- or year-compatible respectively. A
nonmatching stated prefix remains a finer-precision conflict at the shared
precision.
Only source lexicals ending in literal `Z` or an exact signed `HH:MM` offset can
be promoted into typed publication claims. Eligibility is checked before date
normalization: parser-admitted offset-second and fractional-offset-second forms
remain replayable as unresolved publication text and cannot vote or participate
in publication-date comparison, even when a zero-seconds offset normalizes to
the same value as `HH:MM`. Report generation skips these retained non-voting
forms before comparing provider dates, so their presence does not abort an
otherwise valid report.

Work-scoped title and creator links retain the original source values and record
one truthful closed comparison method per link: exact bytes; NFC, case folding
and ASCII-whitespace normalization; the additional mapping of non-breaking
spaces, paired curly quotation marks and en/em dashes; or conservative
single-author `Family, Given` inversion. NFC never strips or transliterates
diacritics.
Creator agreement requires the complete ordered same-role set. Structurally
parsed name-to-identity bindings may treat multiple names for one provider
identity as aliases. Incomplete bindings remain non-authoritative and direct
complete-name comparison still applies; conflicting complete bindings,
reordered, duplicated, multi-author-inverted or role/scope-incompatible sets
cannot agree. Alternative
work titles remain distinct original claims bound to the same admitted work;
titles are never assembled from subtitles. Fuzzy matching, token subsets,
variant/translation/abridgement markers, provider rank and repeated queries do
not create agreement or independent corroboration.

The credential-bearing parent combines those facts with the target projection.
Every distinct checksum-valid ISBN family across bounded package, front, and
rear evidence becomes one exact discovery lane. Equivalent ISBN-10/13 values
share a lane while all original claim references, classification, position,
and provenance remain retained. When the target has one complete,
unambiguous title and author set, one work lane runs alongside the ISBN lanes;
title-only or incomplete authors suppress only that lane. More than eight ISBN
families is `query_limit_exceeded` and makes no provider request rather than
selecting a subset. Candidate admission then requires one exact normalized work title, the
complete ordered author list, one provider-local work identity, and no
translation, adaptation, or uncertain abridgement marker. Competing works and
same-title/different-author results are `no_match`.

One observation coalesces semantically identical provider lookups only. Each
logical discovery lane remains visible with its original provenance and replays
the completed immutable provider result; no result survives the observation or
adds request authority.

Every referenced, enabled Hardcover connection makes one fixed credential-bearing
GraphQL query per lane and at most 27 attempts. Every referenced, enabled Open
Library connection makes at most nine exact Search queries, with at most two
edition and two distinct work queries per lane, at most one retry per request,
four retries per observation, and 49 aggregate attempts. Every referenced,
enabled Google Books connection makes one fixed Volumes query per lane, at most
five retries per query and 54 aggregate attempts. All lanes share one client,
response-byte envelope, and 60-second aggregate deadline; Hardcover and Open
Library use shared one-second request-start pacers. Google Books requests six
rows to detect overflow while retaining no more than five candidates.
Credential-free and API-key modes have identical evidence
semantics; the optional key is read only by the controller and appears in no
report, packet, log, diagnostic, or retained raw artifact.
Search admission
is bounded, deduplicated, and canonical by edition key; a third candidate is
terminal before detail requests. Open Library request starts, including
retries, are spaced by the same fixed one-second pacer. Disabled references produce
one `disabled` observation without client construction, credential access, or
egress. Unreferenced providers are absent.

Each Hardcover ISBN or complete-title/author operation uses only current
documented fields and no more than three relationship levels. The work rows
retain bounded tagging IDs and a bounded sibling `tags` root supplies the exact
category relation; the adapter admits taxonomy only after an exact closed join.
An HTTP 200 response containing a valid bounded GraphQL `errors` envelope is
`unavailable`, even when partial `data` is also present, and contributes no
candidate or cover evidence. Its aggregate diagnostic is
`provider_declared_error`, never success. A malformed, empty, or over-bound
error envelope is `malformed`; its content is never retained.

Provider observations contain only bounded failure diagnostics: category,
closed component/stage, optional retryability/currentness and aggregate count.
Adapters classify each rejected optional source member or additive schema key
at the parser boundary as `schema_construction/provider/schema`; the application
canonically merges that aggregate once with attempt and incomplete-outcome
diagnostics. Reopen uses the stored aggregate without recounting it, provider
construction, or egress. Provider names, record values, attempt ordinals,
delays, timings, headers, bodies, exception text, credentials, and rejected raw
members are not diagnostic fields. Counts saturate at the fixed diagnostic
bound, and provider status retains its existing neutral meaning.

Provider results are neutral. Zero rows are `no_match`; every valid non-empty
bounded set is `candidates`; all candidates survive in provider-identity order
with their distinct edition identity and opaque provider-local work identity.
The work lane retains only work title, complete authors, series and sequence,
description, typed taxonomy, same-work first publication, and same-work cover
evidence. It strips ISBN/ASIN, publisher/imprint, edition language,
explicit/subtitle, and every other edition claim. Hardcover preserves its
upstream category exactly: `Genre` maps to genre and `Mood` or
`ContentWarning` maps to tag. A malformed optional taxonomy row, tag link,
category, series entry, contribution, language, publisher parent, cover link,
or date is field- or member-local: valid siblings remain, an invalid value is
not retained, and a rejected neutral value contributes only to the bounded
schema diagnostic. An over-cap optional collection contributes no prefix.
Open Library subjects remain neutral evidence rather than genres or tags.

Open Library additionally preserves a closed, bounded rich-evidence allowlist
from only the admitted Search row and required edition/work detail records:
author and contributor identities, revision and last-modified provenance,
OCLC/LCCN/Wikidata identifiers, subjects/people/places/times, Dewey and LC
classifications, physical format, page count and pagination, publication place,
copyright and first-publication dates, unresolved publication text,
responsibility/distributor/edition/translation statements, edition
descriptions, exact edition/work cover IDs, the edition-work relation, and
work-scoped Search publisher/date/language/edition context. Edition-detail
facts stay edition-scoped; work detail, structurally paired Search authors and
Search context stay work-scoped. A broken optional author, parent, alias,
description, classification, identifier, cover, or other allowlisted member is
local to that field/member and cannot manufacture creator identity. It retains
no raw payloads, arbitrary links or unknown identifiers and performs no author
traversal, edition enumeration, fuzzy fallback, ranking, voting, or provider
fusion.
Each fixed candidate request asks for one row beyond the retained cap so
candidate overflow remains observable. Across all three providers, an admitted
bounded optional collection retains each valid member in source order and
diagnoses each rejected member once. An over-cap collection is instead rejected
as one field with one diagnostic occurrence and no partial-prefix acceptance. A
non-valid typed claim carries no value; rejected neutral evidence exists only
as the aggregate-safe diagnostic. Invalid UTF-8 or JSON,
response/root/candidate-list shape or cardinality, missing/invalid/duplicate
candidate identity, ambiguous collision, and missing or contradictory required
edition-to-work linkage remain record-level failures and emit no orphaned
candidate evidence. No winner, score, merge, fuzzy search, generic retry
framework, or cache exists.

Google Books retains its bounded volume ID as the edition candidate identity
and records the edition-to-work relationship explicitly as `unresolved`;
Google Books exposes no stable work identifier, so none is manufactured and
unresolved volumes cannot produce lane convergence or ISBN work correlation.
Exact ISBN-family lanes and exact complete-title/ordered-author qualification
use the shared discovery plan. Volume-level format, page-count, revision,
subject, and publication text remain typed evidence. In particular,
`publishedDate` strings become selectable publication-date claims only through
the exact ISBN-family manifestation binding above. Otherwise they remain
evidence with an explicit manifestation-incompatible relation. If a
title-and-authors target metadata lookup has no triggering local claim, that
relation uses `local_reference: null`; null never denotes a local claim and is
accepted only for normalized publication text reclassified as manifestation
publication with a non-comparable `manifestation_incompatible` link from that
target-backed work lookup. Malformed publication text and copyright,
modification, work-first, unknown, or other date events cannot retain an
unbound link.
All provider date facts retain the original lexical string, normalized value
when safe, maximum stated precision, closed event, scope, and explicit or
unresolved linkage confidence. Candidate edition/work identity, provider
authority and query kind, and sibling identifier/format facts supply the
bounded source/access/linkage context. The adapter captures one canonical UTC
`accessed_at` value with fixed microsecond syntax at its first actual HTTP
dispatch and retains that value across every later lane and bounded retry in
the observation. Disabled, insufficient-input, query-overflow, repeated-call,
and deadline or budget outcomes reached before any dispatch retain
`accessed_at: null`. Exact report reopen preserves the stored value and never
reads a clock or contacts a provider. No numerical provider-truth score is
manufactured.
Malformed or oversized scalar claim fields remain explicit field-local states,
so valid sibling claims and evidence survive. Google Books optional authors,
categories, revision, publication text, format, and page count follow the same
field-local rule. Its recognized `industryIdentifiers` candidate shape remains
strict: the collection must be bounded and every entry must be one exact
`type`/`identifier` object, so a malformed entry rejects the candidate rather
than being recovered member-locally. A well-formed unsupported identifier type
is silently ignored. Exact volume identity, candidate limits and record shape
remain strict.

Each provider candidate serializes claims in `raw_order` and rich evidence in
canonical order. Before canonical sorting, every admitted evidence occurrence
receives a non-negative `source_ordinal`; equal values remain separate
occurrences rather than being deduplicated. Each rich-evidence item also keeps
its closed kind, exact bounded value, `work` or `edition` scope, and a
controlled role only for contributor identity. Date evidence additionally
keeps its closed event, maximum lexical precision, normalized value when safe,
and explicit or unresolved linkage confidence. Canonical report reopen
preserves these fields and all field states byte-for-byte. Reviewer evidence
IDs are recomputed from provider, candidate and the complete rich-evidence
object, including `source_ordinal`, then checked against the authoritative
report; another occurrence or an invented sibling cannot satisfy that binding.

Each child lane records its opaque identity, kind, matched ISBN family when
applicable, exact triggering local references, typed status, and
collision-free candidate references. The provider aggregate deduplicates the
same edition and records lanes that converge on one provider-local work.
That convergence is correlation within one source, never another vote or an
upgrade from work-scoped to edition-scoped authority.

In addition to one ABS and one declared EPUB outcome, cover-enabled providers
contribute at most eight admitted sources overall and four per provider. Exact
edition, qualified-work/default, and related-edition designations retain safe
provider/work/edition identities plus available ISBN, language, country or
publication place, publisher, publication date, and format context. Each
source records absence, timeout, rejection, unavailability, or a validated
artifact with media type, dimensions, original/preview digests, and safe
artifact references. Identical bytes share content-addressed storage while all
source provenance remains in the report. Per-provider and aggregate truncation
are explicit terminal rows. Optional Open Library cover context that exceeds
its fixed provenance bounds preserves valid metadata and records that cover as
`oversized`; it neither widens the bound nor makes the provider observation
`malformed`. Raw cover URLs and private paths are never
serialized.

The report ID is derived from the complete canonical document, including any
provider access time. Exact-ID and current-scope reopen return the same ID and
bytes without provider access. A fresh provider re-observation records a fresh
access time and therefore creates an immutable successor even when the
bibliographic facts are otherwise unchanged; evidence or access-time changes
advance only the separate current pointer. Earlier reports in that exact
current schema remain retrievable internally by stable ID.

## Authority

The command can read configured target metadata, read the selected mounted
EPUB, contact explicitly enabled referenced providers, and append neutral
reports. Its repository may initialize an absent database or open an exact
current-schema database after schema-object, foreign-key, and SQLite-integrity
checks. It validates report content and current-pointer scope only when selected
by an exact-ID/current-scope read or involved in publication; opening the
repository does not semantically audit unrelated history. It has no
backward-compatibility authority. It cannot browse inventory, review or decide
claims, apply changes, write a service or media file, run background work,
retry, prune, repair, import, downgrade, retain selectively, or migrate any
state.
