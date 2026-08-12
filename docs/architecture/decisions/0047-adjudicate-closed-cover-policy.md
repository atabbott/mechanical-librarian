# ADR-0047: Adjudicate closed cover policy deterministically

Status: Accepted

## Context

ADR-0046 separates policy-neutral visual observations from identity, quality,
ranking and owner preference. The next proof asks whether those exact typed
facts can support a useful policy-compatible recommendation without granting a
reviewer policy knowledge or introducing another model call.

The contained semantic observer did not execute during the initial 0.15.0
qualification attempt. That negative result and its all-abstention policy replay
remain valid historical evidence: they are not reasons to fabricate labels, add
a second reviewer, or widen sandbox, network, model or corpus authority. The
milestone was later reopened and requalified after bounded transport and
environment remediation. The subsequent reusable engineering benchmark
preserves that initial negative result while proving current policy quality
against fixed independently reviewed covers.

## Decision

Add one repository-only deterministic cover-policy adjudicator and one
`cover-policy-report` scenario. The scenario prepares the existing exact
report-bound packet, derives the conservative identity report locally, and
accepts an optional already-admitted observation report together with the exact
authority-bound contained-reviewer profile bytes that produced it. It never
calls a provider or model.

The initial `cover-policy-v1` has exactly three booleans:

- avoid visible promotional text;
- avoid visible tie-in branding;
- prefer a qualified minimal composition.

Policy is canonical typed data, never prompt text. The adjudicator re-admits
the complete packet and images, recomputes the identity report from the exact
retained source report instead of accepting caller-supplied outcomes, requires
the exact current identity classifier, observation taxonomy, instruction and
output schema. It recomputes the observer reference from the authority-bound
profile plus those frozen identities and verifies every candidate/preview
binding before deciding.
Conflict and unknown-identity candidates are excluded individually. The packet
abstains for identity only when no eligible candidate remains. Unknown
observation evidence needed by an enabled policy still produces an abstention.
A policy cannot rescue an excluded candidate.

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
new policy version. Correcting the initial candidate-admission defect establishes
the candidate-local identity rule of `cover-policy-v1`; it does not add or alter
an owner preference.

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
  exposure. This records the initial qualification attempt, not the later
  reopened semantic requalification.
- The reopened development reproof froze reference decisions first, then reused
  50 retained packet observation artifacts covering all 148 candidates. Across
  400 decisions, acceptable-set agreement was 354/400 (88.5%), recommendation
  agreement was 356/400 (89%), and exact whole-decision agreement was 344/400
  (86%). Every policy combination was at least 78% exact, with zero binding,
  eligibility, invented-reference, policy, privacy or expanded-authority
  violations and no new model or provider call.
- Subsequent diagnosis reproduced the earlier short typed runtime failures as
  Rust allocation aborts under the 512 MiB child address-space limit. A fixed
  exposed-development A/B changed only that limit to 1 GiB: coverage moved from
  4/8 to 8/8 packets, with no abort, retry, binding or authority change. The
  1 GiB limit is therefore part of the qualified workbench profile rather than
  a semantic-policy change.
- The reusable engineering benchmark binds 10 complete packets, 36
  independently reviewed candidates and 80 reference decisions. Under the
  corrected profile the baseline instruction admitted all packets and
  candidates without retry or violation and produced 74/80 exact whole
  decisions; seven policy combinations reached 8/10 or better, while the
  combined tie-in-avoidance and minimal-preference policy reached 7/10.
- One predeclared prompt-only experiment clarified that minimal composition
  means a simple focal layout rather than empty background alone. It preserved
  images, labels, taxonomy, model, reasoning, resource limits and policy.
  Agreement became 26/36 composition, 33/36 medium, 36/36 promotional text and
  35/36 tie-in branding; exact whole-policy agreement became 76/80 and every
  policy combination reached 8/10 or better, with zero retry, binding, provider
  or production-authority violation. These results prove useful behavior on a
  reusable reviewed benchmark; they are not an unseen-generalisation claim.
- Architecture and exact-installed-artifact gates prove no runtime surface,
  persistence or write authority.
