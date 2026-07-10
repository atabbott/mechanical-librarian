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

## Pull requests

A pull request must:

- explain the problem and intended behavior;
- identify user-visible, data, security, and operational impact;
- include tests for success, failure, retry, and idempotency where relevant;
- update documentation and the changelog when behavior changes;
- include an ADR for durable architectural decisions;
- avoid unrelated refactoring;
- pass formatting, linting, typing, unit, contract, integration, security,
  documentation, and container-build checks.

Reviewers should be able to understand the change without reconstructing its
intent from implementation details.

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
