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
- Use structured logging with automatic secret and private-path redaction.

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
- A new abstraction requires a current consumer in the same change. A helper
  with no caller is not an improvement: delete it.
- **Consolidation** — replacing two or more copies of the same logic — must be
  lighter than the copies it replaces. One that costs more lines than the
  longhand it consolidates is not an improvement: delete it and keep the
  longhand. Measure all the implementation it adds — the shared implementation
  plus any plumbing it forces at the former call sites, wrappers, argument
  mapping, conversions — against all the implementation it deletes. Two things
  are exempt and nothing else is: the fixed structure of having a shared home,
  which is the module's header, imports and declaration and one import line per
  consumer; and documentation the governing issue's acceptance demanded,
  reported with the acceptance that required it. Structure costs the same for a
  good abstraction and a bad one, so charging it to the abstraction judges the
  module boundary instead, and rejects the small cross-file consolidations this
  rule most wants: every one of them needs a shared home. A line is
  implementation where it carries any part of an executable expression, even
  where it also carries a declaration, and structure only where it carries none
  — an import, a header, a declaration's bare signature, a closing brace. So
  `export const labelWords = (value: string) => value.replaceAll('_', ' ')` is
  one line of implementation, and the same declaration wrapped across two is one
  of each. Count both sides that way or the comparison is not a comparison: the
  copies being deleted are counted the same, and a rule under which a
  declaration could hide behaviour would let a helper pass on its formatting.
  The test bites at both ends. A shared implementation fatter than the copies
  fails it, and so does a thin one that pushed its bulk out to the call sites.
  The generalised helper carrying a configuration switch for each former call
  site is the case it exists to stop — it removes no duplication, it relabels
  it: delete it and keep the longhand.
- Worked, from #662. `frontend/src/coverChoices.ts` adds 17 lines of shared
  implementation and one line of plumbing at each of its two call sites, and the
  longer name it introduces makes the formatter break Detail's prepare button
  across four lines where it was one. Classify those four the way this rule
  classifies any line: `<button` and `>` carry no expression and are structure,
  while `disabled={disabled || cover.prepare.isPending}` and
  `onClick={() => cover.prepare.mutate()}` each carry one, so the button goes
  from one line of implementation to two and from none of structure to two. A
  reflow forced at a former call site is plumbing like any other, so that added
  line counts with the rest: 17 and 2 and 1 is 20, against the 20 lines of
  duplicated implementation the two routes delete. Equal, and the clause does
  not settle equal: it opens by requiring a consolidation to be lighter than
  the copies it replaces and then rejects one that costs more lines, and those
  two sentences part company at exactly this measurement. #721 owns which of
  them governs a tie; this example reports the figures and does not decide it.
  `frontend/src/labels.ts` adds 3 against the 8 deleted with five formatter
  declarations and two copied patterns, and forces no plumbing. Both still grew
  the tree, `+16` and `+7`, on module structure, seven consumer imports, that
  reflow, and the nine comment lines that change's acceptance demanded. #662's
  pull request reconciles both to those net figures, which is the arithmetic
  this rule requires, and one line of the whole change answers to no bucket
  below: the reflow's two structure lines belong to the structure bucket, and
  its one implementation line belongs to nothing. That single line is #719's,
  recorded here and not closed here. Both would fail on module total, and
  `labels.ts` clears the test here. Deciding a measurement basis is not a
  neutral act: it settles cases the ambiguity left open, and it is what puts
  the cover module on the tie rather than past it.
- **Extraction within a boundary** — pulling a helper out of code that could
  have kept it — with a single caller has to earn its place, and it can.
  Consolidating one copy is not consolidation, so the default is to keep the
  longhand; keep the helper where it makes something possible that the longhand
  does not: a name that carries meaning at the call site, a contract that can be
  tested directly, or a boundary a type checker can enforce. Say which, in the
  change. "It reads better" is a reason; it just has to be written down. A
  helper that buys none of those is decoration — delete it. Line count is not a
  factor. A line count is evidence about duplication — a consolidation heavier
  than the copies it replaces removed none — and a single-caller extraction has
  no duplication for it to weigh: the body moves, a call replaces it, and the
  arithmetic lands in the same place whether the helper earned its place or not.
  So an extraction is neither required to be lighter than the longhand nor
  excused for being heavier; the three grounds above are the whole test, at
  least one of them still has to hold, and none of them is weakened by this,
  which is also why a helper that comes out line-neutral has shown nothing by
  coming out line-neutral. It is still reported under the bullet below, like
  every other change: unmeasured and unreported are different things. The
  exemption belongs to the single caller and not to the word — a helper that
  gains a second caller by replacing a copy of what it already does is a
  consolidation, and is measured as one. One that is genuinely new at both
  sites replaced no copy, so there is nothing to measure it against: it
  answers to the vocabulary rule at the top of this section.
- **Relocation** — moving code across a layer, module, or file boundary because
  its dependencies place it there — is judged differently, and the two rules
  above do not apply to it. Relocation necessarily adds a module header,
  imports, a declaration, and explicit parameters for what were implicit
  captures, so it is expected to add lines and is not required to be lighter.
  A required move stands on correct ownership alone, however few lines it
  carries and however many callers it ends up with. Judge it on whether the code
  now lives where its dependencies allow, and whether the move duplicated
  anything: a relocation that leaves two copies of one implementation has
  failed; one that costs structure has not.
- Where relocation would leave copies of a check or a rejection rule behind,
  express the shared part once through the vocabulary rather than duplicating
  it. Prefer a shared function to an inheritance stack when both remove the same
  duplication — the flatter shape is usually lighter and catches contract errors
  at the definition site.
- A refactoring pull request reports its net line-count delta per changed
  module, attributing the lines to: code moved in or out unchanged, structure
  the change required (a move's header, imports and parameters; an extraction's
  declaration and call site), copies consolidation removed, checks newly added,
  and documentation the governing issue's acceptance demanded, reported with
  that acceptance. Those five reconcile to the net delta — a pure move shows the
  same figure leaving one module and arriving in another. One exception is known
  and still open: where a change makes an expression long enough that the
  formatter re-wraps the line holding it, the implementation the reflow spreads
  over more lines answers to no bucket here. That gap is #719's, not closed by
  the bullet you are reading. Demanded documentation
  earns a bucket rather than an exclusion because an exclusion stops the
  arithmetic finishing: a reader could not tell a demanded comment from a bucket
  the report forgot, and a report nobody can check is not evidence. A site that
  gains a check it never had is expected to grow, and that growth is a success.
  Never weaken, skip, or narrow a check to reach a line count, and never
  manufacture a helper to improve one.
- Exact-type checks are deliberate subclass rejection, not an accident of
  style. `type(value) is int` rejects `bool`, and `type(value) is str` rejects
  a `str` subclass, because a persisted document must carry the exact canonical
  type rather than something that merely behaves like it. Use `isinstance`
  only where accepting a subclass is the intended contract, and say so where
  you do.
- Consolidation must preserve exactly which inputs are rejected, with which
  error identity and message. Where it cannot, leave that site longhand and
  record why. Rejection behavior is the invariant; how it is written is not.

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
