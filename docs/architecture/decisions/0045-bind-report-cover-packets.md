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
dimensions, incumbent state, bounded provider provenance, deterministic
eligibility and omission reasons, and no target or service identity.

Digest-equal previews are one candidate. The inspectable Audiobookshelf
incumbent is retained when present. Remaining candidates are selected
deterministically by identity designation, technical dimensions, and source
diversity. The absolute limits are 12 PNG previews, 1 MiB per preview, and 8 MiB
in total.

The packet's ordered reference/digest mapping is part of the canonical reviewer
request. The generic contained-reviewer entry point is image-free; the sole
image-capable entry point admits a distinct cover request only through one full
`cover-review-packet-v1` parser. That parser closes every packet, candidate,
provenance, context, and omission field; validates the content-bound packet
reference, media/variant, preview byte and dimension facts, incumbent state,
canonical producer candidate order, reason-specific omission preconditions,
domain privacy-safe context text, limits, and exact ordered image mapping. The
same semantic validator runs before producer return and reviewer admission;
indistinguishable normalized provenance multiplicity is discarded. Duplicate
omissions are created only after final selection and always name a retained
representative; when a whole digest group is excluded, each member carries the
actual byte, aggregate, or shortlist omission instead. The generic
request and generic adapter method have no image member or image argument. The
durable pre-egress attempt binds that packet together with the
exact reviewer profile, instruction, and output schema. The local result binds
the request and profile identities. Preview bytes are copied into a separate
ephemeral private directory, mounted read-only in the Linux sandbox, passed as
repeatable ordered `codex exec --image` arguments, and removed with the job.
Unknown, repeated, changed, non-PNG, over-bound, or unbound inputs fail closed.
Only the bounded Instruction is authoritative. Canonical input, provider
metadata, images, and OCR text are explicitly hostile evidence, never
instructions.

This narrowly supersedes ADR-0041's statement that the source-only kernel has
no cover transport. All other ADR-0041 constraints remain current.

## Consequences

Real cover transport can be qualified without a runtime API, CLI, persistence
schema, production target, provider discovery, write authority, generic
attachment system, or second reviewer credential path. The entire capability
remains outside the wheel and container. Private packets, previews, attempts,
and results remain ignored owner-only workbench state.

## Validation

- Synthetic tests prove exact report binding, digest and order identity,
  incumbent retention, deduplication, deterministic selection and omissions,
  hard byte/count limits, hostile-text treatment, read-only sandbox arguments,
  typed failure and cleanup behavior.
- Exact-artifact checks prove no runtime CLI/API or distribution inclusion.
- Private qualification exercises exact five-preview packets with zero mapping
  mismatch and records only aggregate latency and outcome evidence publicly.
