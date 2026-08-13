# ADR-0041: Contract the contained-reviewer kernel

Status: Accepted; cover-only runtime exclusion superseded by ADR-0050

## Context

The F11 qualification-only shadow reviewer accumulated a product application
service, request/result/profile domain documents, four lifecycle tables,
inspection and reevaluation surfaces, browser model advice, and a large private
qualification harness. No current product caller uses that lifecycle. Retaining
it would make obsolete experimental machinery part of the exact-current
runtime, persistence, and trust boundary.

Future source work needs only a small isolated process kernel: a caller supplies
sanitized typed material and receives one bounded typed output or abstention.
The deterministic automatic-decision engine separately relies on the existing
`ShadowReviewPacket`, compiled actions, rule/source identity, and
`shadow_decision`/`shadow_outcome` persistence. Those are product contracts,
despite their historical names, and must not change.

## Decision

Remove the F11 application orchestration, request/attempt/dispatch/result
persistence, runtime/browser model-advice projection, workbench lifecycle
capabilities, qualification scripts, and current operational documents. Under
ADR-0013, an existing database is accepted only when it has the new
exact-current clean schema; no migration or compatibility reader is added.

Retain `ShadowReviewPacket` and the evidence helpers required by the
provider-independent deterministic preview and qualified automatic-decision
engine. Preserve their decision, outcome, source-outcome, and rule identities
exactly.

Retain one semantic-neutral `contained-reviewer` workbench scenario outside
the installed artifact. Its source-owned contract accepts only a bounded
instruction, canonical sanitized JSON input, and a closed canonical output
schema. A purpose-specific isolated adapter binds exact private artifacts,
credentials, limits, and process identity; releases the child only after an
in-memory callback observes that identity; validates canonical output locally against
the bound schema; redacts failures; and kills and proves child death on timeout
or cancellation. Adapter-owned authentication actions may use the same profile
but create no product lifecycle.

The contained reviewer is not composed into runtime bootstrap, imported by the
HTTP/browser application, persisted, or included in the wheel/container. It has
no cover transport, recommendation or decision schema, provider/target
credentials, production write authority, or production-service qualification.

ADRs 0015, 0016, 0024, 0025, 0026, 0027, and 0028 remain immutable historical
records. This ADR supersedes their current qualification-only reviewer
implementation decisions; it does not rewrite their history.

## Consequences

The installed product and clean database lose an unused reviewer lifecycle and
model-advice projection. Source experiments keep the minimum isolated
process/auth/output-boundary capability, while future callers must define their
own typed semantics without reviving F11 persistence.

The adapter still carries security-sensitive process and credential code, so
focused fake-process tests must prove artifact/profile binding, credential and
filesystem isolation, durable pre-invocation attempt evidence, in-memory child
release gating, bounded parsers,
local schema enforcement, redaction, cleanup, and exact process death.

## Validation

- Architecture tests prove runtime/bootstrap has no contained-reviewer import.
- Exact-schema tests reject the removed tables and accept only the current
  deterministic decision/outcome schema.
- Workbench catalogue and artifact tests prove one source-only capability and
  wheel/container exclusion.
- Focused synthetic tests exercise success, malformed/over-bound/schema-invalid
  output, authentication isolation, timeout, cancellation, and cleanup without
  a production service or library.
