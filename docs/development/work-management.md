# Work Management

GitHub is the authoritative execution tracker for Mechanical Librarian. Product,
architecture, operations, and reference documents remain authoritative for
system behavior; issues link to those contracts rather than copying them.

## Repository and project model

- One organization-owned repository contains source, documentation, issues,
  pull requests, releases, and security advisories.
- One organization-level GitHub Project contains the implementation backlog
  and roadmap.
- The repository and Project remain private during initial development and are
  made public only after the publication-readiness audit.
- Discussions contain questions, support, and exploratory ideas. Accepted,
  bounded work is converted into an issue.

## Work hierarchy

Use four issue types:

- **Epic**: an outcome spanning multiple independently deliverable issues;
- **Feature**: a user-visible capability or coherent behavior change;
- **Task**: bounded implementation, testing, operations, or documentation work;
- **Bug**: behavior that violates an implemented contract.

The M0–M9 packages in the
[initial build plan](../product/initial-build-plan.md) are parent Epic issues.
Deliverables become sub-issues. Use native issue dependencies for blocking
relationships; do not encode dependency order only in prose or checklists.

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
M0–M9 for roadmap reporting. Parent relationships remain the authoritative
hierarchy.

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

- **Triage**: `Inbox` and `Triaged`, grouped by issue type;
- **Ready**: unblocked `Ready` work, sorted by priority and risk;
- **Current**: `In progress`, `In review`, and `Blocked` as a board;
- **Roadmap**: Epics and Features by work package and target release;
- **Quality and security**: High or Critical risk plus security-labelled work;
- **Recently completed**: recent `Done` work before automatic archival.

## Automation

The Free-plan auto-add workflow adds all issues and pull requests from the
repository to the Project. Built-in workflows set new items to `Inbox`, closed
issues and merged pull requests to `Done`, and archive completed items after a
retention window.

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
- organization membership, teams, permissions, and contact details;
- repository visibility, Project visibility, Discussions, and security
  reporting settings;
- licence, attribution, support, governance, and security contacts; and
- fixtures, screenshots, diagnostics, paths, URLs, and identifiers.

After publication, enable and enforce branch protection, private vulnerability
reporting, public Discussions, and the supported release policy.
