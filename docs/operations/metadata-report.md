# Metadata report

The `GenerateMetadataReport` application operation remains the single-item
evidence seam used by authenticated review preparation. Contributors can invoke
it only through the source-only typed workbench against disposable state. It is
not an installed command or container entrypoint. See
[Contributor workbench](../development/contributor-workbench.md).

An exact persisted report can be reopened without operational configuration,
credentials, target access, provider construction, or network access:

The current report for a known scope can also be reopened offline:

Success is the exact persisted canonical JSON report. Failure uses one fixed
bounded diagnostic. Diagnostics never include paths,
credentials, target values, provider bodies, or EPUB text.

Cover evidence is report-only. Where valid cover bytes are available, the report
contains a source outcome and a private artifact reference. The only supported
offline read is a report-bound preview:

The preview is retained only as a private digest-addressed workbench artifact.
It never performs target, provider, or remote-cover transport and it cannot
retrieve originals.

An exact current report can be evaluated by the separate fixed-policy
[ProposalSet](proposal-set.md) operation. That operation references
this evidence without changing or duplicating the report and performs no
provider call.

## Retained identity conflicts

Conflicting package/prologue and full-work evidence remains for owner review.
Keep external identity adjudication separate from observed application inputs.
Unsupported historical report schemas cannot be replayed by the current parser;
do not relabel them or rewrite evidence to make them replayable. Current
synthetic proof is separate from historical replay. See
[opening evidence](../product/opening-ebook-evidence.md#full-work-identity-and-body-completeness)
for the body-completeness limit (#1079).

## Preconditions

- The database path is absent for the first invocation or already contains the
  exact current schema required by the installed increment.
- `metadata-report-v1` through `metadata-report-v21`, and all earlier development
  schemas are rejected before mutation. There is no migration, import,
  preservation, or compatibility parser. Keep an old database offline if
  required, initialize a clean database, and rerun supported review preparation.
  This database procedure is the narrower current-runtime application of
  ADR-0013's repository-wide pre-release clean-schema rule.
- On supported Linux deployments, the database parent directory is owned by
  the process user and grants no group or other permissions. The configured
  database basename has no `-journal`, `-wal`, or `-shm` recovery sidecar.
- The artifact-store path already exists as a directory owned by the process
  user and grants no group or other permissions. The operation does not create
  this directory.
- Every absolute parent-path component is a real directory rather than a
  symbolic link. Its directory entry is protected by root/process ownership
  and non-writable permissions, or by sticky-directory ownership.
- The container runs under a dedicated Unix identity with no untrusted
  same-identity processes. Privileged and hostile same-identity filesystem
  mutation is outside the supported unprivileged exclusion boundary.
- The configuration uses `config-v9` and names the requested connection and
  library explicitly.
- The target secret and each enabled referenced provider secret are owner-only
  regular files.
- Ebook libraries map to a read-only media mount. An audiobook library may map
  to one too.
- The selected item is an ebook whose selected file is EPUB, or an audiobook
  whose 1 to 512 audio files are all `m4b`, `m4a`, or `mp3`.

Every other target — an unmounted audiobook library, an audiobook item over 512
audio files or carrying a file outside those containers, a non-EPUB ebook file —
fails before inspection, provider construction, provider credential access,
egress, or report publication. A mounted item over 512 audio files is listed for
attention rather than dropped from the library, so an owner can see the item
they would have to split; it is still never prepared.

Linux production retains the no-follow root-to-parent chain and opens the
database through a private verified hard-link alias while holding the
parent-directory lock for the repository lifetime. It rewalks the current
absolute chain before publication writes and immediately before commit. The
alias and its clean private journal artifacts are removed on close. The lock
coordinates repository instances; ancestor permissions and the dedicated Unix
identity provide exclusion from other unprivileged actors. Non-Linux runs
retain the database no-follow and inode checks for development qualification,
but do not claim the Linux production exclusion guarantee. Same-process
dependent opens borrow the live root's admission there as well, so a sibling's
rollback journal is not recovery state.

## Evidence and replay

The operation reads the current target item directly; it does not persist an
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
 source-explicit series membership and sequence,
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
Series is retained only from an exact `Series: NAME` label or a complete
`Book`, `Volume`, or `Part N of NAME` indicium. Decimal positions remain
lexical values; advertisements, reading lists, prose, and trailing text are
rejected rather than inferred.
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
discriminator is `metadata-report-v43`. Every earlier report discriminator,
including immediate prior `metadata-report-v42`, remains offline and requires a
clean current database under ADR-0013.

An audiobook item in a mounted audiobook library, with 1 to 512 audio files in
supported containers, is prepared as a local-evidence report and nothing
further. Its closed evidence set carries ASIN, narrator contributions, series
and sequence, running time, performance type, language, description, embedded
chapters, and whether the file carries embedded cover art — each attributed to
the surface it was read from: an embedded tag, a sidecar document beside the
file, or the exported file and folder names. Chapters are a bounded structured
section rather than claims, and the embedded cover is a presence fact that
grants no cover authority. Every configured audiobook provider is contacted
while the report is built, and its observations are retained beside the local
evidence. Discovery runs in two phases within that one preparation. The first
is the item's own plan: one product lane per ASIN its tags, sidecars, or path
carry, or a title-and-author search when it carries none. The second feeds the
per-ASIN providers the ASINs no local surface named — those the runtime
corroboration gate promoted from the first phase's candidates, then the one
Audiobookshelf asserts on the item — and is dispatched only to providers whose
first-phase observation answered no lane at all. Their first-phase observation
is replaced rather than merged, so every provider still states exactly one
observation per report. Across all three origins an item runs at most four ASIN
lanes, in the order local, promoted, asserted, and there is no second promotion
round. An item that already carries its own ASIN feeds nothing. A lane's origin
is read off its input references: an asserted lane names the ASIN claim whose
source is the target, and a promoted lane names the same references the search
lane used. A candidate returned on a promoted or asserted lane keeps
edition-scope claims only when the recording's own running time corroborates
it, exactly as an uncorroborated search candidate is demoted to work scope: an
assertion obtains evidence and never settles identity, under
[ADR-0061](../architecture/decisions/0061-audiobook-provider-posture.md). In
the field results the asserted ASIN reads as `valid` on the `target_metadata`
source, which continues to read `unsupported` for every field that surface does
not carry. The two authorities that state a cover asset of their own — the
Audible catalog and iTunes Search — have their covers acquired through the same
bounded cover pipeline an ebook's use, each behind its own `cover_enabled`
switch, and the retained cover evidence records where two independent
authorities picture the same art. Its covers stay evidence only, so no cover consideration is prepared
for one and no cover it carries is elected. What follows the report depends on
what the report says. The runtime corroboration gate is a tight band of 90
whole seconds, measured and owned by the corroboration rules
([ADR-0064](../architecture/decisions/0064-audiobook-provider-evidence-vocabularies.md));
the report's own duration comparison, which decides whether a stated claim
links to a local one at all, states the same number today but is a separate
parameter that is retuned separately. When the runtime corroboration band
admits some candidate the report retains -- one stating this recording's own
running time -- or the narrow chapter fallback below qualifies, preparation queues the proposal stage exactly as an ebook's does, and the
recording becomes a review case the owner decides
([ADR-0069](../architecture/decisions/0069-audiobook-review-cases-and-terminal-outcomes.md)).
Complete chapter timing and structure can corroborate recording identity when
overall runtime is unavailable, through local, target-asserted or WORK-derived
ASIN acquisition. Missing local runtime requires one track with an actual
`duration_unstated` observation, no duration claim or unavailable-track evidence.
Its complete positive chapter table begins at zero without gaps or omitted rows;
the final endpoint supplies coverage only and never creates a duration claim.
With known runtime the endpoint remains within one second. Both complete vectors
contain at least five nonuniform whole-second durations, with direct per-span
agreement within two seconds after the existing nearest-second local projection
and aggregate agreement within 90 seconds.
Malformed, multiple, nonpositive or conflicting matched runtime statements veto
fallback. Supporting runtime is exactly one consistent valid value or explicit
absence; an unmentioned value remains unknown. Existing runtime admission runs
first. Runtime-qualified WORK results retain their existing promotion path.

Failed-set audiobook WORK results retain bounded `recording_lookup_candidates`
for one ASIN acquisition pass and runtime vetoes, separately from admitted
candidates. These hints cannot create metadata alternatives, promotion or owner
choices. Retry reconstructs the same context and lanes from retained evidence.
Library and Detail identify the supporting provider and candidate under Recording
source. This adds owner review without automatic or effect authority.

Those decisions are applied through the same guarded path an ebook's take
([ADR-0071](../architecture/decisions/0071-book-media-guarded-application.md)).
When no candidate is admitted -- unanswered, answered about a work rather than
this product, or stating a running time this recording does not have -- there is
nothing to compare a proposal against, so
preparation completes on the `evidence_only` outcome in the same transaction
that publishes the report, and that transaction records the item's review
package, so the work is browsed and searched by its evidence rather than by a
failure. That package row is also the address of the report: the library row
links to a read-only detail view of the recorded evidence, which is the only
way to read it for a work that has no review case. A recorded completion is not
a failure code: it raises no operational incident, counts toward nothing an
owner is asked to unblock, and does not mark its scan as having failed — a
prepared audiobook counted as a failure would discard every absence its scan
staged, including a disappeared ebook's in a mixed-format library. Each file's digest is bounded by `audio_source_bytes` rather
than the EPUB archive limit, and its catalogue fingerprint follows the audio
files' own revisions, so playing an audiobook does not re-prepare it.

A multi-file item is one preparable unit. Every file is read by its own isolated
child, and the per-file documents are composed into one evidence set: one
running time summed across the tracks, one chapter list stated as
`embedded_concatenated` or `per_track`, and one claim per distinct observation
carrying the ordinals of every track that stated it. Tracks that disagree keep
both values and record the disagreement rather than electing one, and a
cross-track ASIN disagreement opens a discovery lane for each. The item is
bounded by file count, by summed bytes, and by a 600-second reading wall;
exceeding any of them is the terminal `multi_file_budget_exceeded`. The same
wall bounds revalidation, which re-digests every track: a currentness check
that outruns it is that terminal during preparation, and elsewhere — a proposal
set, an admission, a provider retry — the report is simply not shown current
and the item is prepared again. One unreadable or hostile track denies the
whole item that track's own outcome — an item is one closed evidence set or
none. The refusing track's ordinal is recorded on the terminal preparation
record as `track_<ordinal>` and stated in the owner's reason, so a
three-hundred-file item names the one file to look at.
The 512-file bound is measured on the files the service plays, after its own
excluded records are dropped. Every audio section names at least one file: the
manifest is what the item's content digest and per-file claim provenance are
checked against. Each entry's `name` is a projection of the file name the mount
states rather than the name itself: a character the retained report refuses —
a control character left behind by a double-encoded dash, for instance —
becomes U+FFFD where it stood, a name longer than 255 characters is cut to it,
and a name with none of its own characters left is published as
`track-<ordinal>`; whether a name was repaired or replaced is a fact of the
projection, so a file really named `track-1` keeps its name.
Nothing is decided from the name, and a per-track chapter whose file name
projected to nothing readable is left unnamed rather than titled after the
repair. Sidecars and the folder name are item-level evidence, so an
item whose tracks are split across directories is given none of them rather
than the first track's. A report is stale when a track is added, removed,
reordered, edited, or renumbered: the position the service declares for each
track is published as its `sequence`, so the catalogue fingerprint and the
currentness check both follow it. Audiobook
libraries without a named read-only mount stay excluded, exactly as before. See
[ADR-0063](../architecture/decisions/0063-isolated-audiobook-inspector.md) and
[ADR-0066](../architecture/decisions/0066-multi-file-audiobook-composition.md).

The owner-facing evidence view also states what is wrong with the media itself,
as a closed vocabulary of typed observations distinct from the claim set: none
names a target field, and none can become a proposal or a decision. Most of the
section is derived at read time from what the report and the terminal
preparation record already retain, so it exists for every report already
published and no report is rebuilt to obtain it. The rest is read from
observations the preparation recorded, because the evidence they were made from
does not survive publication.

| Observation | Cited | Bound |
| --- | --- | --- |
| `runtime_short_of_provider` | the local `DURATION` claim references and the candidate identity | the local running time is within 90 seconds of the running time the matched edition states |
| `chapter_structure_mismatch` | the chapter composition kind and the candidate identity | the embedded chapter durations align with the matched edition's, allowing one skipped chapter at each end |
| `track_sequence_gap` | each track ordinal and the sequence that track declared | the declared track sequence runs without a hole |
| `track_sequence_disorder` | each track ordinal and the sequence that track declared | the declared track sequence rises with the file order |
| `track_duration_unavailable` | each track ordinal and the sequence that track declared | every track states its own running time |
| `chapter_atoms_partial` | each track ordinal and the sequence that track declared | either every track carries embedded chapters or none does |
| `tag_disagrees_across_tracks` | the field, then each track ordinal and its declared sequence | one item states one value per tagged field |
| `asin_disagrees_across_tracks` | the field, then each track ordinal and its declared sequence | one item states one ASIN across its files |
| `container_unexpected` | each track ordinal and the sequence that track declared | each file holds the container its admitted file type names |
| `codec_unexpected` | each track ordinal and the sequence that track declared | each file's audio stream is aac or mp3 |
| `duration_unstated` | each track ordinal and the sequence that track declared | each file's container states the running time of what it holds |
| `stream_encrypted` | each track ordinal and the sequence that track declared | no file's samples are protected by a rights-management scheme |
| `media_unreadable` | the refusing track, or the item where it has one file | every file of the item opens and reads within the supported bounds |

The last four are composed from four values, and only those four, that each
file's own headers stated about itself: the demuxer family the probe named for
the bytes, the first audio stream's codec name, that stream's sample-entry tag,
and whether the container stated a running time at all. Each is checked against
one thing — the family is compared to the one the file's admitted extension
names, the codec to the two this build expects, the tag to the protected
sample-entry codes, and the running time to its own presence. Nothing else about
the container is read or judged. They are read from headers only: no sample is
decoded, and the probe reads no byte it was not already reading to gate the item
([ADR-0074](../architecture/decisions/0074-headers-only-media-health-facts.md)).
None of them refuses an item; each is a statement an owner acts on.

`stream_encrypted` is stated for the Apple protected sample entries, whose
four-character code the probe reports verbatim as the stream's codec tag. An
ISO common-encryption entry is rewritten to its original format before the
probe prints anything, so this build cannot see it and says nothing about it
rather than guessing.

A file whose headers state no running time is named by `duration_unstated`
alone. `track_duration_unavailable` is true of it as well, so the kind that
names the cause is the one stated and the older kind is left for a track that
stated a running time outside what the domain holds.

A provider's chapter structure survives publication only on a corroborated
candidate, so a chapter mismatch is stated only for items whose identity was
settled by structure. Where the evidence is absent the section is silent rather
than reassuring.

Truncation is stated for every lane, through two routes that never state it
twice about one edition. A provider's running time survives publication only on
a candidate that kept an edition-scope `DURATION` claim — the lanes fed by an
ASIN the item's own tags, sidecars, or path carry, and any candidate the runtime
band admitted — and for those the comparison is made again at read time and
carries both running times. A search, promoted, or asserted candidate the band
did not corroborate is demoted to work scope before the report is written, so
preparation makes the comparison while the claim still exists and records
`runtime_short_of_provider` against that candidate. The record names the
candidate and this recording's own running time, and says plainly that the
edition's was not retained: the number belonged to the scope the candidate did
not earn. A candidate named by both routes is stated once, from the retained
evidence. The record is unanimous or it is not made: where two providers answer
about one edition and one of them compared the same two running times and found
no shortfall, the disagreement is published as silence, because a disagreement
between authorities is not evidence that this recording is short.

A file with no bytes is an unavailable source rather than a parser refusal: the
length is read at the open, so the item ends `unreadable_media` under the empty
track's ordinal and no isolated child is spawned for it. Missing, empty, and
unreadable files are not yet distinguished from one another to the owner.

Google Books descriptions use one inert, bounded
`google-books-html-visible-text-v1` projection. Its report-bound transformation
evidence is retained only when rendering succeeds; scripts, styles, event
attributes, malformed markup, empty output, and bound failures remain
non-usable typed field states. The renderer fetches nothing and adds no
automatic authority. Hardcover taxonomy retains the exact upstream type and
source ordinal alongside any existing manual selection projection. Item-snapshot
tags prefer `media.tags`, including when `media.metadata` is unloaded, and fall
back to metadata tags only when that current ABS field is absent.

Every valid local publication or typed date-event claim retains its exact
observed lexical and binds it to the canonical value and precision produced by
parsing that same lexical. A mismatched canonical date, mismatched precision,
or malformed observed lexical is rejected at the isolated-inspector fact and
IPC replay boundary as well as during claim construction and report replay,
before proposal or deterministic packet evaluation.
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
cannot agree.
An unlabelled Hardcover creator remains a neutral, identity-bound work claim
when its complete ordered set exactly substantiates an existing target-backed
successful ISBN or work lookup. It never becomes author evidence by itself.
An author-labelled creator collection that also contains neutral or other
creator roles is rejected as a whole rather than projected as a typed subset.
One independently qualified provider's complete explicit author set compares
complete ordered identity bindings first. Provider-native identity namespaces are not a global
person registry, so nonmatching namespaces fall back only to exact closed
normalized names.
Repeated endpoints and providers with the same authority are one source;
partial, duplicated, reordered, mixed-role, malformed, or contradictory sets
remain unresolved.
Alternative work titles remain distinct original claims bound to the same admitted work;
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

An ISBN lane suppresses that provider's bounded WORK fallback only when its
entire non-empty candidate set proves one exact target work under the same
closed admission grammar. A set containing one contradictory candidate, one
candidate without enough work identity, or more than one work identity keeps
the fallback. This is a request-scheduling rule, not an evidence election:
failed and unanswered lanes remain unavailable rather than dissent, and every
returned claim stays in the immutable report.

After that fallback, a contradictory provider ISBN candidate remains unavailable
when an existing title query overlaps and its complete creator set differs only
by one omitted middle initial per creator: `Given Family` and `Given I[.] Family`
are the only accepted shapes, with equal two-or-more-letter alphabetic given and
family names and a matching initial wherever both sides state one. The
comparison requires a complete
one-to-one creator matching and rejects duplicates, cardinality changes, full
middle names, initial-only given or family names, and arbitrary punctuation
folding. This narrow display exception can close a contradiction; it never
admits a candidate, establishes positive identity, or grants field authority.

Work search shapes are deterministic and never alter retained target values,
claims, or evidence. The domain owns this closed table; each row remains
independent so later election work can consume the same grammar without
inheriting query authority:

| Family | Query rung | Admission |
| --- | --- | --- |
| filename underscores | normalized | query-only |
| complete first-author filename prefix | normalized | query-only |
| bracketed source or retailer tag | normalized | query-only |
| labelled checksum-valid ISBN or bounded ASIN | normalized | query-only |
| edition label | normalized | query-only |
| parenthesized series name | normalized | query-only |
| parenthesized bare number, excluding years | normalized | query-only |
| parenthesized year | normalized | query-only |
| trailing series or sequence marker | normalized | query-only |
| order-inverted series prefix | normalized | query-only |
| colon or spaced-dash subtitle segment | subtitle-stripped | query-only |
| broad punctuation and ampersand folding | punctuation-folded | query-only |

Every table row stays query-only. Admission comes only from a closed compound
production that names an ordered composition of rows and is reviewed as one
rule; composition is never implicit, and a production admitting never admits any
of its constituent rows alone:

| Production | Ordered families | Side | Admission |
| --- | --- | --- | --- |
| `author_prefix_then_underscores` | complete first-author filename prefix, then filename underscores | local | admission-enabled |

An admitted compound records its ordered lineage in the persisted report beside
the base dispatch lineage:

```json
{
  "families": ["filename_author_title_prefix", "filename_underscores"],
  "production": "author_prefix_then_underscores",
  "side": "local"
}
```

Replay recomputes the admission and rejects any persisted lineage whose ordered
families are not an admission-enabled production, whose `production` name does
not derive from those families, or whose side is not `local`.

The filename-prefix query grammar requires the complete prefix token sequence
to equal the complete first ordered author. It permits no inversion, initial
expansion, reordering, partial author, alternate author, or fuzzy match. The
compound production adds one further closed requirement: the remainder after the
closed separator must contain at least one filename underscore, so a
prefix-only or an underscores-only title is still rejected. Query
shaping grants no identity authority: the same exact domain decision applies
during initial qualification, overflow-witness qualification, persisted
readback, retry-successor merging, and ProposalSet replay.

The work lane dispatches at most three distinct widening variants in that order:
normalized, subtitle-stripped, then punctuation-folded. It stops immediately on
`candidates`. A `candidate_limit_exceeded` result dispatches exactly one
provider-specific narrowing retry and does not resume the widening ladder.

| Provider | Normalized and widening grammar | Narrowing grammar |
| --- | --- | --- |
| Google Books | quoted `intitle` plus one quoted `inauthor` per author | quoted `intitle` plus one joined, quoted `inauthor` phrase |
| Hardcover | GraphQL title equality plus author-name membership in the complete author set | GraphQL title equality plus primary-author equality |
| Inventaire | one `search` term containing the title and ordered authors | the same bounded term after the shared narrowing transformation |
| Open Library | structured `title` and joined `author` search parameters | one quoted title phrase plus quoted author phrase in `q` |

One observation coalesces semantically identical provider lookups only. Each
logical discovery lane remains visible with its original provenance and replays
the completed immutable provider result; no result survives the observation or
adds request authority.

Every referenced, enabled Hardcover connection remains within 27 attempts.
Every referenced, enabled Inventaire connection retains at most two candidates
from a three-row work search and remains within three attempts per request and
27 attempts per observation. Its aggregate response-byte allowance is the
smaller of nine configured response limits and 2 MiB. Entity and label lookups
use repeated query keys in batches of at most eight; they are never comma-joined.
Every referenced, enabled Open Library connection keeps at most one transport
retry per request, four transport retries per observation, and 49 aggregate
attempts. Every referenced, enabled Google Books connection keeps at most five
transport retries per query and 54 aggregate attempts. Query-ladder and
narrowing dispatches count as retry dispatches inside those existing ceilings;
they do not create a separate provider-spend budget. All lanes share one client,
response-byte envelope, and 60-second aggregate deadline; Google Books,
Hardcover, Inventaire, and Open Library use shared one-second request-start
pacers across foreground and recovery work. Google Books requests six
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

Inventaire validates an exact missing-ISBN response before recording no match:
empty entity and redirect maps must accompany the requested ISBN in `notFound`.
A missing ISBN still permits the existing bounded title-and-author fallback.
Search hydrates at most three rows, removes only explicit wrong-work identities,
and retains at most two candidates. Missing author labels remain limitations,
not proof of a different work; possible identities still count toward overflow.
The author-specific `wdt:P50` property supplies WORK author claims, including
absent claims for unresolved names, so a partial author list cannot establish
complete identity. Existing set-level ambiguity and cover-admission guards
remain authoritative. Canonical Inventaire cover outcomes survive report
publication, including unavailable and timeout evidence (#1006).

Inventaire ISBN lookup maps only namespaced `wdt:P212` and `wdt:P957` edition
identifiers. It follows at most one `wdt:P629` work relation, resolves bounded
Wikidata entity labels in English, then `fromclaims`, then deterministic
language-key order, and maps series plus ordinal from `wdt:P179`/`wdt:P1545` at
work scope. Work-search rows are identities only: selected rows are hydrated
through the entity endpoint before title, creator, series or ordinal mapping.
Missing or unusable optional labels omit only their label-dependent claim and
add a bounded schema diagnostic; other valid candidate evidence remains.

Provider observations contain only bounded failure diagnostics: category,
closed component/stage, optional retryability/currentness and aggregate count.
Adapters classify each rejected optional source member or additive schema key
at the parser boundary as `schema_construction/provider/schema`; the application
canonically merges that aggregate once with attempt and incomplete-outcome
diagnostics. Reopen uses the stored aggregate without recounting it, provider
construction, or egress. Provider names, record values, attempt ordinals,
delays, timings, headers, bodies, exception text, credentials, and rejected raw
members are not diagnostic fields. Counts saturate at the fixed diagnostic
bound, and provider status retains its existing neutral meaning. One provider
restating one candidate identity with claims or evidence that disagree is
`malformed` with an `identity/provider/identity` diagnostic on every candidate
lane and on the observation. The status-derived
`malformed_over_bound_response` is withheld only when the observation itself
carries that identity diagnostic and every one of its lanes reporting
`malformed`, `oversized`, or `candidate_limit_exceeded` carries it too, and at
least one such lane exists. A lane holding any of those three statuses for its
own reason therefore keeps `malformed_over_bound_response` alongside the
identity diagnostic.

Provider results are neutral. Zero rows are `no_match`; every valid non-empty
bounded set is `candidates`; all candidates survive in provider-identity order
with their distinct edition identity and opaque provider-local work identity.
When Hardcover's exact six-row edition probe has one safely qualified work, it
records a terminal edition-enrichment overflow witness: only the provider work
identity, validated work title, ordered author claims, and exact author
identity bindings. It is not a candidate; it cannot enter proposal, review, or
cover selection. Every edition identity, field, row, diagnostic, and cover is
discarded. Any incomplete, ambiguous, duplicate, or contradictory linkage
fails closed without a witness.
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
collision-free candidate references. A successful work lane additionally
records the base rung, the ordered exact query transformations, whether its one
candidate-limit narrowing retry occurred, and any admitted transformation
family and side. Dispatched query text is never persisted. The provider aggregate deduplicates the
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
source provenance remains in the report. A provider cover is admitted only
against that same provider's observed candidates: by work identity, or, when
the provider resolved no work and states the unresolved placeholder, by an
edition identity it actually observed. A different or absent edition is refused
before any fetch and records no source row, so a refusal is counted from
provider capture rather than read from the report. Per-provider and aggregate
truncation are explicit terminal rows. Optional Open Library cover context that exceeds
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

The operation can read configured target metadata, read the selected mounted
EPUB, contact explicitly enabled referenced providers, and append neutral
reports. Its repository may initialize an absent database or open an exact
current-schema database after schema-object, foreign-key, SQLite-integrity, and
exact-current metadata-policy document checks. It validates report content and
current-pointer scope only when selected by an exact-ID/current-scope read or
involved in publication; opening the repository otherwise does not semantically
audit unrelated report history. In the supported Linux service, that complete
admission runs once for the root locked inode; derived connections re-prove the
same live filesystem authority and borrow the result. The guarded-write
scheduler likewise retains the end of a completed decision pass while idle and
restarts the pass only when an owner or preparation event wakes it; unattended
recovery admits one permission-waiting candidate per serialized cycle so owner
reads cannot wait behind a large parsing page. It has no
backward-compatibility authority. It cannot browse inventory, review or decide
claims, apply changes, write a service or media file, run background work,
retry, prune, repair, import, downgrade, retain selectively, or migrate any
state.

## Rich metadata policy preview

The policy notebook and `policy-preview` CLI read at most 20 exact-current
review lineages across the complete response, in stable library then
connection/item/case order. The persisted
pair establishes current lineage; preview replays the immutable report through
the pure proposal evaluator under the requested resolved draft, then projects
the eight closed rich-field choices. The response reports the total and whether
the sample was truncated. It performs no provider request, database mutation,
decision, activation, queue transition, target call, or media write.

Provider observation returns one complete application-owned result. Initial report
generation consumes its observation, descriptor tuple and operational evidence
together, normalizes diagnostics once, and validates descriptor ownership before
cover acquisition. An unexpected dispatch failure still yields unavailable lanes
using its declared partial snapshot; cancellation and interruption propagate.
No-dispatch outcomes invent neither access time nor attempts. Later lanes and
report reads retain the first actual access time.

Preparation retains the exact committed successor before report readback or
notification. If settlement cannot commit, **Preparation stopped** preserves the
last durable progress and keeps published Library books available. Restart the
Mechanical Librarian service to resume its retained checkpoints; browser refresh
and Scan cannot clear the runtime stop. The authenticated incident is runtime
evidence, not a durable terminal work row. See [observability](observability.md)
and [the recovery decision](../architecture/decisions/0078-preparation-claim-and-crash-recovery.md).

## Author evidence authority

EPUB author evidence now uses whole attribution statements and their structural context. Title-page By/Written by statements and explicit Author:/Authors: labels retain truthful author roles; neutral package creators and unlabelled semantic creators retain role=None. Photo, cover, copyright and body text cannot supply inferred authorship. Report `metadata-report-v46` and extractor `epub-opening-v4` reject falsely typed predecessor evidence: **clean rebuild required**, with no migration or adoption (#1083).


## Provider title admission

Provider title and subtitle claims must satisfy the existing public-safe and closed comparison validation. Unsupported format characters, including U+200B, become value-free MALFORMED claims; overlong input remains OVERSIZED. Safe Unicode is retained exactly, never stripped into agreement. Rejection references bind the rejected state and absent value; valid sibling references are unchanged. Existing work and recording admission still determines which claims survive. A Hardcover overflow witness requires a valid work title and fails as a malformed provider result otherwise. Report `metadata-report-v47` requires a **clean rebuild**, without migration or adoption (#1084).

Language evidence remains verbatim, including `UND`/`und`. Report v48 binds
English/code equivalence and excludes undetermined language from positive
recording identity; real base or script conflicts still withhold recording
authority. Local audio facts share one selected-audio correlation, including
author evidence. This does not itself establish a recording match. Prior report
and SQL state require a clean rebuild under ADR-0013 (#1071).

## Shared Google burst admission (#1082)

An exact Google burst response holds later HTTP starts in the same composed
process until its monotonic deadline. Suppressed work remains rate-limited and
counts as undispatched incomplete logical work, with zero physical attempts and
no invented access time. Replay counts retain their existing meaning. Generic
429, mixed specific/generic, authentication, authorization and daily-quota
precedence are unchanged. A shared hold does not mean the source has no match.

iTunes Search retains its complete-result bound: five rows with one supported
match and distinct distractors can admit that match; six distinct candidates or
an oversized raw result list overflow. Over-limit lists are rejected before
member projection. No cap increase, truncation or effective narrowing is claimed.
