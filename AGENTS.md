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

## Change requirements

Every change must include the smallest appropriate combination of:

- tests proving behavior and failure handling;
- documentation updates;
- a migration when persisted schemas change;
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
- Fixes, audit corrections, security work, dependencies, documentation,
  tooling, maintenance, and genuinely standalone tickets are not slices. Use a
  focused purpose branch such as `fix/`, `docs/`, `chore/`, or `feature/` and
  one ticket-level pull request.
- Before starting new slice functionality, identify a `Ready` slice tracker
  with an observable executable outcome and complete exit criteria. Create one
  branch from current `main` named `slice/<slice-id>-<short-slug>` and keep the
  contracts, adapters, persistence, interfaces, tests, migrations, and
  documentation required for that outcome together.
- Raise the slice pull request when the end-to-end outcome is demonstrable and
  all slice exit criteria pass. It closes the slice tracker and only those
  constituent tickets whose full acceptance criteria are satisfied.
- Use focused commits within a slice branch. Do not turn those commits into
  horizontal PRs or hide partial ticket acceptance in follow-up work.
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
