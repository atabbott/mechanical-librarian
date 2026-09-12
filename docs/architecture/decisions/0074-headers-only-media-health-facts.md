# ADR-0074: State media health from header facts and a closed vocabulary

- Status: accepted
- Date: 2026-09-11
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

ADR-0063 clause 3 bounds what the isolated audiobook inspector may read: one
bounded probe of a file's container, no decoded sample, no network, and every
count bounded. F08 asks the product to tell an owner what is *wrong* with an
item's media. The first unit of #144 derived that from evidence the report
already retained. What remained needed three things ADR-0063 does not settle.

First, whether the probe may state container and stream facts at all. The
inspector already reads `format` and `codec_type` to gate an item and discards
the rest, so an `.m4b` that is really an MP3, a stream that is AC-3 rather than
AAC, and a container that states no running time are all invisible to an owner
even though the headers said so and the probe already read them.

Second, whether an encryption marker survives the stripped probe the image
ships. That build enables the `mov` and `mp3` demuxers and the `aac`, `mp3`,
`mjpeg` and `png` decoders and nothing else. The contract admitted the class
only if a bounded dry probe proved a marker readable from headers, and said to
drop it rather than approximate it.

Third, where a comparison lives when the evidence it was made from does not
survive publication. ADR-0061 demotes a candidate the runtime band did not
corroborate to work scope, which strips its edition-scope `DURATION` claim. A
recording an hour shorter than the edition a search lane matched is exactly the
case the band refuses to admit, so the fact that it is short is measured during
preparation and then destroyed before anything could read it.

## Decision

**1. The probe states container and stream facts, from headers only.** One
file's inspection carries `format_name`, the first audio stream's `codec_name`
and `codec_tag_string`, and whether `format.duration` was stated at all. Four
values, each of which something composes into an observation: a fact carried on
the protocol that no rule reads is a value every layer pays for and no owner is
ever told. No sample is decoded and no byte outside the existing bounded probe
read is touched, so this is inside ADR-0063 clause 3 rather than an extension of
it. The child emits facts and never kinds: it reads one file and names no
ordinal, so the parent composes which track departed and how.

`format_name` is compared by equality, not by membership. It is one string per
demuxer — ffprobe prints the whole comma-joined list of names a single demuxer
answers to — so the MOV family is the single value `mov,mp4,m4a,3gp,3g2,mj2`
however much it looks like a list, and the comparison is to the family the
file's admitted extension names.

**2. The health vocabulary stays closed and the bounds stay named.** Four kinds
join it -- `container_unexpected`, `codec_unexpected`, `duration_unstated` and
`stream_encrypted` -- each with one bound held in `AUDIO_HEALTH_BOUNDS`. The
admitted containers are the two families the admitted file extensions name, and
the admitted codecs are `{aac, mp3}`, held once as `ADMITTED_AUDIO_CODECS`.
Nothing here refuses an item: an AC-3 stream in an `.m4b` is a real recording an
owner may want to keep, and this build is not the authority that decides
otherwise.

**3. The encryption marker survives, through the codec tag and nothing else.**
The bounded dry probe ran the shipped argument list against a generated MP4
whose audio sample entry is `drms`, and the probe reported
`codec_tag_string = "drms"` with the stream otherwise fully described. The same
probe against an ISO common-encryption entry (`enca` wrapping `sinf`/`frma`)
reported `mp4a`: the demuxer rewrites the entry to its original format before
anything is printed, and no other field carries a marker. So `stream_encrypted`
is stated for the Apple protected sample entries and is silent for
common-encryption. That silence is recorded here rather than approximated,
because a scheme whose marker the probe does not surface is one this build
cannot see without decoding.

**4. A comparison outlives the evidence it was made from, as a record and not
as a claim.** Qualification compares a candidate's stated running time to the
recording's before demotion strips it, and records the one-bit answer on the
shaped candidate; report assembly turns that into one `runtime_short_of_provider`
observation naming the candidate. The number is deliberately not kept: it
belonged to the edition scope the candidate did not earn, and retaining it under
another name would be the demotion undone. Assembly states an edition only when
every candidate naming it was found short: qualification runs once per provider
and never holds a second provider's answer, so assembly is the first place a
disagreement can be seen, and a disagreement between authorities is published as
silence rather than as its more alarming half.

**5. Persisted and derived state the same thing exactly once.** A candidate
whose `DURATION` claim survived publication is compared again at read time,
which carries both running times; a candidate named only by the preparation-time
record is stated from that record. The precedence -- derived wins -- is written
in one place in `derive_audio_health` and proved, rather than relied on. The same
rule settles `duration_unstated` against the older `track_duration_unavailable`:
the kind that names the cause is stated and the older kind is left for a track
that stated a running time the domain could not hold.

## Consequences

### Positive

- An owner is told what their file actually holds, from facts the probe already
  read, and is told which bound each statement broke.
- The one class of truncation the report could never state is now stated, for
  the asserted, promoted and search lanes that ADR-0061 demotes.
- No new probe surface, no decode, no network, and no new failure code: the
  inspection's cost and blast radius are unchanged.

### Negative

- `metadata-report-v39` advances to `v40` under ADR-0013: an observation carries
  a candidate citation, a candidate carries the runtime comparison, and four
  kinds join the closed vocabulary. No migration is supplied.
- A preparation-time record is only as good as the preparation that made it. A
  report published before this build states no truncation for its shaped lanes
  and cannot be made to, because the evidence is gone.

### Neutral or deferred

- Common-encryption protection is invisible to this build. Naming it would need
  a marker the stripped probe does not print, which is a decode question and
  therefore an ADR-0063 trigger rather than an in-flight expansion.
- `codec_tag_string` is reported verbatim, including ffprobe's `[0][0][0][0]`
  for a container with no four-character code. It is a header fact, so it is
  carried as stated rather than normalised.
- The admitted extension set and the admitted container table are two tables
  that must name the same extensions. Admitting a fourth file type into one
  without the other would state `container_unexpected` about every file of that
  type, so they are pinned equal by test rather than by convention.

## Alternatives considered

### Derive the shaped-lane truncation at read time

Nothing survives publication to derive it from: the demoted candidate keeps no
`DURATION` claim, which is the whole point of the demotion. The only way to
derive it would be to weaken `_admitted_candidate_shape` and retain an edition
statement the band refused, which is ADR-0061 reversed for a reporting
convenience.

### Keep the seconds short on the persisted observation

A number read off a claim the report deliberately does not keep is the claim
kept under another name, and it would be the only persisted prose in a
vocabulary that otherwise carries no values at all.

### Approximate encryption from a failed decode or an absent duration

A file this build cannot interpret has many causes, and naming one of them
"encrypted" is a guess presented as a finding. The contract's expansion boundary
required the marker or nothing, and the probe supplied the marker.

## Validation

- `tests/slow/test_audio_probe.py` builds one container per class through the
  real probe binary and asserts exactly one kind and no claim for each, against
  a healthy control that differs in a single header field.
- `tests/unit/test_audio_composition.py` proves the parent composes the ordinals
  the child could not name, proves the duration precedence, and pins the
  admitted extension set equal to the admitted container table.
- `tests/unit/test_audiobook_provider_evidence.py` proves qualification records
  the comparison on the demoted candidate and keeps no runtime, and that a
  corroborated candidate records nothing.
- `tests/unit/test_audio_health.py` proves the persisted and derived lanes state
  one truncation per candidate, and that the derived lane wins.
- `tests/unit/test_metadata_report_persistence.py` proves a `v39` producer's
  document is refused rather than read as current.
