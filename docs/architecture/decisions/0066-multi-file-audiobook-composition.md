# ADR-0066: Compose multi-file audiobook items into one closed evidence set

- Status: accepted
- Date: 2026-09-02
- Owners: project maintainers
- Supersedes: ADR-0063 for the multi-file exclusion
- Superseded by: none

## Context

ADR-0063 admitted audiobook evidence for items whose media is one file and left
multi-file items excluded as a later decision. That exclusion is the larger half
of the library it was written for: an Audiobookshelf item is frequently a folder
of per-chapter or per-part files, and the reference deployment's largest item
carries 352 of them. Those items reached a terminal `unsupported_media` before
any child ran, so their ASIN, narrator, series, duration, chapters, and cover
presence were invisible to the review pipeline even though every surface that
carries them is the same one a single-file item carries.

Composition is what makes the multi-file case different, not extraction. Each
file is still read by the same two isolated children, still yields the same
`AudioInspection` document, and still costs one bounded probe. What has no
precedent is the step after: several per-file documents have to become the one
item-level evidence set the rest of the pipeline consumes — one running time,
one chapter list, one claim per distinct observation, one content digest — and
they have to do it without inventing an election. Local evidence in this project
is retained and attributed, never resolved: two tracks that state different
ASINs are two facts about the item, not a conflict for the inspector to settle.

The item is also the first place where the per-file bounds ADR-0063 installed
are not sufficient on their own. A per-file wall clock and a per-file byte
ceiling multiply by the file count, so an item of 512 files inherits neither a
bounded reading time nor a bounded read volume unless the item itself is bounded.

Two smaller facts were measured while shaping this: Audiobookshelf publishes an
optional `index` and an `exclude` flag per audio file that nothing here read,
and the sidecar sibling listing stopped at 256 entries, so a folder of several
hundred tracks silently produced no sidecar evidence at all — today, and after
any change that only widened the file-count gate.

## Decision

1. **The item is the unit; the file is the provenance.** A multi-file audiobook
   item (2 to `MAX_AUDIO_TRACKS` = 512 files, every one in
   `AUDIO_FILE_FORMATS`) stays one preparable unit. The catalogue keeps
   `singular-supported`, and the item's `file_identity` and
   `file.content_digest` become composite: `audio_set_identity` and
   `audio_set_content_digest` over the ordered per-track values, each of which
   is the identity function on a one-element set so no single-file report
   changes. Order is the target service's own audio-file order, with `index`
   read into `TargetFile.sequence` and files flagged `exclude: true` treated as
   not part of the order, matching what Audiobookshelf itself plays. They are
   dropped before the set is counted, so an item is admitted or refused on the
   width of the recording rather than on the length of its payload's list.
2. **Composition is a pure domain step.** `domain/audio_composition.py` takes
   the per-track `AudioInspection` documents and returns one `ComposedAudio`.
   It performs no I/O, so the rule set that turns several files into one item is
   testable without a probe, a mount, or a child.
3. **Facts collapse, they never elect.** Facts identical in
   `(field, surface, observed, canonical, state, role)` become one claim whose
   provenance names every track ordinal that carried it. Two tracks that state
   different values for the same field and surface keep both claims and add the
   observation `tag_disagrees_across_tracks`; a cross-track ASIN disagreement
   adds `asin_disagrees_across_tracks` and opens one discovery lane per distinct
   canonical ASIN under the existing lane bound. Folder-level surfaces —
   sidecars and `path_folder` — are interpreted once, by track 1's child, and
   carry no track provenance, because they are facts about the item rather than
   about a file. An item whose tracks do not all share one directory has no
   such surface: it is handed no sidecars and no folder name at all, and
   revalidation agrees by expecting an empty manifest. Track 1's directory is
   not the item's when a recording is split over discs, and withheld folder
   evidence is absent rather than wrong.
4. **One duration, composed.** Exactly one `DURATION` claim per item, the
   canonical seconds of the millisecond sum, on surface `container_format`.
   Per-track durations are retained only as `audio.tracks[].duration_milliseconds`.
   A track without a usable duration yields no composed claim at all plus the
   observation `track_duration_unavailable`: a running time short by one part is
   worse evidence than none, because it compares as if it were complete. The
   observation names those tracks, so a set where every track stated a running
   time and only the sum was refused — past the domain's seven-day ceiling —
   records none: the vocabulary is closed, and the absent claim already states
   it.
