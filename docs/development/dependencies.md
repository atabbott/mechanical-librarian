# Dependencies

This document says what each direct dependency may do, what any dependency
must satisfy, and how updates are proposed and merged. It records no version
numbers except for vendored and image artifacts that no lockfile covers.

Authorities:

- `pyproject.toml` and `frontend/package.json` define the direct dependencies.
- `uv.lock` and `frontend/package-lock.json` pin every installed version and
  integrity hash.
- `.github/renovate.json` bounds what update automation may propose.
- [What merging proves](../../CONTRIBUTING.md#what-merging-proves) lists the
  gate's checks.

`scripts/verify_dependency_authority.py` runs in `fast-source`. It fails when
`pyproject.toml` declares a dependency (runtime, any group, or the build
backend) with no grant below, or when a grant names a dependency the manifest
no longer declares. It checks that a grant exists, not that it is true, so
keep each grant in step with the code it describes.

## Reproducibility

- Manifests pin direct dependencies exactly; lockfiles pin everything else.
  Exact direct pins also stop lockfile maintenance from moving a direct
  dependency under a different update class. Change a manifest and regenerate
  its lockfile together.
- Build backends, toolchains, container image digests, GitHub Action SHAs and
  Playwright are exact pins too, because the lockfiles do not govern them.
- CI runs `uv lock --check`, locked or frozen uv operations, and `npm ci`.
- `project.requires-python` is a supported interpreter range, not a pin, and
  automation does not manage it.
- Frontend manifest and lockfile bytes are build provenance, not semantic
  qualification identity ([ADR-0076](../architecture/decisions/0076-dependency-build-provenance.md)).

## Review criteria

Review every change against the resolved lockfile graph and installed
licence files.

- **Least authority.** No dependency gets a network client, filesystem handle,
  credential, database connection, media handle or mutation port beyond its
  grant. A package that needs more is a design decision, not an update.
- **Licence compatibility.** Every licence must be compatible with
  `AGPL-3.0-or-later`, and bundled licence texts must stay in installed
  distributions.
- **Closed closure.** No optional extra is installed. A multipart parser,
  template engine, CLI framework, settings loader, cache, session service,
  identity provider, second HTTP client or second server needs its own
  decision.
- **Development-only stays development-only.** A development dependency lives
  in a dependency group and is absent from `[project.dependencies]`, the wheel
  and sdist, the container image and production imports.

## Granted runtime authority

- **Pydantic** validates hostile input: the configuration document, owner
  settings, the setup and review APIs' request and response bodies, and
  OpenRouter reviewer responses and model output. It is reachable from
  unauthenticated HTTP, so review an update as a parser update. Pydantic
  Settings is not installed and no environment-variable loading is enabled.
- **HTTPX and HTTPcore** carry all application HTTP traffic except remote cover
  downloads: Audiobookshelf requests (`adapters/library_services/`), including
  the production writes (a metadata `PATCH` and a cover `POST`); metadata
  provider requests (`adapters/providers/`); and the OpenRouter reviewer.

  `infrastructure/security/credentials.py` builds the target and provider
  clients with `follow_redirects=False`, `trust_env=False`, `verify=True`, an
  explicit timeout, and one connection without keep-alive. Responses stream
  into a hard byte cap. The target client's `httpx.Auth` attaches the bearer
  token only to same-origin requests that are a `GET` on one of five read
  paths, a `PATCH` on `/api/items/{id}/media`, or a `POST` on
  `/api/items/{id}/cover`. The Hardcover token goes only to the fixed GraphQL
  URL. An optional Google Books API key, from the provider's secret file or a
  stored owner credential, goes only to the fixed Google Books URL as the `key`
  query parameter. Every other provider client, and the raw-cover read, is
  credential-free. The OpenRouter reviewer (`adapters/reviewers/openrouter.py`)
  builds its own client, pinned to `https://openrouter.ai` with redirects
  refused and `trust_env=False`; it alone carries the reviewer key.

  HTTPX performs a write; it does not authorise one. Consent, backup
  readiness, exact-current plan validation, read-back verification and audit
  sit above it. HTTPX gets no persistence or database authority. The provider
  retry policy imports HTTPcore only to recognise the exact remote-disconnect
  cause HTTPX wraps.

  Remote cover downloads (`adapters/providers/remote_cover.py`) use their own
  `socket` and `ssl` transport, which pins each resolved address and
  revalidates every redirect hop. An HTTPX update does not reach them.
- **FastAPI and Starlette** provide `/api/v1` routing, deterministic OpenAPI,
  and ASGI request, response, cookie and middleware primitives. They receive
  only composed services and bounded DTOs. The web interface imports Starlette
  directly, so it is pinned beside FastAPI.
- **Uvicorn** runs one HTTP process with proxy headers and access logging off.
  It gets no target or provider credential. `infrastructure/security/review_protocol.py`
  subclasses Uvicorn's `h11_impl` protocol to enforce a connection-state
  invariant, so review a Uvicorn update as transport code.
- **Argon2-cffi** verifies the owner's Argon2id hash. It receives one submitted
  password and one PHC string; neither is logged, returned or stored in the
  application database.
- **Pillow** (MIT-CMU) is the only dependency that parses hostile media bytes.
  `bootstrap/cover_child.py` decodes a downloaded or target-supplied cover and
  encodes its bounded PNG preview in a one-shot child that first sets
  `RLIMIT_AS` 256 MiB, `RLIMIT_CPU` 30 seconds, `RLIMIT_NOFILE` 64,
  `RLIMIT_NPROC` 1 and `RLIMIT_CORE` 0, replaces the `socket` entry points, and
  on Linux installs a syscall filter that denies networking; byte and pixel
  caps bound the input first. `application/cover_packet_semantics.py` only
  decodes one digest-verified, report-bound preview and treats
  decompression-bomb and unidentified-image errors as rejection. Pillow gets no
  path, network, credential, database or write authority.

## Granted development-only authority

No development dependency is imported by the runtime package or receives a
credential, production path, private corpus or publication authority. Tests
use synthetic credentials, disposable databases and loopback HTTPS.

- `ruff`, `mypy`, `pytest` and `setuptools` read this repository's source and
  build its wheel, with no network from any gate command. `setuptools` is the
  build backend and produces the shipped artifact, so review its updates as
  build changes.
- **Playwright** drives the browser tier: `tests/browser/` launches the pinned
  release's own headless Chromium, the only third-party binary a gate starts.
  `scripts/verify_playwright_browser.py` records its tree digest and, before
  launch, refuses an unsupported platform or an unreadable, absent, empty or
  symlinked payload. The browser reaches only loopback review servers built
  from synthetic fixtures and gets no apply port.
- **Coverage** (Apache-2.0) measures line and branch execution for the
  test-value audit in [testing strategy](testing-strategy.md). Only a
  maintainer runs it, by hand. Its `.pth` hook traces subprocesses only when
  `COVERAGE_PROCESS_START` is set, and no documented command sets it. Do not
  enable subprocess tracing: it imports modules into children whose tests
  assert what they do not import.
- **mutmut** (BSD-3-Clause) is the mutation engine for the same audit and has
  the widest development grant: it rewrites this repository's source into the
  git-ignored `mutants/` and runs the suite against it. Only a maintainer runs
  it, through `scripts/generate_mutants.py` and the audit scripts in
  [testing strategy](testing-strategy.md). Mutated code loads only when
  `PYTHONPATH` names `mutants/src`.

  Neither coverage nor mutmut is a gate or ships, and neither has a threshold
  or score. The container installs with `uv sync --frozen --no-dev`.
- **HTTPX2** is the in-process ASGI transport for `TestClient`; without it,
  `starlette.testclient` warns and `scripts/qualify_exact_artifact.py` fails.
  It is a separate distribution and import name from the runtime `httpx`, and
  its `httpx2-jsfetch` dependency installs only on `emscripten`.
- **Hypothesis** generates bounded in-memory parser and configuration test
  cases.

## Contained-reviewer image artifacts

The supported image includes Codex and its paired Bubblewrap from the exact
`@openai/codex` 0.147.0 platform package. The Docker build supports `amd64`
and `arm64` only, downloads the tarball without lifecycle scripts, verifies its
SHA-256 before extraction, and checks the native Codex version. Node, npm and
the package's other resources stay out of the runtime image.

| Target | Registry version | Tarball SHA-256 |
| --- | --- | --- |
| `linux/amd64` | `0.147.0-linux-x64` | `c969740cf8297e4c31905cd551efeb2c99af5080c12c236bdf825598b250139a` |
| `linux/arm64` | `0.147.0-linux-arm64` | `4d09bf000597768dfa070b0e7c612f0fc258ac7fab364e570104ea625f918546` |

| Retained file | Licence | SHA-256 |
| --- | --- | --- |
| Codex licence, tag `rust-v0.147.0` | Apache-2.0 | `d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc` |
| Bubblewrap 0.11.2 `COPYING` | LGPL-2.0-or-later | `b7993225104d90ddd8024fd838faf300bea5e83d91203eab98e29512acebd69c` |
| Source archive of Codex commit `be6e8eac029b183056b7e4402879f15d2c85f61b`, including the Bubblewrap sources and build recipe | | `f0513d22bd932c53dd4eb267cc30d24b75c6dd6a72865178f092fb8913b9d6f1` |

The image's checksum manifest covers all of these. They get only the
authority in
[ADR-0062](../architecture/decisions/0062-owner-managed-reviewer-access.md).

The supported Compose file loads `config/seccomp/contained-reviewer.json`,
derived from the Moby `profiles/seccomp` `seccomp/v0.2.3` default profile. Its
added rules are described in [the seccomp notice](../../config/seccomp/README.md).

| File | SHA-256 |
| --- | --- |
| Upstream Moby profile | `536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74` |
| Checked-in derivative | `04151878f5bb17b00d216d552f8b8a8bc3683549517193e4f8d6501f1e7530ff` |
| Moby Profiles licence (Apache-2.0), kept beside it | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |

No Renovate rule or runtime updater owns these artifacts. Change them through
tracked work that updates the Dockerfile, these tables and the licence files
together.

## Vendored typeface

The review workspace ships Inter's variable latin and latin-ext faces under
`frontend/src/fonts/`, copied unmodified from `@fontsource-variable/inter`
5.3.0 (integrity
`sha512-OupL48va4JNofb97w6NYeF9S7W/kHNKM0Er8Dem5nqi4jeOLrVJDoE8tZEpnMJmtkvNbB1EIPPwHcdkF6b1oUA==`).
No lockfile, licence allowlist or Renovate rule covers them.

| File | SHA-256 |
| --- | --- |
| `inter-latin-wght-normal.woff2` | `3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62` |
| `inter-latin-ext-wght-normal.woff2` | `34b9c504cab7a73e37b746343a449132e56cf7b5481af2cb81dc74dcff25c956` |

Inter is OFL-1.1. The package's licence text (SHA-256
`3b0a5fca3d17942cde889069889dedbbbd075e9b599968c82a95f4d944e9b345`) is kept
unchanged under a provenance header in `LICENSES/Inter-OFL-1.1.txt`. That file
ships in the sdist and as a wheel `license-files` entry, so the package
declares `AGPL-3.0-or-later AND OFL-1.1`. A `license-files` entry keeps its
path under `.dist-info/licenses/`, and the artifact qualifiers refuse any
installed path naming `frontend`, so the file must stay outside `frontend/`.
The faces are served same-origin and get no authority. Update this table and
the licence header together.

