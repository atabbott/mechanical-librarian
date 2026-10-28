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

Its completeness over direct dependencies is enforced rather than reviewed.
`scripts/verify_dependency_authority.py` runs in `fast-source` and fails on a
dependency `pyproject.toml` declares with no grant below, on a grant naming a
dependency the manifest no longer declares, and on a grant whose own spelled
count no longer matches the dependencies it covers.

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
- **Closed closure.** An addition must not quietly enlarge the runtime closure.
  No optional extra is installed, so no multipart parser, template engine, CLI,
  settings loader, cache, session service, identity provider, second HTTP
  client, or second server process enters the shipped graph without its own
  decision. The runtime's own child processes — the EPUB inspector, the cover
  decoder, the contained reviewer — are composed by this repository and are not
  dependency closure.
- **Development-only stays development-only.** A development dependency is
  locked in a dependency group and is absent from `[project.dependencies]`, the
  shipped wheel's and sdist's contents and metadata, the container image, and
  production imports.

## Granted runtime authority

Each grant below is written from the imports and call sites, and is meant to be
checked against them. A grant that has fallen behind the code is worse than no
grant, because a reviewer assessing an update trusts it.

- **Pydantic** is the validation boundary for two hostile inputs. It validates
  the configuration document (`infrastructure/configuration.py`), and it
  validates and serialises the authenticated API's request and response DTOs
  (`interfaces/web/app.py` imports `BaseModel`, `ConfigDict`, `Field`,
  `StrictStr` and `TypeAdapter`), so it is reachable from unauthenticated HTTP
  and a Pydantic update is a parser update. Pydantic Settings is not installed
  and no environment-variable behaviour is enabled. Validated values are
  projected into frozen dataclasses and domain types before orchestration;
  domain code does not import Pydantic.
- **HTTPX and HTTPcore** carry the application's service and provider traffic:
  Audiobookshelf catalogue, item, raw-cover and field-effect requests
  (`adapters/library_services/`), and Hardcover, Inventaire, Open Library and Google Books
  metadata requests (`adapters/providers/`). That includes **production writes**
  — a metadata `PATCH` and a cover `POST` in
  `audiobookshelf_field_effect.py` — and a **credential-bearing GraphQL POST**
  to Hardcover. The grant is not "no mutation authority"; it is that every one
  of those requests is bounded before it is made.

  It is not all outbound traffic. Remote provider cover acquisition
  (`adapters/providers/remote_cover.py`) uses its own `socket` and `ssl`
  transport, not HTTPX, because it pins each resolved address and revalidates
  every redirect hop itself. An HTTPX update does not reach that path, and a
  change to that path is not a dependency change.

  `infrastructure/security/credentials.py` constructs every credential-bearing
  and provider client, and each one sets `follow_redirects=False`,
  `trust_env=False`, `verify=True`, an explicit `httpx.Timeout`, and
  `httpx.Limits(max_connections=1, max_keepalive_connections=0)`. Responses
  stream into a hard byte cap. The target client's `httpx.Auth` flow is what
  bounds the write authority: it attaches the bearer token only when the request
  is same-origin as the configured endpoint **and** is either a `GET` on one of
  the five permitted read paths, a `PATCH` on `/api/items/{id}/media`, or a
  `POST` on `/api/items/{id}/cover`. Any other method, path, or origin is sent
  without the credential. The Hardcover client attaches its token only to the
  fixed GraphQL URL. Open Library's client carries no secret, and Google Books'
  is credential-free or reads one owner-only key file. The Audiobookshelf
  raw-cover read builds its own credential-free client with an empty user agent
  in `audiobookshelf_item.py`.

  The application's guarded-effect gates — consent, backup readiness,
  exact-current plan validation, read-back verification and audit — sit above
  this transport and are what authorise a write at all; HTTPX performs one, it
  does not decide one. HTTPX receives no persistence handle, no database
  connection, and no reviewer authority. The bounded provider retry policy
  imports the pinned HTTPcore exception type only to recognise HTTPX's exact
  wrapped remote-disconnect cause without parsing exception text; that
  classification dependency grants HTTPcore nothing else.
- **FastAPI and Starlette** provide typed `/api/v1` routing, deterministic
  OpenAPI, and ASGI request, response, cookie and middleware primitives, and
  receive only already composed application services and bounded DTOs.
  `interfaces/web/app.py` imports `starlette.types` directly, so Starlette is
  pinned in `pyproject.toml` beside FastAPI rather than floating under
  FastAPI's open range. FastAPI's `Annotated` documentation metadata is the
  only authority its own transitive `annotated-doc` participates in.
