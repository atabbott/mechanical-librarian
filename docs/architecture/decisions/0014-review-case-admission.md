# ADR-0014: Atomically admit immutable review cases from current ProposalSets

- Status: accepted
- Date: 2026-07-26

## Decision

Production review admission stores immutable `review-case-v1` documents in the
same SQLite database as reports and ProposalSets. Report publication maintains
one current pointer for the stable configured target lineage `(connection,
library, item)`. Admission uses `BEGIN IMMEDIATE` and rechecks the exact
ProposalSet, report, and lineage pointer before it creates a case, replaces a
current case with a successor, or retires applicability without an empty case.

Only `propose_change` and `unresolved_choice` results are actionable. The case
retains all sixteen results as context and remains undecided; no reviewer
decision, target write, provider call, queue, or background work is introduced.

## Consequences

Exact replay converges. Exact retrieval derives current or historical status
without changing a case. ADR-0013 still permits only absent or exact-current
state: no migration, import, conversion, or alias is supported.

Each target-lineage checkpoint records a deterministic transition identity,
fixed `review-admission-v1` actor, source report and ProposalSet, and its
admission outcome. Cases remain `undecided`; this decision grants no F11
decision, reviewer, queue, or target-write authority.
