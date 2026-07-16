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
| [0003](0003-sqlite-default.md) | superseded by [0010](0010-pre-stable-consolidation-baseline.md) | Use SQLite as the default state store |
| [0004](0004-ai-is-untrusted.md) | accepted | Treat AI as an untrusted reviewer |
| [0005](0005-agpl-3-or-later.md) | accepted | License the project under AGPL-3.0-or-later |
| [0006](0006-port-adapters-and-composition-root.md) | accepted | Separate port adapters from runtime infrastructure and use one composition root |
| [0007](0007-transient-epub-package-child.md) | accepted | Isolate EPUB package extraction in a transient descriptor-bound child |
| [0008](0008-transient-epub-opening-parser.md) | accepted | Extend transient EPUB evidence with bounded opening parsing |
| [0010](0010-pre-stable-consolidation-baseline.md) | accepted | Reset the pre-stable state baseline for the 0.4 consolidation checkpoint |

Use [the template](template.md) for new decisions. Never edit the substance of
an accepted ADR to make history match current preference; supersede it.
