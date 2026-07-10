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

Use four work kinds:

- **Epic**: an outcome spanning multiple independently deliverable issues;
- **Feature**: a user-visible capability or coherent behavior change;
- **Task**: bounded implementation, testing, operations, or documentation work;
- **Bug**: behavior that violates an implemented contract.

The initial personal repository uses ordinary GitHub issues because native
organization issue types are unavailable. Issue forms, titles, parent
relationships, and required content express the work kind. Do not create
duplicate type labels solely to simulate native issue types. If the repository
moves to an organization later, native issue types may be adopted through a
reviewed migration.

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

An issue is not a substitute for an ADR. A durable decision is discussed in an
issue and recorded in an ADR before the implementation issue closes.

## Views

Maintain these Project views:

- **Triage**: `Inbox` and `Triaged`, filterable by work package and ownership
  area;
- **Ready**: unblocked `Ready` work, sorted by priority and risk;
- **Current**: `In progress`, `In review`, and `Blocked` as a board;
- **Roadmap**: Epics and Features by work package and target release;
- **Quality and security**: High or Critical risk plus security-labelled work;
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

Every non-trivial pull request links an issue. The issue moves to `In review`
when implementation is ready and closes only after acceptance criteria pass.

Emergency fixes may create the issue and pull request together. Private
security work uses a repository security advisory and temporary private fork,
not a public issue.

## Triage cadence

During initial solo development:

- process `Inbox` before selecting new work;
- keep at most one implementation issue actively in progress unless work is
  independently blocked;
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
