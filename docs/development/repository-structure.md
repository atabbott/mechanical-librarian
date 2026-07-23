# Repository structure

```text
src/mechanical_librarian/
├── domain/          # closed report and EPUB values; no I/O
├── application/     # configuration values, orchestration, owned protocols
├── adapters/
│   ├── library_services/    # scoped Audiobookshelf item reader
│   ├── evidence_extractors/ # private EPUB package/opening implementation
│   ├── providers/           # fixed Hardcover observation
│   └── persistence/         # two-table SQLite reports
├── infrastructure/  # strict config, private files, local media
├── interfaces/cli/  # sole metadata-report surface
└── bootstrap/       # explicit composition and isolated child entrypoint
```

Tests are grouped into unit, integration, and static architecture suites.
Synthetic EPUBs are generated in memory; no media files or production payloads
are committed. `scripts/generate_configuration_schema.py` is the sole product
generator and must reproduce `docs/reference/config-v2.schema.json`.

The dependency direction and responsibilities are defined by the
[architecture overview](../architecture/overview.md). Empty packages for
future workers, reviewers, notifications, migrations, HTTP APIs, or frontend
surfaces are not retained before they have a current caller.
