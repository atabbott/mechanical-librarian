# ADR-0047: Adjudicate closed cover policy deterministically

Status: Accepted

## Context

ADR-0046 separates policy-neutral visual observations from identity, quality,
ranking and owner preference. The next proof asks whether those exact typed
facts can support a useful policy-compatible recommendation without granting a
reviewer policy knowledge or introducing another model call.

The contained semantic observer did not execute during 0.15.0 qualification.
That negative result must remain visible: it is not a reason to fabricate
labels, add a second reviewer, or widen sandbox, network, model or corpus
authority.

## Decision

Add one repository-only deterministic cover-policy adjudicator and one
`cover-policy-report` scenario. The scenario prepares the existing exact
report-bound packet, derives the conservative identity report locally, and
accepts an optional already-admitted observation report. It never calls a
provider or model.

The initial `cover-policy-v1` has exactly three booleans:

- avoid visible promotional text;
- avoid visible tie-in branding;
- prefer a qualified minimal composition.

Policy is canonical typed data, never prompt text. The adjudicator re-admits
the complete packet and images, recomputes the identity report from the exact
retained source report instead of accepting caller-supplied outcomes, requires
the exact current identity classifier, observation taxonomy, instruction and
output schema, and verifies every candidate/preview binding before deciding.
Conflict candidates are excluded.
Unknown identity, or unknown observation evidence needed by an enabled policy,
produces an abstention. A policy cannot rescue an excluded candidate.

The acceptable set contains only exact admitted references that survive
identity and enabled avoid rules. Packet order is the deterministic baseline.
When minimal composition is preferred and at least one acceptable candidate is
qualified minimal, the first such candidate is recommended; otherwise the
first acceptable packet candidate is recommended. No acceptable candidate,
ambiguous evidence, or unavailable observations produces a closed typed
abstention instead of a forced choice.

The canonical report contains only acceptable opaque references, one exact
known recommendation or null, closed reason codes, and exact packet,
classifier, taxonomy, policy, identity-report and observation-report
identities. It contains no image bytes, private values, paths, URLs, prompt,
free prose, write instruction or production authority.

## Consequences

The policy proof is simpler and more auditable than a second semantic reviewer:
the model cannot see policy and deterministic code owns every exclusion,
abstention and recommendation. Adding a field or changing semantics requires a
new policy version.

Real packets without admitted semantic observations honestly return
`observations_unavailable`. Synthetic exhaustive cases prove the closed
decision matrix, but do not establish model quality or production readiness.
The entire surface remains source-only and excluded from installed artifacts.

## Validation

- Focused tests cover every policy value, identity and semantic ambiguity,
  conflict and policy exclusion, packet-order and minimal preference,
  unavailable observations, invented/mismatched references and redacted
  scenario failure.
- Aggregate-safe retained-real replay admitted 100/100 frozen packets and
  applied all eight policies: 800/800 typed
  `observations_unavailable` abstentions, with zero mapping mismatches,
  recommendations, policy violations, model/provider dispatches or private
  exposure.
- Architecture and exact-installed-artifact gates prove no runtime surface,
  persistence or write authority.
