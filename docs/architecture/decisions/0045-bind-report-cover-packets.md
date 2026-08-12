# ADR-0045: Bind report cover packets to isolated reviewer images

Status: Accepted

## Context

The source-only contained-reviewer kernel accepts bounded canonical text but has
no image transport. Real cover experiments therefore cannot prove that the
exact pixels from previews already admitted to a metadata report are the images
examined by the isolated reviewer. Accepting arbitrary attachment paths, URLs,
originals, or remote fetches would create broader authority than this experiment
requires.

## Decision

Add one closed `cover-packet-review` workbench scenario. It reopens one exact
metadata report and reads previews only through the existing report-bound cover
reader. It projects opaque source references, exact source preview digests and
variant, separate derived transport references and digests, separate original
technical and decoded preview dimensions, incumbent state, bounded provider
provenance, deterministic eligibility and omission reasons, and no target or
service identity.

Pixel-equal normalized previews are one candidate. The inspectable
Audiobookshelf incumbent is retained when present. Remaining candidates are selected
deterministically by identity designation, original-cover technical dimensions,
and source diversity; decoded preview dimensions bind the transported PNG but
do not change that order. The absolute limits are 12 PNG previews, 1 MiB per
preview, and 8 MiB in total.

A normalized-pixel-equal group projects the canonical best observed original
dimensions by pixel area, width, then height; source iteration order cannot
choose its technical-quality facts.

Every canonical source kind represented by a normalized-pixel-equal group
contributes to diversity priority and counts. No single lexicographic source
stands in for the group.

The packet's ordered reference/digest mapping is part of the canonical reviewer
request. The generic contained-reviewer entry point is image-free; the sole
image-capable entry point admits a distinct cover request only through one full
`cover-review-packet-v2` parser. Before packet selection, each exact report-bound
source preview is size- and digest-verified, parsed within the existing
dimension and decompression bounds, decoded as single-frame RGBA, and
deterministically re-encoded from its pixel bytes. A platform-independent
encoder writes filter byte zero for every row, one zlib stream made from fixed
uncompressed DEFLATE blocks of at most 65,535 bytes, and explicit Adler-32 and
PNG CRC values. PNG serialization and compression decisions therefore do not
depend on Pillow, libpng, or a platform zlib build; Pillow remains only the
bounded decoder. The derived PNG contains exactly IHDR, one IDAT and IEND; ICC
profiles and all other ancillary metadata are discarded without being exposed
to the reviewer. Exact decoded pixels and dimensions are unchanged. Every candidate separately binds its complete opaque
source-reference/source-digest set to its derived transport reference and
digest, so neither a foreign source nor foreign transport can be substituted
under an existing packet identity. That parser closes every packet, candidate,
provenance, context, and omission field; validates the content-bound packet
reference, media/variant, preview byte and dimension facts, incumbent state,
canonical producer candidate order, reason-specific omission preconditions,
domain privacy-safe context text, limits, and exact ordered image mapping. The
same semantic validator runs before producer return and reviewer admission;
before either can mount a derived image, it also verifies the complete bounded,
canonically ordered PNG chunk structure and checksums and fully decodes a
single-frame RGBA PNG whose dimensions exactly match the distinct packet
preview facts and do not exceed 320 by 480. It re-encodes the decoded pixels and
requires byte equality with the canonical derived transport. The deterministic
profile admits only exact IHDR, one IDAT containing exactly one
completed zlib stream with no trailing bytes, and IEND. ICC profiles, PLTE, and every other
ancillary or unknown chunk are absent from the derived bytes and cannot cross
the reviewer boundary;
indistinguishable normalized provenance multiplicity is discarded. Duplicate
omissions are created only after final selection and always name a retained
representative; when a whole normalized-pixel-equal group is excluded, each
member carries the actual byte, aggregate, or shortlist omission instead. The
generic request and generic adapter method have no image member or image argument. The
durable pre-egress attempt binds that packet together with the
exact reviewer profile, instruction, and output schema. The local result binds
the request and profile identities. Preview bytes are copied into anonymous
Linux memory descriptors, rehashed, and sealed against write, growth, shrink,
and seal removal before process creation. A preview larger than the profile's
child file limit fails before dispatch. After gated release, the child reopens
every sealed descriptor read-only through its own `/proc`, verifies the same
descriptor identity, seals, size, and digest, rewinds the resulting independent
open-file description, and replaces the inherited descriptor before limits and
exec. Bubblewrap then copies from those child-private cursors through
descriptor-data mounts into private read-only sandbox files before passing them
as repeatable ordered `codex exec --image` arguments. This closes both
byte-mutation and shared file-offset races without asking Bubblewrap to resolve
a `/proc` path mount.
Unknown, repeated, changed, non-PNG, over-bound, or unbound inputs fail closed.
The image-capable adapter reconstructs and fully re-admits the packet and exact
ordered image tuple at its own boundary; importing or directly constructing a
private request shape cannot grant image-mount authority.

