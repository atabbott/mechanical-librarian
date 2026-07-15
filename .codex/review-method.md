# Independent review method

Use review tasks, not specialized role identities. Common tasks are acceptance,
code quality, requirements, safety, readiness, and publication challenge.

## Initial review

1. Freeze the exact staged or committed target with
   `.codex/scripts/review_packet.py create`.
2. Give each reviewer its task charter, primary lens, packet path, and digest.
3. Start initial reviewers with a fresh context and no worker conclusions,
   prior verdicts, remediation history, or named green tests.
4. Hold synthesis and editing until the applicable initial wave returns.

The lens prioritizes attention but never prevents reporting another concrete
material blocker. Findings must state impact, evidence, location, and the
smallest defensible remediation. Omit preferences and speculative hardening.

## Proportional tasks

- Acceptance or requirements review is required for a changed outcome.
- Code-quality review is required for executable code or complex tooling.
- Safety review is required only for a Tier 2 safety trigger.
- A challenger is required only for Tier 2 publication or the second material
  expansion of one finding family.

One reviewer invocation may cover multiple explicitly listed tasks when the
change is small and the lenses are compatible. Safety and challenger tasks stay
independent when their trigger exists.

## Remediation and stop

Consolidate overlapping findings before editing. Send one bounded remediation
cohort to the worker. Re-review each accepted family once in its originating
thread against a new packet/digest. Do not restart unaffected reviews or shop
for a passing verdict.

After that re-review, either pass, stop on a remaining blocker, or defer a
non-blocking improvement. A new unrelated observation does not reopen the
current refinement round.

Record observed thread IDs and launch constraints honestly. They prevent
accidental context reuse; they do not prove identity against a malicious local
operator.
