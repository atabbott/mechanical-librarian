# Agent Operating Guide

This repository is the production implementation of Mechanical Librarian. Treat it
as a public open-source project even before publication: never add personal
paths, domains, credentials, media, database snapshots, or unredacted reports.

## Product contract

- Normal deployment is one self-contained container.
- Audiobookshelf is the first production adapter.
- Production metadata and cover writes use supported service APIs only.
- Application databases are never edited directly for normal operation.
- Media mounts are read-only unless an explicitly approved future feature adds
  embedded-file updates with independent backups and safety gates.
- AI reviewers never receive service credentials or production write access.
- Destructive operations are never unattended.
- Backup readiness, plan validation, optimistic concurrency, apply
  verification, and durable audit records are mandatory before writes.

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
  implementation and one line of plumbing at each of its two call sites, against
  the 20 lines of duplicated implementation the two routes delete: 19 against
  20, which passes by one line. `frontend/src/labels.ts` adds 3 against the 8
  deleted with five formatter declarations and two copied patterns, and forces
  no plumbing. Both still grew the tree, `+16` and `+7`, on module structure,
  seven consumer imports, a Prettier rewrap, and the nine comment lines that
  change's acceptance demanded. #662's changelog entry reconciles both to those
  net figures, which is the arithmetic this rule requires, and every figure but
  one answers to a bucket: the cover module's `+3` is Prettier re-wrapping a
  call site around a longer expression, and a reflow the change forced is named
  by no bucket below. That is recorded here, not fixed here. Both would fail on
  module total, and both passed here. Deciding a measurement basis is not a
  neutral act: it settles cases the ambiguity left open, and these two are the
  cases it settles.
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
  excused for being heavier; the three grounds above are the whole test and all
  three stay necessary, which is also why a helper that comes out line-neutral
  has shown nothing by coming out line-neutral. It is still reported under the
  bullet below, like every other change: unmeasured and unreported are different
  things. The exemption belongs to the single caller and not to the word — a
  helper with a second caller is a consolidation, and is measured as one.
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
  same figure leaving one module and arriving in another. Demanded documentation
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
update policy and exact-head qualification. Only the low-risk classes explicitly
enabled there may use GitHub-native pull-request auto-merge after all required
checks pass. Every other Renovate pull request requires the ordinary connector
review and maintainer-owned merge controller; no other bot or human-authored
pull request inherits this exception.

Every change must include the smallest appropriate combination of:

- tests proving behavior and failure handling;
- documentation updates;
- for persisted-schema changes before the first production release, enforce the
  clean-schema policy in ADR-0013: accept only absent or exact-current state;
  do not add migrations, imports, or conversions;
- an ADR when a durable architectural decision changes;
- changelog entry for user-visible behavior;
- security review for credentials, network access, parsers, AI inputs, writes,
  backups, authentication, or upgrades.

Do not weaken tests, safety gates, types, linters, or coverage thresholds to
make a change pass. Fix the implementation or explicitly revise the governing
decision with rationale and review.

## Functional slice delivery

- During the initial application build, new product functionality is normally
  delivered as a functional slice rather than as horizontal layer or
  work-package PRs.
- A concise future product outcome may remain deferred without becoming a
  slice tracker. Deferred outcomes carry `Delivery slice: Unassigned backlog`;
  they have no functional-slice designation or delivery milestone and are not
  eligible for implementation.
- Only explicit promotion of the same deferred issue creates an assigned slice
  tracker. `Deliver the next slice` selects only an assigned, unblocked
  `Ready` tracker and ignores `Unassigned backlog`; selection never implicitly
  promotes deferred work.
- Fixes, audit corrections, security work, dependency-only changes,
  documentation, tooling, maintenance, and genuinely standalone tickets are
  not slices. Use a focused purpose branch such as `fix/`, `docs/`, `chore/`,
  or `feature/` and one ticket-level pull request.
- A ticket or slice may introduce a framework, library, or tool that materially
  helps deliver its current outcome without a separate permission ticket.
  This includes correctly classified development-only dependencies needed for
  testing, generation, browser qualification, builds, analysis, or docs.
  Keep the dependency within that delivery boundary and satisfy the documented
  architecture, licence, security, testing, least-authority, and reproducible
  lockfile requirements. Durable architecture or trust-boundary changes still
  require their governing ADR or security review.
- Before starting new slice functionality, identify a `Ready` slice tracker
  with an observable executable outcome and complete exit criteria. Delivery
  may use one pull request or sequential, independently safe vertical
  increments. Each increment starts from current `main`, is reviewed and
  qualified at its exact head, and normally merges before the next begins.
