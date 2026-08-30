# Contributing

Thank you for helping build Mechanical Librarian. Correctness, safety, clarity, and
maintainability take priority over feature count or delivery speed.

## Before contributing

Read [PRODUCT.md](PRODUCT.md) for the product boundary,
[architecture overview](docs/architecture/overview.md) for the layering rule,
and [testing and quality strategy](docs/development/testing-strategy.md) for
what proving a change costs. Then read this file.

Consult, when the work reaches them:
[security model](docs/architecture/security.md),
[product scope](docs/product/scope.md),
[documentation standards](docs/development/documentation-standards.md),
[work management](docs/development/work-management.md),
[functional slice delivery](docs/development/slice-delivery.md), and
[dependencies](docs/development/dependencies.md).

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

`pyproject.toml` is the authority for the line-width contract, the selected
lint rules, the analysed platform, every disabled code, and every per-file and
per-module relaxation. Each one is recorded there with the reason it exists
beside it; read that reason before changing it, and do not add a silent
relaxation. Four sources are byte-frozen by the F20 rich-metadata qualification
manifest and cannot carry an inline suppression at all -- the suppression would
itself change the bytes the manifest pins -- so they are exempted in
configuration instead. Do not edit those files to satisfy a linter; changing
them requires re-qualification.

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

## What merging proves

`.github/workflows/public-gate.yml` is the only workflow in this repository.
Every check named below is either enforced there -- on each pull-request head
and again on the merged `main` commit -- or named as maintainer-manual. Nothing
else is checked automatically, whatever another document may imply.

The third column is exact text from that workflow for an enforced check, and
text deliberately absent from it for a maintainer-manual one, so the two halves
of this table can be checked against the workflow rather than trusted.

| Check | Enforced by | Command or job |
| --- | --- | --- |
| Lockfile currency | gate | `uv lock --check`, in all four qualification jobs |
| Python formatting | gate | `uv run ruff format --check src tests scripts` in `fast-source` |
| Python lint | gate | `uv run ruff check src tests scripts` in `fast-source` |
| Python typing | gate | `uv run mypy src tests scripts` in `fast-source` |
| Documentation reachability, relative links and the ADR index | gate | `scripts/verify_documentation_integrity.py` in `fast-source` |
| Every direct dependency carries an authority grant, and no grant outlives its dependency | gate | `scripts/verify_dependency_authority.py` in `fast-source` |
| Frontend typing, lint, formatting and Vitest suite | gate | `npm run typecheck`, `npm run lint`, `npm test`; `npm run lint` is what fails on unformatted source |
| Frontend licence inventory and dependency advisories | gate | `npm run licenses`, `npm audit --audit-level=low` |
| The exact Node and npm toolchain, in every job that builds the frontend | gate | `test "$(node --version)"` and `test "$(npm --version)"` |
| DTO generation is deterministic | gate | `cmp "$RUNNER_TEMP/review-api-a.d.ts"` in `fast-source` |
| The asset build is reproducible | gate | `diff -r "$RUNNER_TEMP/frontend-build-a"` in `fast-source` |
| Generating everything leaves the tree clean | gate | `git diff --exit-code` in `fast-source` |
| Unit, integration and architecture tests | gate | `scripts/run_test_tier.py fast` in `fast-source` |
| Committed OpenAPI and configuration schemas still match the code | gate | the fast tier |
| Workflow permissions and the advisory acceptance register | gate | the fast tier |
| Wheel identity, archive and installed record integrity, payload equality, installed console | gate | `scripts/qualify_exact_artifact.py` in `exact-installed` |
| Required installed test tier, run by the isolated wheel's interpreter | gate | `--test-tier required` in `exact-installed` |
| The Chromium payload is well formed, its digest is recorded, and it starts on the runner's own libraries | gate | `scripts/verify_playwright_browser.py` in `protected-browser` |
| Browser tier against that recorded payload | gate | `scripts/run_test_tier.py browser` in `protected-browser` |
| The shipped container image builds from the exact revision, restoring unchanged layers from the Actions cache | gate | `docker/build-push-action` with `tags: mechanical-librarian:gate` and `cache-to: type=gha,mode=max` in `container-image` |
| The probe tier runs the image's own pinned binary, never the runner's | gate | `docker create mechanical-librarian:gate`, `docker cp`, `docker rm`, `sudo install -m 0755 "$RUNNER_TEMP/ffprobe" /usr/local/bin/ffprobe`, the `FFMPEG_VERSION` readback with `ffprobe -version`, and the removal of `/usr/bin/ffprobe`, in `container-image` |
| Real-probe execution over generated media | gate | `scripts/run_test_tier.py probe` in `container-image` |
| Advisories against the locked dependency closure | gate | `--lockfile=uv.lock` in `dependency-audit` |
| Explicit slow tier: scale, endurance, resource ceilings | maintainer-manual | `qualify_exact_artifact.py --test-tier slow` |
| One-command complete exact-head qualification | maintainer-manual | `run_test_tier.py complete` |
| The built container image's lifecycle | maintainer-manual | `scripts/qualify_container_lifecycle.py` |
| Audiobookshelf field-effect qualification | maintainer-manual | `scripts/qualify_audiobookshelf_field_effect.py` |
| Recoverability envelope | maintainer-manual | `scripts/recoverability_evaluator.py` |
| Closing reference to an open issue, and the `tracking-exempt` exception | maintainer-manual | the trusted delivery controller, at preflight and again immediately before merge |
| Documentation, changelog, ADR and security-review requirements | maintainer-manual | review |

Four of the five maintainer-manual qualifiers have their own logic unit-tested
in the fast tier, so for those, what is manual is running the qualification.
`scripts/qualify_container_lifecycle.py` is the exception: nothing under
`tests/` references it, so neither its logic nor its execution is checked by
merging. The `container-image` job builds the image and never runs, loads, or
pushes it, so nothing about the running image is proven by merging either.

There is no coverage threshold, and no separate contract, security, or
documentation test tier. Security and documentation behaviour is proven by
focused tests inside the cohorts above, not by a cohort of its own.

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
- pass every check [the gate enforces](#what-merging-proves).

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
API changes follow ADR-0013: ordinary startup accepts only absent or
exact-current state and rejects older state clearly. Each persisted-schema
change declares `clean rebuild required` or delivers one code-defined,
exact-predecessor, offline adoption plan for reconstructible derived objects in
the same issue and pull request. This is not a supported migration path;
migration notes require a named post-release compatibility obligation. Releases
follow semantic versioning once the first public API is declared stable; then
breaking changes require migration notes and a supported upgrade path.

## Contribution licence

Mechanical Librarian is licensed under `AGPL-3.0-or-later`. By submitting a
contribution, you agree to license that contribution under the same terms and
confirm that you have the right to do so. The project does not currently
require a contributor licence agreement.

Preserve compatible third-party notices and identify generated, adapted, or
copied material in the pull request. Technical interoperability does not remove
the requirement to review the licence and provenance of dependencies, assets,
fixtures, plugins, container contents, or extracted components.
