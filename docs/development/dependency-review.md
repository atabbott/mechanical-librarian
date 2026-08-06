# Runtime Dependency Review

The F03 configuration boundary adds one direct runtime dependency. The exact
graph is locked by hashes in `uv.lock` and was reviewed from installed package
metadata and bundled licence files.

| Package | Version | Relationship | SPDX licence | Product authority |
| --- | --- | --- | --- | --- |
| Pydantic | 2.13.4 | direct | MIT | validates in-memory config transport data only |
| pydantic-core | 2.46.4 | transitive | MIT | Pydantic validation engine |
| annotated-types | 0.7.0 | transitive | MIT | validation annotations |
| typing-extensions | 4.16.0 | transitive | PSF-2.0 | typing compatibility |
| typing-inspection | 0.4.2 | transitive | MIT | type inspection used by Pydantic |

The dependency graph receives no network client, filesystem handle, service or
reviewer credential, database connection, media handle, or mutation port.
Pydantic Settings is not installed, and no environment-variable behavior is
enabled. Application values are projected into frozen dataclasses and domain
types before orchestration; domain code does not import Pydantic.

All listed licences are compatible with distribution of the project under
`AGPL-3.0-or-later`; their bundled licence texts remain present in installed
distributions. Release SBOM and notice generation remains governed by the
release workflow.

F04 adds pinned HTTPX 0.28.1 target-service transport and its locked graph:

| Package | Version | Relationship | SPDX licence | Product authority |
| --- | --- | --- | --- | --- |
| httpx | 0.28.1 | direct | BSD-3-Clause | bounded target-only status/authorization HTTP |
| httpcore | 1.0.9 | direct | BSD-3-Clause | HTTP transport core and exact typed cause authority for narrowly retryable remote disconnects |
| anyio | 4.14.2 | transitive | MIT | timeout/network compatibility |
| certifi | 2026.6.17 | transitive | MPL-2.0 | default TLS CA bundle |
| h11 | 0.16.0 | transitive | MIT | HTTP/1.1 protocol |
| idna | 3.18 | transitive | BSD-3-Clause | hostname handling |

The client disables ambient environment/proxy trust and redirects, sends no
cookies, retains default TLS verification, sets every timeout phase and pool
bound, and streams into a hard response cap. Composition supplies only a
target-service scoped credential-bearing client. HTTPX receives no persistence,
media, provider, reviewer, or mutation authority. These licences are compatible
with AGPL-3.0-or-later distribution; MPL-2.0 applies at file level to Certifi.
The bounded provider retry policy imports the pinned HTTPcore exception type
only to recognize HTTPX's exact wrapped remote-disconnect cause without parsing
exception text. HTTPcore receives no additional authority through that direct
classification dependency.

F16a #447 adds the one-process authenticated HTTP and password-verification
boundary. The lockfile pins the complete closure:

| Package | Version | Relationship | SPDX licence | Product authority |
| --- | --- | --- | --- | --- |
| FastAPI | 0.116.1 | direct | MIT | typed `/api/v1` routing and deterministic OpenAPI only |
| Starlette | 0.47.3 | transitive | BSD-3-Clause | ASGI request, response, cookie, and middleware primitives |
| Uvicorn | 0.35.0 | direct | BSD-3-Clause | one HTTP process with proxy and access logging disabled by composition |
| Click | 8.4.2 | transitive | BSD-3-Clause | Uvicorn command/runtime support only |
| Argon2-cffi | 25.1.0 | direct | MIT | verify one owner Argon2id PHC hash; no hash or password persistence |
| argon2-cffi-bindings | 25.1.0 | transitive | MIT | Argon2 implementation binding |
| CFFI | 2.1.1 | transitive | MIT | native binding support |
| pycparser | 3.0 | transitive | BSD-3-Clause | CFFI build/runtime support |

FastAPI and Starlette receive only already composed application services and
bounded DTOs. Uvicorn receives no target or provider credentials and cannot
trust forwarded headers implicitly. Argon2 receives one submitted password and
one descriptor-bound PHC string; neither is logged, returned, or stored in the
application database. No Node runtime, browser framework, template engine,
multipart parser, identity provider, cache, session service, or second process
is introduced. The licences are compatible with AGPL-3.0-or-later; release
notice and SBOM generation remain separate release gates.

F16a #450 adds one pinned development-only browser qualification dependency and
its locked Python closure. None is imported by the runtime package or included
as application authority.