The browser tests carry one test-only face,
`tests/browser/fonts/DejaVuSans-Bold-subset.ttf` (SHA-256
`d61157a5b9caa6c2f73aba7a5ff9d13ae4ca1b19d50a5a85745518f012a23e57`), so one
wrapping test does not depend on host fonts. Its provenance and licence are in
`tests/browser/fonts/LICENSE`. `tests/` never ships.

## Frontend build graph

Node and npm are required at the exact `engines` versions in
`frontend/package.json`, and npm is installed explicitly from that manifest.
`scripts/build_frontend.py` refuses to build when the installed `node` or `npm`
version differs. Neither enters the runtime image. Node is MIT with
incorporated third-party notices; npm is Artistic-2.0. The lockfile binds every
integrity hash, `npm ci` runs without lifecycle scripts, and generated DTOs
come only from the committed OpenAPI document. The build gets no credential,
private data or runtime authority.

`npm run licenses` fails if a locked package lacks licence metadata or
declares a licence outside the allowlist in
`frontend/scripts/check-licenses.mjs`.

## Standard-library boundary

The application also relies on components it does not lock:

| Component | Supported boundary | SPDX licence | Authority |
| --- | --- | --- | --- |
| CPython | the `requires-python` range | Python-2.0 | standard runtime, bounded local parsing and processes |
| Expat via `xml.parsers.expat` | `EXPAT_VERSION >= 2.7.2`, checked before input is opened | MIT | event-driven XML tokenisation |
| zlib via `zipfile` | the version shipped with CPython | Zlib | stored and Deflate decoding |

