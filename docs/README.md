# Documentation

Documentation is part of the product and follows the same review, testing, and
release standards as code.

## Project policies

- [Contributing](../CONTRIBUTING.md)
- [Governance](../GOVERNANCE.md)
- [Security reporting](../SECURITY.md)
- [Support](../SUPPORT.md)
- [Code of Conduct](../CODE_OF_CONDUCT.md)

## Product

- [Vision](product/vision.md)
- [Scope and non-goals](product/scope.md)
- [Automation policy](product/automation-policy.md)
- [Bounded EPUB package and opening-matter evidence](product/opening-ebook-evidence.md)
- [Bounded opening-audio evidence](product/opening-audio-evidence.md)
- [Review experience](product/review-experience.md)
- [Post-1.0 reviewer backends](product/future-reviewer-backends.md)
- [Roadmap](product/roadmap.md)
- [Initial build plan](product/initial-build-plan.md)
- [Delivery slices](product/delivery-slices.md)
- [Licence decision](product/license-decision.md)

## Architecture

- [Overview](architecture/overview.md)
- [Domain model](architecture/domain-model.md)
- [Security and threat model](architecture/security.md)
- [Data privacy](architecture/data-privacy.md)
- [AI trust boundary](architecture/ai-boundary.md)
- [HTTP API contract](architecture/http-api.md)
- [Architecture decisions](architecture/decisions/README.md)

## Development

- [Quality strategy](development/quality-strategy.md)
- [Runtime dependency review](development/dependency-review.md)
- [Testing strategy](development/testing-strategy.md)
- [Documentation standards](development/documentation-standards.md)
- [Functional slice delivery](development/slice-delivery.md)
- [Exact-head pull-request merging](development/exact-head-merging.md)
- [Work management](development/work-management.md)
- [Release engineering](development/release-engineering.md)
- [Repository structure](development/repository-structure.md)
- [Prototype extraction policy](development/prototype-extraction.md)
- [DUX prototype extraction record](development/dux-extraction.md)
- [DUX fixture and regression corpus](development/dux-fixture-corpus.md)

## Operations

- [Durable state check](operations/state-check.md)
- [Recoverable synthetic job heartbeat](operations/job-heartbeat.md)
- [Synthetic inventory round trip](operations/synthetic-inventory.md)
- [Audiobookshelf inventory](operations/audiobookshelf-inventory.md)
- [Audiobookshelf connection check](operations/audiobookshelf-connection.md)
- [EPUB package evidence report](operations/epub-package-report.md)
- [EPUB opening-matter evidence report](operations/epub-opening-report.md)
- [Durable EPUB opening evidence](operations/epub-opening-evidence.md)
- [EPUB fixture materialization](operations/epub-fixture-materialization.md)
- [Synthetic title proposal report](operations/synthetic-title-proposal-report.md)
- [Hardcover title proposal report](operations/hardcover-title-proposal-report.md)
- [Synthetic title review case report](operations/synthetic-title-review-case-report.md)
- [Deployment model](operations/deployment.md)
- [Backups and recovery](operations/backups-and-recovery.md)
- [Observability and supportability](operations/observability.md)
- [Upgrades and migrations](operations/upgrades.md)

## Reference

- [Configuration contract](reference/configuration.md)
- [Compatibility policy](reference/compatibility.md)
- [F09b Hardcover proof-of-concept contract](reference/hardcover-f09b-contract.md)
- [Lifecycle and state machines](reference/state-machines.md)
- [Glossary](reference/glossary.md)

## Documentation types

- Product documents explain outcomes, scope, and sequencing.
- Architecture documents explain boundaries, constraints, and system shape.
- ADRs record durable decisions and their consequences.
- Development documents define contribution and quality expectations.
- Operations documents define deployment, recovery, and diagnosis.
- Reference documents specify exact contracts and vocabulary.

New documents must have one clear owner and purpose. Avoid duplicating the same
rule in multiple files; link to the authoritative source instead.
