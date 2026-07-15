# Pull-request review evidence

Use these records to keep review decisions traceable across concurrent PRs and
changing head commits.

## Queue row

| Field | Required value |
| --- | --- |
| PR | Number, URL, title |
| Delivery | Slice, Defect, Bug, or focused work kind |
| Scope | Linked tracker/ticket and frozen update marker |
| Git | Base, head branch, frozen head SHA, writable status |
| State | Draft/readiness, checks, review decision, connector review SHA |
| Risk | Project risk plus changed high-risk surfaces |
| Dependencies | Blockers, stacked base, overlapping active branch |
| Scheduling | Independent cohort eligibility or exact dependency, ownership, worktree, or scope-overlap reason |
| Disposition | Assess, awaiting state, blocked, or excluded with reason |

## Assessment cohort

For every scheduled read-only cohort, record the PR number, frozen head SHA,
base/tree, linked requirements, assigned reviewer roles, wave, canonical packet
ID/digest, primary lens, `FRESH_ISOLATED` thread, and binding attestations. Record every task
as pending, active, complete, or invalidated by a head change. A cohort is a
scheduling boundary only: the root retains queue selection, synthesis,
mutation, and completion authority. Reuse specialist threads across cohorts and
do not represent a partially staffed wave as a complete review.
Initial reviewers use `fork_turns="none"`; only the exact originating thread may
perform a later `TARGETED_CONTINUATION`.

Record the queue-wide writer lease holder, target PR, isolated worktree, grant
point, and release point whenever remediation begins. No two PRs may hold the
lease concurrently.

## Feedback finding

Record every unresolved thread, substantive top-level review comment, and
independent finding with:

- stable comment/thread or reviewer identifier;
- author, file and tight anchor, and head SHA assessed;
- concise claim and concrete impact;
- classification: `ACCEPT`, `QUESTION`, `DUPLICATE`, `OUTDATED`, `DISPUTE`,
  `ALREADY SATISFIED`, `OUT OF SCOPE`, or `PRIVATE SECURITY`;
- violated contract, credible reachability, concrete material impact, and
  proportionality assessment;
- code, contract, ticket, test, or history evidence;
- smallest defensible action or response;
- status: unresolved, remediated, disproved, user-accepted, or blocking; and
- verification required after any change.

When a finding is accepted, link the root-owned family record: violated
invariant, source findings, known manifestations and safe siblings, search
evidence, expansion count, miss reason when applicable, and remediation test
matrix. Multiple comments exposing one invariant remain one family.

`ACCEPT` means the claim clears every finding threshold, is in scope, and
requires change. `DISPUTE` requires affirmative contrary evidence, not
preference. `OUT OF SCOPE` must identify whether concrete expected value
justifies post-delivery triage. Treat `PRIVATE SECURITY` as an immediate stop
and private routing decision.

## Deferred finding

For a concrete non-blocking or out-of-scope finding, record the originating PR
and review, affected behavior, impact, reproduction or contract evidence,
duplicate search, and disposition: discard, duplicate, already tracked, or new
defect ticket. A future ticket must not delay the current qualified PR.

## Remediation packet

Freeze the PR and ticket markers, list accepted finding identifiers, expected
behavior, permitted files or behavior, prohibited scope, required tests and
documentation, safety obligations, and every accepted finding-family record.
Consolidate the accepted findings known on the frozen head into one packet. One
packet is assigned to one writer.

For a versioned external boundary, attach the exact upstream revision,
authoritative sources, decision matrix, deliberate omissions, fixture-fidelity
evidence, and any compatibility drift check. Do not classify a fixture-derived
assumption as a frozen contract.

## Qualification record

Record final head/tree, exact repository/package/module/wheel artifact identities,
canonical environments, and each evidence state (`FRESH`, `INHERITED`, explicit
`NOT_APPLICABLE`, `UNAVAILABLE`, or `STALE`). Every N/A or unavailable duty
links its explicit skip-manifest entry. Also record changed files, commit and push result, targeted and full
checks, reviewer lenses and re-runs, publication challenge when required,
remaining findings, replies, and thread resolution. Record connector round,
opening/requested head, reaction target, target ID, `PENDING_PICKUP`,
`IN_PROGRESS`, `CLEAN`, `FINDINGS_POSTED`, or `PICKUP_UNOBSERVED`, and whether
branch protection requires completion. A new C2 trigger is terminal: capture
its comment result without a follow-up refresh and inspect its reactions in the
next maintenance round.

Also record that each accepted family was reviewed against the final diff,
whether new evidence expanded it, the miss reason, escalation decision, and
current PR ordinal. Record connector `C1` or `C2` separately; no local
remediation count authorizes `C3`.

Record the one pre-edit synthesis matrix; pre/post status and declared paths;
the explicit formatter/linter path list or `NOT APPLICABLE`; targeted cohorts
and complete-gate invalidation points; logical commit boundaries; local gates,
observed remote CI, and unavailable #36/#37/#38-owned evidence without a parity
claim; and the Project-field mutations performed, their evidence, any
unresolved alignment conflict, and the final Project state.

For a pushed `NOT READY` checkpoint, additionally record the proof that the
remaining blocker existed on the frozen pre-remediation head or was neither
introduced nor worsened by the packet, the repaired families that independently
qualified, the unresolved blocker disposition, and that no connector review was
requested.
