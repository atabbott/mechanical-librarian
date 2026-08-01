# ADR-0028: Normalize transient reviewer JSON and partition combinations

- Status: accepted
- Date: 2026-08-01
- Owners: project maintainers
- Supersedes: ADR-0027 for the exact-current request schema and finite
  recommendation-array construction
- Superseded by: none

## Context

An owner-private exposed development comparison reached the provider cleanly on
43 cases. Six otherwise valid result objects were rejected because the provider
emitted semantically insignificant JSON whitespace or object-member ordering.
Three more results selected individually compiled actions whose bibliographic
combination sets had no common member. The request-v8 schema enumerated each
action exactly, but its single item union could not express that cross-action
constraint.

Canonical persisted documents remain valuable, but requiring a generative
provider to emit one byte serialization is not a semantic or security boundary.
The adapter already bounds raw bytes, strictly decodes UTF-8, rejects duplicate
keys and non-finite constants, requires an object root, validates exact request
identity, and retains raw private evidence until cleanup.

## Decision

At the transient provider-result boundary, accept insignificant JSON whitespace
and object-member ordering after the existing byte bound, strict UTF-8 decode,
duplicate-key rejection, constant rejection, JSON parse, and object-root check.
Do not mutate the raw evidence. Canonically serialize the parsed object before
constructing the typed `shadow-review-result-v2`; canonical persistence and
replay remain unchanged.

When a packet has bibliographic combinations, generate one recommendation-array
`anyOf` branch per allowed combination. Each branch includes:

- every action for an unconstrained destination;
- every manual action for a constrained destination; and
- only constrained nonmanual actions whose compiled `combination_ids` contain
  that branch's combination.

The array still requires exactly one recommendation per proposal, and trusted
domain validation still requires exact compiled actions, citation closure,
coverage, and one coherent combination. The schema does not infer, repair, or
substitute an action. Every compiled action remains reachable in at least one
branch; unknown applicability continues to fail closed in the compiler.

Advance the clean exact-current request contract to
`shadow-review-request-v9`. Packet v5, prompt v3, profile v2, result v2, model,
effort, sandbox, compiler policy, and qualification-only authority are
unchanged. Request-v8 stores are unsupported pre-release state and require a
clean rebuild under ADR-0013.

## Consequences

### Positive

- Provider formatting variance no longer creates false malformed-output
  failures.
- Structured generation cannot combine mutually incompatible nonmanual
  bibliographic actions in one schema branch.
- Raw evidence and canonical trusted state remain distinct and auditable.

### Negative

- Combination branches duplicate action-schema bytes. The existing one-megabyte
  schema bound remains authoritative and construction fails before dispatch on
  overflow.
- Exact request and output-schema bytes change and require fresh provider and
  reviewer qualification.
- Existing pre-release request-v8 state requires a clean rebuild.

### Neutral or deferred

- Whitespace tolerance does not accept invalid UTF-8, invalid JSON, duplicate
  keys, non-object roots, foreign identities, unknown citations, incomplete
  coverage, or semantic disagreement.
- No activation, write authority, production-service use, or qualification
  claim follows from this development correction.

## Alternatives considered

### Continue requiring provider-canonical bytes

Rejected because formatting is not part of the structured-output semantics and
the observed provider does not reliably preserve the one canonical encoding.

### Repair incompatible recommendations after generation

Rejected because choosing or substituting an action after output would hide a
semantic failure and could invent reviewer intent.

### Add a free-standing combination identifier

Rejected because an identifier alone would not constrain the per-proposal
actions. Trusted validation would still catch disagreement, but the schema
would continue to permit it during generation.

## Validation

- Adapter tests accept formatted duplicate-free result JSON and still reject
  malformed UTF-8/JSON and duplicate keys with bounded, value-free diagnostics.
- Synthetic two-combination tests prove all actions remain reachable, each
  combination-specific action appears in exactly its compatible branch, and
  incompatible actions do not share a branch.
- The generated schemas for the complete exposed 43-case development corpus
  pass the local provider-subset validator, remain below the byte bound, retain
  100% compiled-action reachability, and have no cross-combination leakage.
- Persistence and compatibility tests bind request v9 and reject request-v8
  state without changing prior database bytes.
- Focused, architecture, full-suite, exact-artifact, provider-transport, and
  fresh qualification gates remain required before activation.
