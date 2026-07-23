# Mechanical Librarian

Mechanical Librarian is a self-hosted, evidence-first metadata curator for
ebook and audiobook libraries. The project is pre-release; there is no
production image, supported release, or write-capable runtime yet.

## Current checkpoint

The 0.4 development checkpoint installs one synchronous, report-only command:

```text
mechanical-librarian metadata-report \
  --config /run/secrets/mechanical-librarian.json \
  --database /var/lib/mechanical-librarian/reports.sqlite \
  --connection primary --library ebooks --item ITEM_ID --file-index 0
```

It reads one explicitly selected Audiobookshelf EPUB, extracts bounded title
and ISBN facts in an isolated child, independently observes each explicitly
referenced provider, and stores one neutral canonical JSON report. Success
writes exactly that persisted document and a newline to stdout. The command
has no inventory, review, decision, apply, target-write, or media-write
authority.

Only a clean 0.4 database is supported. Earlier development databases and
retired commands are intentionally not migrated or recognized.

See the [metadata report operations guide](docs/operations/metadata-report.md),
[configuration reference](docs/reference/configuration.md), and
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
