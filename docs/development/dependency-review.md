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
| httpcore | 1.0.9 | transitive | BSD-3-Clause | HTTP transport core |
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