5. **Chapters state which composition produced them.** `AudioChapterComposition`
   is closed: `single_file`, `embedded_concatenated` (every track carries
   chapter atoms; they are concatenated at cumulative track offsets and the
   ordinals renumbered), and `per_track` (no track carries atoms, so each track
   is one chapter). A mixed item is `per_track` plus the observation
   `chapter_atoms_partial`, never a silent blend of two grammars. An atom
   reaching past its own track's running time would be offset into the next
   track, so its track composes as carrying no atoms and the item takes the
   mixed shape; within one file the same atom is still that file's own claim
   about itself. The `MAX_CHAPTERS` = 1,024 ceiling and the explicit
   `chapters_truncated` flag are unchanged.
6. **The child protocol gains exactly one field.** `audio-inspection-v2` is
   `v1` plus a nullable `title`, so a `per_track` chapter can be named by the
   track's own title tag, falling back to the file-name stem. The title is
   projected whole or not at all: a title cut to the projection bound would
   name the chapter something the file never said, and the exported file name
   is at least a real one. Embedded-cover
   track ordinals are recorded on the report but are computed by the parent and
   deliberately kept off the child protocol: widening the untrusted boundary is
   the cost this record is least willing to pay for a convenience.
7. **The item carries its own budget, and exceeding it is named.** File count
   ≤ 512, per-file bytes ≤ `audio_source_bytes` checked on the opened length
   before anything is digested — which bounds the whole item at that ceiling
   times its file count, implied by the per-file check rather than enforced as
   a second sum — and an item wall clock of `MULTI_FILE_WALL_SECONDS` = 600,
   enforced through the `cancelled` hook the per-file children already honour.
   A composed claim set over `MAX_FACTS` or over the report document ceiling is
   the same failure. Every one of them is the new typed terminal
   `multi_file_budget_exceeded`, which is a real owner-facing failure code
   rather than a reuse of `parser_refused`: the owner's remedy is to reduce the
   item, which is not the remedy any parser failure asks for. The budget arms
   only above one track, so a single-file item is bounded exactly as ADR-0063
   left it. A mounted item wider than the file count is catalogued
   `needs-attention` rather than dropped from the library: an item the owner
   can split on the service is a state they can act on, and an item they cannot
   see is not. Revalidation re-digests every track and so carries the same
   wall: a currentness check that outruns it raises the same terminal, naming
   the ordinal it stopped at. Preparation reports that terminal under its own
   code; the other three currentness callers cannot carry it and answer within
   their own vocabularies instead — the proposal stage and the admission stage
   as `stale`, and a provider retry as a changed source — each of which
   prepares the item again, which is where the budget states itself.
8. **No partial evidence, ever.** One unreadable or hostile track denies the
   whole item. The item takes that track's own existing typed outcome —
   `unreadable_media`, `parser_refused`, `unsupported_media` — with the failing
   track's ordinal recorded on the terminal preparation record as
   `track_<ordinal>` and read into the owner's stated reason, so the owner is
   told which file to look at without a new vocabulary being invented to say
   it. Half an item's tags is not a weaker evidence set; it is a
   confident and wrong one.
9. **Currentness follows every track.** The catalogue reads the ordered
   per-track revision list (the single-track shape unchanged), each entry
   carrying the position the service declares for that track beside its
   revision, and falls back to the item revision only when a track lacks one.
   The publication guard compares the per-track identities and declared
   positions in order and then re-digests every track in manifest order,
   comparing per-ordinal digests before the composed digest. Any add, removal,
   reorder, edit, or renumbering re-prepares the item exactly once — a
   renumbering because the declared index is published as `sequence` and as the
   gap and disorder observations composed from it, and nothing else moves when
   only it does.
10. **The sidecar sibling listing bound rises from 256 to 1,024 entries**
    (hard cap 4,096), because the previous bound was a silent evidence loss for
    exactly the folders this record admits.

The report discriminator advances `metadata-report-v34` to `metadata-report-v35`
under the ADR-0013 clean-rebuild rule: `AudioFacts` gains `composition`,
`tracks`, and `observations`, and no earlier document can be read forward.
`tracks` always names at least one file, so every audio section states the
source its digest and claim provenance are checked against. A track's `name` is
a projection of the file name the mount states rather than the name itself: a
real library holds names no bounded evidence value admits — 71 files of one
75-file item carry the C1 controls a double-encoded en dash leaves behind — and
denying a whole item its evidence set over a value nothing is decided from is
the wrong trade. Every refused codepoint becomes U+FFFD where it stood, the
result is cut to 255 characters, and a name with nothing projectable left is
published as `track-<ordinal>`. The validator is not loosened: a retained
document whose name still carries a raw control character fails closed as
before, and a per-track chapter whose file name projected to nothing readable is
left unnamed rather than titled after this build's own repair.