- `Deliver slice <ID>` is standing authority to complete that bounded
  transaction: create branches and pull requests, remediate CI and review
  findings on the same branch and pull request, merge an exact qualified head,
  delete merged branches, and reconcile issue and Project state. `Deliver and
  merge slice <ID>` is an explicit synonym. The authority does not permit
  bypasses, force pushes, administrative merges, weakened safety, releases, or
  production-service use.
- A new pull request is reserved for a distinct vertical outcome or a required
  security or privacy separation. A new head on the active pull request
  invalidates affected review and qualification evidence and must be reviewed
  and qualified again.
- Non-activation increments close only constituent tickets whose complete
  acceptance criteria they satisfy. The activation increment proves the whole
  supported outcome and alone closes the slice tracker. A one-issue slice may
  use that tracker as its only closing reference and needs no constituent
  ticket. Incomplete behavior remains unreachable, disabled, or report-only
  until activation.
- Every ready increment pull request must fully satisfy and natively close at
  least one ticket: a non-activation increment closes at least one constituent,
  while the activation increment may satisfy this requirement by closing the
  tracker. If it cannot, keep the pull request draft and re-slice the increment
  or refine the ticket plan before ready review.
- Finish only when the complete slice is verified on `main` and tracking and
  branch cleanup are reconciled, or a genuine external prerequisite or
  maintainer decision blocks safe progress. A partially merged transaction is
  recoverable `In progress` work, not a rollback trigger or a completed slice.
- The trusted controller, validators, and evidence remain maintainer-owned and
  ignored under `.codex/**`. The controller retains the receipt-bound
  `CLIENT_ENFORCED_FREE_PLAN` validation mode identifier for evidence
  compatibility. On GitHub Pro, the active default-branch ruleset independently
  enforces pull requests and strict required checks; its observations do not
  become client-side merge authority. Every successful receipt still records
  `EXTERNAL_ACTOR_MUTATION_NOT_PREVENTED`: another qualified pull request or an
  administrator changing enforcement can move or race `main`, and GitHub's
  conditional merge locks the pull-request head but not the base SHA. Exact
  local, connector, check, review, exact repository/branch provenance,
  internally persisted dry-run, conditional-merge, exact closing-reference,
  and post-merge gates remain mandatory.
  Dry-run freshness ends at the trusted mutation-attempt instant, not terminal
  completion. Activation permits at most 99 constituents plus its tracker;
  non-activation permits at most 100 constituents.
  A trusted monotonic deadline starts before the dry-receipt wall timestamp and
  persistence, including both, and its guard follows the captured mutation
  timestamp as the only local operation between final
  preflight and merge. Ambiguous responses persist typed post-attempt evidence
  no earlier than the mutation attempt, require actual drift for connector
  failure, treat timing-invalid connector evidence—including invalid elapsed
  type, range, finiteness, or integer magnitude beyond the supported timestamp
  domain—as unavailable without a digest, reject raw signed JSON integer tokens
  longer than 4,300 digits at the bounded parser, and apply the same numeric
  checks before conversion in normal and recovery validation while accepting
  the exact maximum timestamp delta. Route authoritative GitHub responses,
  trusted evidence, and top-level run-record input through one bounded JSON
  parser that translates decode, integer-conversion, Unicode, and recursion
  failures into context-specific record errors while preserving size,
  canonical-byte, and digest checks. Enforce a deterministic maximum structural
  depth of 100 with a JSON-string-and-escape-aware lexical scan, and capture
  authoritative REST and GraphQL stdout and stderr as bytes so invalid UTF-8
  reaches that boundary without exposing raw stderr. Strictly decode every byte
  input as UTF-8 once and use that same string for depth scanning and JSON
  decoding, forbidding UTF-16/32 auto-detection. Byte-capture and canonical-
  strict-decode both branch-cleanup response streams; only decoded HTTP 404
  in the canonical single-line GitHub diagnostic with empty stdout proves
  absence. Require the message's `splitlines()` result to contain only that
  message and reject semantic line separators and noncanonical whitespace,
  control, or format characters; mixed, prefixed, multiline, repeated, or
  trailing diagnostics do not, while malformed encodings are record errors;
  restrict protocol
  variance to ambiguous connector failure, and remain recovery-only.
- After the initial product has stable executable seams, maintainers may deliver
  small bounded additions as ordinary `feature/` PRs. Cross-cutting, high-risk,
  or independently demonstrable capabilities continue to use slices.
- Follow [functional slice delivery](docs/development/slice-delivery.md) and the
  ordered [delivery-slice plan](docs/product/delivery-slices.md). Governance,
  urgent incident, and private security exceptions follow the narrow rules in
  the delivery workflow and do not redefine normal product delivery.

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

See [docs/development/quality-strategy.md](docs/development/quality-strategy.md)
and [docs/architecture/security.md](docs/architecture/security.md) for the full
quality and safety contracts.
