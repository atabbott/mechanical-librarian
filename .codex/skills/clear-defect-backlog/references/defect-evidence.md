# Defect evidence formats

Use direct issue, code, test, command, and supported-surface evidence. Keep
triage hypotheses separate from verified facts.

## Queue

| Rank | Ticket and update marker | State | Priority and risk | Reproduction | Dependencies | Overlap or duplicate evidence | Disposition | Next action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Issue and frozen revision | READY, BLOCKED, NEEDS EVIDENCE, ALREADY FIXED, DUPLICATE CANDIDATE, NOT A DEFECT, or DONE | Project values | Confirmed, missing, or unsafe | Issue identifiers | Evidence or none | Queue decision | Exact action |

## Frozen ticket baseline

Record:

- issue number, title, update marker, status, Project fields, and dependencies;
- reported environment and smallest supplied reproduction;
- expected behavior and governing contract;
- actual behavior and impact;
- every acceptance criterion and required test layer;
- documentation, compatibility, migration, recovery, privacy, and security
  implications; and
- focused branch and pull-request boundary.

## Reproduction record

Record exact environment assumptions, synthetic input, commands or supported-
surface steps, expected output, actual output, determinism, smallest known
case, current-main result, suspected fault boundary, and uncertainty. Do not
record secrets, private paths, production data, or unsafe payloads.

## Acceptance and regression matrix

| Criterion | Status | Implementation evidence | Regression-test evidence | Reproduction evidence | Missing work |
| --- | --- | --- | --- | --- | --- |
| One criterion | PASS, FAIL, PARTIAL, NOT DEMONSTRATED, or justified NOT APPLICABLE | File and symbol | Test and asserted behavior | Before and after result | Gap or none |

End with an explicit ticket-closure recommendation.

## Review finding

Record severity, reviewer, file and tight location, violated contract, credible
reachability, concrete material impact, evidence, smallest remediation,
proportionality, action (`FIX NOW`, `DEFER`, `DISPROVE`, or `OMIT`), status, and
rerun evidence.

## Ticket qualification

Record final issue-drift check, clean-state evidence, complete gate results,
before/after reproduction, regression and negative tests, final AC matrix,
quality and safety dispositions, direct orchestrator high-risk inspection,
changed documentation or migrations, residual risks, focused branch/PR state,
and whether the queue may safely advance. Also record distinct reviewer lenses,
attempted falsifications, the root family/remediation matrix, expansion counts,
miss reasons, and publication challenge when required; pre/post status,
declared and formatter/linter paths; targeted cohorts and complete-gate
invalidation; logical commit boundaries; local gates versus observed or
unavailable CI; and the Project-field mutations performed, their evidence, any
unresolved alignment conflict, connector pickup/completion state, and the final
Project state.
