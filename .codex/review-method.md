# Independent review method

Independent review is a set of context-isolated attempts to falsify readiness,
not several agents completing one inherited checklist. Freeze authoritative
requirements, base, target ref/tree, policy bundle, external-contract baseline,
and applicable artifacts before review. Apply `.codex/binding-preflight.md` and
give each initial reviewer only its canonical raw packet, role charter, and one
primary lens from `.codex/review-lenses.md`.

The root owns cross-review synthesis under
`.codex/finding-family-audit.md`. Reviewers report raw evidence; they do not
self-certify family completeness or see the root's prior conclusions.

## Evidence-before-review gate

Before the final broad review wave, classify every required local, private,
Linux, installed-artifact, and external/live qualification obligation from the
frozen tickets and repository contracts. Record exact artifact and environment
identity under `.codex/structured-records.md`:

- `FRESH`: executed for the current target ref/tree and exact artifact after
  the last change covered by its invalidation rule;
- `INHERITED`: direct evidence from the identical ref/tree, artifact, and
  environment whose recorded invalidation rule has not fired;
- `NOT_APPLICABLE`: the contract does not require the evidence and a matching
  explicit skip-manifest entry gives test ID, platform, reason, owning gate,
  authority, and time;
- `UNAVAILABLE`: applicable evidence could not be obtained and a matching skip
  records why; required unavailable evidence blocks review/qualification; or
- `STALE`: identity or invalidation no longer matches and the evidence must be
  rerun.

Never infer artifact identity from a branch name, filename, or nearby build.
Bind repository/tree plus every applicable package, module, wheel, document, or
installed unit by its recorded digest and build environment. Private evidence
must retain only its permitted aggregate; external evidence records source,
date, exact ref/artifact, and invalidation rule.

Run every mandatory private, Linux, and external qualification before spawning
the final broad reviewers. A missing run is not an implicit skip. `UNAVAILABLE`
may support an `AWAITING_EXTERNAL` or blocked handoff, never a readiness claim.
Use the strict frozen JSON packet accepted by
`.codex/scripts/review_preflight.py`; do not replace required PASS evidence with
a CLI bypass. After any invalidating edit, mark affected evidence stale and
rerun it before targeted re-review or final qualification.

## Initial reviewer creation

Create every initial acceptance, requirements, defect/focused verification,
quality, safety, feedback, or readiness review with `fork_turns="none"`. The
v3 review must be `FRESH_ISOLATED`, have a unique non-null qualified reviewer
thread, cite the exact frozen ref, lens and packet digest, and have no
originating thread. Publication challenges follow the stricter rule below.

The canonical packet must not contain worker summaries, orchestrator
conclusions, earlier reviewer verdicts, family dispositions, remediation
history, iteration labels, commit-message framing, or named green-test results.
Raw tests, assertions, source, requirements, and exact evidence records are
permitted. If the target moves, invalidate unfinished reviews and rebuild the
packet; do not combine reviews of different refs.

Every reviewer must:

1. identify packet ID/digest, frozen target/ref, role, and primary lens;
2. derive its expected outcome or primary risk before reading test layout;
3. inspect raw source and authoritative contracts;
4. run the cheapest safe decisive diagnostic when material doubt remains;
5. read assertions rather than trusting test names or status;
6. apply `.codex/engineering-judgment.md` to findings; and
7. report findings or explicit no-findings evidence, attempted falsification,
   commands, deliberately unexamined areas, and residual uncertainty.

The primary lens is a focus, not a gag: report any substantiated blocker found
while tracing it. Do not manufacture unrelated coverage or a universal sibling
inventory; root performs cross-review family synthesis.

## Role charters

### Acceptance and requirements

Derive each promised user/operator observation before tests. Map criteria to
implementation, asserted tests, supported demonstration, and missing evidence.
Try to falsify at least one material criterion.

### Defect and focused outcome

Begin from the frozen reproduction or focused outcome. Establish the smallest
observable contract, then probe the nearest realistic regression or delivery-
classification failure.

### Whole-diff maintainability

Read the cumulative `base...head` diff as its future maintainer. Seek concrete
interaction defects, regressions, misleading abstractions, policy drift,
weakened gates, and stranded or contradictory states.

### Safety

Identify forbidden effects first, then trace hostile input/state, dependency
failure, excess authority, concurrency, recovery, or replay. Separate current
contracts from optional future hardening.

### Pull-request feedback

Treat each comment as a claim. Reproduce, disprove, or bound it before adopting
its framing, severity, or repair.

### Readiness

Determine whether the proposed vertical packet is implementable and observable
without hidden decisions. Challenge classification, boundaries, dependencies,
failure partitions, evidence, and closure mapping, not wording taste.

## Review-wave barrier and targeted re-review

All applicable initial reviews for one target must reference the same frozen
ref and complete before the root synthesizes findings, sends any remediation,
or claims the wave passed. Capacity may split execution into scheduling waves;
it never splits the barrier. A blocked, missing, wrong-ref, or unqualified
review keeps the barrier open or explicitly unavailable.

After a coherent repair, send only the affected family and nearest interactions
to the exact original reviewer thread using a new invocation binding. Record
`TARGETED_CONTINUATION`, its originating review/binding/thread, resulting ref,
and new packet digest. Do not replace that reviewer to obtain approval or
restart unaffected broad reviews. Proven thread loss uses a new binding/thread
under `REPLACEMENT_ISOLATED`, complete origin lineage, and a full affected-
charter review. If repair exposes a new material manifestation, return to root
synthesis and apply the family expansion thresholds before more editing.

## Fresh publication challenge

Run `publication_challenger` after local remediation, required evidence, and
qualification when any of these applies:

- hostile parsing, credentials, authorization, persistence, migrations,
  concurrency, recovery, rollback, production writes, or external trust
  boundaries changed;
- implementation reached `I2` or PR maintenance reached `PR2`;
- a family reached expansion count 2; or
- cumulative remediation materially changed interacting subsystems.

Create a new `fork_turns="none"` thread for every fresh challenge. Give it authoritative
requirements, applicable contracts, optional base, final workflow artifact/ref, raw source/artifacts,
its lens, and packet digest only. Exclude this method, the family policy, all
prior findings/summaries, iteration labels, and named test results. If it finds
a defect, synthesize normally; the same challenger thread performs only the
targeted verification of its own repaired finding.

The challenge complements connector `C1`; it does not consume or replace it.
