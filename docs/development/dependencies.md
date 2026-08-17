# Dependencies

Authorities: `pyproject.toml` and `frontend/package.json` for what is a direct
dependency, `uv.lock` and `frontend/package-lock.json` for every installed
version and integrity hash, `.github/renovate.json` for what automation may
propose, and [what merging proves](../../CONTRIBUTING.md#what-merging-proves)
for which of these checks the gate runs.

This document states the criteria a dependency must satisfy, the authority each
direct dependency is granted, and the trust boundary of the automation that
proposes updates. It records no version number: a version repeated in prose is
a copy of the lockfile that goes stale, and every earlier copy in this corpus
did.

## Reproducibility boundary

The manifests pin direct dependencies exactly. The lockfiles retain the exact
transitive versions and integrity data installed by development, CI, container
builds, and production. Update the manifest and regenerate the lockfile
together.

Build backends, language and package-manager toolchains, container image
digests, GitHub Action SHAs, and Playwright remain exact pins because the
application lockfiles do not govern them. Exact direct pins also stop lockfile
maintenance from quietly moving a direct dependency through a differently
classified update.

CI uses `uv lock --check`, locked or frozen uv operations, and `npm ci`; no
uncommitted resolution ever becomes current. The PEP 621
`project.requires-python` field is a supported interpreter compatibility range
rather than a pin, so automation does not manage it.

## Review criteria

A dependency change is reviewed against the resolved lockfile graph, read from
installed package metadata and the bundled licence files, on every update.

- **Least authority.** The dependency graph receives no network client,
  filesystem handle, service or reviewer credential, database connection, media
  handle, or mutation port beyond the grants named below. A package that needs
  more than its stated grant is a tracked design decision, not an update.
- **Licence compatibility.** Every declared licence must be compatible with
  distribution under `AGPL-3.0-or-later`, and bundled licence texts must remain
  present in installed distributions. Release SBOM and notice generation remain
  governed by the release workflow.
- **Closed closure.** An addition must not quietly enlarge the closure. No
  optional extra is installed, so no multipart parser, template engine, CLI,
  settings loader, cache, session service, identity provider, second HTTP
  client, or second process enters the graph without its own decision.
- **Development-only stays development-only.** A development dependency is
  locked in a dependency group and is absent from `[project.dependencies]`, the
  shipped wheel's and sdist's contents and metadata, the container image, and
  production imports.

## Granted runtime authority

- **Pydantic** validates in-memory configuration transport data only. Pydantic
  Settings is not installed and no environment-variable behaviour is enabled.
  Application values are projected into frozen dataclasses and domain types
  before orchestration; domain code does not import Pydantic.
- **HTTPX and HTTPcore** carry bounded target-service status and authorization
  HTTP. The client disables ambient environment and proxy trust and redirects,
  sends no cookies, retains default TLS verification, sets every timeout phase
  and pool bound, and streams into a hard response cap. Composition supplies
  only a target-service scoped credential-bearing client. Neither receives
  persistence, media, provider, reviewer, or mutation authority. The bounded
  provider retry policy imports the pinned HTTPcore exception type only to
  recognise HTTPX's exact wrapped remote-disconnect cause without parsing
  exception text; that classification dependency grants HTTPcore nothing else.
- **FastAPI and Starlette** provide typed `/api/v1` routing, deterministic
  OpenAPI, and ASGI request, response, cookie and middleware primitives, and
  receive only already composed application services and bounded DTOs.
  `interfaces/web/app.py` imports `starlette.types` directly, so Starlette is
  pinned in `pyproject.toml` beside FastAPI rather than floating under
  FastAPI's open range. FastAPI's `Annotated` documentation metadata is the
  only authority its own transitive `annotated-doc` participates in.
- **Uvicorn** runs one HTTP process with proxy and access logging disabled by
  composition. It receives no target or provider credential and cannot trust
  forwarded headers implicitly.
- **Argon2-cffi** verifies one owner Argon2id PHC hash. It receives one
  submitted password and one descriptor-bound PHC string; neither is logged,
  returned, or stored in the application database.

## Granted development-only authority

No development dependency is imported by the runtime package or included as
application authority. None receives a target, provider, or reviewer
credential, a production path, private corpus, database, persistence, media,
apply-port, subprocess, or publication authority. Tests use synthetic
credentials, hostile strings, disposable owner-only databases, loopback HTTPS,
and no production service; property cases use repository-authored synthetic
structures.

- **Playwright** drives disposable same-origin browser qualification only. A
  Playwright update requires explicit approval and must update the browser
  qualification evidence. The pinned release binds one Chromium revision, which
  the public gate downloads before the exact installed-artifact suite;
  `scripts/verify_playwright_browser.py` records that payload's tree digest and
  rejects an unsupported platform, an unreadable layout, and an absent, empty,
  or symlinked payload. Browser binaries are development cache content, not
  application packages or published artifacts; the browser's own release
  inventory is a qualification input and a future release-SBOM concern, not
  runtime content.
- **HTTPX2** is the in-process ASGI transport for `TestClient` only, and is
  pinned in the `dev` group rather than in `[project.dependencies]`.
  `starlette.testclient` prefers `httpx2` and emits a
  `StarletteDeprecationWarning` when it falls back to `httpx`, and
  `scripts/qualify_exact_artifact.py` fails its installed-workspace probe on
  any output at all, so that warning would fail the `exact-installed` gate
  rather than merely annotate it. It does not replace the runtime `httpx` the
  Audiobookshelf, Google Books, Open Library, and Hardcover adapters call: the
  two are separate distributions with separate import names and separate locked
  closures, only `httpx` is a runtime dependency, and no adapter's client
  construction, least-authority `Auth` flow, or wrapped-disconnect
  classification moves. `httpx2-jsfetch` appears in `uv.lock` gated on
  `sys_platform == 'emscripten'` and installs on no supported platform.
- **Hypothesis** generates bounded in-memory parser and configuration test
  cases only.

## Frontend build graph

Node and its bundled npm are required exactly and neither enters the runtime
image; Node is MIT with its incorporated third-party notices, npm is
Artistic-2.0. The lockfile binds every transitive integrity hash, `npm ci`
disables package lifecycle scripts, generated DTOs consume only the committed
OpenAPI document, and the build receives no credential, private data,
network-runtime, database, media, target, provider, or effect authority.

`npm run licenses` enforces the reviewed declaration allowlist: MIT, MIT-0,
ISC, Apache-2.0, BSD-2-Clause, BSD-3-Clause, BlueOak-1.0.0, MPL-2.0,
Python-2.0, Unlicense, CC0-1.0, CC-BY-4.0, and `(MIT OR CC0-1.0)`. The gate
fails if a locked entry lacks licence metadata or introduces another
declaration. All reviewed declarations are compatible with this build, test,
and browser-distribution use under `AGPL-3.0-or-later`. `scripts/check-licenses.mjs`
is the executable form of this list.

## Standard-library boundary

The application also depends on components it does not lock, recorded here for
dependency inventory, notices, and the release SBOM:

| Component | Supported boundary | SPDX licence | Product authority |
| --- | --- | --- | --- |
| CPython | the `requires-python` range in `pyproject.toml` | Python-2.0 | standard runtime and bounded local parsing/process facilities |
| Expat through `xml.parsers.expat` | `pyexpat.EXPAT_VERSION >= 2.7.2`, checked before input is opened | MIT | event-driven XML tokenization only |
| zlib through `zipfile` | the incorporated or system version shipped with the running CPython | Zlib | stored/Deflate stream decode only |

The adapter boundary is the replacement seam if a targeted CPython patch
release no longer satisfies the frozen contract. The standard-library choice
grants no network, credential, database, reviewer, persistence, media write,
shell, or product-file authority, and it is not a kernel sandbox. Bounded
deterministic XHTML and HTML recovery uses `html.parser.HTMLParser` from that
same library; it receives only already selected local member bytes and has no
browser, CSS, script, URL or base resolution, network, filesystem extraction,
source rewrite, or retained-tree authority. Parser, recovery, projection,
selection, normalizer, vocabulary, limits, extractor, and report versions are
all compatibility identity. The interpreter, Expat, and zlib combinations
qualification covers are recorded by the qualification evidence, not here;
future published combinations require their own exact patch and container
evidence.

## Automated updates

Mechanical Librarian uses the Mend-hosted Renovate GitHub App to discover,
group, and prepare dependency updates. `.github/renovate.json` bounds that
preparation authority. Renovate has no direct-write or ruleset-bypass
authority. Do not duplicate version or security update pull requests with
Dependabot.

Renovate checks in one weekly window, waits seven days after an ordinary
release, limits concurrent branches and pull requests to five, creates at most
two pull requests per hour, and groups compatible updates by ecosystem. GitHub
vulnerability alerts skip the ordinary schedule and use the lowest fixed
version, but retain the same merge policy.

Renovate creates a pull request as soon as it creates an eligible branch,
because the exact-head qualification workflow runs on pull requests, not on
unreviewed branch pushes. The pull request is what starts the checks; creating
it never authorizes a merge.

Renovate prepares a pull request and requests GitHub-native squash auto-merge
for:

- ordinary Python and npm patch updates, including Playwright patches;
- stable Python and npm library minor updates; and
- scheduled lockfile-only maintenance refreshes.

It prepares a pull request without requesting auto-merge for:

- pre-1.0 minor updates;
- Docker images and GitHub Actions;
- Python, Node, npm, and uv runtimes or toolchains;
- the current frontend compiler, bundler, linter, generator, test runner, test
  environment, and build plugins;
- Python build backends; and
- Playwright minor updates.

Major updates and dependency replacements require Dependency Dashboard approval
before Renovate prepares their pull requests, and never auto-merge. Newly
introduced dependencies, licensing changes, and changes to network, credential,
persistence, parser, or production authority remain tracked maintainer work.

The default branch requires a pull request, a head current with `main`, and the
required checks the gate enforces. Renovate receives no bypass. GitHub holds an
eligible pull request until those rules pass, then performs the configured
squash merge without depending on a maintainer workstation. A failing, pending,
stale, conflicted, or manually edited pull request cannot auto-merge. Close an
unsuitable update or disable it in the repository configuration with a
package-specific rationale; never weaken a test or safety gate to admit it.
Non-auto-merge Renovate pull requests use connector review and the
maintainer-owned exact-head controller.

Only pull requests whose GitHub author is `renovate[bot]` are exempt from the
normal closing-issue requirement. Titles, labels, branch names, and commit
authors do not confer that exception. Human-authored dependency changes remain
tracked focused work.

To pause all automated updates, set top-level `enabled` to `false` in the
Renovate configuration through an ordinary tracked pull request. For urgent
containment, a repository administrator may suspend or uninstall the GitHub
App, then record and reconcile that external action. Existing open Renovate
pull requests can be closed without merging. Reverting an already merged
dependency update follows the normal tracked defect workflow and restores a
previously qualified lockfile resolution.

## Automation security review

The Renovate App is an external repository-maintenance actor. It reads
dependency manifests, lockfiles, Dockerfiles, GitHub workflows, release
metadata, and GitHub vulnerability alerts, and may create branches, commits,
issues, and pull requests. For the low-risk classes named above it may request
GitHub-native pull-request auto-merge. It cannot write directly to `main` or
bypass the default-branch ruleset.

The App receives no Mechanical Librarian application credential, owner
password, target-service credential, reviewer credential, production path,
database, media, backup, provider input, deployment secret, package-registry
secret, or production write authority. All current dependencies are public, so
no Renovate host rule or Mend-managed secret is required.

Compromise or misclassification by the hosted service could propose untrusted
third-party code or an update outside maintainer intent. The protected assets
are repository integrity, CI execution, reproducible production artifacts, and
the maintainer's review boundary. Beyond the update policy above, the controls
are:

- the repository configuration admits only PEP 621/uv, npm, Dockerfile, and
  GitHub Actions managers;
- exact lockfiles and image and Action pins make the complete proposed artifact
  reviewable;
- the active default-branch ruleset requires pull requests and strict current
  results from GitHub Actions, with no Renovate bypass actor;
- CI installs without npm lifecycle scripts, verifies lock currency, audits npm
  dependencies and licences, builds deterministically, and exercises the exact
  installed Python and protected browser artifacts;
- a proposed update carrying a known advisory is visible before merge: the
  public gate scans `uv.lock` against OSV on every pull request and every push
  to `main`, under the scanning boundary recorded in
  [security architecture](../architecture/security.md);
- Renovate pull requests have no Actions secrets, production credentials, or
  deployment authority.

Repository policy cannot defend against a fully compromised GitHub App that
uses its granted repository permissions outside Renovate's documented
behaviour. Restrict the App installation to this repository, review its GitHub
permission changes before accepting them, and suspend or uninstall it on any
unexpected branch, workflow, configuration, or merge activity.

Registry unavailability, an unsupported manifest, a lockfile conflict, a failed
update command, a failing check, or base drift leaves the update unmerged for
maintainer inspection. The supported recovery is to close or rebase the pull
request, correct the repository configuration through tracked work, or suspend
the App. Revert an unsuitable merged update through the normal tracked defect
workflow. No dependency automation failure authorizes a direct `main` write,
test bypass, production-service access, or unreviewed compatibility expansion.
