# ADR-0027: Use a provider-admissible shadow-output schema

- Status: accepted
- Date: 2026-08-01
- Owners: project maintainers
- Supersedes: the structured-output union mechanism in ADR-0025 and the
  exact-current request version in ADR-0026
- Superseded by: ADR-0028 for the exact-current request schema and
  recommendation-array combination partitioning

## Context

The packet-only compiler produces the complete set of policy-admissible
actions before reviewer dispatch. Request v7 projected those exact object
variants through a nested JSON Schema `oneOf`. A real synthetic transport
probe reached the provider but was rejected before inference because that
structured-output boundary does not permit `oneOf` in recommendation items.

The same failure arrived in a bounded JSONL `turn.failed` event whose message
was itself a JSON service-error document. The existing classifier recognized
only the earlier direct-message form, so it conservatively reported a generic
runtime failure rather than the more precise response-format category.

## Decision

Express the unchanged compiled action variants through nested `anyOf`. Each
variant retains its exact proposal, recommendation, candidate, citation
membership and cardinality bindings. `oneOf` is removed from the locally
allowed provider-schema subset so it fails before dispatch if reintroduced.

This is a transport representation, not the policy boundary. Trusted domain
validation continues to require exactly one compiled action per proposal,
the complete action-bound citation closure, exact request identity and every
existing semantic bound. Provider schema acceptance cannot admit an action
the compiler excluded.

Recognize a nested schema-rejection message only when its decoded service
document has the exact bounded shape: top-level service `error`, integer HTTP
400, `invalid_request_error`, `invalid_json_schema`, parameter
`text.format.schema`, and a message beginning with the pinned response-format
prefix. Direct pinned messages remain supported. Extra members, malformed
JSON, different status/type/code/parameter, non-empty stderr or mixed terminal
shape continue to fail closed as generic runtime errors. Raw service text is
never included in public diagnostics.

Advance the exact-current request contract to `shadow-review-request-v8`
because the bound output-schema bytes change. Packet v5, prompt v3, profile
v2, result v2, compiler policy, model, effort, sandbox and qualification-only
authority remain unchanged. Request-v7 stores are unsupported pre-release
state and require a clean rebuild under ADR-0013.

## Consequences

### Positive

- The provider can accept the exact compiled-action schema without weakening
  trusted action validation.
- Schema transport failures retain a precise bounded category across both
  observed provider envelopes.
- Reintroducing an unsupported union keyword fails locally before dispatch.

### Negative

- Exact output-schema and request bytes change and therefore require fresh
  transport and reviewer qualification.
- Existing pre-release request-v7 state requires a clean rebuild.

### Neutral or deferred

- No reviewer output is accepted merely because it satisfies `anyOf`; the
  compiler catalogue and domain validator remain authoritative.
- No activation, write authority or production-service use follows from this
  transport repair.

## Alternatives considered

### Broaden recommendation items to one generic object

Rejected because it would remove useful exact action guidance from structured
generation even though domain validation would still fail closed.

### Keep `oneOf` and retry

Rejected because the provider deterministically rejects the schema before
inference; retrying would repeat a construction failure.

### Parse arbitrary nested service errors

Rejected because permissive error parsing could misclassify unrelated private
runtime output. Only the exact observed bounded envelope is recognized.

## Validation

- Focused tests prove every compiled action variant remains present under
  `anyOf`, candidate-null and citation-cardinality bindings are unchanged, and
  `oneOf` is rejected by the local schema validator.
- Failure-signal tests prove both pinned direct and nested schema-rejection
  envelopes map to `response_format`, while malformed and extended variants
  remain generic runtime failures.
- Persistence and compatibility tests bind request v8 and reject earlier
  pre-release schemas under the clean-schema policy.
- A synthetic provider transport canary must pass before the exposed
  development comparison or any fresh holdout resumes.
