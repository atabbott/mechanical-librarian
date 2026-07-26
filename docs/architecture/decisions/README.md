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
| [0015](0015-isolated-shadow-reviewer.md) | accepted | Isolate one exact qualification-only Codex shadow reviewer |

Use [the template](template.md) for new decisions. Never edit the substance of
an accepted ADR to make history match current preference; supersede it.
