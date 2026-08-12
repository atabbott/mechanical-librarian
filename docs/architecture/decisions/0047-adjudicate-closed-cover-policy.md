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
environment remediation; admitted semantic observations now require a separate
current policy-quality replay without erasing the initial negative result.
In particular, sub-threshold minimal-positive precision and tie-in-positive
recall leave both related policy uses unproven until that replay.

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
  exposure. This records the initial qualification attempt, not the later
  reopened semantic requalification.
- Architecture and exact-installed-artifact gates prove no runtime surface,
  persistence or write authority.

### Reopened semantic-policy qualification (2026-08-12)

The reopened proof preserved the initial all-abstention replay above. The
independent references originally covered only the 100 selected candidates.
Before tested policy outputs were opened, the other 132 packet candidates were
independently reviewed twice and evidence-adjudicated, completing reference
observations for 232 candidate instances in 79 packets. At least one reviewer
field differed in 88 cases. Evidence adjudication reopened or corrected 139
fields: 26 composition, 67 medium, 39 promotional-text, and 7 tie-in fields.
The accepted disputed-field source was the first review for 121 fields, the
second review for 16, and the adjudicator for 2. These remain independent
review judgements, not claimed human ground truth.

The retained report facts and media were transferred one-to-one from the v22
evidence shape into the exact clean v23 schema. Opaque references changed, but
no semantic or identity outcome changed. This was a binding-equivalent
reconstruction, not a provider acquisition, model run, or corpus rebuild.

All eight policies were applied with bit order `promotional / tie-in / minimal`,
where `1` enables that policy. Development exact acceptable-set,
recommendation, status, and outcome-triple agreement were each 50/50 for every
policy. The outcome triple—acceptable set, recommendation, and status—is the
frozen issue metric and does not include reason-code equality. Exact reason
agreement requires the complete ordered reason list to match; strict agreement
requires both the outcome triple and ordered reasons. Development exact reason,
strict row, and strict all-selected-row packet agreement were 50/50 for `000`,
`010`, `100`, `101`, `110`, and `111`, and 49/50 for `001` and `011`. One row
under the two minimal-only combinations retained the same outcome triple while
differing on the `preferred_minimal` reason. Conservative identity had already
made 46 of those 50 packets
ambiguous; on the four semantic-active packets, all four selected rows matched
under every policy. That active result is descriptive because of its size.

The one-shot recovery-final replay retained 48 admitted observation rows over
29 packets. Two missing rows in one packet produced
`observations_unavailable` and were not imputed. Aggregate agreements were:

| Policy bits | Acceptable set | Recommendation | Status | Exact outcome triple | Distinct packets with all selected rows outcome-triple exact |
| --- | ---: | ---: | ---: | ---: | ---: |
| `000` | 50/50 | 50/50 | 50/50 | 50/50 (100%) | 29/29 |
| `001` | 50/50 | 47/50 | 50/50 | 47/50 (94%) | 28/29 |
| `010` | 45/50 | 49/50 | 50/50 | 45/50 (90%) | 27/29 |
| `011` | 45/50 | 46/50 | 50/50 | 42/50 (84%) | 26/29 |
| `100` | 44/50 | 47/50 | 47/50 | 44/50 (88%) | 27/29 |
| `101` | 44/50 | 47/50 | 47/50 | 44/50 (88%) | 27/29 |
| `110` | 46/50 | 47/50 | 47/50 | 46/50 (92%) | 27/29 |
| `111` | 46/50 | 47/50 | 47/50 | 46/50 (92%) | 27/29 |

The separate explanation and canonical strict views were:

| Policy bits | Exact ordered reasons | Strict outcome plus reasons | Distinct packets with all selected rows strict |
| --- | ---: | ---: | ---: |
| `000` | 48/50 | 48/50 (96%) | 28/29 |
| `001` | 47/50 | 44/50 (88%) | 26/29 |
| `010` | 47/50 | 43/50 (86%) | 26/29 |
| `011` | 46/50 | 40/50 (80%) | 25/29 |
| `100` | 45/50 | 42/50 (84%) | 26/29 |
| `101` | 44/50 | 42/50 (84%) | 26/29 |
| `110` | 44/50 | 43/50 (86%) | 25/29 |
| `111` | 43/50 | 42/50 (84%) | 25/29 |

One packet had repeat-artifact decision variation, only when promotional
avoidance was enabled. Conservative identity preempted semantic policy on 26 of
the 29 final packets. The post-hoc descriptive semantic-active subset contained
only 3 packets and 8 selected rows; exact outcome triples for `000` through
`111` were respectively 8/8, 5/8, 3/8, 0/8, 2/8, 2/8, 4/8, and 4/8. This was
not a frozen acceptance subset and prevents the identity-dominated overall
score from being read as broad semantic preference quality.

Development reference and tested abstention categories matched exactly. Final
tested results added two `observations_unavailable` rows under every policy;
policies with promotional avoidance also added three
`observations_ambiguous` rows. The missing rows were identity-ambiguous and did
not reduce the semantic-active subset. Mapping, binding, eligibility, explicit
policy, invented-reference, and privacy violations were all zero. Provider,
external-model, corpus-rebuild, production-library, and write counts were also
zero. The aggregate safety conclusion is derived from those zero component
boundary and authority counters; safety was not an independent receipt metric.

The unchanged deterministic pipeline and fail-closed behavior are therefore
proved. The no-preference baseline is strong and overall outcome-triple
agreement across policies is mixed to strong at 84–100%, while strict
outcome-plus-reasons agreement is mixed to strong at 80–96%. The two missing
rows explain the `000` reason difference: both sides abstained with the same
outcome triple, but the reference reason was `identity_ambiguous` and the tested
reason was `observations_unavailable`. Other reason differences reflect the
already disclosed minimal, tie-in, promotional, and three promotional-policy
`observations_ambiguous` explanation differences without changing the frozen
metric. Those overall results are identity-dominated. On the very small
semantic-active subset, the no-preference baseline was strong and every
preference-enabled policy was below the frozen 75% useful threshold.
Semantic-active preference evidence is therefore insufficient. This does not
establish broad strong semantic-policy quality or runtime readiness, and it
does not promote #501 / 0.15.1.
