# ADR-0025: Compile policy-admissible reviewer actions before dispatch

- Status: accepted
- Date: 2026-08-01
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Packet v5 provides source-validated decision summaries, but a reviewer still
received a generic result schema containing every packet candidate plus
keep-current and manual recommendation kinds. A proposal-level evidence lane
cannot establish whether each concrete action satisfies its own policy,
citation, manifestation, publication, and bibliographic-combination
obligations. It can also hide representable actions when an independent manual
outcome exists.

The qualification reviewer is untrusted and report-only. Policy admissibility
must be established before dispatch without using a gold answer, prior reviewer
output, performance score, response metadata, or any post-score adjustment.
Syntax validity alone is not an admissibility boundary.

## Decision

The domain owns a pure
`compile_shadow_review_actions(packet) -> PolicyActionCatalogue` boundary. It
accepts only one validated packet and fixed in-code policy. For every proposal
it evaluates each candidate action and keep-current action independently, and
always represents an explicit manual action. No proposal-level outcome lane
suppresses another otherwise admissible action.

`shadow-review-action-catalogue-v1` contains deterministic, canonically ordered
actions and typed exclusions under `shadow-review-action-policy-v1`. An action
has a stable identity derived from proposal, recommendation, and candidate
identity, plus exact citation and bibliographic-combination closures. Its
identity does not depend on the opaque review-case digest. A rejected
nonmanual action has a stable exclusion identity derived from the action and
sorted reason codes. Unknown assessment, scope, support, correlation,
manifestation, publication, citation, or combination obligations fail closed.

Candidate actions require positive source support, citation and correlation
closure, compatible evidence scope, and the applicable manifestation,
publication, and combination bindings. A report-only publication action must
retain its exact event, precision, paired-destination consistency, and
unsupported-write state. Keep-current requires evidence-backed preservation,
closed compatible language evidence, or an allowed bibliographic preserve
member. Bibliographic preservation cites only evidence participating in its
allowed combinations. Manual actions cite only evidence structurally relevant
to their proposal and remain available without manufactured citations when no
such evidence exists.

`shadow-review-request-v6` binds the canonical packet and a finite structured
output schema whose recommendation item is a `oneOf` over compiled actions.
Each variant fixes the proposal, recommendation, candidate, and allowed
citations. `shadow-review-result-v1` remains the wire result, but trusted
semantic validation now accepts only an exact catalogue action with
action-bound citations and an exact allowed bibliographic combination where
applicable.

Although the output schema is derived, request identity must distinguish the
old generic policy boundary from the compiled one. The exact-current SQLite
request schema therefore advances to request v6. Under ADR-0013, request-v5
stores are rejected without migration, import, or conversion.

The change does not alter the packet, result, prompt, model, reasoning effort,
provider invocation, retry policy, write authority, or production activation.
Fresh model-free and provider qualification remain separate downstream gates.

## Consequences

### Positive

- The reviewer cannot select a grounded but policy-inadmissible action merely
  because its candidate identifier appeared in the packet.
- Candidate, keep-current, and manual representability no longer depend on a
  mutually exclusive proposal-level lane.
- Typed exclusions make fail-closed decisions auditable without exposing
  private values or reviewer outputs.
- The exact request schema and semantic validator enforce the same catalogue.

### Negative

- Per-action schema variants increase bounded request-schema size.
- Policy changes that alter admissibility require a request-contract change or
  other explicit identity binding; silent reinterpretation is forbidden.
- Existing pre-release request-v5 databases require a clean rebuild.

### Neutral or deferred

- The compiler does not decide which admissible action is preferable.
- Qualification results cannot grant write authority or activate normal
  review.

## Alternatives considered

### Keep the generic syntax schema and validate only after response

Rejected because it dispatches actions the trusted policy already knows are
inadmissible and makes schema legality look like policy legality.

### Project the accepted action from a gold corpus

Rejected because it would tune the boundary to evaluation answers and prevent
independent qualification.

### Use proposal-level evidence-sufficiency lanes

Rejected because candidate, keep-current, and manual actions have independent
obligations and may coexist.

## Validation

- Unit and property tests prove deterministic canonical identity, independence
  from opaque case identity, action/citation closure, and fail-closed unknowns.
- Crossed synthetic controls place admissible actions in historically
  all-manual destinations and adverse actions in historically feasible
  destinations across structure, support, manifestation, publication,
  citation, and combination factors.
- Exact-current persistence tests reject populated request-v5 state without
  changing its bytes.
- A development-only exposed corpus may measure implementation recall, but no
  provider qualification claim is made from it. Fresh frozen model-free gates
  must pass before downstream provider experiments resume.
