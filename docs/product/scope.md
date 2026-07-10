# Scope and Non-Goals

## Initial scope

- One self-contained Linux container.
- Audiobookshelf ebook and audiobook libraries.
- Event-driven incremental processing plus scheduled reconciliation.
- Application-level metadata and cover updates through supported APIs.
- EPUB evidence and read-only audio tags, chapters, and duration.
- Replaceable catalogue/provider adapters.
- Replaceable AI reviewer adapters, beginning with Codex CLI.
- SQLite-backed durable state, job recovery, audit history, and configuration.
- Readiness, backup, validation, apply, verification, rollback, and alerting.
- Private single-user API and bundled interface implementing the
  [review-experience contract](review-experience.md).

## Planned extensions

- AZW3 and MOBI metadata evidence.
- Optional bounded opening-audio transcription and spoken-credit extraction
  under the [opening-audio evidence contract](opening-audio-evidence.md).
- Calibre-Web/Calibre-compatible service adapter.
- Additional AI backends, including API and local models.
- Embedded metadata writes with separate media backup gates.
- Additional notification and identity providers.

## Explicit non-goals for the first stable release

- Deleting media files automatically.
- Unattended duplicate removal or format removal.
- Editing Audiobookshelf or other application databases directly.
- Audiobook transcription.
- OCR or broad PDF text extraction.
- Kubernetes or horizontally scaled deployment.
- Mandatory PostgreSQL, Redis, message broker, or public-cloud account.
- A general media acquisition or download-management system.
- Replacing Audiobookshelf's playback, reading, user, or library-management UI.
- Perfect edition matching as a prerequisite for useful cover improvement.

The audiobook-transcription non-goal applies to the first stable release. The
planned extension is limited to qualified opening-credit windows; general
story-content and whole-book transcription remain outside this scope.

## Compatibility posture

Service adapters must detect and publish their capabilities. Unsupported fields
remain unsupported rather than falling back to undocumented database or UI
automation. A service version change that invalidates a capability must block
affected writes until compatibility is re-established.
