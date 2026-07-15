# Pragmatic engineering judgment

Deliver the smallest complete result that satisfies the frozen requirements,
repository contracts, and concrete material risks. Prefer clear, ordinary code
and supported behavior over speculative flexibility or theoretical perfection.

During the initial build, prioritize working end-to-end functionality. Do not
introduce policy frameworks, enforcement infrastructure, hardening layers, or
compliance-style evidence unless the current acceptance criteria or a concrete
high-risk surface requires them. Keep report-only product defaults and explicit
repository safety contracts intact.

- Do not chase marginal optimizations, stylistic micro-refinements, speculative
  abstractions, future-proofing, or exhaustive handling of hypothetical cases.
- Cover explicit acceptance criteria, known regressions, supported boundaries,
  and realistic success and failure paths. Use representative tests rather than
  combinatorial coverage of every possible exception.
- Validate inputs and dependencies where the contract requires it. Let
  unexpected failures propagate or convert them into a clear, actionable error;
  never silently swallow, disguise, or endlessly recover from them.
- Preserve mandatory safety gates for credentials, writes, persistence,
  concurrency, recovery, and trust boundaries. Pragmatism does not waive an
  explicit product, security, compatibility, or acceptance contract.
- Invoke dedicated safety review only for a credible change to credentials,
  authentication or authorization, external network access, hostile-input
  parsing, private data exposure, persistence or migrations, concurrency,
  backup or recovery, upgrades, or production write authority. Ordinary domain,
  application, interface, documentation, and test work uses normal quality and
  acceptance review.
- Raise review findings only for demonstrated correctness, acceptance, safety,
  regression, operability, or material maintainability impact. Omit preference,
  theoretical hardening, and optimization-only findings unless evidence makes
  them material to the current scope.
- Stop refining when all criteria pass, required gates and demonstrations are
  clean, and no substantive finding remains. Put worthwhile extra work behind a
  separate issue only when it has concrete value; do not create speculative
  follow-up work merely to record an idea.

## Review finding threshold

A reviewer may classify a finding as actionable only when the evidence clears
all four gates:

1. **Contract:** Name the frozen requirement, repository contract, supported
   behavior, or current material maintainability property that is violated.
   Preference, an alternative design, or generic best practice is not a
   contract.
2. **Reachability:** Show a credible supported input, state, call path,
   migration, failure, retry, or operational sequence that reaches the problem.
   Corrupt-state cases count only where validation, recovery, or fail-closed
   behavior is part of the current contract.
3. **Impact:** Describe the concrete current-scope effect on correctness,
   acceptance, data integrity, privacy, security, operability, compatibility,
   or materially safe maintenance. "Could be cleaner", negligible resource
   savings, and theoretical future misuse are not material impact.
4. **Proportionality:** Identify a bounded remediation whose expected value
   exceeds its implementation, regression, complexity, and review cost. A real
   edge case can still be not worth changing when the safe fix is broader or
   riskier than the impact.

If evidence for one gate is uncertain, run the cheapest decisive check. If it
remains uncertain, classify the item as a non-blocking question or omit it; do
not send it to a writer as a finding. This does not relax requirements
verification: a frozen acceptance criterion with missing or ambiguous evidence
still fails closed.

Use these dispositions independently of a reviewer's P0-P3 label:

- **FIX NOW:** Clears all four gates and the current change introduces or
  worsens it, it fails frozen acceptance, or it violates a mandatory current
  safety or compatibility contract. It must be resolved, removed from the
  change, or explicitly accepted by the user before qualification. A broad fix
  does not make a newly introduced defect deferrable; shrinking or reverting
  the offending behavior is also a bounded remediation.
- **DEFER:** Clears contract, reachability, and impact, is not introduced or
  worsened by the current change, and is a concrete pre-existing or otherwise
  independently existing non-blocker whose remediation is outside scope or
  disproportionate for this delivery. Do not block the current change. Create
  a follow-up only when the reproduction and expected value are concrete enough
  to justify backlog and future review cost.
- **NOT READY:** A current change still introduces or worsens a contract or
  correctness defect, but continued bounded remediation cannot close it because
  the family is unbounded or contradictory, required evidence or gates cannot
  be obtained, or the repair would exceed authority or scope. Do not relabel it
  `DEFER`. Qualification requires an explicit maintainer decision or bounded
  reversion of the offending change.
- **DISPROVE:** The claim is contradicted by direct contract, code, test, or
  supported-surface evidence. Record that evidence when responding to existing
  feedback.
- **OMIT:** Style preference, naming taste without ambiguity, optional polish,
  refactoring, speculative abstraction, generic hardening, unsupported corrupt
  input, marginal optimization, extra test permutation without a distinct
  risk, or a contrived edge case with no material current impact. Do not create
  code changes or follow-up work for it.

Material maintainability requires a present correctness or operability
consequence: for example, duplicated behavior already diverges, control flow
prevents reliable verification, or ambiguity makes a supported operation unsafe
to change. Complexity, length, repetition, or unfamiliarity alone do not clear
the threshold.

When evidence is uncertain, run the cheapest decisive check. If a failure only
exists hypothetically and is outside the supported contract, do not expand the
change to prevent it. If it appears in real operation, fail loudly, capture a
minimal reproduction, and fix it through the defect workflow.
