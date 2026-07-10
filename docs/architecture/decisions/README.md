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
| [0003](0003-sqlite-default.md) | accepted | Use SQLite as the default state store |
| [0004](0004-ai-is-untrusted.md) | accepted | Treat AI as an untrusted reviewer |
| [0005](0005-agpl-3-or-later.md) | accepted | License the project under AGPL-3.0-or-later |

Use [the template](template.md) for new decisions. Never edit the substance of
an accepted ADR to make history match current preference; supersede it.
