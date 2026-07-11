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

Do not use a slice for:

- a bug fix or audit correction;
- security remediation or incident recovery;
- documentation or governance;
- CI, developer tooling, release chores, or dependency updates;
- refactoring or maintenance with no new product capability; or
- a genuinely standalone ticket that is already independently reviewable.

Those changes use a focused purpose branch such as `fix/<issue>-<slug>`,
`docs/<issue>-<slug>`, `chore/<issue>-<slug>`, or
`feature/<issue>-<slug>`, and one ticket-level pull request. They still meet all
risk-proportionate quality and documentation gates. A fix that blocks a slice is
merged separately before the slice continues; it is not absorbed into the slice
merely to make the slice green.

## Unit of delivery

Every planned new-functionality slice branch has exactly one slice tracker
issue. The tracker states:

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

## Branch workflow

1. Select the highest-priority unblocked slice tracker in `Ready`.
2. Confirm that every ticket the slice intends to close is sufficiently
   specified and mapped to that slice in the Project.
3. Create one branch from current `main` named `slice/<slice-id>-<short-slug>`,
   for example `slice/f03-synthetic-inventory`.
4. Move the slice tracker to `In progress`. Constituent tickets may move to
   `In progress` as their work begins, but the branch remains the shared unit of
   delivery.
5. Implement the end-to-end path in focused commits. Intermediate commits may
   complete individual contracts, but they are not separate delivery branches
   or pull requests.
6. Rebase or merge current `main` before final verification. Resolve conflicts
   without weakening the slice contract or hiding unrelated changes.
7. Run the complete repository gates plus the slice's demonstration and
   failure/recovery checks from a clean checkout.
8. Open the pull request only when the slice outcome and exit criteria are
   complete. Mark the slice tracker and completed constituent tickets
   `In review`.
9. The pull request uses an auto-closing reference for the slice tracker and
   each constituent ticket whose full acceptance criteria it satisfies.
10. Merge only after review confirms the observable outcome, safety boundaries,
    tests, documentation, migrations, and recovery behavior. Delete the slice
    branch after merge.

Draft pull requests are not the normal progress-reporting mechanism. Use the
Project, issue comments, and focused commits to expose progress. A draft may be
opened when remote CI or an external review is necessary to finish the slice,
but it must not be presented as ready for review and must not close tickets.

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
workflow primitive is not a completed functional slice unless that primitive is
itself the documented executable outcome. Keep such work on the active slice
branch until the end-to-end behavior is reviewable.

## Scope control

- One `slice/` branch and one ready pull request deliver one new-functionality
  slice.
- A branch may close multiple tickets only when all contribute to the same
  demonstrated outcome.
- Unrelated refactoring, opportunistic cleanup, and future-slice scaffolding are
  excluded.
- A slice expected to exceed an `L` review should be divided into smaller
  independently demonstrable slices before implementation starts.
- Stacked slice branches require an explicit dependency and must remain
  independently reviewable; they are exceptional, not the default.
- Fixes, corrections, security work, governance, tooling, dependencies,
  maintenance, and standalone tickets remain focused PRs outside the slice.
- Urgent incident recovery and private security-advisory work follow their
  dedicated focused workflow.

## Relaxing the rule after the initial build

The slice-first rule is intentionally strongest while the application lacks
stable executable seams and every new capability tends to cross domain,
application, persistence, adapter, and interface boundaries.

At 1.0.0 release qualification, maintainers review this rule. After stable
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
- the ready pull request is reviewable as one coherent outcome.

Merging a slice does not imply that every contributing work package is
complete. It proves only the bounded cross-package outcome named by the slice.
