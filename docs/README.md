# Documentation

Start with the [README](../README.md) for what Mechanical Librarian does and
where it stands, and [PRODUCT.md](../PRODUCT.md) for the boundaries it keeps.
Every live document is listed here or linked from a page listed here.

## Running it

- [Deployment](operations/deployment.md): the container, first run, HTTPS and
  reviewers
- [Backups and recovery](operations/backups-and-recovery.md)
- [Upgrades](operations/upgrades.md)
- [Observability](operations/observability.md)
- [Provider recovery](operations/provider-recovery.md)
- [Cold-start report](operations/cold-start-report.md)

How the pipeline prepares each work, stage by stage:
[catalogue](operations/catalogue.md),
[ebook evidence batches](operations/ebook-evidence-batches.md),
[metadata report](operations/metadata-report.md),
[ProposalSet](operations/proposal-set.md) and
[review cases](operations/review-case.md).

## Reference

- [Configuration](reference/configuration.md), its
  [schema](reference/config-v9.schema.json) and an
  [example](examples/synthetic-config.json)
- [Review HTTP API](reference/review-openapi-v1.json) (generated)
- [Compatibility and clean-state rules](reference/compatibility.md)
- [Glossary](reference/glossary.md)
- [F38 contributor-display qualification manifest](reference/f38-contributor-display-qualification-manifest-v1.json)

## Product

- [Scope and non-goals](product/scope.md)
- [Roadmap](product/roadmap.md)
- [Review experience](product/review-experience.md)
- [Automation policy](product/automation-policy.md) and
  [rich-metadata qualification](product/rich-metadata-qualification.md)
- [Opening-ebook evidence](product/opening-ebook-evidence.md) and
  [opening-audio evidence](product/opening-audio-evidence.md)
- [Future reviewer backends](product/future-reviewer-backends.md)
- [Design system](../DESIGN.md)

## Architecture

- [Overview](architecture/overview.md)
- [Domain model](architecture/domain-model.md)
- [Application service contracts](architecture/service-port-contracts.md)
- [SQLite topology](architecture/sqlite-topology.md)
- [Security and threat model](architecture/security.md)
- [Data privacy](architecture/data-privacy.md)
- [AI trust boundary](architecture/ai-boundary.md)
- [Architecture decisions (ADRs)](architecture/decisions/README.md)

## Development

- [CONTRIBUTING.md](../CONTRIBUTING.md) and [AGENTS.md](../AGENTS.md)
- [Testing strategy](development/testing-strategy.md)
- [Dependencies](development/dependencies.md)
- [Documentation standards](development/documentation-standards.md)
- [Work management](development/work-management.md)
- [Delivery and merging](development/slice-delivery.md)
- [Release engineering](development/release-engineering.md)
- [Contributor workbench](development/contributor-workbench.md),
  [provider evidence replay](development/provider-evidence-replay.md),
  [reviewer cost measurement](development/reviewer-cost-measurement.md) and
  [recoverability qualification](development/recoverability-qualification.md)

## Research

- [Metadata-provider retry policy](research/metadata-provider-retry-assessment.md)
- [Audiobook recording-source comparison](research/audiobook-recording-source-comparison.md)

## Project

[CHANGELOG.md](../CHANGELOG.md), [SECURITY.md](../SECURITY.md),
[SUPPORT.md](../SUPPORT.md) and [GOVERNANCE.md](../GOVERNANCE.md).