| Package | Version | Relationship | SPDX licence | Product authority |
| --- | --- | --- | --- | --- |
| Playwright | 1.61.0 | direct development only | Apache-2.0 | drives disposable same-origin browser qualification only |
| greenlet | 3.5.4 | transitive development only | MIT | Playwright synchronous test scheduling only |
| pyee | 13.0.1 | transitive development only | MIT | Playwright test event dispatch only |

The pinned Playwright release supports Python 3.13/3.14 and binds Chromium
149.0.7827.55 (browser revision 1228). The public gate downloads that browser
before running the exact installed-artifact suite. Before any launch, the
repository verifier hashes the complete sorted headless-shell payload with the
`playwright-browser-tree-v1` framing and requires
`8165f22c947b69d93b32288b8d00acde78f11e287da8084906c632c90b55781c`
on Linux x86-64 or
`ad7a45dccec74e1ceec66d023122a03d2c0351d25c169dfe18c9c4aec49a48b3`
on macOS arm64. A changed, incomplete, symlinked, extra-file, or unsupported
platform payload fails before browser execution. Tests use synthetic owner
credentials, hostile strings, disposable owner-only databases, loopback HTTPS,
and no production service. Playwright receives no target/provider credential,
production path, target client, apply port, or publication authority. Browser
binaries remain development cache content and are not application packages or
published artifacts. All three Python-package licences are compatible with
AGPL-3.0-or-later distribution; the browser's release inventory remains a
qualification input and future release-SBOM concern, not runtime content.

F06 adds no direct or transitive runtime dependency and changes no lockfile. It
targets the CPython 3.13/3.14 standard-library boundary, with these
incorporated or system components recorded for dependency inventory, notices,
and the release SBOM:

| Component | Supported boundary | SPDX licence | Product authority |
| --- | --- | --- | --- |
| CPython | 3.13.14 and 3.14.6 macOS development; 3.13.11 Linux F06 cohort | Python-2.0 | standard runtime and bounded local parsing/process facilities |
| Expat through `xml.parsers.expat` | `pyexpat.EXPAT_VERSION >= 2.7.2` before input is opened | MIT | event-driven XML tokenization only |
| zlib through `zipfile` | incorporated/system version shipped with the targeted CPython runtime | Zlib | stored/Deflate stream decode only |

The adapter boundary is the replacement seam if a targeted CPython patch
release no longer satisfies the frozen contract. The standard-library choice
does not grant network, credential, database, reviewer, persistence, media
write, shell, or product-file authority, and it is not described as a kernel
sandbox. Full-suite development qualification covers CPython 3.13.14 with
Expat 2.8.1 and CPython 3.14.6 with Expat 2.7.4 on macOS; both use zlib 1.2.12.
The isolated Linux F06 cohort covers CPython 3.13.11, Expat 2.7.3 and zlib
1.2.13 with a read-only root/environment, disabled networking, and exact proof
of the 256 MiB `RLIMIT_AS`, one-process `RLIMIT_NPROC`, CPU, descriptor and core
gates. Future published combinations require their own exact patch and
container evidence.

F07a retains the F06 runtime graph and adds no runtime dependency. Strict
opening XHTML uses the same fresh `xml.parsers.expat` boundary. Bounded
deterministic XHTML/HTML recovery uses `html.parser.HTMLParser` from the
targeted CPython standard library. It receives only already selected local
member bytes and has no browser, CSS, script, URL/base resolution, network,
filesystem extraction, source rewrite, or retained-tree authority. Parser,
recovery, projection, selection, normalizer, vocabulary, limits, extractor,
and report versions are all compatibility identity.

F07a adds one pinned development-only property-testing dependency. It is
locked in the development group and absent from runtime wheel/sdist contents
and production imports.

| Package | Version | Relationship | SPDX licence | Product authority |
| --- | --- | --- | --- | --- |
| Hypothesis | 6.156.6 | direct development only | MPL-2.0 | generates bounded in-memory parser/configuration test cases only |
| sortedcontainers | 2.4.0 | transitive development only | Apache-2.0 | Hypothesis internal data structures only |

Hypothesis 6.156.6 was released 2026-07-10, declares Python 3.10 or newer and
the project-targeted 3.13/3.14 classifiers, and is maintained under MPL-2.0.
It receives no production media, private corpus, credential, network client,
database, persistence, reviewer, provider, subprocess, or write authority.
Property cases use repository-authored synthetic EPUB structures. Both
development licences are compatible with the project distribution; neither
package is included in the application runtime dependency graph.
