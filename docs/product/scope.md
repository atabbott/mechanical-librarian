# Scope and Non-Goals

## Initial scope

- One self-contained Linux container.
- Audiobookshelf ebook and audiobook libraries as the first production
  target-service adapter, behind vendor-neutral application/domain contracts.
- Event-driven incremental processing plus scheduled reconciliation.
- Application-level metadata and cover updates through supported APIs.
- EPUB package, bounded opening-matter, navigation, and structural evidence plus
  read-only audio tags, chapters, and duration.
- Replaceable catalogue/provider adapters.
- SQLite-backed durable state, job recovery, audit history, and configuration.
- Readiness, backup, validation, apply, verification, rollback, and alerting.
- Private single-user API and bundled interface implementing the
  [review-experience contract](review-experience.md).

## Planned extensions

- AZW3 and MOBI metadata evidence.
- Optional bounded opening-audio transcription and spoken-credit extraction
  under the [opening-audio evidence contract](opening-audio-evidence.md).
- Calibre-Web/Calibre-compatible service adapter.
- Additional AI backends under the explicitly deferred
  [post-1.0 reviewer-backend plan](future-reviewer-backends.md); none is a
  current product capability or assigned delivery slice.
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

Until the first production release, ADR-0013 applies repository-wide: every
database, report, artifact, configuration, and internal-document schema accepts
only absent or exact-current state. Earlier state fails clearly and is never
silently deleted; a clean rebuild may be required. Compatibility needs a named
present user need, while current-schema restart, replay, and crash safety remain
in scope.

Service adapters must detect and publish their capabilities. Unsupported fields
remain unsupported rather than falling back to undocumented database or UI
automation. A service version change that invalidates a capability must block
affected writes until compatibility is re-established.

Initial Audiobookshelf support does not make its payloads, identifiers, getters,
provider integrations, or provider availability part of the domain contract.
Providerless report-only inventory, local evidence, deterministic no-op and
rejection handling, bounded proposals, and human review remain within scope.

The planned Calibre-Web Automated/Calibre-compatible adapter must use a
supported service API and explicit capability declarations. A CWA-shaped fake
may qualify the common target-service contract, but the initial scope adds no
CWA runtime adapter, database access, UI automation, or production write path.
