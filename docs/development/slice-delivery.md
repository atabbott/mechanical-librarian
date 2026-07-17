# Functional Slice Delivery

## Purpose

During the initial application build, new Mechanical Librarian functionality is
implemented and reviewed as functional slices. A slice is the smallest new
end-to-end product outcome that can be demonstrated through a supported
executable surface while preserving the product's safety and recovery
contracts. Work packages and implementation tickets still own capabilities;
they do not define slice boundaries.

This document is the authoritative branch and pull-request workflow. The
[delivery-slice plan](../product/delivery-slices.md) defines the ordered product
outcomes, and [work management](work-management.md) defines Project fields and
issue state.

## Delivery classification

Use a functional slice when the primary purpose is to add a new user- or
operator-observable application capability and the outcome requires coherent
work across more than one contract or layer.

Do not use a slice when the primary outcome is:

- a bug fix or audit correction;
- security remediation or incident recovery;
- documentation or governance;
- CI, developer tooling, release chores, or dependency-only updates;
- refactoring or maintenance with no new product capability; or
- a genuinely standalone ticket that is already independently reviewable.

Those changes use a focused purpose branch such as `fix/<issue>-<slug>`,
`docs/<issue>-<slug>`, `chore/<issue>-<slug>`, or
`feature/<issue>-<slug>`, and one ticket-level pull request. They still meet all
risk-proportionate quality and documentation gates. When a required gate fails
on an active pull request, the smallest bounded root-cause correction may be
made and requalified on that same pull request even when the exposed defect is
distinct from its principal outcome, provided the correction preserves the
governing safety contract. Use a separate pull request only when the correction
is a genuinely different deliverable that requires separation, or when a
security or privacy boundary requires it.

A slice may introduce a runtime or development framework, library, or tool
needed to deliver or qualify its observable outcome. This includes scoped test,
fixture-generation, code-generation, browser, build, analysis, and
documentation dependencies. The dependency remains an implementation choice
inside the slice rather than becoming a horizontal permission ticket. It must
solve a current requirement, be classified and locked correctly, stay out of
runtime artifacts when development-only, and meet the repository dependency
policy. A standalone dependency addition, upgrade, removal, or repository-wide
tooling change remains focused work.

## Unit of delivery

Every planned new-functionality slice has exactly one slice tracker issue. The
tracker is the outcome and lifecycle unit; a pull request is a bounded merge
unit. The tracker states:

- the observable operator or contributor outcome;
- the executable demonstration path;
- the implementation tickets completed by the slice;
- entry dependencies and governing contracts;
- success, failure, restart, privacy, and recovery behavior; and
- slice-level exit criteria and required test layers.

Implementation tickets contributing to new functionality retain their
work-package parents. The slice tracker is a cross-cutting execution record, not
a replacement parent. A contributing ticket belongs to one primary delivery
slice. If its acceptance criteria span more than one slice, split the ticket
before it reaches `Ready`; do not close part of a ticket in one slice and carry
hidden remainder into another. Non-slice tickets use `Not applicable` in the
Project `Delivery slice` field.

A small slice may still use one branch and pull request. A larger slice uses
sequential increments. Each increment is a complete, independently safe
vertical change that crosses only the layers needed for one observable step.
It may close constituent tickets only when their acceptance criteria are fully
satisfied. The final activation increment composes and demonstrates the whole
supported outcome and alone closes the slice tracker.

An increment must not expose an incomplete authority path, incompatible
persisted state, unsupported public contract, or behavior that depends on a
later pull request for safety. Pre-activation capability remains unreachable,
explicitly disabled, or report-only under a documented contract. A horizontal
type, repository, migration, or adapter primitive is not an increment merely
because it can merge alone.

## Autonomous delivery transaction

The instruction `Deliver slice <ID>` authorizes one bounded autonomous delivery
transaction for the selected Ready tracker. `Deliver and merge slice <ID>` is
an explicit synonym. Within the frozen outcome and exclusions, the transaction
may:

- plan ordered vertical increments;
- create purpose-based branches and draft or ready pull requests;
- push commits, obtain required review, remediate findings and CI failures, and
  repeat invalidated exact-head evidence;
- merge each exact reviewed and qualified head into `main` when every mutation
  prerequisite is available;
- delete merged delivery branches; and
- reconcile constituent tickets, the slice tracker, and Project fields.

Standing authority never permits check or review bypasses, administrative or
forced merges, force-pushing `main`, weakening a contract to pass a gate,
closing partially satisfied work, release publication, production-service
access, or unrelated changes.

Trusted merge control, validators, and evidence are maintainer-owned and remain
ignored under `.codex/**`, independently qualified from candidate content. The
current private account plan cannot provide the required strict server
enforcement. The standing transaction authority is active. Until the linked
external prerequisite is satisfied, its automatic mutation step returns
structured `BLOCKED` before mutation. Activating the authority does not claim
automatic mutation works in the current repository.

The transaction has two terminal results:

- `MERGED`: every increment and the activation outcome are verified on `main`,
  all intended closing references took effect, Project state is reconciled,
  and merged branches are removed; or
- `BLOCKED`: a concrete external prerequisite or maintainer decision prevents
  safe progress after in-scope recovery has been exhausted. The blocker and
  exact state already merged to `main` are recorded on the slice tracker.

An open pull request, review handoff, locally passing branch, or partially
merged transaction is not a terminal result.

## Branch workflow

1. When `Deliver slice <ID>` names a tracker, select that tracker and require it
   to be unblocked and `Ready`. Only an instruction without an ID selects the
   highest-priority unblocked slice tracker in `Ready`.
