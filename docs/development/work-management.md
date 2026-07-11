# Work Management

GitHub is the authoritative execution tracker for Mechanical Librarian. Product,
architecture, operations, and reference documents remain authoritative for
system behavior; issues link to those contracts rather than copying them.

## Repository and project model

- One repository owned by the initial maintainer's GitHub account contains
  source, documentation, issues, pull requests, releases, and security
  advisories.
- One account-owned GitHub Project contains the implementation backlog and
  roadmap.
- The repository and Project remain private during initial development and are
  made public only after the publication-readiness audit.
- Discussions contain questions, support, and exploratory ideas. Accepted,
  bounded work is converted into an issue.
- Ownership may move to an organization later if contributor governance or
  project scale justifies it. The transfer must preserve issue, Project,
  release, security, package, and automation behavior and update this document
  in the same change.

## Work hierarchy

Use six work kinds:

- **Epic**: an outcome spanning multiple independently deliverable issues;
- **Feature**: a user-visible capability or coherent behavior change;
- **Task**: bounded implementation, testing, operations, or documentation work;
- **Defect**: an internally discovered or pre-release nonconformance where the
  implementation, tests, documentation, or delivered ticket fails an accepted
  contract or acceptance criterion; and
- **Bug**: behavior previously treated as delivered and working that fails in a
  supported build or observable user/operator workflow.

Use `Defect` for audit findings such as an incomplete closed ticket, a failed
qualification gate, or a contract violation found before the affected behavior
is released as supported. Use `Bug` for regressions and incorrect behavior
reported against a delivered build. Discovery source does not determine the
kind: a user may expose a pre-release defect, and an automated test may expose a
released bug.

The initial personal repository uses ordinary GitHub issues because native
issue types are unavailable. The Project `Work kind` field is authoritative;
issue forms express submission intent and triage assigns the field. Do not
create duplicate type labels solely to simulate issue types. If the repository
moves to an organization later, native issue types may be adopted through a
reviewed migration while preserving the Defect/Bug distinction where it remains
useful.

The WP0–WP9 packages in the
[initial build plan](../product/initial-build-plan.md) are parent work-package
issues. Deliverables become sub-issues. Use native issue dependencies for
blocking relationships; do not encode dependency order only in prose or
checklists.

The M0–M7 outcome milestones in the [roadmap](../product/roadmap.md) are a
separate product progression. `WP` always means implementation work package;
`M` always means product milestone.

Milestones represent release trains such as `0.1.0`, `0.2.0`, and `1.0.0`.
They do not duplicate work packages.

### Functional slice trackers

A functional slice tracker is the cross-work-package execution unit for new
product functionality during the initial application build. It is an ordinary
issue with a stable slice ID, an executable outcome, a demonstration path, the
exact tickets it intends to close, and slice-level exit criteria. It does not
replace the work-package parent of those tickets.

One slice tracker maps to one `slice/<slice-id>-<short-slug>` branch and one
ready pull request. A new-functionality ticket has one primary delivery slice.
Split a ticket whose acceptance criteria would require multiple slice pull
requests before the ticket becomes `Ready`.

Fixes, audit corrections, security work, documentation, CI and developer
tooling, dependencies, maintenance, and independently reviewable standalone
tickets are not slice trackers or slice constituents. They use focused branches
and ticket-level pull requests. A focused correction may block a slice through a
native dependency, but it remains independently reviewed and merged.

See [functional slice delivery](slice-delivery.md) for the mandatory branch and
review workflow and [delivery slices](../product/delivery-slices.md) for the
ordered product outcomes.

## Project fields

### Status

- `Inbox`: newly created and not yet assessed;
- `Triaged`: valid work that still needs scope or scheduling;
- `Ready`: sufficiently specified and unblocked;
- `In progress`: actively being implemented;
- `In review`: implementation is awaiting review or verification;
- `Blocked`: cannot progress because a linked dependency or decision is open;
- `Done`: acceptance criteria are verified and the issue is closed.

Only `Ready` work may be started normally. Urgent security recovery may follow
the private advisory process instead.

### Priority

