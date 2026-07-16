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

The public repository gate runs on every pull request and push to `main`. It
uses a pinned `uv` binary, rejects a stale committed lockfile, and then installs
that lockfile in a non-editable shape. GitHub Actions and direct dependencies
are pinned. Local private-corpus or credential-bearing
qualification remains outside public CI and publishes only the redacted,
aggregate exact-commit evidence allowed by its governing contract.

For an autonomously merged slice increment, passing tests are necessary but not
sufficient. Immediately before mutation, the delivery controller must bind the
named repository and pull request, expected base ref and commit, exact reviewed
head, required successful public checks, resolved review state, and required
local review and qualification evidence. A changed base or head invalidates the
evidence it can affect. The controller fails closed and must not use bypass,
administrative, force, or branch-protection override authority.

The validated `deliver-functional-slice` run record determines mandatory
local review coverage: slice acceptance and code quality in every tier, with
safety and publication challenge added for Tier 2. Callers supply the exact
envelopes but cannot select, omit, or extend that task set.

The review root is an explicit maintainer-controlled local trust boundary.
Validator paths are fixed beneath that root, and validator controls plus input
artifacts are hash-checked before and after use. Pull-request content cannot
select validator code. A malicious authenticated local operator is outside
this controller's threat model because that actor can already invoke the merge
credential directly.

Autonomous mode additionally requires server-side strict checks and reviews
with no administrator or named bypass allowance. After a merge it proves the
squash commit's exact qualified parent and waits boundedly for required checks
on that main commit. A post-mutation failure is recoverable structured state,
not a success or an automatic rollback.

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

- Frameworks, libraries, and developer tools may be introduced during ordinary
  delivery when they materially reduce current implementation effort,
  maintenance burden, or operational risk. A contributor does not need to
  prove that a standard-library implementation is impossible first.
- A dependency needed for the selected ticket or slice does not require a
  separate permission ticket or maintainer approval merely because it is new.
  Keep it inside that delivery boundary and satisfy the applicable
  architecture, licence, security, testing, and reproducibility requirements.
- Development-only dependencies may likewise be selected and added by the
  delivery that first needs them, including test, fixture-generation,
  code-generation, browser-qualification, build, analysis, and documentation
  tools. Classify and lock them as development dependencies, keep them out of
  runtime artifacts, and test or review the behavior the delivery relies on.
  Create separate focused work only when changing repository-wide developer
  infrastructure is itself the primary outcome.
- Use dependencies for current requirements rather than speculative future
  work. Dependency-only additions, upgrades, and removals remain focused
  changes outside functional slices.
- Prefer well-maintained libraries with clear licences and security history.
- Keep runtime dependencies smaller than development dependencies.
- Pin direct dependencies and commit a reproducible lockfile.
- Review meaningful transitive and licence changes whenever a dependency is
  introduced or updated.
- Test the behavior and failure handling that the product relies on; do not
  treat the dependency itself as proof of the product contract.
- Preserve least authority: a dependency must not silently gain network,
  credential, filesystem, private-data, or production-write access.
- Remove unused dependencies promptly.
- Do not adopt a framework merely to implement a small stable contract.
- Record an ADR or perform the governing security review when a framework
  establishes or materially changes a durable architecture, public contract,
  parser boundary, authentication or authorization design, or another trust
  boundary.
- Record SPDX identities and required notices for dependencies, assets,
  fixtures, generated material, copied components, plugins, and container
  contents.
- Reject material that is incompatible with `AGPL-3.0-or-later` or lacks
  sufficient authorship, provenance, and redistribution evidence.

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

- when the change is a new-functionality slice, it is demonstrable through the
  supported surface and meets every slice exit criterion;
- when the change is focused ticket work, its reproduction or bounded outcome
  satisfies that ticket without unrelated slice scope;
- behavior and failure semantics are documented;
- tests prove the intended contract;
- telemetry supports diagnosis without exposing private data;
- upgrade and rollback impact is understood;
- user-facing changes have release notes;
- no required work is hidden in an untracked local artifact.
