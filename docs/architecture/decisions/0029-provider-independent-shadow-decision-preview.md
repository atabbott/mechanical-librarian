# ADR-0029: Add a provider-independent shadow decision preview

- Status: accepted
- Date: 2026-08-05
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The qualification-only shadow reviewer can recommend an action, and the trusted
compiler can enumerate admissible actions, but neither provides a deterministic
operator decision. Retained development evidence showed that compiler admission
alone is not a correctness rule and that requiring provider and local support on
the same projected candidate suppresses valid source-supported report-only
actions: local and provider observations are deliberately represented as
separate candidates.

Operators need a reproducible preview that can be inspected without model or
provider execution and without granting decision, apply, or write authority.

## Decision

Add a deep domain module that compiles one exact source-validated
`ShadowReviewPacket` and selects exactly one existing catalogue action for every
proposal. The module emits a canonical `shadow-decision-set-v1` document with
closed packet, compiler, catalogue, policy, action, citation, combination, and
decision identities. Unknown, unsupported, conflicting, ambiguous, or
non-unique state selects the catalogue's exact manual action; malformed or
source-invalid state fails the whole preview.

Expose that module through the application-owned repository interface and the
read-only command:

```text
shadow-decision-preview --database PATH --review-case-id ID
```

The command opens an existing exact-current SQLite database read-only and
no-create. It does not construct a provider, model, target-service, or network
client and does not persist the decision.

Policy v5 treats selected-EPUB source evidence as sufficient for a report-only
candidate in `authors`, `language`, `publisher`, `publishedDate`, and
`publishedYear`. This deliberately does not inherit proposal write-eligibility
or require a same-candidate provider correlation. Publication actions still
require manifestation-publication binding, internally consistent paired
destinations, and supported precision. A timestamp normalized to a calendar day
still needs two provider correlations. Title retains the stricter proposal,
target-preservation, local-support, and same-candidate provider-correlation
requirements. Every other destination remains manual.

Exactly one qualifying action is required. Active bibliographic combinations
remain atomic: a partial or incompatible combination makes every constrained
proposal manual. Keep-current remains evidence-positive and is never inferred
from absent evidence.

## Later evolution

Policy v15 (#1083) makes Authors obey the complete-set proposal sufficiency and
material-conflict result. It removes the historical source-only bypass and
mandatory local witness for Authors. Supported local fill/preservation and
independent provider agreement can qualify; local-only populated replacement
remains manual. Structural attribution and neutral package corroboration preserve
truthful roles. The v5 rationale and measured results below remain historical.

Policy v5 and its validation figures below are historical. Current publication
admission does not accept local-only dates: both candidate formation and the
publication preview require local evidence plus two independent provider
correlations at the selected precision. Publication is one canonical
`publishedDate` decision, with `publishedYear` retained as derived context.
Packet closure, event and precision checks, and target-year consistency still
apply. Candidate existence does not establish Ask-mode owner-action reachability
or automatic persistence/write authority. See
[current publication sufficiency](../../operations/proposal-set.md#publication-candidate-sufficiency)
for the delivered behavior and the bounded #1003 investigation. The alternative
rejected here and these historical results do not qualify a current quorum
relaxation.

## Consequences

### Positive

- Identical exact-current cases produce byte-identical decisions without
  network or model availability.
- Source-supported report-only facts remain useful when provider access is
  unavailable or projected separately.
- Catalogue admission, exact closure, and manual abstention remain trusted
  safety boundaries.

### Negative

- The deterministic policy is another versioned decision surface that requires
  qualification when output-affecting rules change.
- Conservative ambiguity leaves some otherwise plausible actions for manual
  review.

### Neutral or deferred

- The preview is not a normal recommendation, approval, persisted decision, or
  apply input.
- It adds no provider/model call, credential, retry, migration, background
  execution, write authority, release, or default integration.

## Alternatives considered

### Use the compiler's sole nonmanual action

Rejected because retained evidence showed poor recall and because compiler
admission proves policy representability, not correctness.

### Require local and provider support on one candidate

Rejected because the packet intentionally keeps those source observations as
separate alternatives. That rule abstained on every exposed development
opportunity even when the selected EPUB directly supported the report-only
fact.

### Reuse the model reviewer

Rejected because it would add provider availability, credentials, latency, and
untrusted output to an otherwise deterministic operator preview.

## Validation

- Domain tests cover supported and unsupported fields, ambiguity, insufficient
  support, keep-current, combinations, closure, canonical identities, replay,
  and malformed inputs.
- Read-only repository and CLI tests prove whole-command failure and filesystem
  no-mutation behavior.
- The exact installed candidate passed the frozen 43-case exposed-development
  gate with 159 correct nonmanual decisions, zero wrong or unsupported
  nonmanual decisions, 100% precision, and 159/172 recall.
- A structurally selected 43-case read-only production-data cohort completed
  acquisition, construction, decision, and byte-identical replay without
  target database or media writes.
- Fresh pilot and confirmation qualification remain mandatory before merge.
