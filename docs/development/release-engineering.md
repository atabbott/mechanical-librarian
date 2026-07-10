# Release Engineering

## Release goals

Releases must be reproducible, attributable, upgradeable, recoverable, and
verifiable by users.

## Versioning

Use semantic versioning after the first stable release. Before stability, every
release still documents breaking configuration, schema, API, and behavior
changes.

## Build

- Build through CI from a tagged, reviewed commit.
- Use a minimal runtime stage separate from build tooling.
- Pin the base image and direct dependencies.
- Produce the supported architecture set explicitly; do not publish an
  untested architecture tag.
- Embed version, commit, build timestamp, schema compatibility, and licence
  metadata.
- Generate an SBOM and build provenance.
- Scan the final image, not only source dependencies.

## Publication

- Publish immutable semantic-version tags and digests.
- Sign images and release artifacts.
- Publish checksums, SBOM, provenance, changelog, migration notes, and known
  issues.
- Treat `latest` as a convenience pointer, never a reproducibility mechanism.

## Upgrade contract

Before changing persisted state:

1. Validate the current schema and available disk space.
2. Create or verify a compatible backup.
3. Acquire an upgrade lock.
4. Run forward migrations transactionally where possible.
5. Validate post-migration invariants.
6. Record the migration result.
7. Start background work only after readiness passes.

Failed migrations leave writes disabled and report a recovery procedure.

## Release qualification

- All required CI gates pass.
- Upgrade tests pass from every supported prior release.
- Backup and restore drill passes.
- Container security and secret-isolation tests pass.
- Synthetic end-to-end curation passes.
- Documentation matches the release artifacts.
- A release candidate has been exercised in report-only mode before production
  write enablement is recommended.
