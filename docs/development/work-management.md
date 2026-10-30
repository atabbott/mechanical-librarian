# Work Management

GitHub issues and milestones track all work, and they are where to follow
progress. The maintainer's private GitHub Project adds the planning fields
below.
Issues link to the product, architecture and reference documents that govern
behaviour rather than copying them. Questions, support and loose ideas belong
in Discussions; accepted, bounded work becomes an issue.
[Delivery and merging](slice-delivery.md) covers branches and pull requests.

## Work kinds

The Project's `Work kind` field classifies every issue; do not use type labels.

- **Epic**: an outcome spanning several independently deliverable issues.
- **Feature**: a user-visible capability or behaviour change.
- **Task**: bounded implementation, testing, operations or documentation work.
- **Defect**: a pre-release failure to meet an accepted contract, closed
  ticket or qualification gate, including audit findings.
- **Bug**: behaviour that was delivered and working and now fails in a
  supported build or workflow.

Unassessed items are `Unclassified` and cannot become `Ready`. Changing
between Defect and Bug needs a short comment saying why.

## Milestones and checkpoint versioning

Each milestone is one checkpoint titled `X.Y.Z — Outcome`, and its
`Target release` option is exactly `X.Y.Z`. Before 1.0.0, completing a
checkpoint does not publish a release; the maintainer separately decides which
delivered checkpoint becomes 1.0.0, the first public release.

Before 1.0.0, numbers are `0.MINOR.PATCH`. A milestone holding any Feature or
Epic takes the next minor; one holding only Defect, Bug and Task work takes the
next patch. A required clean rebuild does not raise the number; the changelog
states it. After 1.0.0, semantic versioning applies.

Numbers follow planned delivery order. An unstarted milestone is renumbered
when it is reordered or when a feature joins a patch milestone; a started or
closed one keeps its number. A theme with no scheduled position is an
unnumbered milestone named by its outcome. `Release readiness` holds the
cross-cutting work that gates 1.0.0.

## Deferred and planned outcomes

A deferred outcome is a future product outcome with no delivery commitment.
Its Project state is `Work kind: Feature`, `Status: Triaged`,
`Delivery slice: Unassigned backlog` and `Target release: Backlog`. It has no
GitHub milestone and no functional-slice designation. Its issue body needs only
an observable user or operator outcome, its material included and excluded
boundaries, known dependencies or open decisions, and enough context to tell it
apart from duplicates. Do not write implementation design or constituents for
speculative work.

A planned but unpromoted outcome reserves a named delivery checkpoint without
yet becoming a slice. It has `Work kind: Feature`, `Status: Triaged` or
`Blocked`, `Delivery slice: Unassigned backlog`, a named `Target release` other
than `Backlog`, and the matching GitHub milestone. It cannot be started.

When an outcome is selected for delivery, explicitly promote the same issue
into a slice tracker: assign its slice ID, `Delivery slice` value, checkpoint
and milestone, and add the demonstration, acceptance criteria, dependencies,
governing contracts and closure mapping that `Ready` needs. Create constituents
only for independently valuable supported increments that can close on their
own; otherwise the tracker is the only issue.

## Project fields

- **Status:** `Inbox` (not yet assessed), `Triaged` (valid, needs scope or
  scheduling), `Ready` (specified and unblocked), `In progress`, `In review`,
  `Blocked` (waiting on a linked dependency or decision), `Done` (verified and
  closed). Only `Ready` work is started normally.
- **Priority:** `P0 Critical` (active security, data-loss or integrity
  incident), `P1 High` (release blocker or major safety gap), `P2 Normal`,
  `P3 Low`.
- **Risk:** `Low`, `Medium`, `High` (credentials, parsers, AI inputs, public
  API, production writes, backups, authentication, upgrades), `Critical`
  (destructive behaviour, recovery authority, or possible broad silent
  corruption). Risk sets review depth.
- **Size:** `XS` to `XL`. Split an `XL` issue before it becomes `Ready`.
- **Target release:** the checkpoint, or `Backlog`.
- **Delivery slice:** the slice ID for new functionality, `Cross-slice parent`
  for a current parent issue, `Unassigned backlog` for unpromoted outcomes, and
  `Not applicable` for focused changes.

Labels carry only cross-cutting signals: `needs-triage`, `needs-design`,
`needs-adr`, `good-first-issue`, `help-wanted`, `breaking-change`, `security`,
and `area:*` ownership labels. Do not create status, priority, risk, size,
milestone or type labels.

## Ready issues

An issue reaching `Ready` states a concrete outcome, its governing document or
ADR, verifiable acceptance criteria, success and failure behaviour, required
test layers, documentation and compatibility impact, dependencies, and privacy
and security considerations proportionate to risk. A Defect or Bug also states
the affected commit or build, the violated contract, a deterministic
reproduction, expected and actual behaviour, impact, and the regression
evidence that will prevent recurrence. A durable decision is recorded in an ADR
before its implementation issue closes.

## Pull requests and tracking

Every ready pull request closes at least one open issue in this repository with
a standalone GitHub closing line (`Closes #N`, or `fixes`/`resolves`). Defects
and bugs use `fix/<issue>-<slug>` and include the reproduction, root cause and
regression evidence. Private security work uses a repository security advisory,
not a public issue.

Only spelling, formatting, broken-link or comment-only cleanup may skip the
issue: a maintainer applies the exact `tracking-exempt` label and the pull
request explains why under a `Tracking exemption` heading. Anything touching
behaviour, dependencies, CI, configuration, security, architecture, governance,
the product contract or the delivery plan needs an issue.

Renovate pull requests are authorised by `.github/renovate.json` instead and
need neither; see [dependencies](dependencies.md).

New issues and pull requests are added to the Project when created.
Automation never changes priority, risk or release scope from AI-generated
issue text.
