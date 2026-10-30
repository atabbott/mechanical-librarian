# Scope and Non-Goals

[PRODUCT.md](../../PRODUCT.md) states the product boundary; this page records
what the first release admits and refuses within it.

## Initial scope

- One self-contained Linux container.
- Audiobookshelf ebook and audiobook libraries as the first production
  target-service adapter, behind vendor-neutral application/domain contracts.
- Interval polling of each library plus owner-triggered scans, with incremental
  per-item preparation.
- Application-level metadata and cover updates through supported APIs.
- EPUB package, bounded opening-matter, navigation, and structural evidence plus
  read-only audio tags, chapters, and duration.
- Replaceable catalogue/provider adapters.
- SQLite-backed durable state, job recovery, audit history, and configuration.
- Readiness, backup, validation, apply, read-back verification and recovery,
  with guarded state shown in Activity.
- Private single-user API and bundled interface implementing the
  [review-experience contract](review-experience.md).

## Planned extensions

- AZW3 and MOBI metadata evidence.
- Optional bounded opening-audio transcription and spoken-credit extraction;
  see [opening-audio evidence](opening-audio-evidence.md).
- Calibre-Web/Calibre-compatible service adapter.
- Further AI reviewer backends beyond the current closed cover and
  description tasks; see [future reviewer backends](future-reviewer-backends.md).
- Embedded metadata writes with separate media backup gates.
- Notification providers and additional identity providers.

## Explicit non-goals for the first stable release

- Deleting media files automatically.
- Unattended duplicate removal or format removal.
- Editing Audiobookshelf or other application databases directly.
- Whole-book or general audiobook transcription. Only bounded opening
  credits are planned.
- OCR or broad PDF text extraction.
- Kubernetes or horizontally scaled deployment.
- Mandatory PostgreSQL, Redis, message broker, or public-cloud account.
- A general media acquisition or download-management system.
- Replacing Audiobookshelf's playback, reading, user, or library-management UI.
- Perfect edition matching as a prerequisite for useful cover improvement.

## User promise

Beyond the operating context in PRODUCT.md, first-release scope commits to:

- new items remain immediately available for consumption, with curation
  happening asynchronously after files stabilize;
- the normal review queue stays small, and automatic decisions are visible in
  history without demanding routine review;
- failures stop affected work safely and explain how to recover; and
- configuration and the whole `/data` volume can be backed up and restored
  with ordinary tools, without vendor lock-in.

## Measures of success

- Most new items become acceptably correct without human action.
- Provider, AI, authentication, backup, or apply failures cannot silently
  corrupt production metadata.
- Reprocessing an unchanged item produces no additional production change.
- Users can understand why a field changed and which evidence supported it.
- Recovery from a failed upgrade, expired AI login, or bad apply is documented
  and testable.
- A new contributor can locate the governing contract for a behaviour without
  relying on maintainer memory.
- The product is installable and operable without knowledge of its
  implementation or the original author's environment.

## Compatibility posture

Persisted state follows the pre-release clean-state rule in
[compatibility](../reference/compatibility.md#the-clean-state-rule).

Service adapters must detect and publish their capabilities. Unsupported fields
remain unsupported rather than falling back to undocumented database or UI
automation. A service version change that invalidates a capability must block
affected writes until compatibility is re-established.

Initial Audiobookshelf support does not make its payloads, identifiers, getters,
provider integrations, or provider availability part of the domain contract.
Providerless report-only inventory, local evidence, deterministic no-op and
rejection handling, bounded proposals, and human review remain within scope.

The planned Calibre-Web Automated/Calibre-compatible adapter must use a
supported service API and explicit capability declarations. There is no CWA
runtime adapter, database access, UI automation or write path today.
