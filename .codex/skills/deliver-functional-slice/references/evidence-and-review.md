# Evidence and review formats

Use these formats throughout slice delivery. Keep evidence concrete and cite
repository paths, symbols, test assertions, commands, or supported-surface
results. Do not substitute summaries for evidence.

## Frozen requirement baseline

| Item | Updated marker | Delivery state | Governing source | Intended closure |
| --- | --- | --- | --- | --- |
| Tracker or ticket | Timestamp or revision | Ready and mapped state | Link or document | Yes or no |

Record the observable outcome, supported demonstration, exact closure list,
dependencies, risk, branch boundary, and all criteria beneath the table.

For a versioned external boundary, also record the exact upstream revision and
authoritative sources, the decision matrix, deliberate omissions, fixture-
fidelity evidence, and the compatibility drift check or reason it is not
practical.

## Acceptance evidence matrix

| Item | Criterion | Status | Implementation evidence | Test evidence | Demonstration evidence | Missing work |
| --- | --- | --- | --- | --- | --- | --- |
| Tracker or ticket | One criterion only | PASS, FAIL, PARTIAL, NOT DEMONSTRATED, or justified NOT APPLICABLE | File and symbol | Test and asserted behavior | Command or supported-surface result | Concrete gap or none |

Do not combine criteria in one row. `PASS` requires implementation plus
proportionate verification. End with a closure recommendation for each ticket
and the tracker.

## Review finding

For each actionable finding record:

- severity;
- reviewer and review area;
- file and symbol or tight line location;
- violated contract, credible reachability, concrete material impact, and
  evidence;
- smallest defensible remediation;
- proportionality assessment;
- action: `FIX NOW`, `DEFER`, `DISPROVE`, or `OMIT`;
- status: unresolved, resolved, accepted by the user, or blocking; and
- rerun evidence when the affected area changes.

## Final qualification record

Record:

1. final requirement-drift check;
2. clean working-state or checkout evidence;
3. repository-wide gate commands and results;
4. supported slice demonstration and result;
5. risk-proportionate negative and recovery evidence;
6. final acceptance matrix and closure recommendations;
7. quality and safety review dispositions;
8. direct orchestrator inspection of high-risk paths;
9. changed documentation, migrations, changelog, and generated contracts;
10. external-contract and fixture-fidelity verdict when applicable;
11. each reviewer's distinct lens, attempted falsification, raw evidence, and
    residual uncertainty;
12. the root family/remediation synthesis matrix, expansion counts, and miss
    reasons;
13. pre/post status, declared paths, formatter/linter path list or `NOT
    APPLICABLE`, targeted cohorts, complete-gate invalidation points, and
    logical commit boundaries;
14. local gates, observed remote CI, and unavailable #36/#37/#38 evidence
    without a parity claim;
15. Project-field mutations performed, their evidence, any unresolved alignment
    conflict, and the final Project state; and
16. publication-challenge evidence when required, connector pickup/completion
    state, residual risks, and any explicitly deferred work.
