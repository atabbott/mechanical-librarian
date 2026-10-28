# Agent Operating Guide

This repository is the production implementation of Mechanical Librarian. Treat it
as a public open-source project even before publication: never add personal
paths, domains, credentials, media, database snapshots, or unredacted reports.

## Reading

Required before any change: this file and [PRODUCT.md](PRODUCT.md) for the
product boundary. Read [testing and quality strategy](docs/development/testing-strategy.md)
when designing executable changes, choosing proof, or running validation, and
[CONTRIBUTING.md](CONTRIBUTING.md) when planning delivery. Read both before
publication: together they establish what merging actually proves.

Everything else is reference, read when the work reaches it and not before.
[Security and threat model](docs/architecture/security.md) is the authority for
any change touching credentials, network access, parsers, AI inputs, writes,
backups, authentication, or upgrades.
[Functional slice delivery](docs/development/slice-delivery.md) and
[exact-head merging](docs/development/exact-head-merging.md) govern how a change
is delivered and merged; [the delivery-slice plan](docs/product/delivery-slices.md)
is the ordered ledger of what is planned, not a rulebook.
[Dependencies](docs/development/dependencies.md),
[work management](docs/development/work-management.md), and
[documentation standards](docs/development/documentation-standards.md) each own
their own subject. Reading a reference you do not need costs the session more
than it returns.

## Waiting and asynchronous work

Minimize model-level polling. After delegating, continue useful independent
work while it exists. At a true dependency boundary, call `wait_agent` once
with the longest practical timeout and let an agent message or completion wake
the thread. If it times out without an event, enter another long wait unless
there is a concrete reason to inspect or intervene. Do not insert short waits,
agent-status reads, or agent-list calls merely to confirm that nothing changed.

Let long-running commands block in the terminal until they exit when the tool
supports it. For background processes, prefer process exit, shell `wait`, or a
native watcher. For an external condition without a blocking primitive, use a
small bounded script that polls locally and returns on change or timeout. Local
process polling is preferable to repeated model turns that sleep and reread the
same state.

Do not spawn an agent or subagent merely to monitor a process or condition. When
a small purpose-built watcher would avoid model wakeups, write it ad hoc in a
task-specific temporary directory and run it in the terminal. It should remain
silent while progress is healthy and return only on completion, meaningful
state change, a diagnosed stall, or its explicit boundary. Choose its sampling
interval and stall definition from the process being observed; a slow first run
may warrant checking progress only every 20 minutes, while a shorter job may
need a different bound. Remove the temporary watcher afterwards. Add a watcher
to the repository only when it has a current repeated consumer.

Starting a watcher does not make the coordinator idle. Continue useful
independent work while the watcher runs. When that work is exhausted and the
watched event is still required, stop active reasoning and enter one long
blocking wait on the watcher session so process output or exit wakes the task.
Do not return a final answer, repeatedly poll the session, or remain active as a
scheduler while a required watcher is outstanding.

After useful work is exhausted, emit no progress commentary announcing that a
watcher is still running or that nothing has changed. Yield directly to the
outstanding watcher session. The next model turn must be caused by watcher
completion, failure, meaningful change, diagnosed stall, explicit boundary,
new user input, or another genuinely actionable event. A native command is not
suitable merely because it blocks: do not use a progress-printing watcher such
as `gh pr checks --watch` or `gh run watch` as the model-facing wait.

## Product contract

One self-contained container beside an existing library; Audiobookshelf first;
supported service APIs only, never a direct database edit; read-only media
mounts; AI reviewers with no credential or write authority; no unattended
destructive work; and consent, backup readiness, plan validation, optimistic
concurrency, read-back verification and durable audit records before any write.
[PRODUCT.md](PRODUCT.md) is the single product-boundary statement and states
each of those once.

## Architecture rules

- Domain code must not import infrastructure, web framework, database, or
  provider implementations.
- Adapters implement contracts owned by the domain or application layer.
- Orchestration coordinates domain services; it does not contain provider-
  specific parsing or HTTP payload construction.
- API and CLI surfaces call the same application services.
- Background work is persisted before execution and must be idempotent.
- State transitions must be explicit, validated, and covered by tests.
- Configuration must use documented schema fields; do not add hidden
  environment-variable behavior.
- Avoid global mutable state and import-time side effects.
- Operational events are value-free JSON lines, each written to stderr in one
  best-effort call that cannot raise into the caller. They are redacted by
  construction: allow-listed keys, closed vocabularies and bounded integers,
  never a message, path, title, credential or exception text. Nothing
  configures `logging`, so its INFO records are dropped and `extra` fields never
  appear; do not use it for an event someone needs.

The allowed dependency direction is:

```text
interfaces -> application -> domain
adapters --------------------^
infrastructure -> application/domain contracts
```

Run the static architecture-boundary test whenever imports or packages move.

## Code economy

- Express field validation through the shared validation vocabulary rather
  than expanding the same checks longhand for every field. The backend
  vocabulary is `mechanical_librarian.domain.validation`; the browser's field
  validators live beside their mapping functions in `frontend/src/api/http.ts`.
  Extend a vocabulary when a check is genuinely new; do not reintroduce a
  hand-written copy of one it already names.
- No helper without a consumer. A new abstraction, helper or option has a
  current caller in the same change; one with none is deleted.
- A consolidation must remove duplication. Replacing copies of the same logic
  with a shared implementation is worth it only when the result is smaller than
  the copies it replaces, counting the plumbing it forces at the call sites. A
  generalised helper with a switch for each former caller relabels the
  duplication rather than removing it: keep the longhand.
