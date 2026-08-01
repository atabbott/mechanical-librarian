# ADR-0026: Bind final shadow-recommendation semantics

- Status: accepted
- Date: 2026-08-01
- Owners: project maintainers
- Supersedes: the prompt/profile/result tuple in ADR-0016
- Superseded by: none

## Context

The compiled action catalogue closes policy admissibility before dispatch, but
the remaining reviewer-facing contract had three mismatches. Result v1 required
a non-empty `problems` list for every manual recommendation, even when a cited
manual conclusion had no adverse fact to report. Prompt v2 did not state that
`candidate_id` is null outside candidate recommendations. It also described
manual as the conservative choice without making clear that keep-current is a
positive, evidence-backed conclusion rather than an uncertainty fallback.

Fabricating an adverse fact to satisfy a schema weakens evidence integrity.
Supplying a candidate identity for a non-candidate action violates the domain
contract. Treating keep-current as a fallback can turn insufficient or
unrepresentable evidence into unsupported nonmanual advice.

## Decision

`shadow-review-result-v2` keeps the same closed required fields and bounds, but
permits the required `problems` list to be empty for every recommendation.
Every recommendation still requires at least one bounded reason and the exact
compiled action's citation closure. A present problem must be a bounded actual
unresolved or adverse fact; it must not be manufactured to validate manual
advice.

`shadow-review-prompt-v3` states the action semantics directly:

- `candidate_id` is non-null only for `recommend_candidate`, where it names the
  exact supported packet candidate; it is null for manual and keep-current;
- keep-current is evidence-positive and requires cited packet evidence plus
  decision-summary support for the current value under the closed destination
  policy;
- manual is required for insufficient, conflicting, or explicitly manual
  evidence, and when the supported value is absent from packet candidates; and
- no closest, plausible, familiar, or out-of-packet value may be selected.

`codex-shadow-profile-v2` binds prompt v3 and result v2 with the existing model,
effort, packet v5, compiler policy, provider transport, and sandbox. Packet v5
does not change because its document shape and admissibility boundary are
unchanged. The exact-current request advances to `shadow-review-request-v7`
because it binds the changed generated result-schema bytes and profile tuple;
request v6 state must not be silently reinterpreted.

Result v1 and profile v1 are unsupported pre-release state. They are rejected
without output repair, inferred candidates, migration, import, or conversion.
The reviewer remains qualification-only untrusted advice with no decision,
apply, write, retry, or activation authority.

## Consequences

### Positive

- Manual advice can remain truthful when no adverse fact exists.
- Prompt, generated schema, domain validation, and profile identity express one
  candidate-binding contract.
- Evidence-poor cases fail to manual instead of using keep-current as a
  conservative nonmanual fallback.

### Negative

- Existing pre-release request-v6/result-v1/profile-v1 state requires a clean
  rebuild.
- The prompt is longer and its exact bytes require fresh qualification.

### Neutral or deferred

- The compiler continues to decide which actions are admissible; the prompt
  does not widen its catalogue.
- No qualification or activation claim follows from this contract change.

## Alternatives considered

### Require a placeholder manual problem

Rejected because a placeholder is fabricated evidence and gives `problems` a
control meaning it does not have.

### Repair candidate identities after reviewer output

Rejected because output repair hides semantic failure and can infer a decision
the reviewer did not validly express.

### Use keep-current as the default conservative action

Rejected because preservation is a positive policy conclusion. Uncertainty and
unrepresentable support belong to manual review.

## Validation

- Focused tests prove empty-problem manual acceptance with a bounded reason and
  exact citations, while malformed or over-bound problem values still fail.
- Generated-schema tests prove candidate actions use string candidate IDs and
  every represented manual or keep-current action uses JSON `null`.
- Prompt/profile tests bind the exact v3/v2/v2 tuple and the evidence-positive
  keep-current and manual-fallback wording.
- Existing action-compiler, citation, membership, coverage, combination,
  isolation, persistence, and exact-artifact suites remain green.
- Exposed development material is rerun before any fresh holdout is spent.