`html.parser.HTMLParser` recovers XHTML and HTML from already selected member
bytes, with no URL resolution, network, extraction or rewrite. None of these
gets network, credential, database, reviewer, persistence, write or shell
authority, and none is a sandbox. Parser, recovery, projection, selection,
normaliser, vocabulary, extractor, limits and report versions are
compatibility identity ([ADR-0009](../architecture/decisions/0009-durable-epub-opening-evidence.md)).

A Python update that changes `unicodedata.unidata_version` requires a clean
rebuild of the report database; see schema admission in
[security architecture](../architecture/security.md).

## Automated updates

The Mend-hosted Renovate GitHub App proposes updates within
`.github/renovate.json`. Do not duplicate its pull requests with Dependabot.

- **Schedule.** New branches start in one weekly window (before 6am Monday,
  Europe/London). Branches behind `main` rebase and requalify any time.
- **Release age.** Ordinary releases wait seven days. Node, Python and uv
  images carry no release dates, so they are proposed at the first window.
  Vulnerability alerts skip the schedule and take the lowest fixed version,
  but follow the same auto-merge policy.
- **Limits.** At most five open branches and pull requests, and two new pull
  requests per hour. Renovate opens each pull request at once so the gate runs;
  opening it never authorises a merge.
- **Groups.** Each of Node, Python, uv and npm has one group across all its
  declarations, and parity tests refuse a partial update.
