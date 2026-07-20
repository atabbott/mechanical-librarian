# Data Privacy

Mechanical Librarian is designed for personal libraries, but library metadata and reading history can still reveal sensitive interests. Privacy is therefore an architectural property, not only a deployment preference.

## Principles

- Process locally by default.
- Collect and retain only what is needed for an explainable workflow.
- Make every external disclosure visible and configurable.
- Keep secrets separate from ordinary application data.
- Let users export and delete project-owned data without touching their media library.
- Never make telemetry a condition of use.

## Data classes

### Secrets

Examples include target-service tokens, reviewer credentials, session keys, and encryption material.

Secrets must:

- be redacted from logs, errors, audit events, exports, and support bundles;
- never be included in AI prompts;
- use the narrowest permissions available; and
- be replaceable without rewriting historical records.

### Private library data

Examples include titles, authors, identifiers, filenames, folder structure, covers, descriptions, reading state, and target-service URLs.

Private library data remains inside the deployment unless a configured provider or reviewer requires a disclosed subset. The product must document that subset before the integration is enabled.

### Content evidence

Examples include embedded metadata, table-of-contents text, audio tags, chapters, duration, and bounded excerpts used to identify a work or edition.

Evidence extraction should prefer structured fields over prose. Whole books, whole audio files, and audiobook transcripts are outside the initial product scope and must not be sent to an external reviewer.

Initial EPUB extraction follows the
[opening-ebook evidence contract](../product/opening-ebook-evidence.md). It
returns package facts or minimum cited spans from bounded opening resources in
an explicitly owner-invoked local response. F07a persists none of that response.
Complete decoded documents, navigation trees, repaired trees, unrestricted
XHTML, and unrelated body prose are transient and excluded from logs, ordinary
APIs, reviewer packets, exports, and support bundles. Future durable F07b state
may retain only the accepted bounded evidence contract, never decoded or
diagnostic markup.

Private EPUB compatibility qualification captures raw local reports only in
bounded process memory, reduces them to controlled aggregate outcome, field,
warning, parser-mode, limit, runtime, and privacy categories, and then discards
them. Repository, CI, issue, and pull-request evidence contains no private
filename, path, member name, title, author, identifier, corpus/source hash,
metadata value, excerpt, citation, per-book result, raw report, or publication
content.

The F10a synthetic review case retains only the bounded canonical F09a manifest
and F06 title provenance needed to explain the case. It persists no database or
media path, generated EPUB bytes or descriptors, credential, raw provider
request or response, provider upstream identifier, or fabricated ordinary
evidence-reference record. The supported report uses the same bounded local
projection and never sends it to a provider or reviewer.

Disabled F09f discloses nothing and returns before private-query access.
Enabled activation durably accepts work before disclosing only canonical
ISBN-13 and, for 978 identities, its exact equivalent ISBN-10. Local title is
never sent and its comparison remains transient. The integration otherwise
sends only the fixed GraphQL operation and minimum allowlisted data needed for
its bounded Hardcover lookup.
It does not send a target-service identifier, library or connection identity,
filename, media or database path, excerpt, cover, rich metadata, raw provider
payload, or unrelated library field. The account credential remains a secret
reference resolved only when the provider is enabled and is never shared with
the target service or another consumer.

Durable F09f state contains only an opaque contract-bound work key, closed
workflow/attempt evidence, structural matched outcome and bounded row count,
and provenance needed to explain freshness, replay, or invalidation. It stores
no ISBN, title, provider book/edition identifier, or provider payload. Raw
requests, responses, headers, tokens, secret
reference values, paths, unrestricted upstream identifiers, traffic captures,
and provider caches are not retained. F09g retains the minimum bounded local,
target, and provider projections needed to explain its proposal or exact typed
zero-proposal result; valid local evidence survives provider failure.

F09f/F09g provider observations and report results use documented provider-data
retention and deletion classes and are included in project-owned export and
deletion behavior without implying deletion at Hardcover, Audiobookshelf, or
the filesystem. Private live qualification uses disposable state and may
retain only a redacted aggregate result; it retains no library field values,
ISBN, provider identity, raw traffic, secret, path, or private report.

A planned post-foundation capability may derive evidence from bounded audiobook
opening windows under the
[opening-audio evidence contract](../product/opening-audio-evidence.md). Raw
audio samples are transient by default, complete bounded transcript retention
is a separate local configuration choice, and reviewer packets contain only
cited spans. Sending any audio to a hosted transcription service requires an
explicit destination-specific opt-in and data-use disclosure.

### Operational data

Examples include job timing, error codes, capability flags, resource use, and software versions. Operational data may be included in an opt-in support bundle after redaction and preview.

## External processing

For every metadata provider or AI backend, the product must disclose:

- which fields leave the deployment;
- the purpose of the transfer;
- whether the provider requires an account or credential;
- the provider's relevant retention controls, where known;
- what functionality is lost when the integration is disabled; and
- how to test the integration without sending full library data.

The reviewer receives the smallest structured evidence packet that can answer the decision question. Candidate retrieval and target mutation remain separate capabilities.

Reviewer packets and ordinary evidence API responses use separate versioned,
allowlisted projections. Only centrally controlled projection-safe fact classes
may enter them; untrusted records cannot self-declare content safe. They never
carry lineage, artifact locations, raw provider or
extractor payloads, unrestricted excerpts, whole works, production paths, or
credentials. Domain representations and exception diagnostics redact private
field values, excerpts, warnings, and evidence content.

## Retention

Schema 8 stores logical mount names and relative media paths only in the private
application database, together with bounded cited opening reports. Opaque
selectors, job events, CLI output, logs, and qualification aggregates never
contain a path, filename, service item ID, source hash, title, identifier,
excerpt, citation, or raw report. Database backups and support access must
therefore be protected as library metadata. Qualification uses a disposable
shadow copy and deletes its selector map, database, WAL, and SHM state.

Default retention should preserve enough history to explain and reverse changes while avoiding indefinite storage of bulky artifacts.

Retention classes must be configurable for:

- immutable audit records;
- evidence and candidate payloads;
- downloaded cover candidates;
- job logs;
- backups and rollback material;
- rejected review cases; and
- generated support bundles.

Deletion must be a planned, logged operation. Active apply plans, unresolved rollback records, legal notices, and configured minimum recovery points are protected from ordinary retention cleanup.

## Export and deletion

Users must be able to:

- export configuration with secrets removed;
- export review, audit, and decision history in documented formats;
- delete cached provider data and generated artifacts;
- delete a Mechanical Librarian connection without deleting target items or media; and
- reset the application database through an explicit destructive workflow.

Project-owned deletion must never imply deletion from Audiobookshelf, CWA, or the filesystem unless the user selects a separate, clearly scoped target operation.

## Telemetry

The initial product should ship with no outbound product analytics. If telemetry is introduced later, it must be:

- opt-in;
- documented field by field;
- independent from update checks;
- disabled without loss of core functionality; and
- governed by a new ADR and privacy review.

## Logs and diagnostics

Logs should use stable item IDs and correlation IDs in preference to titles and paths. Diagnostic views may resolve those IDs locally for the authenticated user. See [Observability](../operations/observability.md) and [Security architecture](security.md).

## Review checklist

Any feature that adds a data source, external destination, persistent field, export, or log property must answer:

1. What data is involved?
2. Why is it required?
3. Where is it processed and stored?
4. How is it protected and redacted?
5. How long is it retained?
6. How can the user inspect, export, and delete it?
7. What safe behavior remains when the feature is disabled or unavailable?