The cover-only orchestration recognizes one narrow local CLI recovery signal:
return code 134, bounded nonempty stderr, and strict bounded JSONL containing
exactly one `turn.started` and no `turn.completed` or `turn.failed`. It retries
that signal once after one second, using a fresh job and child process inside
the same bound adapter and authentication lock, with the same admitted request,
profile, prompt, images, model, limits and credential binding. Both dispatches
and the first failure's fixed code, byte count and digest are retained as
value-free attempt evidence. Every other nonzero exit,
including near-miss 134, schema, authentication, setup, sandbox, malformed
output and timeout failures remains fail-closed with no retry.
The cover observation report is version 2 and carries that evidence; frozen
version-1 observation reports remain readable without it only under their exact
frozen instruction identity.
Only the bounded Instruction is authoritative. Canonical input, provider
metadata, images, and OCR text are explicitly hostile evidence, never
instructions.

This narrowly supersedes ADR-0041's statement that the source-only kernel has
no cover transport. All other ADR-0041 constraints remain current.

`cover-identity-report` is a separate local inspector, not a packet extension
or reviewer input. It replays the exact report-bound packet and reports one
closed identity outcome for every retained candidate:
`verified_manifestation_match`, `verified_work_match`, `plausible_match`,
`unknown`, or `conflict`. Verification requires an exact retained provider
identity binding plus compatible accepted identifier evidence (or, for a work,
accepted or associated evidence), except that an exact retrieved EPUB cover
already bound by the report and packet verifies its source manifestation only.
It never confers identity on a provider cover or Audiobookshelf incumbent.
Unrelated identifier evidence, retained edition/work contradictions,
incompatible manifestation publication/format evidence win over provider
verification. An otherwise unresolved report-bound provider cover is eligible
for this limited discovery inference only when its exact edition/work pair is
retained by an ISBN lane; a mixed aggregate does not let a work-only candidate
borrow another lane's provenance. Frozen legacy reports with no lane-candidate
mappings retain the aggregate discovery fallback. Such a cover is
`plausible_match` only with retained title agreement and without a retained
creator/contributor disagreement that has no creator/contributor agreement
(including exact retained overlap), or a deterministic work contradiction.
Missing creator values neither confirm nor veto it; pairwise comparison
disagreements alongside an agreement do not veto it; edition-specific title alternatives,
identifiers, language, publisher, date, or format disagreements do not erase
positive title support. No title agreement, or unopposed creator/contributor
disagreement, remains `unknown`. `plausible_match` is not verified identity.
The projection binds report,
packet, candidate, classifier, and opaque evidence references. It emits fixed
reason codes only, never source/provider identities, metadata values, paths,
URLs, or credentials, and makes no visual, provider, policy, persistence, or
write decision. Candidate admission reopens every bound source preview through
the report reader, verifies its source digest, deterministically re-normalizes
it to the candidate transport identity, and recomputes the report-bound opaque
source reference, normalized provenance, incumbent state, original dimensions,
and preview variant; matching preview bytes alone cannot bind a foreign packet.
Provider default covers with explicit work identity are work-designated for
verification. Edition-specific date evidence is not plausibility support or a
veto once title support is established.

## Consequences

Real cover transport can be qualified without a runtime API, CLI, persistence
schema, production target, provider discovery, write authority, generic
attachment system, or second reviewer credential path. The entire capability
remains outside the wheel and container. Private packets, previews, attempts,
and results remain ignored owner-only workbench state.

## Validation

- Synthetic tests prove exact report binding, digest and order identity,
  incumbent retention, deduplication, deterministic selection and omissions,
  hard byte/count limits, complete PNG decoding and dimension/mode agreement,
  hostile-text treatment, read-only sandbox arguments, typed failure and
  cleanup behavior. Exact byte and digest fixtures cover both a single stored
  block and a multi-block image, including row filters, block boundaries,
  checksums and independent Pillow decoding.
- Exact-artifact checks prove no runtime CLI/API or distribution inclusion.
- Aggregate retained replay under macOS and canonical Linux with Pillow 12.3
  produced exact byte/digest parity for all 147 affected previews, with 147/147
  pixel- and dimension-equal normalizations and deterministic repeats. The same
  frozen 10 packets containing 50 candidates admitted 10/10 on both platforms,
  with zero provider or model calls and zero production access or writes.
- Historical sealed-v2 qualification attempted 17 exact five-preview
  development and qualification-holdout packets. Its aggregate-only receipt
  records 17 `contained_reviewer_invocation_insufficient` failures, zero mapping
  mismatches, zero admitted image or output bytes, p50 129 ms, p95 160 ms,
  successful cleanup, and a failed sandbox aggregate.
- Diagnosis proved that the historic attempt never reached reviewer execution:
  Bubblewrap could not resolve sealed image `/proc` paths. The corrected adapter
  uses child-private verified descriptor cursors as descriptor-data mounts. A
  five-image no-model Linux proof reproduced exact bytes inside the sandbox even
  while the parent repeatedly sought its inherited descriptors to EOF.
- Authentication-only success had also hidden an under-sized private inference
  envelope. A bounded 512 MiB memory and 32-task isolated profile completed a
  structured reviewer probe without widening model, prompt, network, image,
  output, provider, corpus, or production authority.
- The corrected exact implementation **QUALIFIED** one retained real
  five-preview development packet with five admitted ordered observations, zero
  reference mismatch, 16.263-second end-to-end latency, and successful cleanup.
  This remains report-only transport/schema evidence and does not activate
  runtime behavior or establish semantic quality.