- **Auto-merge.** Every managed update class, including lockfile maintenance,
  requests squash auto-merge, except a new Node major, a new Python minor or
  major, or a replacement. Those need Dependency Dashboard approval and do not
  auto-merge.

[Delivery and merging](slice-delivery.md#renovate-platform-auto-merge-exception)
says what an auto-merge must still pass, and
[work management](work-management.md#pull-requests-and-tracking) says why
Renovate pull requests carry no closing issue.

New or replacement dependencies, compatibility-policy changes, the Python
range, and the FFmpeg and contained-reviewer bundles are manual tracked work.
Renovate publishes no releases.

To stop updates, set top-level `enabled` to `false` through a tracked pull
request. For urgent containment, a repository administrator may suspend or
uninstall the App and record that action. Close unwanted Renovate pull requests
unmerged; revert a merged one as a defect.

## Automation security review

Renovate reads manifests, lockfiles, Dockerfiles, workflows, release metadata
and vulnerability alerts, and may create branches, commits, issues and pull
requests and request auto-merge. It gets no credential, production data or
deployment or registry secret; all dependencies are public.

Beyond the update policy above:

- only the `pep621` (Python, with `uv.lock`), `npm`, `dockerfile` and
  `github-actions` managers are enabled, plus one `custom.regex` manager for
  the gate's OSV scanner image;
- Renovate pull requests get no Actions secrets or deployment authority;
- the gate scans `uv.lock` against OSV on every pull request and push to
  `main` (see the scanning boundary in
  [security architecture](../architecture/security.md)).

Repository policy cannot stop a compromised App misusing its repository
permissions. Restrict the installation to this repository, review its
permission changes before accepting them, and suspend it on any unexpected
activity.

Any automation failure (registry outage, lockfile conflict, failing check,
base drift) leaves the update unmerged. Recover by closing or rebasing the pull
request, fixing configuration through tracked work, or suspending the App. No
failure authorises a direct `main` write, a test bypass or production-service
access.
