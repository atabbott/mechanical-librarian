# Architecture Decision Records

ADRs record durable choices that constrain future implementation. They explain
context and consequences rather than reproducing code.

## Status vocabulary

- `proposed`: under discussion and not binding;
- `accepted`: current project decision;
- `superseded`: replaced by a newer ADR;
- `deprecated`: still present but scheduled for removal;
- `rejected`: considered and deliberately not chosen.

## Index

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-single-container.md) | accepted | Ship one self-contained container |
| [0002](0002-audiobookshelf-first.md) | accepted | Build the first adapter for Audiobookshelf |
| [0003](0003-sqlite-default.md) | superseded by 0012; pre-release schema provisions superseded by 0013 | Use SQLite as the default state store |
| [0004](0004-ai-is-untrusted.md) | accepted | Treat AI as an untrusted reviewer |
| [0005](0005-agpl-3-or-later.md) | accepted | License the project under AGPL-3.0-or-later |
| [0006](0006-port-adapters-and-composition-root.md) | accepted; migration provisions superseded by 0012 and 0013 | Separate port adapters from runtime infrastructure and use one composition root |
| [0007](0007-transient-epub-package-child.md) | superseded by 0011 | Isolate EPUB package extraction in a transient descriptor-bound child |
| [0008](0008-transient-epub-opening-parser.md) | accepted | Extend transient EPUB evidence with bounded opening parsing |
| [0009](0009-durable-epub-opening-evidence.md) | superseded by 0012; pre-release schema provisions superseded by 0013 | Bind durable EPUB opening evidence to inventory snapshots |
| [0010](0010-0-4-neutral-evidence-baseline.md) | accepted | Set the 0.4 clean-state neutral evidence baseline |
| [0011](0011-isolated-epub-inspector.md) | accepted | Keep EPUB package inspection private to its isolated child |
| [0012](0012-metadata-report-activation.md) | accepted; pre-release schema policy superseded by 0013 | Activate the 0.4 clean-state two-table DELETE-mode metadata-report runtime; 0.5 later amended it |
| [0013](0013-pre-release-clean-schema.md) | accepted | Require clean schemas until the first production release |
| [0014](0014-review-case-admission.md) | accepted | Atomically admit immutable review cases from current ProposalSets |
| [0015](0015-isolated-shadow-reviewer.md) | superseded by 0016 | Isolate one exact qualification-only Codex shadow reviewer |
| [0016](0016-freeze-explainable-shadow-reviewer.md) | prompt/profile/result tuple superseded by 0026; publication-date packet projection superseded by 0019 | Freeze explainable publication-date shadow review |
| [0017](0017-bounded-failure-diagnostics.md) | accepted | Bound provider and reviewer failure diagnostics |
| [0018](0018-closed-title-creator-comparison.md) | accepted | Close title and complete-creator comparison |
| [0019](0019-publication-date-precision-comparison.md) | accepted | Close precision-aware publication-date comparison and manifestation binding |
| [0020](0020-field-specific-acceptable-outcomes.md) | accepted | Model field-specific acceptable metadata outcomes without collapsing evidence |
| [0021](0021-coherent-bibliographic-combinations.md) | accepted | Bind dependent bibliographic advice to coherent combinations |
| [0022](0022-decision-ready-reviewer-packets.md) | accepted | Project source-validated decision summaries without deciding |
| [0024](0024-contain-qualification-reviewer-provider-state.md) | accepted | Separate primary reviewer credentials from disposable provider state and execution provenance |
| [0025](0025-compile-policy-admissible-review-actions.md) | accepted; structured-output union mechanism superseded by 0027 | Compile packet-only policy-admissible reviewer actions before dispatch |
| [0026](0026-bind-shadow-recommendation-semantics.md) | accepted; exact-current request version advanced by 0027 | Bind final candidate, keep-current, and manual reviewer semantics |
| [0027](0027-provider-admissible-shadow-output-schema.md) | accepted; exact-current request version and recommendation-array construction superseded by 0028 | Use a provider-admissible exact-action union and bounded schema-rejection envelope |
| [0028](0028-normalize-transient-reviewer-json-and-partition-combinations.md) | accepted | Normalize transient reviewer JSON and partition finite actions by one allowed bibliographic combination |
| [0029](0029-provider-independent-shadow-decision-preview.md) | accepted | Add a provider-independent, read-only deterministic shadow decision preview |
| [0030](0030-authenticated-review-web-boundary.md) | accepted | Use one authenticated Python review web boundary |
| [0031](0031-persisted-review-preparation.md) | accepted | Persist progressive review preparation beside the review boundary |
| [0033](0033-versioned-policy-draft-preview.md) | accepted | Persist immutable metadata-policy drafts before activation |
| [0034](0034-atomic-policy-generation-cutover.md) | accepted | Cut over metadata policy with one atomic generation |
| [0035](0035-persist-non-authoritative-policy-preview-outcomes.md) | accepted | Persist non-authoritative policy Preview outcomes |
| [0036](0036-qualified-decision-only-automatic-policy.md) | accepted | Qualify decision-only automatic policy defaults |
| [0037](0037-origin-neutral-guarded-field-effects.md) | accepted | Use one origin-neutral guarded field-effect boundary |
| [0038](0038-bounded-single-container-operations.md) | accepted | Bound operational evidence inside the single container |
| [0039](0039-continuous-guarded-application.md) | accepted | Run continuous guarded application without target CAS |
| [0040](0040-source-only-contributor-workbench.md) | accepted | Separate the runtime launcher from the contributor workbench |
| [0041](0041-contract-contained-reviewer-kernel.md) | accepted; no-cover-transport provision superseded by 0045 | Contract the contained-reviewer kernel and retire F11 product workflow |
| [0042](0042-compose-authenticated-browser-operations.md) | accepted | Compose authenticated browser operations before transport |
| [0043](0043-reproducible-react-browser-platform.md) | accepted | Build a reproducible, currently unactivated React browser platform |
| [0044](0044-activate-persistent-react-workspace.md) | accepted | Activate one persistent authenticated React workspace |
| [0045](0045-bind-report-cover-packets.md) | accepted | Bind report cover packets to isolated reviewer images |
| [0046](0046-observe-report-bound-covers.md) | accepted | Observe report-bound covers with a small closed vocabulary |
| [0047](0047-adjudicate-closed-cover-policy.md) | accepted; source-only activation provision superseded by 0049 | Adjudicate closed cover policy deterministically |
| [0048](0048-compile-rich-metadata-policy-preview.md) | accepted | Compile closed rich-metadata policy previews |
| [0049](0049-owner-confirmed-cover-considerations.md) | accepted | Activate owner-confirmed cover considerations |

Use [the template](template.md) for new decisions. Never edit the substance of
an accepted ADR to make history match current preference; supersede it.
