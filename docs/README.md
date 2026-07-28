# Documentation

## Current 0.10 development runtime

- [Current book catalogue](operations/catalogue.md)
- [Foreground ebook evidence batches](operations/ebook-evidence-batches.md)
- [Metadata report operation](operations/metadata-report.md)
- [ProposalSet operation](operations/proposal-set.md)
- [Review-case admission](operations/review-case.md)
- [Qualification-only shadow review](operations/shadow-review.md)
- [Observability](operations/observability.md)
- [Deployment model](operations/deployment.md)
- [Configuration](reference/configuration.md)
- [Compatibility and clean-state boundary](reference/compatibility.md)
- [Architecture overview](architecture/overview.md)
- [Application service contracts](architecture/service-port-contracts.md)
- [Security and threat model](architecture/security.md)
- [Data privacy](architecture/data-privacy.md)
- [ADR-0010: clean-state neutral evidence baseline](architecture/decisions/0010-0-4-neutral-evidence-baseline.md)
- [ADR-0011: isolated EPUB inspector](architecture/decisions/0011-isolated-epub-inspector.md)
- [ADR-0012: metadata-report activation](architecture/decisions/0012-metadata-report-activation.md)
- [ADR-0013: pre-release clean schema](architecture/decisions/0013-pre-release-clean-schema.md)
- [ADR-0014: review-case admission](architecture/decisions/0014-review-case-admission.md)
- [ADR-0015: isolated qualification-only shadow review](architecture/decisions/0015-isolated-shadow-reviewer.md)

## Development

- [Quality strategy](development/quality-strategy.md)
- [Documentation standards](development/documentation-standards.md)
- [Recoverability qualification](development/recoverability-qualification.md)
- [Testing strategy](development/testing-strategy.md)
- [Repository structure](development/repository-structure.md)
- [Functional slice delivery](development/slice-delivery.md)
- [Exact-head merging](development/exact-head-merging.md)
- [Work management](development/work-management.md)
- [Release engineering](development/release-engineering.md)

## Research

- [Metadata-provider retry assessment](research/metadata-provider-retry-assessment.md)
- [Google Books 503 retry policy](research/google-books-retry-policy.md)

These notes justify the current fixed provider-local policy; they do not grant
runtime authority beyond the operations, compatibility, and security contracts
listed above.

## Product and governance

- [Vision](product/vision.md)
- [Scope](product/scope.md)
- [Roadmap](product/roadmap.md)
- [Delivery slices](product/delivery-slices.md)
- [0.7.0 metadata qualification scope](product/0-7-metadata-qualification.md)
- [0.8.0 independent multi-provider evidence contract](product/0-8-working-plan.md)
- [0.9.0 explainable ebook proposal plan](product/0-9-working-plan.md)
- [Architecture decisions](architecture/decisions/README.md)
- [Contributing](../CONTRIBUTING.md)
- [Security reporting](../SECURITY.md)
- [Support](../SUPPORT.md)

Historical product and architecture documents describe future direction, not
installed 0.10 development authority. Current operations and compatibility are
defined only by the first section above.
