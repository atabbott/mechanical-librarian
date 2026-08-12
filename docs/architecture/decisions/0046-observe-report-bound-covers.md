# ADR-0046: Observe report-bound covers with a small closed vocabulary

Status: Accepted

## Context

ADR-0045 proves that exact report-bound cover previews can reach one isolated,
image-capable reviewer. The next semantic question is deliberately smaller than
an extensible cover ontology: can one exact preview receive a few useful visual
facts without mixing observation with owner policy, identity, eligibility,
ranking, or a winner?

A quota-driven discovery programme would obscure that question and encourage
manufactured class prevalence. The retained private inventory already contains
enough natural provider covers for a best-effort proof.

## Decision

Add one repository-only `cover-observation-report` scenario. It reopens an
exact metadata report, prepares the existing canonical cover packet, and uses
ADR-0045's sole image-capable reviewer entry. It adds no provider request,
production-library access, persistence schema, runtime API/CLI/UI, or write
authority.

Freeze this small policy-neutral vocabulary:

| Property | Closed values | Observable meaning |
| --- | --- | --- |
| Medium | `photographic`, `illustrated`, `typographic`, `mixed`, `unknown` | The primary visible image-making medium; `mixed` requires material photographic and non-photographic media with no clear primary. |
| Composition | `minimal`, `dense`, `unknown` | Minimal means one dominant image or figure in a simple supporting layout; dense requires competing focal elements or a genuinely crowded layout. |
| Promotional text | `present`, `absent`, `unknown` | Visible quoted praise, selling claim, award, or bestseller blurb. |
| Tie-in branding | `present`, `absent`, `unknown` | Visible film, television, game, or stage adaptation branding. |

These are per-cover observations, not work facts. `unknown` is a complete result
when visible evidence does not support another closed value. No confidence,
policy, acceptability, quality, preference, ranking, identity, or winner is
projected.

The module owns the instruction and exact output schema. The instruction is
authoritative and declares packet values, provider context, images, visible
text, and OCR-like text hostile evidence. The reviewer returns exactly one row
for every admitted opaque candidate. Local admission rejects missing,
repeated, invented, extra, or out-of-vocabulary results and rechecks the exact
profile/request, packet, ordered image, and result digest bindings produced by
the contained reviewer.

The canonical report binds the packet reference, candidate reference, preview
digest and variant, taxonomy reference, and opaque instruction, output-schema,
and observer identities. It does not retain private source values or raw model
output.

Qualification uses a frozen seeded random best-effort sample of 100 usable
private preview candidates and a shuffled seeded 20-case repeat subset. Natural
label prevalence and exact repeat agreement are reported as aggregates. There
is no label quota, balancing, F1 target, or post-result tuning. If the existing
contained transport cannot execute, its negative result is preserved and the
general-principle proof remains the deterministic typed contract plus the
strongest aggregate-safe real admission replay available; authority or limits
are not widened to manufacture a pass.

## Consequences

Later deterministic policy can consume closed observations without granting
the observer policy knowledge. The vocabulary is intentionally not an ontology
or plugin surface. Adding or redefining a label requires a new version and new
qualification; absence of a natural class is evidence, not a reason to weaken
or tune the contract.

All code and private evidence remain in the source workbench and ignored
owner-private storage. Installed artifacts contain none of this capability.

## Validation

- Focused tests prove exact packet/preview/result binding, all closed values,
  invented and duplicate reference rejection, hostile-value rejection, and a
  redacted typed scenario failure.
- Architecture and installed-artifact checks prove the workbench remains
  outside the runtime distribution.
- The aggregate-only best-effort receipt records 100 random real candidates,
  the 50/50 development/final split, 100 exact admissions, zero binding
  mismatches, and 20/20 shuffled deterministic request/mapping repeats.
- The existing contained semantic observer did not execute under #542's
  `contained_reviewer_invocation_insufficient` result. Semantic label counts,
  semantic repeat agreement, and model quality are therefore not claimed; no
  reviewer retry or authority widening was used to manufacture them.

### Reopened requalification (2026-08-12)

- Independent blinded reference judgements were frozen before tested outputs
  were opened. The final reference judgements were independently reviewed twice
  and adjudicated. These are review judgements, not claimed human ground truth;
  public evidence remains aggregate-only.
- The first development configuration admitted all 50 candidates. Exact
  agreement was 86% for composition, 84% for medium, 92% for promotional text,
  and 98% for tie-in branding. A shuffled seeded 20-case repeat matched every
  field and the joint result in all 20 cases. Because every field cleared the
  predeclared overall-agreement threshold and repeatability was exact, no second
  prompt/packaging configuration was justified; class-specific policy
  usefulness remained a separate interpretation. Development minimal-positive
  precision was 11/14 (78.57%) and reference-minimal recall was 11/15 (73.33%),
  a mixed and sparse result rather than proof of prefer-minimal usefulness.
- A fresh one-shot, work-family-disjoint final cohort admitted 48 of 50
  candidates. Exact agreement was 83.33% for composition, 97.92% for medium,
  93.75% for promotional text, and 95.83% for tie-in branding; 36 of 48
  candidate rows matched all four fields. The 50 selected candidates came from
  29 distinct packets, including 25 multi-choice packets, so rows from the same
  packet are correlated rather than independent admissions, and one admitted
  packet result can score multiple selected rows. Two post-dispatch packet-
  invocation runtime failures occurred before result admission and were retained
  without retry. Aggregate receipts do not bind the failures to distinct packet
  identities, so distinct-packet success and failure counts are unavailable.
  The exact shared cause was not proven. The 4% candidate missingness rate
  remains within the frozen 5% candidate-coverage tolerance, but it is not a
  packet-dispatch coverage rate; its bias magnitude is unknown and conclusions
  are conditional on the admitted candidate rows. Per-field conclusions survive
  a missing-as-disagreement sensitivity check; the 75% joint admitted-row result
  does not.
- Final composition agreement was 40/48 (83.33%), but minimal-positive
  precision was 15/21 (71.43%), below the useful band, while reference-minimal
  recall was 15/17 (88.24%). The overall agreement therefore does not establish
  useful prefer-minimal policy behavior; #543 must replay that policy and may
  validly fail it.
- Final tie-in absent-case specificity was 44/44, but branded-positive recall
  was only 2/4 (50%), below the useful band. Its strong overall agreement is
  prevalence-sensitive and does not establish useful tie-in detection. The
  development cohort detected its sole branded-positive case, which is too
  sparse for a strong claim. Later avoid-tie-in policy qualification must report
  this class-specific limitation and may validly fail.
- Qualification recorded zero binding or safety violations, provider calls,
  production-library accesses, and writes. Earlier final cohorts invalidated or
  spent by local setup failures supplied no semantic evidence and were replaced
  only with fresh disjoint cohorts after diagnosis and corrected preflight.