- `P0 Critical`: active security, data-loss, or production-integrity incident;
- `P1 High`: release blocker or major safety/correctness gap;
- `P2 Normal`: planned product work;
- `P3 Low`: worthwhile but deferrable improvement.

Priority describes consequence and sequencing, not contributor importance.

### Risk

- `Low`: isolated documentation, presentation, or pure behavior;
- `Medium`: bounded integration or persistence behavior with established gates;
- `High`: credentials, parsers, AI inputs, public API, migrations, production
  writes, backups, authentication, or upgrades;
- `Critical`: destructive behavior, recovery authority, update trust, or a
  change capable of broad silent corruption.

### Size

Use `XS`, `S`, `M`, `L`, or `XL` as a relative planning signal. An `XL` issue
must normally be decomposed before it becomes `Ready`.

### Target release and work package

Target release connects work to a release milestone. Work package maps work to
WP0–WP9 for implementation reporting. Parent relationships remain the
authoritative hierarchy.

### Work kind

`Work kind` is the structured classification for `Epic`, `Feature`, `Task`,
`Defect`, and `Bug`. New or legacy items that have not yet been assessed use
`Unclassified`; an item cannot move to `Ready` until triage replaces that value.

Work kind does not encode urgency or danger. Priority controls sequencing and
Risk controls review depth. A low-impact bug can be `P3 Low`; a pre-release
defect capable of silent corruption can be `P0 Critical` with `Critical` risk.

Changing between `Defect` and `Bug` requires a brief issue comment explaining
which delivery/support boundary changed or was misunderstood. Reclassification
does not erase history, reproduction evidence, or linked review findings.

### Delivery slice

`Delivery slice` maps new-functionality work to the primary executable outcome
and branch that will complete it. Values use the stable IDs in the
delivery-slice plan plus:

- `Cross-slice parent` for work-package and other genuine parent issues;
- `Unassigned backlog` for future functionality not yet scheduled; and
- `Not applicable` for fixes, corrections, security work, documentation,
  tooling, dependencies, maintenance, and standalone tickets.

A new-functionality ticket cannot move to `Ready` without a delivery-slice
value. Non-slice work may move to `Ready` with `Not applicable`. Slice tracker
issues use their own slice value.

## Labels

Labels describe cross-cutting properties that are unsuitable as structured
fields:

- workflow signals: `needs-triage`, `needs-design`, `needs-adr`;
- contribution signals: `good-first-issue`, `help-wanted`;
- change signals: `breaking-change`, `security`;
- ownership areas: `area:domain`, `area:application`, `area:adapter`,
  `area:infrastructure`, `area:api`, `area:web`, `area:operations`,
  `area:documentation`, and `area:security`.

Do not create status, priority, risk, size, milestone, or issue-type labels.

## Required issue content

An issue reaching `Ready` must contain:

- a concrete outcome;
- governing document, ADR, invariant, threat, or parent link;
- verifiable acceptance criteria;
- relevant success and failure behavior;
- required test layers;
- documentation and compatibility impact;
- dependencies and migration or rollback needs; and
- privacy and security considerations proportionate to risk.

A Defect or Bug additionally requires:

- the affected commit, build, or release when known;
- the violated contract or previously working behavior;
- a minimal deterministic reproduction or qualification failure;
- expected and actual behavior;
- impact and safe workaround, if any; and
- the regression test or other evidence required to prevent recurrence.

An issue is not a substitute for an ADR. A durable decision is discussed in an
issue and recorded in an ADR before the implementation issue closes.

## Views

Maintain these Project views:

- **Triage**: `Inbox` and `Triaged`, filterable by work package and ownership
  area;
- **Ready**: unblocked `Ready` work, sorted by priority and risk;
- **Current**: `In progress`, `In review`, and `Blocked` as a board;
- **Delivery slices**: new-functionality trackers and mapped tickets grouped by
  `Delivery slice`, excluding `Not applicable`, and ordered by target release
  and slice ID;
- **Roadmap**: Epics and Features by work package and target release;
- **Quality and security**: High or Critical risk plus security-labelled work;
- **Defects and bugs**: `Defect` and `Bug`, grouped by status and sorted by
  priority, risk, and age;
- **Recently completed**: recent `Done` work before automatic archival.