- A single-caller extraction needs a stated reason: a name that carries meaning
  at the call site, a contract tested directly, or a boundary a type checker
  enforces. Without one, keep the longhand.
- A relocation is judged on ownership, not size. Moving code to where its
  dependencies allow may add structure; it must not leave two copies behind.
- Split a function over about 200 lines, or a frontend route file over about
  1,500, when a unit already changes it. Never split as a standalone change:
  splitting adds lines and churn with no behaviour change.
- Exact-type checks are deliberate. `type(value) is int` rejects `bool`, and
  `type(value) is str` rejects a `str` subclass, because a persisted document
  carries the exact canonical type. Use `isinstance` only where accepting a
  subclass is the intended contract, and say so where you do.
- Rewriting validation must keep its contract: the same inputs accepted and
  rejected, with the same exception types, wherever rejection is a contract
  (public API responses, persisted-document acceptance). Internal error
  messages that no consumer reads may change. Never weaken, skip or narrow a
  check to save lines.

## Change requirements

Ready pull requests normally require a supported closing reference to an open
repository issue. Only spelling, formatting, broken-link, or comment-only
cleanup with no material impact may use the maintainer-applied exact
`tracking-exempt` label plus visible prose under one top-level
`Tracking exemption` heading. Never use the exemption for executable behavior,
dependencies or lockfiles, build/CI/workflow, release, configuration,
migration, security, architecture, governance, product-contract, or
delivery-plan changes.

Renovate-authored dependency pull requests are the sole automation exception
to that tracking rule. They require neither an issue nor the
`tracking-exempt` label because `.github/renovate.json` is their maintainer-
reviewed authority boundary. They remain subject to the documented dependency
update policy and exact-head qualification. Only the managed version updates explicitly
enabled there may use GitHub-native pull-request auto-merge after all required
checks pass. Every other Renovate pull request requires the ordinary connector
review and maintainer-owned merge controller; no other bot or human-authored
pull request inherits this exception.

Every change must include the smallest appropriate combination of:

- tests proving behavior and failure handling;
- documentation updates;
- for persisted-schema changes before the first production release, enforce
  ADR-0013: declare `clean rebuild required` or deliver one code-defined
  exact-predecessor adoption plan in the same issue and pull request. Ordinary
  startup accepts only absent or exact-current state; an adoption may be an
  explicit offline operation over reconstructible derived objects only, never
  a generic migration, import, repair, downgrade, or canonical-data conversion;
- an ADR when a durable architectural decision changes;
- changelog entry for user-visible behavior, stating the outcome in at most six
  lines with its issue reference and leaving the arithmetic, proof narrative,
  and review history to the pull request and issue it points to;
- security review for credentials, network access, parsers, AI inputs, writes,
  backups, authentication, or upgrades.

Do not weaken tests, safety gates, types, or linters to make a change pass. Fix
the implementation or explicitly revise the governing decision with rationale
and review. There is no coverage threshold to weaken: the decision not to
enforce one is recorded in
[testing and quality strategy](docs/development/testing-strategy.md), and
[CONTRIBUTING.md](CONTRIBUTING.md) lists every check the gate does enforce
beside the ones that are maintainer-manual.

## Functional slice delivery

New product functionality is delivered as one functional slice per explicitly
promoted, assigned `Ready` tracker: a vertical, observable outcome delivered as
one pull request or as ordered independently safe increments, each started from
current `main` and reviewed and qualified at its exact head. `Deliver slice
<ID>`, and its synonym `Deliver and merge slice <ID>`, is standing authority for
that bounded transaction and for nothing beyond it: no bypass, force push,
administrative merge, weakened safety, release, or production-service use.

Everything else -- fixes, audit corrections, security work, documentation,
tooling, dependency-only changes, maintenance, and genuinely standalone tickets
-- uses a focused `fix/`, `docs/`, `chore/`, or `feature/` branch and one
ticket-level pull request. Do not create a slice tracker to rename one of those.

- The trusted merge controller, its validators, and its evidence remain
  maintainer-owned and ignored under `.codex/**`; its gates, receipt
  contracts, parser bounds, and the residual
  `EXTERNAL_ACTOR_MUTATION_NOT_PREVENTED` risk it accepts are stated in
  [exact-head merging](docs/development/exact-head-merging.md).
- [Functional slice delivery](docs/development/slice-delivery.md) owns the
  complete workflow: selection and the `Unassigned backlog` boundary, the
  dependency a slice may introduce without a separate permission ticket, which
  increment closes which ticket, partial-transaction recovery, and when a small
  addition may leave the slice workflow after the initial build. The ordered
  [delivery-slice plan](docs/product/delivery-slices.md) is the planned ledger.
  Governance, urgent incident, and private security exceptions follow the narrow
  rules in the delivery workflow and do not redefine normal product delivery.

## Repository hygiene

- Use synthetic or irreversibly sanitized fixtures.
- Pin direct dependencies and verify lockfile changes.
- Do not commit `.env`, `auth.json`, tokens, cookies, media, covers from real
  libraries, SQLite production files, logs containing paths, or generated
  runtime state.
- Keep generated API schemas and documentation reproducible.
- Keep examples executable and covered by documentation tests.

## Delivery stages

Until a production release exists, default all integrations to report-only.
Write-capable development requires explicit test fixtures or isolated fake
services. Never point development or CI at a contributor's live library.
