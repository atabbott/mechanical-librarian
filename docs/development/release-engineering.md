# Release Engineering

## Checkpoint and release-publication boundary

The currently numbered delivery milestones are internal pre-release checkpoints.
Completing one does not itself create a tag, GitHub Release, supported version,
or published release artifact, even when release-qualification evidence is one
of its exit criteria.

The first release publication occurs only when a maintainer explicitly selects a
checkpoint containing genuinely usable functionality that provides value to the
owner or another user. This policy deliberately sets no numeric, checklist-based,
or automatic threshold for that judgment and selects neither the checkpoint nor
its version. Once release publication is selected, every applicable build, tag,
signing, provenance, SBOM, release-candidate, qualification, and
release-publication gate in this document remains mandatory.

After the first published release, maintainers may explicitly designate later
milestones as release trains. That designation is not an automatic consequence
of release publication or checkpoint completion.

### 0.4.0 boundary

0.4.0 is specifically a pre-deployment clean-state simplification checkpoint.
Its completion, including the then-installed report demonstration, does
not authorize a tag, GitHub Release, supported version, release artifact, or
publication. It has no supported pre-0.4 upgrade source; its clean-state reset
must not be presented as a release migration. The governing scope and safety
split are recorded in [ADR-0010](../architecture/decisions/0010-0-4-neutral-evidence-baseline.md).

## Release goals

Until the first production release, ADR-0013 requires clean schemas: each
database, report, artifact, configuration, and internal-document schema accepts
only absent or exact-current state during ordinary startup. A schema change may
also deliver one explicit offline exact-predecessor adoption for reconstructible
derived objects in the same change; that bounded preservation operation is not
an upgrade migration. Migration notes and the migration procedure below apply
only after an explicit post-release compatibility decision.

Releases must be reproducible, attributable, upgradeable, recoverable, and
verifiable by users.

## Versioning

Use semantic versioning after the first stable release. Before stability, every
release still documents breaking configuration, schema, API, and behavior
changes. [Checkpoint versioning](work-management.md#checkpoint-versioning)
assigns every milestone's number.

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
- Publish the `AGPL-3.0-or-later` licence and all required third-party notices
  with every source, package, container, and release distribution.
- Treat `latest` as a convenience pointer, never a reproducibility mechanism.

## Upgrade contract

After an explicit post-release migration decision, before changing persisted state:

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
