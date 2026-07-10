# ADR-0005: License the project under AGPL-3.0-or-later

- Status: accepted
- Date: 2026-07-10
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Mechanical Librarian is a self-hosted network service intended to improve
through public collaboration. Its primary value is delivered through a web
interface and background service, so distributing binaries is not the only
realistic way that modified versions may be offered to users.

The repository needs an OSI-approved licence before it becomes public or
accepts external contributions. The choice must also establish clear inbound
contribution terms and a basis for dependency, fixture, generated-material,
plugin, and container-content review.

## Decision

License the project under the GNU Affero General Public License, version 3 or,
at the recipient's option, any later version: SPDX identifier
`AGPL-3.0-or-later`.

Unless a file or directory contains an approved, compatible licence notice:

- project source code and documentation use `AGPL-3.0-or-later`;
- contributions are submitted under the same licence;
- project-authored generated material uses the same licence;
- fixtures and extracted components require recorded provenance and compatible
  licensing before inclusion; and
- plugins distributed in this repository use the same licence.

Interoperability with an external service or separately distributed plugin
does not by itself establish compatibility. Each dependency, bundled asset,
copied component, generated input, container layer, and distribution boundary
still requires an explicit licence and provenance review.

Relicensing requires a new governance decision and all permissions legally
required at that time. Maintainer status alone does not imply ownership of
other contributors' copyright.

## Consequences

### Positive

- Users of modified network-hosted versions receive the corresponding source
  rights required by the licence.
- Contributors and downstream users have explicit redistribution,
  modification, and source-access terms.
- Inbound and outbound project contribution terms are aligned.
- The choice fits a self-hosted service whose value is exposed over a network.

### Negative

- Some organizations prohibit or avoid AGPL-licensed software.
- Dependency, plugin, asset, and distribution-boundary reviews require
  continuing licence expertise.
- Combining the project with incompatibly licensed material is prohibited even
  when the combination would be technically convenient.

### Neutral or deferred

- A third-party notice inventory and automated policy enforcement are
  implemented with the dependency and documentation quality gates.
- No contributor licence agreement is adopted by this decision.
- This ADR does not decide whether separately maintained integrations are one
  combined work; that requires case-specific review.

## Alternatives considered

### Apache-2.0

Apache-2.0 offers broad commercial compatibility and an explicit patent grant,
but it permits modified hosted versions to remain closed. That does not match
the selected reciprocity goal for this network service.

### AGPL-3.0-only

Version 3 only would prevent recipients from choosing a later published AGPL
version. The project prefers the conventional `or-later` grant so future
compatibility can improve without requiring a project-wide relicensing event.

## Validation

- The repository root contains the canonical GNU AGPL version 3 text.
- README, contribution, governance, dependency, release, and publication
  documentation identify `AGPL-3.0-or-later` consistently.
- Dependency and documentation gates verify the licence file, SPDX identity,
  compatible dependency policy, notices, and provenance before publication.
- The private-to-public audit treats unresolved licence or notice findings as
  a stop condition.
