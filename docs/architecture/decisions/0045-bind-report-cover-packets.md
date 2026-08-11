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
request. The durable pre-egress attempt binds that packet together with the
exact reviewer profile, instruction, and output schema. The local result binds
the request and profile identities. Preview bytes are copied into a separate
ephemeral private directory, mounted read-only in the Linux sandbox, passed as
repeatable ordered `codex exec --image` arguments, and removed with the job.
Unknown, repeated, changed, non-PNG, over-bound, or unbound inputs fail closed.
Image and OCR text are explicitly untrusted evidence, never instructions.

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
