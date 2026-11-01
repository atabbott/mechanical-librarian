# Contributing

Thank you for helping build Mechanical Librarian. Correctness, safety and
clarity come before feature count or speed.

Read [PRODUCT.md](PRODUCT.md) for the product boundary and the
[architecture overview](docs/architecture/overview.md) for the layering rule.
Open a discussion or design issue before you implement a new library-service
or AI adapter, a persisted-schema or public API change, a new write path,
embedded media-file updates, an authentication change, a new background
execution model, or a dependency that changes the container or threat model.

## Principles

- Prefer small, complete changes with clear contracts.
- Keep domain behaviour independent of frameworks and vendors.
- Make failure states explicit and testable, and keep work idempotent and safe
  to retry.
- Never treat a network or provider failure as negative metadata evidence.
- Never let AI confidence alone justify a production write.
- Record provenance for every derived value and decision.

## Repository layout

```text
src/mechanical_librarian/
├── domain/          # values and rules; no I/O
├── application/     # orchestration and the ports adapters implement
├── adapters/        # Audiobookshelf, providers, evidence extractors, reviewers, SQLite
├── infrastructure/  # configuration, first boot, security
├── interfaces/      # CLI and the authenticated web workspace
└── bootstrap/       # composition and entry points
frontend/            # the React owner workspace
workbench/           # source-only contributor scenarios, never installed
tests/               # unit, integration, architecture, installed, browser and slow tiers
```

## Your first pull request