- **Uvicorn** runs one HTTP process with proxy and access logging disabled by
  composition. It receives no target or provider credential and cannot trust
  forwarded headers implicitly. `infrastructure/security/review_protocol.py`
  subclasses Uvicorn's own `h11_impl` HTTP protocol to enforce a connection-state
  invariant, so a Uvicorn update can change that seam and is reviewed as
  transport code rather than as a server version.
- **Argon2-cffi** verifies one owner Argon2id PHC hash. It receives one
  submitted password and one descriptor-bound PHC string; neither is logged,
  returned, or stored in the application database.
- **Pillow** (MIT-CMU, compatible with `AGPL-3.0-or-later`) is the image
  decoder, and it is the only dependency that parses hostile media bytes. It is
  used in two places and never as a serialiser. In `bootstrap/cover_child.py` it
  decodes a downloaded or target-supplied cover inside a one-shot child that
  first sets `RLIMIT_AS` to 256 MiB, `RLIMIT_CPU` to 30 seconds, `RLIMIT_NOFILE`
  to 64, `RLIMIT_NPROC` to 1 and `RLIMIT_CORE` to 0, then denies networking both
  by replacing the `socket` entry points and, on Linux, by installing a seccomp
  filter over the socket syscalls; original and preview byte caps and a pixel
  cap bound the input before Pillow sees it. In
  `application/cover_packet_semantics.py` it decodes one already
  digest-verified report-bound preview, with `DecompressionBombError`,
  `DecompressionBombWarning` and `UnidentifiedImageError` all treated as
  rejection; the derived transport PNG is re-encoded by this repository's own
  platform-independent encoder, not by Pillow. Pillow receives no path, network,
  credential, database, or write authority in either site.

## Granted development-only authority

No development dependency is imported by the runtime package or included as
application authority. None receives a target, provider, or reviewer
credential, a production path, private corpus, or publication authority. Tests
use synthetic credentials, hostile strings, disposable owner-only databases,
loopback HTTPS, and no production service; property cases use
repository-authored synthetic structures.

- `ruff`, `mypy`, `pytest` and `setuptools` read and analyse this repository's
  own source and build its wheel from it. They are granted the checkout and
  nothing else: no credential, no network use in any gate command, no target or
  provider, and no runtime import. `setuptools` is the build backend and is the
  only one of the four that produces a shipped artifact; a `setuptools` update
  is reviewed as a build change, which is why `.github/renovate.json` keeps it
  out of the auto-merge classes.
- **Playwright** drives disposable same-origin browser qualification only, and
  it holds **process-creation authority**: `tests/browser/` calls
  `playwright.chromium.launch(headless=True)`, so a Playwright update changes
  what process the browser tier starts. It is the only development dependency
  that starts a process inside a gate, and the only one that starts a
  third-party binary rather than this interpreter; `mutmut` below also creates
  processes, but only under a manual audit no gate runs. That browser
  is the pinned release's own bound Chromium revision, which the public gate
  downloads before the exact installed-artifact suite, and
  `scripts/verify_playwright_browser.py` records the payload's tree digest and
  rejects an unsupported platform, an unreadable layout, and an absent, empty,
  or symlinked payload before anything launches. The browser reaches only
  loopback HTTPS review servers built from synthetic fixtures; it receives no
  target or provider credential, no production path, and no apply port. Browser
  binaries are development cache content, not application packages or published
  artifacts; the browser's own release inventory is a qualification input and a
  future release-SBOM concern, not runtime content.

  Existing Playwright version updates may auto-merge after all eight required
  checks pass, including protected-browser payload verification. The repository
  Renovate policy governs majors as well as minor and patch updates.
- **Coverage** (Apache-2.0, compatible with `AGPL-3.0-or-later`) traces line and
  branch execution of this repository's own source for the test-value audit
  recorded in [testing strategy](testing-strategy.md). It is invoked by hand and
  by nothing else: `coverage run --branch --source=src/mechanical_librarian
  scripts/run_test_tier.py fast`, then a terminal report. It is granted the
  checkout, and receives no credential, network use, database, target, or
  provider.

  Its one authority beyond that is a `.pth` file installed into the development
  virtualenv, which activates coverage in **subprocesses** when
  `COVERAGE_PROCESS_START` is set. No documented command sets it, so the hook is
  inert in ordinary use — but the hook itself is unconditional and is coverage's,
  not this repository's, which is why the grant names it rather than the command.
  Enabling it during #737 to trace child processes made
  `tests/unit/test_epub_child.py::test_entrypoint_import_does_not_load_network_database_or_secret_implementations`
  fail, because tracing pulled `sqlite3` into a child whose entire assertion is
  which modules it does not import. The activation was removed and subprocess
  tracing abandoned rather than the test relaxed; a child-process coverage figure
  would have to be obtained some other way, because this mechanism changes what
  it measures.
