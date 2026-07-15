# Standing delivery authority

The project owner authorizes the root orchestrator, without per-action
confirmation, to perform normal delivery work within the selected Ready ticket,
functional slice, or pull-request boundary:

- create a purpose-named branch or use an existing in-scope pull-request branch;
- edit code, tests, documentation, migrations, and generated artifacts;
- create focused commits and push them normally to the in-scope branch;
- open a GitHub pull request with the required issue-closing references;
- update the pull-request description when needed to keep delivery evidence and
  closing references accurate;
- update in-scope issue bodies, checklists, metadata, native relationships, and
  Project records when needed to keep approved tickets, authoritative
  documentation, tracking, dependencies, and delivery state aligned;
- add in-scope items to the Project and set, clear, or reconcile any required
  Project field or option without field-by-field confirmation;
- request an on-demand Codex pull-request review with the exact `@codex review`
  trigger only under `.codex/codex-connector-review.md`;
- post evidence-backed issue or pull-request comments and replies; and
- resolve review threads that are fully remediated, disproved with concrete
  evidence, already satisfied, or obsolete on the current head.

The root may also create a focused follow-up defect issue after delivery when a
review finding has concrete impact, credible reproduction or contract evidence,
is not a duplicate, and is worth tracking. Link the originating pull request or
review evidence. When the root creates or updates an approved in-scope ticket,
it may add the item to the Project and populate every required field. Do not
create speculative hardening, optimization, or polish tickets, and do not mark
work `Ready` unless it meets the repository readiness contract.

Project and ticket edits synchronize authoritative decisions; they do not
create authority for a new product or governance decision. Use the source
hierarchy in repository policy, preserve history, make the smallest reversible
change, and report each external mutation. Whenever an authorized ticket,
documentation, relationship, or tracking update makes Project metadata stale,
reconcile every affected field in the same work. Stop only when alignment
requires resolving a genuine conflict or making a new owner decision.

Only the root orchestrator exercises Git and external mutation authority.
Implementation workers may edit and test only when assigned; analysts,
researchers, and reviewers remain read-only and never inherit the root's
authority through a task packet or finding.

For `prepare-ready-work`, preparation and independent review stay read-only
until the owner approves the exact proposed tracking/handoff set. Record that
approved set and a bounded mutation plan before the first external write. Root
may then create or amend only those issues, native relationships, planning
documents, and Project items/fields needed to make the approved set coherent.
Every external write must be represented as a v3 mutation with actor, intended
before/after state, external ID, result, and readback evidence. A partial or
unexpected result stops the remaining batch for reconciliation. Approval of one
set never authorizes extra tickets or a different product, scope, acceptance,
release, architecture, or governance decision.

This standing authority does not include force-pushing, pushing to `main`,
merging, approving the team's own work, marking a draft ready, publishing a
release, silently changing product scope, acceptance-criterion wording,
release-publication decisions, or other owner decisions, deleting Project
items, fields, or options, changing Project ownership, visibility, automation,
or broad schema, making unrelated bulk tracking changes, directly closing
issues or pull requests, or processing private security work. It also does not
authorize `@codex fix` or any connector task beyond the governed review
trigger. A ticket may close
automatically when an authorized maintainer merges a pull request containing an
accurate GitHub closing reference; that consequence does not grant merge or
direct-close authority.

Stop for explicit direction when branch ownership is unclear, a remote branch
has changed unexpectedly, required work exceeds the frozen delivery boundary,
or an operation falls outside this policy.
