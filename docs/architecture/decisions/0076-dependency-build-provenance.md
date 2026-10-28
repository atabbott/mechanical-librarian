# ADR-0076: Separate dependency build provenance from semantic qualification

- Status: accepted
- Date: 2026-09-21
- Owners: project maintainers
- Supersedes: ADR-0043 fixed Node/npm version selection only
- Superseded by: ADR-0073 compact current records, for source closure representation only; ADR-0087, for the semantic source closure

## Context

Existing dependency upgrades required manual policy-binding rotation even when
only frontend package and lockfile bytes changed. Those inputs describe build
provenance rather than the qualified decision algorithm. Issue #1089 authorizes
managed upgrades, including majors, after the strict five-job public gate.

## Decision

Use the frontend manifest as the exact Node/npm authority and install npm
explicitly in build environments. Existing Renovate-managed upgrades may use
the [platform auto-merge exception](../../development/exact-head-merging.md#renovate-platform-auto-merge-exception).
New dependencies, replacements and compatibility-policy changes remain manual.

Create fixed one-hop [F46v9](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v9.json),
[F19av26](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v26.json) and
[F20v40](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v40.json)
successors to the exact current records. Their shared source amendment removes exactly `frontend/package.json`
and `frontend/package-lock.json` from the live semantic closure. Every other
source, option and measurement remains inherited, except explicitly recorded
binding and test/documentation hashes. Historical record bytes never change.
Predecessor decisions remain readable but cannot grant new decision or effect
authority. No schema, algorithm, consent or write-safety behavior changes.

## Consequences

Dependency changes retain exact candidate, lockfile, asset-manifest, deterministic
build, wheel RECORD and installed-artifact provenance without claiming new
policy measurements. Semantic changes still fail the source guard. CI cannot
prove arbitrary upstream code harmless; known failures block merging and require
a manual fix or reviewed revert. FFmpeg and contained-reviewer checksum bundles
remain outside managed scope. This decision grants no release authority.

## Validation

Actual Renovate processing proves extraction, grouping and representative updates.
Source-partition mutation tests reject semantic drift and unknown exclusions.
Base-produced decisions reopen unchanged, fail new authority, and cannot reach
the scheduler's authority/capability ports. Current producer rederivation checks
binding-derived identities and existing paired effect behavior.

## Superseded closure representation

The Decision paragraph beginning “Create fixed one-hop” is historical. ADR-0073's
compact-current amendment supersedes its full-record closure representation and
its claim that every non-dependency source remains semantic. The explicit reviewed
shared partition now owns that classification. Historical bytes and decoding
permissions remain frozen; dependency automation, exact toolchain selection,
auto-merge eligibility and all five remote gates remain unchanged.