- **mutmut** (BSD-3-Clause, read from the licence text bundled in the installed
  distribution, compatible with `AGPL-3.0-or-later`) is the mutation engine for
  that same audit, and it holds the widest development grant in this list: it
  **rewrites this repository's source** into `mutants/`, and the suite is then
  executed against the rewritten tree. That is filesystem-write authority over a
  generated tree plus process-creation authority, and both are exercised only by
  a maintainer running the audit by hand. No tier and no gate invokes it.

  `scripts/generate_mutants.py` is its only caller and copies the package,
  generates the mutants, and collects which tests reach which function;
  `scripts/run_mutation_audit.py` and `scripts/mutation_kills_for_test.py` then
  execute the rewritten tree through ordinary `subprocess` calls this repository
  composes. Every write lands under `mutants/`, which is generated output,
  git-ignored, and never committed. Loading the rewritten package requires
  `PYTHONPATH` to name `mutants/src`, so nothing mutated is on a gate's import
  path. It is granted the checkout and receives no credential, network use,
  database, target, or provider.

  Not verified: whether mutmut writes anywhere outside `mutants/` under inputs
  this repository does not give it, and what authority its own transitive
  closure holds. Both are bounded by the fact that it runs only when a
  maintainer runs it, never in CI.

  Neither instrument ships. Both are locked in the `dev` group only: absent from
  `[project.dependencies]` and from the built wheel's `Requires-Dist`, imported
  by nothing under `src/`, and excluded from the container image, which installs
  with `uv sync --frozen --no-dev`. Neither is a gate, deliberately.
  [What merging proves](../../CONTRIBUTING.md#what-merging-proves)
  records that there is no coverage threshold; there is no mutation score
  either, and neither tool is referenced anywhere under `.github/`. Each is a
  recorded analysis instrument, so an update to either can change a figure a
  maintainer reads but cannot change what merging proves.
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

## Contained-reviewer image artifacts

The supported image includes two executable files extracted from the exact
platform package for `@openai/codex` 0.147.0. The Docker build maps only
`amd64` and `arm64`, downloads the matching npm registry tarball without
lifecycle scripts, verifies its SHA-256 before extraction, checks the native
Codex version, and copies only Codex plus the package's paired Bubblewrap into
the runtime image. Node, npm, the package wrapper, code-mode host, shell, and
search resources do not enter the runtime closure.

| Target | Registry version | Tarball SHA-256 |
| --- | --- | --- |
| `linux/amd64` | `0.147.0-linux-x64` | `c969740cf8297e4c31905cd551efeb2c99af5080c12c236bdf825598b250139a` |
| `linux/arm64` | `0.147.0-linux-arm64` | `4d09bf000597768dfa070b0e7c612f0fc258ac7fab364e570104ea625f918546` |

The Codex package declares Apache-2.0. The image includes the exact licence from
the upstream `rust-v0.147.0` tag, checksum
`d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc`,
alongside package metadata. The paired Bubblewrap 0.11.2 source declares
LGPL-2.0-or-later and carries its upstream `COPYING` file, checksum
`b7993225104d90ddd8024fd838faf300bea5e83d91203eab98e29512acebd69c`.
The image retains the complete source archive for exact Codex commit
`be6e8eac029b183056b7e4402879f15d2c85f61b`, checksum
`f0513d22bd932c53dd4eb267cc30d24b75c6dd6a72865178f092fb8913b9d6f1`;
that archive contains the vendored Bubblewrap sources and `codex-rs/bwrap`
build recipe corresponding to the distributed binary. Both licences, the
source archive, installed binaries, and metadata are covered by the image's
checksum manifest. These artifacts receive only the closed device-authentication and contained
cover-observation authority in ADR-0062. Changing their version, architecture
set, source, licence, or checksum requires an ordinary tracked dependency and
security review; no runtime updater or Renovate rule owns them.

The supported Compose file also loads
`config/seccomp/contained-reviewer.json`, a derivative of Moby
`profiles/seccomp` `seccomp/v0.2.3` default profile. The exact upstream
document has SHA-256
`536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74`;
the checked-in derivative has SHA-256
`04151878f5bb17b00d216d552f8b8a8bc3683549517193e4f8d6501f1e7530ff`.
Its three appended rules admit only the two traced Bubblewrap clone masks and
the mount, pivot-root, and unmount calls used to construct the nested filesystem.
The inner post-construction filter is application-owned rather than an outer
Docker-profile admission. Docker's other default syscall admissions and
denials remain unchanged. Moby Profiles is Apache-2.0; the exact tagged licence,
SHA-256
`cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`,
is retained beside the derivative.

## Vendored typeface

The review workspace ships Inter's variable latin and latin-ext faces as two
files under `frontend/src/fonts/`, copied unmodified from the npm package
`@fontsource-variable/inter` 5.3.0 (tarball integrity
`sha512-OupL48va4JNofb97w6NYeF9S7W/kHNKM0Er8Dem5nqi4jeOLrVJDoE8tZEpnMJmtkvNbB1EIPPwHcdkF6b1oUA==`).
It is a vendored asset, not an npm dependency, so no lockfile entry, licence
allowlist entry or Renovate rule covers it.

| File | SHA-256 |
| --- | --- |
| `inter-latin-wght-normal.woff2` | `3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62` |
| `inter-latin-ext-wght-normal.woff2` | `34b9c504cab7a73e37b746343a449132e56cf7b5481af2cb81dc74dcff25c956` |

Inter is OFL-1.1. The package's licence text, SHA-256
`3b0a5fca3d17942cde889069889dedbbbd075e9b599968c82a95f4d944e9b345`, is
retained in `LICENSES/Inter-OFL-1.1.txt` under a provenance header, ships in
the sdist, and is a wheel `license-files` entry, so the package declares
`AGPL-3.0-or-later AND OFL-1.1`. It sits outside `frontend/` because a
`license-files` entry keeps its path inside the wheel's `.dist-info/licenses/`,
and the exact-artifact and container qualifiers refuse any installed path that
names a `frontend` directory. The faces are content-hashed
into the review asset bundle and served same-origin; they receive no authority.
Changing their version, subsets, source or checksum is an ordinary tracked
change that updates this table and the licence header together.

## Frontend build graph

Node and the explicitly installed manifest-pinned npm are required exactly and neither enters the runtime
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
two pull requests per hour, and groups repeated tool declarations by logical tool. GitHub
vulnerability alerts skip the ordinary schedule and use the lowest fixed
version, but retain the same merge policy.

Renovate creates a pull request as soon as it creates an eligible branch,
because the exact-head qualification workflow runs on pull requests, not on
unreviewed branch pushes. The pull request is what starts the checks; creating
it never authorizes a merge.

Renovate requests GitHub-native squash auto-merge for existing admitted
managed dependency updates: major, minor, patch, pre-1.0, runtime, build tools,
Action references, image tags/digests, pinning and lockfile maintenance. Each
logical Node, Python, uv and npm tool has one group spanning its native manager
declarations. The parity tests refuse incomplete updates. A Python update that
changes `unicodedata.unidata_version` requires a clean rebuild of the report
database ([Schema admission](../architecture/security.md#schema-admission)).
npm is installed
explicitly from the canonical frontend manifest in CI, Docker and local Linux
qualification; local frontend builds validate the actual executables against it.

The weekly schedule limits new branches. Behind-main branches refresh and
requalify outside that window. GitHub requires a pull request current with
main and all eight checks: `fast-source`, `fast-source-2`, `fast-source-3`,
`fast-source-4`, `exact-installed`, `protected-browser`, `container-image`,
and `dependency-audit`. Pending,
failed or stale checks block merging. Renovate has no bypass or direct main
write authority. A compatibility failure stays open for a manual fix or a
reviewed revert; tests and safety gates are never weakened to admit an update.

New or replacement dependencies and compatibility-policy changes remain manual,
tracked work. Replacements need Dependency Dashboard approval and cannot
auto-merge. The Python compatibility range remains unmanaged. The checksum-
qualified FFmpeg and contained-reviewer bundles remain outside Renovate scope.
This policy does not publish an application release.

Frontend package and lockfile bytes are build provenance, not semantic policy
qualification identity. The fixed F46v9, F19av26 and F20v40 one-hop successors
preserve every prior record, option and measurement while removing exactly
those two paths from the live semantic source closure. Semantic source changes
still fail the closure guard and require reviewed qualification. Predecessor
decisions remain readable but cannot authorize new decisions or effects.
Candidate SHA, lockfiles, asset manifests, deterministic builds, wheel RECORD
and exact-installed evidence continue to bind the dependency closure.

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
issues, and pull requests. For the managed updates named above it may request
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
  GitHub Actions managers, plus one narrow OSV workflow-image regex manager;
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