You need Python 3.13 or 3.14 and [uv](https://docs.astral.sh/uv/); Node and npm
only for frontend work. Run the formatting, lint, type and fast test commands
below, open a pull request that closes an issue, and CI does the rest. The
slice and tracker rules in the process documents govern maintainer delivery;
for an outside contribution, a clear issue and a focused pull request are
enough.

Some issues quote a "retained snapshot": a private measurement run over the
maintainer's own library. Issues and pull requests quote such runs only as
aggregates, and the snapshots themselves never enter the repository.

## Backend setup

Do not install project tools globally.

```text
uv lock --check
uv run python scripts/run_test_tier.py fast
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy src tests scripts
```

`uv run ruff format src tests scripts` and
`uv run ruff check --fix src tests scripts` apply fixes. ruff and mypy (strict)
are configured only in `pyproject.toml`, which records the reason beside every
relaxation; do not add a silent one. Suppress a finding only where the code is
right and the tool is wrong, inline and with a reason:

```text
result = handler()  # type: ignore[arg-type]  # reason: <why this is sound>
```

The fast tier compares two committed references with the code and fails when
either drifts. After a review API change, regenerate the OpenAPI document from
the repository root, then rerun the frontend checks, whose API types come from
it. After a configuration model change, regenerate the configuration schema:

```text
uv run python scripts/generate_review_openapi.py
uv run python scripts/generate_configuration_schema.py
```

They rewrite `docs/reference/review-openapi-v1.json` and
`docs/reference/config-v9.schema.json`; commit the result with the change.

When the dependency graph changes, update `pyproject.toml` and `uv.lock`
together, and add the dependency's grant to
[dependencies](docs/development/dependencies.md). The
[testing strategy](docs/development/testing-strategy.md) covers the installed,
browser, slow and complete qualification tiers.

## Frontend setup

The workspace in `frontend/` uses the exact Node and npm versions in
`frontend/package.json`:

```text
cd frontend
npm install --global --ignore-scripts "npm@$(node -p 'require("./package.json").engines.npm')"
npm ci --ignore-scripts
npm run typecheck
npm run lint
npm test
npm run build
```

Each of those commands regenerates the API types from
`docs/reference/review-openapi-v1.json` first. `npm run lint` also checks
Prettier formatting and `npm run format` fixes it. Keep formatting-only changes
in their own commit, for Python and TypeScript alike.

## Pull requests

- Link the issue the pull request closes (`Closes #N`).
  [Work management](docs/development/work-management.md) covers issues and the
  narrow `tracking-exempt` exception.
- New functionality crossing several layers is delivered as a functional
  slice; everything else is one focused pull request. See
  [delivery and merging](docs/development/slice-delivery.md).
- Explain the problem and the resulting behaviour, and its user, data,
  security and operational impact.
- Test success, failure, retry and idempotency where they apply.
- Update the documentation the change makes stale, in the same pull request,
  and edit it in place rather than appending history. Add a changelog entry for
  user-visible behaviour and an ADR for a durable architectural decision. See
  [documentation standards](docs/development/documentation-standards.md).
- Before 1.0.0, a persisted-schema change declares `clean rebuild required` or
  ships one exact-predecessor adoption, as
  [ADR-0013](docs/architecture/decisions/0013-pre-release-clean-schema.md)
  requires.
- Keep unrelated refactoring out, and never weaken a test, type, linter or
  safety gate to make a change pass.
- Pass every check below.

Use the defect form for a pre-release failure to meet an accepted contract and
the bug form for something that worked in a supported build and broke. Report
suspected vulnerabilities privately, as [SECURITY.md](SECURITY.md) describes.

## What merging proves

`.github/workflows/public-gate.yml` is the only workflow. It runs every `gate`
check below on each pull-request head and again on the merged `main` commit.
`maintainer-manual` checks are run by hand or in review. The third column is
exact text from the workflow for a gate check, and text deliberately absent
from it for a manual one, so the table can be checked against the workflow.

| Check | Enforced by | Command or job |
| --- | --- | --- |
| Lockfile currency | gate | `uv lock --check`, in all four qualification jobs |
| Python formatting | gate | `uv run ruff format --check src tests scripts` in `fast-source` |
| Python lint | gate | `uv run ruff check src tests scripts` in `fast-source` |
| Python typing | gate | `uv run mypy src tests scripts` in `fast-source` |
| Documentation reachability, relative links, the ADR index, issue-free headings and the word budget | gate | `scripts/verify_documentation_integrity.py` in `fast-source` |
| Every direct dependency carries an authority grant, and no grant outlives its dependency | gate | `scripts/verify_dependency_authority.py` in `fast-source` |
| Frontend typing, lint, formatting and Vitest suite | gate | `npm run typecheck`, `npm run lint`, `npm test`; `npm run lint` is what fails on unformatted source |
| Frontend licence inventory and dependency advisories | gate | `npm run licenses`, `npm audit --audit-level=low` |
| The exact Node and npm toolchain, in every job that builds the frontend | gate | `test "$(node --version)"` and `test "$(npm --version)"` |
| DTO generation is deterministic | gate | `cmp "$RUNNER_TEMP/review-api-a.d.ts"` in `fast-source` |
| The asset build is reproducible | gate | `diff -r "$RUNNER_TEMP/frontend-build-a"` in `fast-source` |
| Generating everything leaves the tree clean | gate | `git diff --exit-code` in `fast-source` |
| Unit, integration and architecture tests | gate | `scripts/run_test_tier.py fast` in `fast-source` and its three shard jobs |
| Committed OpenAPI and configuration schemas still match the code | gate | the fast tier |
| Workflow permissions and the advisory acceptance register | gate | the fast tier |
| Wheel identity, archive and installed record integrity, payload equality, installed console | gate | `scripts/qualify_exact_artifact.py` in `exact-installed` |
| Required installed test tier, run by the isolated wheel's interpreter | gate | `--test-tier required` in `exact-installed` |
| The Chromium payload is well formed, its digest is recorded, and it starts on the runner's own libraries | gate | `scripts/verify_playwright_browser.py` in `protected-browser` |
| Browser tier against that recorded payload | gate | `scripts/run_test_tier.py browser` in `protected-browser` |
| The shipped container image builds from the exact revision, restoring unchanged layers from the Actions cache, which only a push to `main` writes | gate | `docker/build-push-action` with `tags: mechanical-librarian:gate`, `cache-from: type=gha`, and `cache-to` set to `type=gha,mode=max` only when `github.event_name == 'push'`, in `container-image` |
| The probe tier runs the image's own pinned binary, never the runner's | gate | `docker create mechanical-librarian:gate`, `docker cp`, `docker rm`, `sudo install -m 0755 "$RUNNER_TEMP/ffprobe" /usr/local/bin/ffprobe`, the `FFMPEG_VERSION` readback with `ffprobe -version`, and the removal of `/usr/bin/ffprobe`, in `container-image` |
| Real-probe execution over generated media | gate | `scripts/run_test_tier.py probe` in `container-image` |
| Plan and VM-step read-budget assertions run on the SQLite the image ships, never the runner's | gate | `mkdir "$RUNNER_TEMP/shipped-sqlite"`, `docker cp -L`, the `LD_LIBRARY_PATH` prefix, the `libsqlite3-0` version readback with `sqlite3.sqlite_version`, and `scripts/run_test_tier.py shipped-sqlite`, in `container-image` |
| Advisories against the locked dependency closure | gate | `--lockfile=uv.lock` in `dependency-audit` |
| Explicit slow tier: scale, endurance, resource ceilings | maintainer-manual | `qualify_exact_artifact.py --test-tier slow` |
| One-command complete exact-head qualification | maintainer-manual | `run_test_tier.py complete` |
| The built container image's lifecycle | maintainer-manual | `scripts/qualify_container_lifecycle.py` |
| Audiobookshelf field-effect qualification | maintainer-manual | `scripts/qualify_audiobookshelf_field_effect.py` |
| Recoverability envelope | maintainer-manual | `scripts/recoverability_evaluator.py` |
| Closing reference to an open issue, and the `tracking-exempt` exception | maintainer-manual | the trusted delivery controller, at preflight and again immediately before merge |
| Documentation, changelog, ADR and security-review requirements | maintainer-manual | review |

There is no coverage threshold. The container job builds the image only to
extract the `ffprobe` and SQLite it ships; it never runs or pushes the image.

## Fixtures and privacy

Use synthetic fixtures. Never commit real library databases, copyrighted book
text or cover images, captured provider responses, personal paths or domains,
tokens, or unredacted reports. A sanitised production-derived fixture is
acceptable only when its owner agrees, every identifying value is replaced,
its media is newly generated or redistributable, and a reviewer confirms it
cannot reasonably be re-identified.

## Licence

Mechanical Librarian is licensed under `AGPL-3.0-or-later`. By contributing,
you license your contribution under the same terms and confirm you have the
right to. There is no contributor licence agreement. Preserve third-party
notices, and say in the pull request when material is generated, adapted or
copied.
