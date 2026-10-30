# Metadata report

A metadata report is the immutable, single-item record of what is known about
one book before anyone proposes a change: the current Audiobookshelf metadata,
the evidence read from the book's own files, and what each provider said.
Review preparation builds one per item through the `GenerateMetadataReport`
application operation. The report decides nothing; the
[ProposalSet](proposal-set.md) evaluates it. The report schema version is
listed under
[current versions](../reference/compatibility.md#current-versions).

## Running and reopening a report

There is no installed command or container entrypoint. Contributors run the
operation only through the `report-observe`, `report-inspect` and
`cover-inspect` scenarios of the
[contributor workbench](../development/contributor-workbench.md), against
disposable state. Its `ReportRequest` actions are `observe` (build a report
for a named connection, library and item), `inspect` (reopen one report by
ID), `inspect-current` (reopen the current report for a known scope) and
`cover` (a report-bound cover preview).

The last three need no configuration, credentials, target access, provider or
network. Success returns the exact persisted canonical JSON; failure returns
one fixed, bounded diagnostic with no paths, credentials, target values,
provider bodies or EPUB text. A report's cover evidence is a source outcome
and a private artifact reference. The cover preview is a private
digest-addressed artifact that contacts nothing and cannot fetch originals.

## Preconditions

- The database is absent on first use, or holds the exact current schema.
  Anything else is refused before any write; start a clean database and
  prepare again
  ([clean-state rule](../reference/compatibility.md#the-clean-state-rule)).
- On Linux, the database parent directory is owned by the process user with no
  group or other permissions, and there is no `-journal`, `-wal` or `-shm`
  sidecar. How the database is bound to one file is in
  [SQLite topology](../architecture/sqlite-topology.md#opening-and-file-binding).
- The artifact-store directory already exists, owned by the process user with
  no group or other permissions. The operation does not create it.
- Every absolute parent-path component is a real directory, not a symbolic
  link, protected by root or process ownership and non-writable permissions, or
  by sticky-directory ownership.
- The container runs as a dedicated Unix identity with no untrusted processes
  under it. Privileged or hostile same-identity changes are out of scope.
- The configuration is the current file-mode version
  ([configuration and setup](../reference/compatibility.md#configuration-and-setup))
  and names the connection and library.
- The target secret and each enabled, referenced provider secret are
  owner-only regular files.
- Ebook libraries map to a read-only media mount. An audiobook library is
  prepared only when it does too.
- The item is an ebook whose selected file is EPUB, or an audiobook with 1 to
  512 audio files, all `m4b`, `m4a` or `mp3`, counted after Audiobookshelf's
  own exclusions.

Any other target fails before inspection, credential access, network traffic
or publication. An item over 512 audio files is listed for attention, so the
owner can see what to split, but is never prepared.

## What the report records

### Target metadata

The item is read directly from Audiobookshelf; no inventory is kept. The
projection holds title, subtitle, authors, narrators, series, genres,
publishedYear, publishedDate, publisher, description, isbn, asin, language,
explicit, tags and cover. Each keeps one state (`value`, `missing`, `null`,
`empty`, `unloaded`, `malformed` or `oversized`) with list order intact, and
all feed the target fingerprint and the final currentness check.

- A safe value over its bound is `oversized`; unsafe, private-path,
  credential-shaped or invalid input is `malformed`. Text whose only defect is
  C1 control characters is refused with its own reason but shown as
  `malformed`.
- An unsafe title rejects the whole response before a report exists. Any other
  unusable title keeps its state and the report continues.
- A description is admitted as Audiobookshelf shows it. Markup is rendered,
  with paragraphs and lines joined by ` • `; plain text keeps its exact text,
  with character references decoded. Text that is markup only once decoded, or
  would decode again, is kept as stored. `restorable` records whether writing
  it back would store it unchanged.
- Tags come from `media.tags`, falling back to metadata tags only when that
  field is absent.
- The selected file resolves to an opaque identity, so reordering the file
  list does not change the target.
- An audiobook report records `library_languages`, the languages its library
  declared at preparation.

### Ebook evidence

An isolated child parses one digest-verified snapshot of the read-only EPUB in
three passes: package metadata, opening matter and rear indicia. What each pass
admits, the role and ISBN rules, the shared limits and the full-work
completeness limit are in
[opening ebook evidence](../product/opening-ebook-evidence.md). A package
creator with no stated role keeps no role. Front and rear values stay distinct
claims with their excerpt and position, and conflicts stay in source order.

Every report lists each supported local field in fixed order with one state
(`valid`, `absent`, `malformed`, `oversized`, `unsupported`, `untyped` or
`inapplicable`) plus per-source states. These are summaries, not a ranking.

### Audiobook evidence

Local audiobook evidence covers ASIN, narrators, series and sequence, running
time, performance type, language, description, embedded chapters, and whether
embedded cover art exists. Each claim names its surface: embedded tag, sidecar,
or file and folder names. Chapters are a structured section, not claims.
Embedded cover art is a presence fact with no cover authority.

- A description over 512 characters keeps its first 512 as `oversized`, with
  no canonical value, and is never proposed.
- `und`, `mul`, `mis`, `zxx` and `xxx` are not language claims; undetermined
  language never supports recording identity.
- The MP4 `prID` atom is not an ASIN.

A multi-file item is one unit
([ADR-0066](../architecture/decisions/0066-multi-file-audiobook-composition.md)).
Each file is read by its own isolated child
([ADR-0063](../architecture/decisions/0063-isolated-audiobook-inspector.md)),
and the results compose into one running time, one chapter list
(`embedded_concatenated` or `per_track`), and one claim per distinct
observation carrying the ordinals of the tracks that stated it. Disagreeing
tracks keep both values; disagreeing ASINs each get a discovery lane.

- The item is bounded by file count, summed bytes (`audio_source_bytes`) and a
  600-second reading wall. Exceeding one is `multi_file_budget_exceeded`.
  Revalidation has the same wall; outside preparation an overrun just means the
  report is not current and the item is prepared again.
- One unreadable or hostile track ends the item, recorded as `track_<ordinal>`
  in the owner's reason. A zero-byte file ends it as `unreadable_media`.
  Missing, empty and unreadable files are not distinguished to the owner.
- File names are safe projections: refused characters become U+FFFD, names are
  cut at 255 characters, and an empty result becomes `track-<ordinal>`. Nothing
  is decided from a name.
- Sidecars and folder names are item-level; an item spanning directories gets
  none.
- Adding, removing, reordering, editing or renumbering a track makes the report
  stale. Playing an audiobook does not.

An audiobook holding one EPUB (a supplementary ebook) records it in
`companion`: identity, digest, and the title, creator, ISBN and publisher the
EPUB inspector read. These corroborate the work but are never the recording's
own claims. An unreadable EPUB keeps only its identity, and changing the EPUB
makes the report stale. Other reports have a null `companion`.

### Media health

Media health is a set of typed observations, separate from claims. None names
a field or can become a proposal.

| Observation | Expectation it checks |
| --- | --- |
| `runtime_short_of_provider` | a complete recording runs within 1 min 30 s of the edition's stated running time |
| `chapter_structure_mismatch` | chapter durations align with the matched edition's, allowing one skipped chapter at each end |
| `track_sequence_gap` | the declared track sequence has no hole |
| `track_sequence_disorder` | the declared sequence rises with file order |
| `track_duration_unavailable` | every track states a usable running time |
| `chapter_atoms_partial` | every track carries embedded chapters, or none does |
| `tag_disagrees_across_tracks` | one value per tagged field |
| `asin_disagrees_across_tracks` | one ASIN across the files |
| `container_unexpected` | each file holds the container its extension names |
| `codec_unexpected` | each audio stream is aac or mp3 |
| `duration_unstated` | each container states its running time |
| `stream_encrypted` | no Apple-protected sample entries |
| `media_unreadable` | every file opens and reads within bounds |

The last four come from headers only; no sample is decoded
([ADR-0074](../architecture/decisions/0074-headers-only-media-health-facts.md)).
ISO common encryption is invisible to the probe, so nothing is said about it.
A chapter mismatch is stated only
where identity was settled by chapters. A shortfall is stated once per
edition, and not at all if another provider compared the same edition and
found none. No observation refuses an item. Where the evidence needed is
absent the section says nothing, so an empty section is not an all-clear.

## Provider discovery

Only enabled, referenced providers are contacted. A disabled reference records
one `disabled` observation with no client, credential access or traffic.
Results are neutral: zero rows is `no_match`, any valid bounded set is
`candidates`, and every candidate is kept. There is no winner, score, merge,
fuzzy search or cache. Lanes converging on one provider work count as one
source.

### Ebook lanes

Each distinct checksum-valid ISBN family becomes one lane; ISBN-10 and ISBN-13
forms share it, and print and invalid ISBNs are never sent as ebook inputs. A
target with one complete title and author set adds one work lane. More than
eight ISBN families is `query_limit_exceeded` and sends nothing.

A work candidate is admitted only with one exact normalized title, the complete
ordered authors, one provider work identity, and no translation, adaptation or
uncertain abridgement marker. An ISBN lane skips the work fallback only when
every candidate proves the same target work. Failed lanes are unavailable, not
dissent. A creator list differing only by an omitted middle initial closes a
contradiction but never admits.

### Audiobook lanes

Audiobooks go to the Audible catalog, Audnexus, AudioSilo, Hardcover and Open
Library, never to Google Books, Inventaire or iTunes Search
([ADR-0061](../architecture/decisions/0061-audiobook-provider-posture.md)).
Discovery has two phases:

1. **The item's own plan.** One lane per ASIN in the tags, sidecars or path, or
   a title-and-author search when there is none. An item with no ASIN whose
   previous report for the same tracks admitted an edition carries that ASIN as
   a `prior_admission` claim, which Audible looks up first
   ([ADR-0088](../architecture/decisions/0088-carried-recording-admission.md)).
2. **The ASIN feed.** Only for an item with no ASIN of its own: providers
   that answered no lane are fed unnamed ASINs in order — carried, promoted
   from search by the runtime band, asserted by Audiobookshelf, then
   recording-lookup hints. The fed observation replaces the first one, so each
   provider still states one observation.

At most four ASIN lanes run per item. Hardcover and Open Library have no ASIN
lookup and record `insufficient_input` for an ASIN-only plan.

An ASIN from the item's own tags, sidecar or path is this recording's
identifier, so its answers keep edition scope as returned. Every fed ASIN
(carried, promoted, asserted or hint) obtains evidence but never settles
identity by itself: it admits only through the runtime gate below, and an
answer naming another work keeps only its ASIN and leaves no proposal.

A carried edition stays while any authority verifies it or any lookup failed,
and is dropped only once a lookup answered, none verified it and none failed. A
verified carried ASIN is the one proposed. The owner's current ASIN is never
withheld. Regional ASINs for one recording count once.

The audio section records up to two `discovery_titles` (folder leaf, then a
single-file title tag), dropping any equal to the title, an author, a series
name or each other. With no local ASIN, the first can drive one alternative
work lane, which Audible alone asks after the asserted lane ends `no_match`. It
gives recording authority only.

### Work search ladder

A work lane asks at most three rungs — normalized, subtitle-stripped,
punctuation-folded — and stops on `candidates`. `candidate_limit_exceeded`
triggers one narrowing retry and ends the ladder. On an audiobook plan a fully
refused rung moves on, and an exhausted lane records each rung as
`exhausted_dispatch`. Query text is never stored.

Every shaping family is query-only:

| Family | Rung |
| --- | --- |
| filename underscores; complete first-author filename prefix; bracketed source or retailer tag; labelled ISBN or ASIN; edition label; parenthesized series name, bare number or year; trailing series marker; order-inverted series prefix | normalized |
| `leading_sequence_token`, `right_hand_segment` (audiobook only) | normalized |
| colon or spaced-dash subtitle segment | subtitle-stripped |
| punctuation and ampersand folding | punctuation-folded |
| dotted initials folded to spaced letters (audiobook only) | none |
| `leading_number_stripped`, e.g. `12 Example Title` (Audible asserted audiobook lane only) | last |

Admission comes only from a named compound production
([ADR-0067](../architecture/decisions/0067-decoration-equivalence-and-compound-admission.md)),
never from its families alone:

| Production | Families | Admission |
| --- | --- | --- |
| `author_prefix_then_underscores` | first-author filename prefix, then underscores | admission-enabled |
| `band_parenthesized_series_name` | parenthesized series name, alone | tight band only |
| `band_order_inverted_series_prefix` | order-inverted series prefix, alone | tight band only |
| `band_initials_fold` | dotted initials folded | tight band only |
| `band_corroborated_title_relation` | corroborated title relation, alone | tight band, plus narrator or series agreement |

An admitted production records its `production` and its `applications`
(family, field, ordinal and side) beside the dispatch lineage. Replay
recomputes admission and refuses lineage that is not a production.

### Recording admission

The runtime corroboration gate is a tight band of 90 seconds; the loose band
is 600 seconds
([ADR-0064](../architecture/decisions/0064-audiobook-provider-evidence-vocabularies.md)).
Whole-minute provider runtimes are read as `[60m, 60m + 59]` seconds, used only
where the exact band found nothing.

- A promoted, asserted or carried candidate keeps edition-scope claims only
  when the recording's running time corroborates it; otherwise it is demoted to
  work scope. On the carried lane, a product whose narrators share nobody with
  the recording's own credits is also refused. A recording or product with no
  narrator credits is never disjoint.
- Band productions admit only on an audiobook plan's asserted work lane, inside
  the tight band of a single runtime, with no language or format conflict. The
  band-first and corroborated rules below apply on Audible's asserted lane
  only. A title production needs the product's own series to state what the
  rung stripped. A local title with a variant or format term (dramatised,
  abridged, unabridged, full cast, radio and similar) never admits.
- **Band-first:** if several products admit, one is in the tight band and the
  rest are outside the loose band, the tight one is kept unless it conflicts.
- **Corroborated:** if nothing admits on any lane, one product may admit when
  it is in the tight band, its title is related to the lane title, its narrator
  or series number agrees, and nothing contradicts it. Related means equal
  after folding, equal with the subtitle joined, one a contiguous run of the
  other, or sharing at least half their words. Among several, each must state a
  narrator and none may disagree; the closest runtime wins.
- **Chapter fallback:** when runtime is missing because a track has a
  `duration_unstated` observation, a complete local chapter table starting at
  zero can corroborate an ASIN lane. Both tables need at least five non-uniform
  chapters, each within two seconds and the total within 90 seconds.
  Conflicting runtime statements veto it.

Further audiobook records grant nothing by themselves:

- **`recording_lookup_hints`**: on Audible's asserted lane only, up to two
  title-refused products in the tight band with an ASIN no candidate holds;
  fed last. Every other lane records `[]`.
- **`recording_lookup_candidates`**: bounded candidates kept on a failed set
  for one ASIN pass. Library and Detail show them under Recording source.
- **`refused_products`**: the product a provider returned for each ASIN-only
  stub. Only the carried lane may admit one, through the corroborated rule.
- **`dispatch`**: each lane's `outcome` (`admitted`, `empty`, `refused`,
  `overflow`, `unavailable`, `starved`, `not_dispatched` or
  `empty_or_refused`), `rungs` walked, and on Audible's asserted lane
  `near_miss_seconds`. Ebook lanes record `null`.

Report validation re-derives all of these from the report's own claims.

### Provider limits

Each provider's lanes share one client and byte envelope, and retries, ladder
rungs and narrowing count inside each provider's ceilings. The per-item
deadline is in [time limits](provider-recovery.md#time-limits); request pacing
and rate-limit holds are in
[the retry schedule](provider-recovery.md#retry-schedule).

| Provider | Limits |
| --- | --- |
| Audible catalog | 6 results per search; 25 when the item states one composed runtime, under a 262,144-byte bound, keeping products in the loose band or stating no runtime (more than five is `candidate_limit_exceeded`); 1 MiB aggregate |
| Google Books | requests 6 rows, keeps 5; 5 transport retries per query; 54 attempts |
| Hardcover | 27 attempts; three relationship levels |
| Inventaire | 3-row search, 2 candidates; 3 attempts per request, 27 per observation; at most 2 MiB |
| Open Library | 1 transport retry per request, 4 per observation; 49 attempts |
| iTunes Search | ebook work lanes only; 5 rows; 6 distinct candidates overflow |

- **Hardcover.** An HTTP 200 with a GraphQL `errors` envelope is `unavailable`
  (`provider_declared_error`). `Genre` maps to genre; `Mood` and
  `ContentWarning` to tag. An unlabelled creator is never author evidence
  alone.
- **Google Books.** The work relation is `unresolved`; none is invented.
  Descriptions use the inert Google Books visible-text projection. The
  optional API key appears in no report, log or artifact.
- **Open Library.** A closed allowlist of rich evidence is kept; subjects stay
  neutral, not genres.
- **AudioSilo.** A 404 is `empty_no_match`, not a failure.

A bad optional member is dropped and counted while valid siblings stay; bad
record shape or identity fails the whole record. One provider restating a
candidate differently is `malformed`. Diagnostics hold only category, stage,
retryability and a saturating count — never values, bodies or credentials.
Failed, throttled or deadline-stopped dispatches write value-free `provider_*`
stderr lines ([observability](observability.md)).

### Dates, titles and creators

- Dates keep their exact lexical, precision, event and scope; precision is
  never filled in. Only `Z` or `±HH:MM` offsets become typed claims.
- A provider date counts for this edition only when a checksum-valid local and
  provider ISBN family match exactly. Other dates stay visible as incompatible.
- Title and creator links record one comparison method: exact; normalized
  (NFC, case, whitespace); typographic punctuation (non-breaking spaces, curly
  quotes, en and em dashes); or single-author `Family, Given` inversion.
  Diacritics are never stripped.
- A provider title or subtitle with a control or format character (such as
  U+200B) is a value-free `malformed` claim; over-long text is `oversized`.
  Safe Unicode is kept exactly and never stripped into agreement.
- Creator agreement needs the complete ordered same-role set. Fuzzy matching,
  token subsets, rank and repeated queries never create agreement.

### Covers

The current Audiobookshelf cover is fetched first, within the item's 60-second
window. Besides it and one declared EPUB cover, cover-enabled providers add at
most eight sources, four per provider. A provider cover is admitted only for a
work or edition that provider observed; anything else is refused before
fetching. Identical bytes share storage. Raw cover URLs and private paths are
never stored. For audiobooks, Audible is the only provider whose cover is
fetched, behind its own `cover_enabled` switch, and that cover stays evidence.

## After the report

An ebook, or an audiobook whose report admitted a recording, goes on to a
[ProposalSet](proposal-set.md) and a [review case](review-case.md).

An audiobook with no admitted recording completes as `evidence_only`. It stays
browsable, and its library row links to a read-only evidence view. This is not
a failure and does not fail its scan. Its work is searched again seven days
after publication
([ADR-0089](../architecture/decisions/0089-unmatched-audiobook-research.md)).
If that still admits nothing, an open case is unchanged; if it admits a
recording, the case is replaced.

For an evidence-only item, a work match or an unavailable provider does not
mean no recording exists. Check the provider statuses and the title, author and
ASIN. If runtime disagrees, check the edition, track grouping and completeness.
Retry only when an input or provider condition has changed.

If settlement cannot commit, preparation stops; see
[Preparation stopped](observability.md#preparation-stopped).

## Replay and identity

The report ID is derived from the complete canonical document, including each
provider's `accessed_at`, captured at its first real request (`null` if none
was sent). Reopening returns the same bytes and contacts nothing. A new
observation creates a new immutable report even when the facts are unchanged;
only the current pointer moves. Replay rechecks every invariant above from the
stored report. Reports from earlier schemas cannot be replayed; do not relabel
or rewrite them.

Policy preview replays exact-current reports without changing them; see
[automation policy](../product/automation-policy.md#policy-preview).

## Authority

The operation can read target metadata and the selected mounted media,
contact enabled referenced providers, and append neutral reports. Its
repository may create an absent database or open an exact current one after
schema, foreign-key, integrity and metadata-policy checks. It validates report
content only for an exact read or a publication.

It cannot browse inventory, review or decide claims, apply changes, write a
service or media file, run background work, retry, prune, repair, import,
downgrade or migrate state.
