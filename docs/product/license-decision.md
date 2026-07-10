# Licence Decision

Status: **accepted**.

Mechanical Librarian is licensed under the GNU Affero General Public License,
version 3 or any later version: SPDX identifier `AGPL-3.0-or-later`. The
canonical licence text is in the repository-root [LICENSE](../../LICENSE) file,
and [ADR-0005](../architecture/decisions/0005-agpl-3-or-later.md) records the
decision and consequences.

## Decision

AGPL-3.0-or-later was selected because Mechanical Librarian is a self-hosted
network service and reciprocal source availability for modified hosted versions
is a core project value.

The decision provides:

- explicit rights to use, study, modify, and redistribute the project under the
  licence terms;
- network-source reciprocity for modified versions offered to users; and
- aligned inbound and outbound contribution terms.

Accepted trade-offs include:

- some organizations prohibit or avoid AGPL software; and
- dependencies, plugins, bundled assets, fixtures, generated material, copied
  components, and container contents require continuing compatibility and
  provenance review.

## Project material policy

- Project code and documentation use `AGPL-3.0-or-later` unless an approved,
  compatible notice says otherwise.
- Contributions are submitted under the same licence. The project does not
  currently require a contributor licence agreement.
- Project-authored generated material uses the same licence.
- Fixtures and extracted components require recorded authorship, provenance,
  redistribution permission, and compatibility evidence.
- Plugins distributed in this repository use the same licence. Separately
  distributed integrations require boundary-specific review rather than an
  assumed conclusion.
- Third-party notices remain intact and are distributed where their licences
  require them.

## Enforcement and publication

Dependency and documentation quality gates track licence-file integrity, SPDX
metadata, dependency compatibility, third-party notices, and provenance. The
private-to-public audit treats any unresolved licence or redistribution issue
as a stop condition.

Relicensing requires a new governance decision and all permissions legally
required at that time; maintainer status does not imply ownership of contributor
copyright.