2. Confirm that every ticket the slice intends to close is sufficiently
   specified and mapped to that slice in the Project.
3. Record a one-increment plan or ordered vertical increments and move the
   tracker to `In progress`.
4. Create the first branch from current `main` as
   `slice/<slice-id>-<increment>-<short-slug>`. A one-increment slice may omit
   the increment segment.
5. Implement and demonstrate the bounded vertical increment in focused commits.
   A draft may expose progress or obtain remote CI, but must not claim
   completion or carry premature closing references.
6. Bring the branch onto current `main`, then freeze the head. Run the required
   review and exact-head public and local qualification.
7. Mark the pull request ready and use auto-closing references only for
   constituent tickets fully completed by that increment. Only the activation
   pull request closes the slice tracker.
8. Remediate CI failures and all C1, C2, or human findings on the same branch
   and pull request. Each changed head invalidates affected evidence and repeats
   review and qualification. Finding count is not a reason to create a new pull
   request.
9. Create another pull request only for a distinct vertical outcome or a
   security or privacy boundary that requires separation.
10. Immediately before mutation, use the ignored trusted control to repeat all
    race-sensitive gates and require the expected exact head. If strict server
    enforcement is unavailable, return structured pre-mutation `BLOCKED`.
11. After a permitted merge, verify the merge result on fetched remote `main`,
    delete the merged branch, and reconcile closed tickets and Project status.
12. Refresh from `main` and repeat from step 4 for the next increment. Do not
    maintain an obsolete unmerged stack.
13. In the activation increment, run the complete slice demonstration and
    aggregate qualification, close the tracker and remaining fully satisfied
    tickets, then verify the `MERGED` terminal state.

### Partial-transaction recovery

Every verified increment already on `main` is durable progress; do not roll it
back merely because a later increment is delayed or fails. Keep the tracker
`In progress`, or move it to `Blocked` only for a concrete external dependency
or maintainer decision. Record the last verified merge and commit, remaining
increments, invalidated evidence, and exact restart point. Resume with a new
branch from current `main`. If the outcome itself must change, return the
tracker to triage and revise it visibly; never report a partial transaction as
`MERGED` or `Done`.

## Review boundary

A ready new-functionality slice pull request must let a reviewer answer all of
the following without reconstructing behavior from isolated commits:

- What can a user, operator, or contributor do after this merge that they could
  not do before?
- How is that outcome demonstrated through the CLI, HTTP API, browser, or
  supported container surface?
- Which exact tickets and governing contracts are completed?
- What happens for invalid input, unavailable dependencies, duplicate work,
  restart, stale state, and bounded resource failure?
- What production authority exists, and what authority remains deliberately
  absent?
- How are persisted data, migrations, compatibility, backup, and rollback
  handled?
- Which automated and manual evidence proves the exit criteria?

A pull request that only adds a horizontal type, repository, adapter method, or
workflow primitive is not a safe increment unless that primitive is itself a
documented executable outcome. Keep it with the vertical behavior that proves
its safety and usefulness.

## Scope control

- One slice tracker defines one outcome; one or more sequential `slice/`
  branches and ready pull requests may deliver it.
- An increment may close multiple tickets only when it satisfies each in full
  through the same bounded demonstrated step.
- Unrelated refactoring, opportunistic cleanup, and future-slice scaffolding are
  excluded.
- Split a slice only when the resulting trackers describe genuinely distinct
  outcomes; split pull requests only at safe vertical or required security or
  privacy boundaries.
- Stacked slice branches require an explicit dependency and must remain
  independently safe and reviewable; sequential merge-and-refresh is the
  default.
- Fixes, corrections, security work, governance, tooling, dependency-only
  changes, maintenance, and standalone tickets remain focused PRs outside the
  slice.
- Urgent incident recovery and private security-advisory work follow their
  dedicated focused workflow.

## Relaxing the rule after the initial build

The slice-first rule is intentionally strongest while the application lacks
stable executable seams and every new capability tends to cross domain,
application, persistence, adapter, and interface boundaries.

At the 1.0.0 checkpoint's qualification boundary, maintainers review this rule.
After stable
contracts, test harnesses, adapters, repositories, and operator surfaces exist,
a small new feature may use an ordinary `feature/<issue>-<slug>` branch and
ticket-level PR when all of the following are true:

- it is independently demonstrable within an established surface;
- it does not introduce a new architectural boundary, authority path, migration
  strategy, or cross-cutting lifecycle;
- its failure and compatibility behavior fits existing contract suites; and
- the focused PR remains easier to review than an artificial multi-ticket
  slice.

Larger, cross-cutting, high-risk, or independently valuable capabilities retain
the slice workflow. The project should optimize for coherent review, not keep
slices as permanent ceremony after their original benefit has diminished.

## Definition of slice done

A new-functionality slice is done only when:

- its supported demonstration works from a clean checkout;
- every linked ticket's acceptance criteria are satisfied in full;
- success, negative, retry, restart, idempotency, and recovery behavior is
  tested in proportion to risk;
- architecture, security, privacy, compatibility, and resource bounds remain
  intact;
- persisted changes include migrations and recovery evidence;
- operator and contributor documentation describes the delivered behavior;
- changelog and generated contracts are current;
- the branch contains no unrelated or deferred work disguised as TODOs; and
- the activation pull request and accumulated increments are reviewable as one
  coherent outcome; and
- the complete outcome, merge results, tracking state, and cleanup are verified
  in the `MERGED` terminal state.

Merging a slice does not imply that every contributing work package is
complete. It proves only the bounded cross-package outcome named by the slice.
