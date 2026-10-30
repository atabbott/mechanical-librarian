# Release Engineering

No release has been published. The gates on this page apply from 1.0.0, the
first public release. Checkpoint numbering before then is in
[Work management](work-management.md#milestones-and-checkpoint-versioning),
and the rules for persisted state are in
[Compatibility](../reference/compatibility.md).

## Build

- Build in CI from a tagged, reviewed commit, with a minimal runtime stage.
- Pin the base image and direct dependencies.
- Publish only architectures that were tested.
- Embed version, commit, build time, schema compatibility and licence metadata.
- Generate an SBOM and build provenance, and scan the final image.

## Publication

- Publish immutable semantic-version tags and digests; `latest` is only a
  convenience pointer.
- Sign images and artifacts, and publish checksums, SBOM, provenance,
  changelog, migration notes and known issues.
- Ship the `AGPL-3.0-or-later` licence and required third-party notices with
  every distribution.

## Upgrades

Once a release supports upgrades, an upgrade validates the current schema and
free space, makes or verifies a backup, takes an upgrade lock, migrates
transactionally where possible, validates the result, records it, and starts
background work only after readiness passes. A failed migration leaves writes
disabled and reports how to recover.

## Release qualification

All CI gates pass; upgrades from every supported prior release pass; a backup
and restore drill passes; container security and secret-isolation tests pass;
synthetic end-to-end curation passes; documentation matches the artifacts; and
the release candidate has run in report-only mode before production writes are
recommended.
