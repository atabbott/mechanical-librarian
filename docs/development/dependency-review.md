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
