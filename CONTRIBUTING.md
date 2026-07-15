# Contributing

Thank you for helping build Mechanical Librarian. Correctness, safety, clarity, and
maintainability take priority over feature count or delivery speed.

## Before contributing

Read:

1. [Product scope](docs/product/scope.md)
2. [Architecture overview](docs/architecture/overview.md)
3. [Security model](docs/architecture/security.md)
4. [Quality strategy](docs/development/quality-strategy.md)
5. [Documentation standards](docs/development/documentation-standards.md)
6. [Work management](docs/development/work-management.md)
7. [Functional slice delivery](docs/development/slice-delivery.md)
8. [Delivery control plane](docs/development/workflow-control-plane.md)

Use a discussion or design issue before implementing:

- a new library-service or AI adapter;
- a persisted schema or public API change;
- a new production write path;
- embedded media-file updates;
- authentication or authorization changes;
- a new background execution model;
- a dependency that materially changes the container or threat model.

## Development principles

- Prefer small, complete changes with clear contracts.
- Keep domain behavior independent of frameworks and vendors.
- Make failure states explicit and testable.
- Preserve idempotency and retry safety.
- Never make network or provider failure look like negative metadata evidence.
- Never make AI confidence alone sufficient for a production write.
- Add provenance to every derived value and decision.

## Local package bootstrap

The initial scaffold uses Python 3.13 or 3.14 and
[uv](https://docs.astral.sh/uv/) to create an isolated environment from the
committed lockfile:

```text
uv sync --frozen --no-editable
uv run --frozen --no-editable python -m unittest discover -s tests -p 'test_*.py'
uv build
uv run --frozen --no-editable mechanical-librarian state-check --database ./state.sqlite
```

The final command exercises the current supported operator diagnostic and
creates local SQLite state; remove that generated state before committing.
Issue #36 owns the future platform-neutral bootstrap and one-command
verification wrapper. Re-run the non-editable sync after changing package
source so tests exercise the same installed shape as a built wheel.

Do not install project tools globally or add unrecorded dependencies. Update
`pyproject.toml` and regenerate `uv.lock` together when the dependency graph
changes.

## Delivery-control qualification

The tracked `.codex` tree contains the canonical delivery workflows, profiles,
and deterministic control tests. Changes confined to that workflow-control
tree do not require a tracking ticket, but still require the qualification
commands in [the delivery-control guide](docs/development/workflow-control-plane.md).
Normal tracking rules still apply when a change also affects product, release,
dependency, security, architecture, schema, or CI behavior. Private run
records, local resource paths, and session artifacts remain untracked.

## Pull requests

During the initial application build, new product functionality is delivered as
one functional slice per branch and ready pull request. Select a `Ready` slice
tracker, branch from current `main` as `slice/<slice-id>-<short-slug>`, and keep
the cross-layer work together until the documented outcome is executable.

Fixes, security corrections, documentation, tooling, dependency updates,
maintenance, and standalone tickets use focused purpose branches and
ticket-level pull requests instead. Do not create a slice tracker merely to
rename one of those changes.

A new-functionality slice pull request must:

- close the slice tracker and only fully completed constituent tickets;
- demonstrate the observable outcome through a supported surface;
- explain the problem and intended behavior;
- identify user-visible, data, security, and operational impact;
- include tests for success, failure, retry, and idempotency where relevant;
- update documentation and the changelog when behavior changes;
- include an ADR for durable architectural decisions;
- avoid unrelated refactoring;
- pass formatting, linting, typing, unit, contract, integration, security,
  documentation, and container-build checks.

Reviewers should be able to understand the change without reconstructing its
intent from implementation details or from a chain of partial pull requests.
The complete workflow and permitted exceptions are defined in
[functional slice delivery](docs/development/slice-delivery.md).

## Defects and bugs

Use the defect form for an internally discovered or pre-release failure to meet
an accepted contract, closed-ticket criterion, or qualification gate. Use the
bug form when behavior previously treated as delivered and working fails in a
supported build or user/operator workflow.

Triage records the authoritative distinction in the Project `Work kind` field;
do not add type labels. Both kinds use a focused `fix/<issue>-<short-slug>`
branch and one ticket-level pull request. Include a deterministic reproduction,
expected and actual behavior, affected build, impact, root cause, and regression
evidence. Suspected undisclosed vulnerabilities use a private security advisory
instead of either public form.

## Fixtures and privacy

Use synthetic fixtures by default. A sanitized production-derived fixture is
acceptable only when:

- the original content owner authorizes its use;
- titles, people, identifiers, paths, domains, and unique descriptions are
  replaced;
- media and cover bytes are newly generated or explicitly redistributable;
- the fixture preserves only the structural behavior being tested;
- a reviewer confirms that re-identification is not reasonably possible.

## Commit and release discipline

Use focused commits with descriptive messages. Do not commit generated runtime
state. Releases follow semantic versioning once the first public API is declared
stable. Breaking changes require migration notes and a supported upgrade path.

## Contribution licence

Mechanical Librarian is licensed under `AGPL-3.0-or-later`. By submitting a
contribution, you agree to license that contribution under the same terms and
confirm that you have the right to do so. The project does not currently
require a contributor licence agreement.

Preserve compatible third-party notices and identify generated, adapted, or
copied material in the pull request. Technical interoperability does not remove
the requirement to review the licence and provenance of dependencies, assets,
fixtures, plugins, container contents, or extracted components.
