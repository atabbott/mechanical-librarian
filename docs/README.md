# Documentation

Every live document in this repository is listed here, including the
repository-root files. A document under `docs/` or at the root that is not
listed has been deleted; git history is its archive.

Every section except **Historical record** is current authority. The historical
section is record: those documents describe the delivery context they were
written in, not current installed behaviour, and each row names what still
depends on it.

## Repository root

- [AGENTS.md](../AGENTS.md) — the agent operating guide, and the entry point
  that names what is required reading
- [README.md](../README.md) — the current checkpoint and runtime boundary
- [PRODUCT.md](../PRODUCT.md) — the single product-boundary statement
- [CONTRIBUTING.md](../CONTRIBUTING.md) — including the table reconciling every
  documented check against the workflow that enforces it
- [DESIGN.md](../DESIGN.md) — the durable visual design record; `.impeccable/`
  derives `design.json` from it
- [CHANGELOG.md](../CHANGELOG.md)
- [SECURITY.md](../SECURITY.md) — vulnerability reporting
- [SUPPORT.md](../SUPPORT.md)
- [GOVERNANCE.md](../GOVERNANCE.md) and
  [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) — GitHub community-health files,
  found by convention rather than by link

## Operations

- [Current book catalogue](operations/catalogue.md)
- [Foreground ebook evidence batches](operations/ebook-evidence-batches.md)
- [Metadata report operation](operations/metadata-report.md)
- [ProposalSet operation](operations/proposal-set.md)
- [Review-case admission](operations/review-case.md)
- [Provider recovery](operations/provider-recovery.md)
- [Observability](operations/observability.md)
- [Cold-start report](operations/cold-start-report.md)
- [Deployment model](operations/deployment.md)
- [Backups and recovery](operations/backups-and-recovery.md)
- [Upgrades](operations/upgrades.md)

## Reference

- [Configuration](reference/configuration.md)
- [Configuration schema](reference/config-v8.schema.json)
- [Example configuration](examples/synthetic-config.json)
- [Compatibility and clean-state boundary](reference/compatibility.md)
- [Glossary](reference/glossary.md)
- [Review HTTP API](reference/review-openapi-v1.json) — the generated authority
  for the `/api/v1` surface; its boundary decision is
  [ADR-0030](architecture/decisions/0030-authenticated-review-web-boundary.md)
- [F19a qualification manifest](reference/f19a-qualification-manifest-v4.json) —
  `F19A_QUALIFICATION_MANIFEST_SHA256` in
  `src/mechanical_librarian/domain/automatic_decision.py` pins this file's
  sha256; changing a byte changes what automatic authority admits
- [F20 rich-metadata qualification candidate](reference/f20-rich-metadata-qualification-candidate-v4.json) —
  `tests/unit/test_rich_metadata_qualification_manifest.py` and the ruff and
  mypy exemptions in `pyproject.toml` are bound to the sources it freezes

## Architecture

- [Architecture overview](architecture/overview.md)
- [Domain model](architecture/domain-model.md)
- [Application service contracts](architecture/service-port-contracts.md)
- [Security and threat model](architecture/security.md)
- [Data privacy](architecture/data-privacy.md)
- [AI trust boundary](architecture/ai-boundary.md)
- [Architecture decisions](architecture/decisions/README.md)

## Development

- [Testing and quality strategy](development/testing-strategy.md)
- [Dependencies](development/dependencies.md)
- [Documentation standards](development/documentation-standards.md)
- [Repository structure](development/repository-structure.md)
- [Functional slice delivery](development/slice-delivery.md)
- [Exact-head merging](development/exact-head-merging.md)
- [Work management](development/work-management.md)
- [Release engineering](development/release-engineering.md)
- [Recoverability qualification](development/recoverability-qualification.md)
- [Contributor workbench](development/contributor-workbench.md)
- [Foreground evidence batch](development/foreground-evidence-batch.md)
- [Provider evidence replay qualification](development/provider-evidence-replay.md)

## Product and governance

The product boundary itself is [PRODUCT.md](../PRODUCT.md), listed under
Repository root above.

- [Scope and non-goals](product/scope.md)
- [Review experience](product/review-experience.md)
- [Automation policy](product/automation-policy.md)
- [Roadmap](product/roadmap.md)
- [Delivery slices](product/delivery-slices.md)
- [Opening-ebook evidence contract](product/opening-ebook-evidence.md)
- [Opening-audio evidence contract](product/opening-audio-evidence.md)
- [Post-1.0 reviewer backends](product/future-reviewer-backends.md)

## Research

- [Metadata-provider retry assessment](research/metadata-provider-retry-assessment.md)
- [Google Books 503 retry policy](research/google-books-retry-policy.md)

These notes justify the current fixed provider-local retry policy; they do not
grant runtime authority beyond the operations, compatibility, and security
contracts above. The assessment is the entry point and the Google Books note is
the worked case it cites.

## Historical record

Retained, with what depends on each:

- [Router platform comparison](development/router-platform-comparison.md) —
  cited by accepted
  [ADR-0043](architecture/decisions/0043-reproducible-react-browser-platform.md),
  whose substance may not be edited
- [0.14.1 interface-fidelity reconciliation](development/0-14-1-interface-fidelity.md) —
  cited by accepted
  [ADR-0044](architecture/decisions/0044-activate-persistent-react-workspace.md)
  and by `README.md` for the protected-reference boundary
- [Initial build plan](product/initial-build-plan.md) — cited by
  [work management](development/work-management.md) as the capability record,
  and the only inbound link to the opening-ebook evidence contract above
- [Semantic cover corpus readiness](development/semantic-cover-corpus-readiness.md) —
  cited by the [contributor workbench](development/contributor-workbench.md)
- [0.8.0 independent multi-provider evidence contract](product/0-8-working-plan.md) —
  cited by [delivery slices](product/delivery-slices.md) and the
  [roadmap](product/roadmap.md)
- [0.9.0 explainable ebook proposal plan](product/0-9-working-plan.md) — cited
  by [delivery slices](product/delivery-slices.md) and the
  [roadmap](product/roadmap.md)
- [0.14.0 release-candidate reconciliation](development/0-14-release-candidate.md) —
  the frozen evidence behind the 0.14.0 checkpoint, and the document the 0.14.1
  reconciliation above supersedes rather than replaces
- [Interface fidelity living audit](product/interface-fidelity-living-audit.md) —
  the frozen per-surface evidence behind the 0.14.1 checkpoint `README.md`
  states
- [0.7.0 metadata qualification scope](product/0-7-metadata-qualification.md) —
  the qualification scope the current fixed proposal policy was frozen against
- [Rich-metadata qualification candidate](product/rich-metadata-qualification.md) —
  the prose record of the F20 candidate the reference manifest above pins
