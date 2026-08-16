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
uv lock --check
uv run python scripts/run_test_tier.py fast
python3 scripts/qualify_exact_artifact.py \
  --source-sha "$(git rev-parse HEAD)" \
  --test-tier required
```

The qualification commands run the routine source and
[canonical exact-artifact procedure](docs/development/testing-strategy.md)
against a clean checkout. The integration suite exercises the installed command
end to end against disposable synthetic target, media, credential, and database
fixtures.
Issue #36 owns the future platform-neutral bootstrap and one-command
verification wrapper. The qualifier alone supplies source-bound installed-
artifact evidence and runs the representative installed tier against that exact
wheel. The testing strategy also documents the independent browser, explicit
slow, and one-command complete exact-head qualifications.

Do not install project tools globally or add unrecorded dependencies. Update
`pyproject.toml` and regenerate `uv.lock` together when the dependency graph
changes.

## Python formatting, linting and typing

The backend is formatted and linted by ruff and type-checked by mypy in strict
mode. Both tools are exact-pinned in the `dev` dependency group and configured
entirely in `pyproject.toml`; there is no separate tool configuration file.
From a clean checkout, these are the exact commands the `fast-source` gate job
runs, and all three must pass before a pull request is ready:

```text
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy src tests scripts
```

`uv run ruff format src tests scripts` rewrites the source and
`uv run ruff check --fix src tests scripts` applies the automatic lint fixes.
Keep formatting-only changes in their own commit so behaviour diffs stay
reviewable, the same rule the frontend follows under Prettier.

`ruff format` is the backend's line-width contract at `line-length = 88`. Four
sources are excluded from it, for the reason given below.

ruff selects pyflakes (`F`), pycodestyle (`E`, `W`), bugbear (`B`), and import
ordering (`I`). `E501` (line length) is not enforced on top of the formatter.
Every line still wider than 88 after formatting is content the formatter
deliberately did not touch -- DDL that `sqlite_master` stores verbatim, EPUB
and XML fixture documents, Python modules embedded as string literals, and
single-token dotted import paths -- so E501 would measure data rather than
formatting, and no threshold or exemption list makes it pass without making it
meaningless. `pyproject.toml` records the measurement. Enforcing it for real
means moving those literals out of Python source.

mypy runs in strict mode over `src/`, which is what ships with `py.typed`.
`tests/` and `scripts/` are checked too, but without strict's annotation
requirements and without `arg-type`, `attr-defined`, or `index`: a suite proves
behaviour with deliberately narrow fakes and indexes the JSON documents the
code under test returns, so those three classes measure fixtures rather than
defects. Every relaxation is an explicit `[[tool.mypy.overrides]]` block in
`pyproject.toml` with its reason beside it; do not add a silent one.

The analysed platform is pinned to `linux` so the commands above reproduce the
ubuntu gate byte for byte from any development host.

Suppress a finding only where the code is right and the tool is wrong, and
always inline, named, and justified:

```text
value = mapping[key]  # noqa: B023 - the closure is consumed in this iteration
result = handler()  # type: ignore[arg-type]  # reason: <why this is sound>
```

mypy honours `# type: ignore` only as the first trailing comment on a line, so
when a line needs both, put the type suppression first; ruff finds its `noqa`
anywhere in the trailing comments. `warn_unused_ignores` is on, so a
suppression that stops suppressing anything fails the gate and must be deleted
rather than left behind.

Four sources are byte-frozen by the F20 rich-metadata qualification manifest
and appear in `[tool.ruff.lint.per-file-ignores]` and a mypy override, because
an inline suppression would itself change the bytes that
`docs/reference/f20-rich-metadata-qualification-candidate-v1.json` pins. Do not
edit those files to satisfy a linter; changing them requires re-qualification.

## Frontend bootstrap

The authenticated-browser source under `frontend/` uses Node 24.19.0 and npm
11.17.0. A clean checkout needs no manual generation step:

```text
cd frontend
npm ci --ignore-scripts
npm run typecheck
npm run lint
npm test
npm run build
```

`typecheck`, `lint`, `test`, and `build` each run `npm run generate` first, so
the gitignored DTOs in `src/generated/` are always current with
`docs/reference/review-openapi-v1.json`. Run `npm run generate` directly only
when you want the generated types without a check.

`frontend/src` is formatted by Prettier using the committed
`frontend/.prettierrc.json`. `npm run lint` fails on unformatted source and
`npm run format` rewrites it. Keep formatting-only changes in their own commit
so behaviour diffs stay reviewable. `src/styles.css` and the generated DTOs are
excluded in `frontend/.prettierignore`; `frontend/prototype/` is scratch space
excluded from lint.

## Pull requests

During the initial application build, new product functionality is delivered as
one functional slice outcome per tracker. Select a `Ready` tracker and use one
pull request or sequential, independently safe vertical increments. Start each
increment from current `main`, normally merge it before starting the next, and
reserve closure of the slice tracker for the activation increment that proves
the complete documented outcome.

Fixes, security corrections, documentation, tooling, dependency updates,
maintenance, and standalone tickets use focused purpose branches and
ticket-level pull requests instead. Do not create a slice tracker merely to
rename one of those changes.

A new-functionality slice increment pull request must:

- when ready, fully satisfy and natively close at least one ticket: a
  non-activation increment closes at least one constituent, while the
  activation increment may use the slice tracker and alone closes it;
- remain draft while its ticket boundary cannot support that closing reference,
  then re-slice the increment or refine the ticket plan before ready review;
- demonstrate its independently safe vertical step through a supported surface;
- explain the problem and intended behavior;
- identify user-visible, data, security, and operational impact;
- include tests for success, failure, retry, and idempotency where relevant;
- update documentation and the changelog when behavior changes;
- include an ADR for durable architectural decisions;
- avoid unrelated refactoring;
- pass formatting, linting, typing, unit, contract, integration, security,
  documentation, and container-build checks.

Reviewers should be able to understand each increment and its place in the
frozen slice outcome without reconstructing intent from horizontal fragments.
CI failures and review findings remain on that increment's branch and pull
request; after remediation, the new exact head repeats affected review and
qualification. A new pull request represents a distinct vertical outcome or a
required security or privacy separation. The complete workflow and permitted
exceptions are defined in
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
state. Before the first production release, breaking persisted-schema or public
API changes follow ADR-0013: accept only absent or exact-current state, reject
older state clearly, and require a clean rebuild where needed. Do not add
migration notes or a supported upgrade path unless a named compatibility decision
creates a post-release obligation. Releases follow semantic versioning once the
first public API is declared stable; then breaking changes require migration
notes and a supported upgrade path.

## Contribution licence

Mechanical Librarian is licensed under `AGPL-3.0-or-later`. By submitting a
contribution, you agree to license that contribution under the same terms and
confirm that you have the right to do so. The project does not currently
require a contributor licence agreement.

Preserve compatible third-party notices and identify generated, adapted, or
copied material in the pull request. Technical interoperability does not remove
the requirement to review the licence and provenance of dependencies, assets,
fixtures, plugins, container contents, or extracted components.
