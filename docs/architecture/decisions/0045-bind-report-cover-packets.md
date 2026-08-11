# ADR-0045: Bind report cover packets to isolated reviewer images

Status: Accepted

## Context

The source-only contained-reviewer kernel accepts bounded canonical text but has
no image transport. Real cover experiments therefore cannot prove that the
exact previews already admitted to a metadata report are the images examined by
the isolated reviewer. Accepting arbitrary attachment paths, URLs, originals,
or remote fetches would create broader authority than this experiment requires.

## Decision

Add one closed `cover-packet-review` workbench scenario. It reopens one exact
metadata report and reads previews only through the existing report-bound cover
reader. It projects opaque references, exact preview digests and variant,
separate original technical and decoded preview dimensions, incumbent state,
bounded provider provenance, deterministic
eligibility and omission reasons, and no target or service identity.

Digest-equal previews are one candidate. The inspectable Audiobookshelf
incumbent is retained when present. Remaining candidates are selected
deterministically by identity designation, original-cover technical dimensions,
and source diversity; decoded preview dimensions bind the transported PNG but
do not change that order. The absolute limits are 12 PNG previews, 1 MiB per
preview, and 8 MiB in total.

A digest-equal group projects the canonical best observed original dimensions
by pixel area, width, then height; source iteration order cannot choose its
technical-quality facts.

Every canonical source kind represented by a digest-equal group contributes to
diversity priority and counts. No single lexicographic source stands in for the
group.

The packet's ordered reference/digest mapping is part of the canonical reviewer
request. The generic contained-reviewer entry point is image-free; the sole
image-capable entry point admits a distinct cover request only through one full
`cover-review-packet-v1` parser. That parser closes every packet, candidate,
provenance, context, and omission field; validates the content-bound packet
reference, media/variant, preview byte and dimension facts, incumbent state,
canonical producer candidate order, reason-specific omission preconditions,
domain privacy-safe context text, limits, and exact ordered image mapping. The
same semantic validator runs before producer return and reviewer admission;
before either can mount an image, it also verifies the complete bounded,
canonically ordered PNG chunk structure and checksums and fully decodes a
single-frame RGBA PNG whose dimensions exactly match the distinct packet
preview facts and do not exceed 320 by 480. The deterministic profile admits
only exact IHDR, one consecutive IDAT run containing exactly one completed zlib
stream with no trailing bytes, and IEND. ICC profiles, PLTE, and
every other ancillary or unknown chunk fail before mount so their bytes cannot
cross the reviewer boundary;
indistinguishable normalized provenance multiplicity is discarded. Duplicate
omissions are created only after final selection and always name a retained
representative; when a whole digest group is excluded, each member carries the
actual byte, aggregate, or shortlist omission instead. The generic
request and generic adapter method have no image member or image argument. The
durable pre-egress attempt binds that packet together with the
exact reviewer profile, instruction, and output schema. The local result binds
the request and profile identities. Preview bytes are copied into anonymous
Linux memory descriptors, rehashed, sealed against write, growth, shrink, and
seal removal before process creation, bound directly into the read-only
sandbox, and passed as repeatable ordered `codex exec --image` arguments. This
closes callback and same-UID mutation races before gated child release.
Unknown, repeated, changed, non-PNG, over-bound, or unbound inputs fail closed.
The image-capable adapter reconstructs and fully re-admits the packet and exact
ordered image tuple at its own boundary; importing or directly constructing a
private request shape cannot grant image-mount authority.
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
accepted or associated evidence); a retained edition/work contradiction wins.
The projection binds report, packet, candidate, classifier, and opaque evidence
references. It emits fixed reason codes only, never source/provider identities,
metadata values, paths, URLs, or credentials, and makes no visual, provider,
policy, persistence, or write decision.

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
  cleanup behavior.
- Exact-artifact checks prove no runtime CLI/API or distribution inclusion.
- Historical sealed-v2 qualification attempted 17 exact five-preview
  development and qualification-holdout packets. Its aggregate-only receipt
  records 17 `contained_reviewer_invocation_insufficient` failures, zero mapping
  mismatches, zero admitted image or output bytes, p50 129 ms, p95 160 ms,
  successful cleanup, and a failed sandbox aggregate.
- The current exact-head admission-only replay has 17 packets: eight eligible,
  nine typed `icc_bearing_preview_rejected` before dispatch, and no other
  rejection. No model rerun occurred. Sealed v2 has no per-packet outcome or
  latency mapping, so the eligible subset cannot be rebound
  (`sealed_subset_mapping_insufficient`), and its historical latency must not be
  attributed to the current head. The required real transport bound **DID NOT
  QUALIFY**. No retry or limit, profile, model, or threshold change was made.
  Synthetic verification remains report-only evidence and does not activate
  runtime behavior.
