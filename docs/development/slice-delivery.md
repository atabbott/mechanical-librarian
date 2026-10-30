# Delivery and Merging

How a change gets from an issue to `main`. [Work management](work-management.md)
covers issues and Project fields; [CONTRIBUTING.md](../../CONTRIBUTING.md)
covers the checks every pull request must pass.

## Two kinds of delivery

**Functional slices** add new product functionality that crosses more than one
layer. A slice is the smallest end-to-end outcome a user or operator can see
through a supported surface (CLI, HTTP API, browser or container) without
breaking a safety or recovery contract.

**Focused changes** are everything else: fixes, audit corrections, security
work, documentation, tooling, CI, dependency updates, maintenance and
standalone tickets. Each uses one branch (`fix/<issue>-<slug>`,
`docs/<issue>-<slug>`, `chore/<issue>-<slug>` or `feature/<issue>-<slug>`) and
one ticket-level pull request. Do not create a slice tracker for one of these.

A slice may add a framework, library or tool its outcome needs, inside the
slice. A standalone dependency or tooling change is a focused change.

## Slices

Every promoted slice has exactly one tracker issue. It states the outcome, the
demonstration, the tickets it closes, its dependencies and governing
contracts, its failure, restart and recovery behaviour, and its exit criteria.
A promoted tracker may be the slice's only issue; one activation pull request
then closes it directly.

Larger slices use sequential increments. Each increment:

- starts from current `main`;
- is a complete, independently safe vertical step: anything not yet complete
  stays unreachable, disabled or report-only;
- fully satisfies and closes at least one ticket before it is marked ready (a
  non-activation increment closes a constituent; only the final activation
  increment closes the tracker);
- stays draft until it can carry that closing reference.

A horizontal type, repository, migration or adapter primitive is not an
increment on its own; keep it with the behaviour that proves it. A deliberately
narrow adapter or interface is fine when it fully demonstrates the outcome and
leaves room to extend. No slice defers its safety, recovery, observability,
documentation, clean-schema or test obligations to a later slice.

Slices follow the order of their GitHub milestones. A future outcome stays a
short deferred issue with no slice ID until that same issue is promoted
([work management](work-management.md#deferred-and-planned-outcomes)).

`Deliver slice <ID>` (or `Deliver and merge slice <ID>`) authorises one bounded
transaction for that assigned `Ready` tracker: plan increments, branch, open
and update pull requests, get review, fix findings and CI, merge each qualified
head, delete merged branches, and reconcile issues and Project fields. It never
authorises a bypass, force push, administrative merge, weakened gate, release,
production-service access or unrelated change. Without an ID, it selects the
highest-priority unblocked assigned `Ready` tracker. Never select or implicitly
promote an `Unassigned backlog` outcome.

The transaction ends `MERGED` (everything verified on `main`, tracking
reconciled, branches removed) or `BLOCKED` (a concrete external prerequisite or
maintainer decision, recorded on the tracker with what already merged). An open
pull request or partly merged slice is neither. Merged increments are durable
progress: do not revert them because a later one is delayed; resume from
current `main`.

A ready slice pull request lets a reviewer answer: what can someone do after
this that they could not before, how is it demonstrated, which tickets and
contracts does it complete, what happens on invalid input, unavailable
dependencies, duplicates, restarts and stale state, what authority exists and
what is deliberately absent, and what evidence proves it.

After 1.0.0, maintainers review whether small features still need the slice
workflow.

## Findings and CI

CI failures and review findings are fixed on the same branch and pull request.
Every new head repeats the affected review and qualification. Open a new pull
request only for a genuinely separate outcome or a required security or privacy
separation.

## Merging

`.github/workflows/public-gate.yml` is the public gate. It runs on every
pull-request head and on each push to `main`, checks out the exact head SHA
with read-only permissions, and holds no credential or private corpus.
[What merging proves](../../CONTRIBUTING.md#what-merging-proves) lists every
check.

The merge itself is decided outside the candidate. The maintainer-owned merge
controller and its validators live in the separate development repository and
are linked into the checkout under the ignored `.codex/**`; content in a pull
request can never change what decides whether that pull request merges. Before
merging, the controller binds the exact reviewed head, the live required
checks, resolved review threads, the closing issue and the review evidence,
repeats those checks immediately before a conditional squash merge on the
expected head, and afterwards verifies that the merge commit has the qualified
tree and is on `main`. Ambiguous outcomes become explicit recovery states, never
success. The default-branch ruleset independently requires a pull request and
passing required checks; nobody has a bypass.

### Renovate platform auto-merge exception

Pull requests authored by `renovate[bot]` whose update class requests
auto-merge may use GitHub-native squash auto-merge instead of the controller.
The default-branch ruleset still requires a pull request, a head current with
`main`, and a passing check run for each of the eight gate jobs
(`fast-source`, `fast-source-2`, `fast-source-3`, `fast-source-4`,
`exact-installed`, `protected-browser`, `container-image` and
`dependency-audit`). Renovate has no bypass and cannot write to `main`. A
failing update stays open for a manual fix; no gate is weakened to admit one.
Updates that need Dependency Dashboard approval merge through the controller.

The exception is bound to the author and does not transfer through a title,
label, branch name or human account.
[Dependencies](dependencies.md#automated-updates) says which update classes
auto-merge, and
[work management](work-management.md#pull-requests-and-tracking) covers the
tracking exemption.

## Definition of done

A slice or change is done when its demonstration works from a clean checkout,
every closed ticket is satisfied in full, behaviour is tested in proportion to
risk, documentation and the changelog describe what merged, and the merge,
tracking and cleanup are verified.