## Consequences

### Positive

- Multi-file items produce the same closed, per-surface-attributed evidence set
  single-file items produce, and enter the same provider discovery and
  corroboration path with no change to either.
- Cross-file disagreement is recorded rather than hidden. An item whose tracks
  state two ASINs opens a lane for each and says so on the report; the runtime
  corroboration gate remains the only election.
- The reading of a large item is bounded in count, bytes, and seconds, and the
  bound has a name an owner can act on.
- Composition is a pure function, so the rules that matter most — collapse,
  disagreement, duration, chapter kind — are proved without a probe binary.

### Negative

- The child protocol moved. `audio-inspection-v2` is one field wider, and any
  future field pays the same review cost against the same untrusted boundary.
- Preparation of a 352-track item is a serial sequence of 352 probes plus 352
  digests. It is not fast, and the item wall is a fixed constant rather than a
  tuned one; the reference 352-file item is measured against it after
  activation.
- A new terminal code widens the failure vocabulary that six sites hold equal by
  test, and every one of them had to move together.

### Neutral or deferred

- The per-track chapter title is the track's `title` tag or its file-name stem.
  Neither is authoritative: publishers write "Track 07" into both. Deriving a
  better title — from sidecar chapter documents, or from cross-track title
  structure — is a separate decision with its own evidence, and this record
  deliberately does not make it.
- The 2-second chapter-boundary tolerance the provider corroboration gate uses
  was measured against single-file items. Concatenated per-track boundaries may
  want a different one; that is measured after this lands, not assumed here.
- Cross-item duplicate reconciliation, re-tagging, chapter writes, folder
  restructuring, and transcription stay out of scope, and nothing here decodes
  a sample. ADR-0061's provider posture and ADR-0064's evidence vocabularies are
  unchanged.
- Unmounted audiobook libraries stay excluded exactly as ADR-0063 left them.

## Alternatives considered

### Concatenate the tracks and probe the result

Rejected: it requires writing a multi-gigabyte temporary file per item on a
deployment whose mounts are read-only, and it destroys per-file provenance —
the one thing that lets an owner see which part of an item carries which
observation.

### Probe only the first track and treat it as the item

Rejected: it is the partial evidence this record refuses, dressed as a cheap
optimisation. A first track routinely lacks the series, description, or cover
that later parts carry, and its duration is the item's running time only by
accident.

### Elect one value per field across tracks

Rejected: local extraction has no authority to resolve a conflict, and any rule
that picks a winner — most common, first track, longest — would present a
guess with the same confidence as agreement. Retaining both and naming the
disagreement is what the rest of the pipeline is built to consume.

### Map the budget failures onto `parser_refused`

Rejected: the code an owner reads should imply the action they can take. Nothing
about a 600-file item is a parser refusal, and telling an owner their file is
malformed when it is merely numerous sends them to fix the wrong thing.

### A configurable item wall clock and file-count bound

Rejected: both are safety bounds on untrusted input rather than preferences, and
a knob on them is a knob for disabling them. They are domain constants,
measured, and moved by a record like this one when measurement says so.

## Validation

- The fast tier proves composition, the item budget, the admission gate, the
  document invariants, and the catalogue currentness shape against recorded
  probe documents, with no probe binary present.
- The probe tier executes the real probe over a generated multi-track item and
  proves the composed running time, the per-track chapter titles, a cross-track
  disagreement, and single-file replay equality. That the failing track's
  ordinal survives to the owner is proved by the unit and persistence tests:
  the stage that raises it, the row that stores it, and the reason that states
  it are three separate seams, and only the last one is what an owner reads.
- Reopen triggers: a measured need to derive chapter titles from something
  better than a track tag, an item whose reading cannot fit the 600-second wall
  on supported hardware, or an audiobook capability that requires decoding
  rather than probing.

The #1004 missing-runtime chapter fallback is restricted to a single file.
Multi-file chapter offsets still require each track's stated duration; no
chapter endpoint substitutes for the durations this composition requires.