## Automation

Until a repository-scoped auto-add workflow is explicitly configured and
verified, issue and pull-request creation must add the item to the Project in
the same operation. Built-in workflows may set new items to `Inbox`, closed
issues and merged pull requests to `Done`, and archive completed items only
after their configured behavior has been tested against this Project.

Additional automation may use GitHub Actions and the Projects API, but field
changes must remain understandable and reversible. Automation never changes
priority, risk, or release scope based only on issue text generated by AI.

## Pull requests

During the initial application build, new product functionality follows one
functional slice per branch and ready pull request. The slice tracker moves to
`In progress` when its branch starts and to `In review` only after the supported
demonstration and all exit criteria pass. Constituent tickets close in that pull
request only when their complete acceptance criteria are satisfied.

Ready slice pull requests must use GitHub auto-closing references for the slice
tracker and every completed constituent ticket. The linked-work workflow rejects
placeholder, nonexistent, closed, or missing references. A horizontal ticket
commit may exist on the slice branch, but completion of that commit alone does
not justify a separate ready pull request.

The gate uses GitHub-recognized closing-link metadata rather than parsing pull
request Markdown. Examples, quotations, fenced code, manual links, nonexistent
issues, and closed issues do not satisfy it. At least one `Closes`, `Fixes`, or
`Resolves` reference must resolve to an open issue in this repository, and every
recognized closing reference used by the gate must remain open when the pull
request becomes ready. Cross-repository and non-default-branch closing links do
not satisfy this repository-local gate. Drafts are exempt and are checked on the
`ready_for_review` transition; description edits are checked again.
The bounded gate supports at most 100 recognized references and reports a
specific limit failure rather than evaluating incomplete pagination. If the
same issue is both manually linked and named by a closing keyword, place the
closing reference on its own line so the gate can distinguish the body source
using GitHub's rendered closing-keyword and issue-link annotations without
treating examples, custom links, formatting, quotations, or code as closing
instructions.
The workflow runs from protected base-branch context with explicit read-only
permissions, checks out only the recorded base SHA, and treats pull-request
metadata as data. It never executes the pull request checkout or receives write
authority.

This keeps issue and slice lifecycles aligned with the merge commit without
granting a workflow token authority to change priority, risk, or scope. The
required branch naming, scope, demonstration, review, and exception rules are in
[functional slice delivery](slice-delivery.md).

Fixes, corrections, security work, documentation, tooling, dependency updates,
maintenance, and standalone tickets use one purpose-based branch and
ticket-level ready pull request. Their issue moves through the same status and
quality gates without creating a slice tracker.

Defects and bugs use `fix/<issue>-<short-slug>`. Their pull requests include the
pre-fix reproduction, root cause, regression evidence, affected-version scope,
and any recovery or compatibility impact. Closing a Defect or Bug without a
regression test requires an explicit reason and alternative durable evidence.

Emergency fixes may create the issue and pull request together. Private
security work uses a repository security advisory and temporary private fork,
not a public issue.

## Triage cadence

During initial solo development:

- process `Inbox` before selecting new work;
- replace `Unclassified` with an assessed Work kind before moving work to
  `Ready`;
- keep at most one new-functionality slice actively in progress unless it is
  independently blocked; focused non-slice work may proceed when it does not
  obscure or destabilize the active slice;
- review blocked work weekly;
- update parent Epic status when sub-issue scope changes; and
- publish a Project status update at each release milestone.

The cadence may evolve with the contributor community, but status meanings and
quality gates must not silently change.

## Private-to-public transition

Treat private development as pre-public, not confidential scratch space.
Before publication, audit:

- complete Git history, branches, tags, and release assets;
- issues, pull requests, comments, attachments, and Projects;
- Actions logs, artifacts, caches, variables, and secrets;
- repository collaborators, account permissions, future organization transfer
  settings, and contact details;
- repository visibility, Project visibility, Discussions, and security
  reporting settings;
- licence, attribution, support, governance, and security contacts; and
- fixtures, screenshots, diagnostics, paths, URLs, and identifiers.

After publication, enable and enforce branch protection, private vulnerability
reporting, public Discussions, and the supported release policy.
