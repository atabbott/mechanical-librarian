# Mechanical Librarian

Mechanical Librarian is a self-hosted, evidence-first metadata curator for
ebook and audiobook libraries. The project is pre-release; there is no
production image, supported release, or write-capable runtime yet.

## Current checkpoint

The current 0.9 development checkpoint installs a bounded, report-only ebook
evidence workflow. An operator can:

- refresh and inspect a current catalogue across configured Audiobookshelf book
  libraries;
- create and inspect an immutable manifest for explicit selections or all
  eligible EPUBs;
- run that frozen batch serially in the foreground; and
- generate or reopen one complete source-attributed EPUB metadata-and-cover
  evidence report.

The report combines all 16 current Audiobookshelf metadata destinations,
bounded structured EPUB evidence, independent configured Hardcover and Open
Library observations, and validated cover artifacts. One usable ISBN family
keeps the exact ISBN path. Only when no usable ISBN exists may a configured
provider receive one exact title-plus-complete-author work lookup; qualified
work evidence never becomes edition evidence. The provider gallery retains at
most eight sources and four per provider with safe provenance and explicit
truncation. It does not fuse, rank, or propose from provider evidence.
Catalogue and batch operations retain the same read-only boundary. There is no
review, decision, apply, target write, media write, background execution,
automatic retry, or supported release.

Only absent or exact-current pre-release state is supported. Earlier
development databases, manifests, configuration schemas, and retired commands
are intentionally rejected rather than migrated or interpreted.

See the [catalogue guide](docs/operations/catalogue.md),
[foreground batch guide](docs/operations/ebook-evidence-batches.md),
[metadata report guide](docs/operations/metadata-report.md), and
[documentation index](docs/README.md).

## Product direction

The intended product remains one self-contained container beside a media
library, with Audiobookshelf as the first adapter, supported service APIs for
future metadata writes, read-only media mounts by default, explicit safety
gates, and durable auditability. AI reviewers remain untrusted and never
receive service credentials or production write authority.

Do not use real library databases, copyrighted book contents, personal
domains, tokens, or unredacted reports in issues, tests, or pull requests.

## Contributing and licence

Development and review expectations are in [CONTRIBUTING.md](CONTRIBUTING.md).
Mechanical Librarian is licensed under [AGPL-3.0-or-later](LICENSE).
