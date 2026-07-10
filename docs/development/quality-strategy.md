# Quality Strategy

## Quality definition

Quality means more than passing tests. A change is production-quality when it
is understandable, bounded, secure, observable, recoverable, compatible, and
supported by evidence proportionate to its risk.

## Required gates

Every pull request must pass:

1. Deterministic formatting.
2. Linting with warnings treated as failures.
3. Strict static typing for production packages.
4. Architecture dependency checks.
5. Unit and property-based tests.
6. Adapter contract tests.
7. Integration tests for changed boundaries.
8. Database migration tests when persistence changes.
9. Security and secret scanning.
10. Documentation formatting, links, spelling, and executable-example checks.
11. Container build and smoke tests for runtime changes.

Release candidates additionally require end-to-end workflow, upgrade,
backup/restore, and container isolation tests.

## Code standards

- Prefer small modules with one reason to change.
- Prefer typed immutable value objects at boundaries.
- Make invalid states difficult or impossible to construct.
- Use explicit result and failure types for expected operational failures.
- Preserve exception causes while redacting sensitive values.
- Bound network, file, parser, and subprocess operations with timeout and size
  limits.
- Avoid boolean parameters when a named policy or enum expresses intent.
- Avoid parsing human-readable console output between components.
- Use structured schemas for durable and inter-process communication.
- Keep functions deterministic unless their name and contract declare an
  external effect.

## Coverage policy

Coverage is a backstop, not a target substitute.

- Overall branch coverage must not fall below the repository threshold.
- Domain policy, validators, state transitions, migrations, and apply/rollback
  logic require near-complete branch coverage.
- New error-handling branches require explicit tests.
- Exclusions must be narrow, explained, and reviewed.
- Mutation testing should be introduced for policy and validation modules after
  their interfaces stabilize.

Exact numeric thresholds should be set with the first code scaffold and changed
only through an ADR or quality-policy pull request.

## Complexity and maintainability

- Complexity warnings fail CI at the agreed threshold.
- Large files and functions require refactoring or an explicit design reason.
- Circular imports are forbidden.
- Deprecated interfaces include removal milestones and compatibility tests.
- Suppression comments name the tool rule and explain why suppression is safer
  than correction.
- TODOs link to an issue and identify the blocking condition.

## Dependency policy

- Add dependencies only when they materially reduce risk or maintenance.
- Prefer well-maintained libraries with clear licences and security history.
- Keep runtime dependencies smaller than development dependencies.
- Pin direct dependencies and commit a reproducible lockfile.
- Review transitive and licence changes in dependency pull requests.
- Remove unused dependencies promptly.
- Do not adopt a framework merely to implement a small stable contract.

## Review depth by risk

### Low

Documentation, presentation, and isolated read-only transformations. Normal
review and CI gates apply.

### Medium

Provider calls, parsers, persistent schema, scheduling, authentication UX, and
container packaging. Require boundary and failure tests plus operational review.

### High

Credentials, AI isolation, backups, production writes, rollback, destructive
plans, authorization, migrations with data transformation, or update systems.
Require explicit threat analysis, ADR or governing contract, end-to-end tests,
and maintainer approval.

## Definition of done

A change is done only when:

- behavior and failure semantics are documented;
- tests prove the intended contract;
- telemetry supports diagnosis without exposing private data;
- upgrade and rollback impact is understood;
- user-facing changes have release notes;
- no required work is hidden in an untracked local artifact.
